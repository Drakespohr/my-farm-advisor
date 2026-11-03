"""Accounts, sessions, profiles, and field access control (SQLite, standard library only).

Security notes:
- Passwords: scrypt (n=2^14, r=8, p=1) with a per-user random salt; constant-time compare.
- Sessions: 256-bit random tokens in an HttpOnly, SameSite=Lax cookie; only a SHA-256 of the
  token is stored, so a leaked database can't be replayed as live sessions.
- CSRF: every state-changing /api request must carry `X-Requested-With: fieldmonitor`.
  Browsers can't add custom headers cross-site without a CORS preflight, which this app
  never grants.
- Login attempts are rate-limited per email and per client address.
- Google sign-in (OAuth 2.0 code flow) is enabled only when GOOGLE_CLIENT_ID and
  GOOGLE_CLIENT_SECRET are set; accounts link by verified email.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SESSION_COOKIE = "fm_session"
SESSION_DAYS = 30
ROLES = ("farmer", "agronomist", "service", "dealer", "buyer")
ROLE_LABELS = {
    "farmer": "Farmer / grower",
    "agronomist": "Agronomist / consultant",
    "service": "Service provider",
    "dealer": "Input & equipment dealer",
    "buyer": "Crop buyer",
}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
MIN_PASSWORD = 10

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT,
  google_sub TEXT UNIQUE,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  display_name TEXT NOT NULL,
  business_name TEXT,
  roles TEXT NOT NULL DEFAULT '[]',
  bio TEXT,
  location TEXT,
  website TEXT,
  phone TEXT,
  updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  created TEXT NOT NULL,
  expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_access (
  field_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner', 'viewer')),
  created TEXT NOT NULL,
  PRIMARY KEY (field_id, user_id)
);
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  price TEXT,
  location TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS listings_by_status ON listings(status, created);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  a_id TEXT NOT NULL REFERENCES users(id),
  b_id TEXT NOT NULL REFERENCES users(id),
  listing_id TEXT,
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  UNIQUE (a_id, b_id, listing_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  sender_id TEXT NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_reads (
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  last_read_id INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (conversation_id, user_id)
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  created TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(9)}"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(test.hex(), dk)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    def __init__(self, limit: int = 10, window_s: float = 900.0) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        t = time.monotonic()
        with self._lock:
            hits = [h for h in self._hits.get(key, []) if t - h < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(t)
            self._hits[key] = hits
            return True


class Accounts:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.db() as c:
            c.executescript(SCHEMA)
        self.login_limiter = RateLimiter()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---- users ---------------------------------------------------------------
    def user_count(self) -> int:
        with self.db() as c:
            return int(c.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.db() as c:
            row = c.execute("SELECT id, email, is_admin, created, google_sub IS NOT NULL AS google FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.db() as c:
            row = c.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return self.get_user(row["id"]) if row else None

    def register(self, email: str, password: str | None, display_name: str, roles: list[str], google_sub: str | None = None) -> dict[str, Any]:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise ValueError("Enter a valid email address.")
        if password is not None and len(password) < MIN_PASSWORD:
            raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
        name = display_name.strip()[:80]
        if not name:
            raise ValueError("Enter your name.")
        roles = [r for r in roles if r in ROLES] or ["farmer"]
        uid = new_id("u")
        with self.db() as c:
            if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise FileExistsError("An account with that email already exists. Log in instead.")
            first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            c.execute(
                "INSERT INTO users (id, email, password_hash, google_sub, is_admin, created) VALUES (?,?,?,?,?,?)",
                (uid, email, hash_password(password) if password else None, google_sub, 1 if first else 0, now()),
            )
            c.execute(
                "INSERT INTO profiles (user_id, display_name, roles, updated) VALUES (?,?,?,?)",
                (uid, name, json.dumps(roles), now()),
            )
        user = self.get_user(uid)
        assert user
        return user

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        with self.db() as c:
            row = c.execute("SELECT id, password_hash FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not row:
            verify_password(password, hash_password("timing-equalizer"))  # don't leak which emails exist via timing
            return None
        return self.get_user(row["id"]) if verify_password(password, row["password_hash"]) else None

    def google_login(self, sub: str, email: str, name: str) -> dict[str, Any]:
        """Find the account by Google id, else link by verified email, else create one."""
        with self.db() as c:
            row = c.execute("SELECT id FROM users WHERE google_sub=?", (sub,)).fetchone()
            if row:
                return self.get_user(row["id"])  # type: ignore[return-value]
            row = c.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
            if row:
                c.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, row["id"]))
                return self.get_user(row["id"])  # type: ignore[return-value]
        return self.register(email, None, name or email.split("@")[0], ["farmer"], google_sub=sub)

    # ---- sessions ------------------------------------------------------------
    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat()
        with self.db() as c:
            c.execute("INSERT INTO sessions (token_hash, user_id, created, expires) VALUES (?,?,?,?)", (_token_hash(token), user_id, now(), exp))
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.db() as c:
            row = c.execute("SELECT user_id, expires FROM sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
        if not row or row["expires"] < now():
            return None
        return self.get_user(row["user_id"])

    def end_session(self, token: str | None) -> None:
        if token:
            with self.db() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    # ---- profiles ------------------------------------------------------------
    def profile(self, user_id: str, *, private: bool = False) -> dict[str, Any] | None:
        with self.db() as c:
            row = c.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        p = dict(row)
        p["roles"] = json.loads(p["roles"] or "[]")
        p["role_labels"] = [ROLE_LABELS[r] for r in p["roles"] if r in ROLE_LABELS]
        p["id"] = p.pop("user_id")
        if private:
            u = self.get_user(user_id)
            p["email"] = u["email"] if u else None
        return p

    def update_profile(self, user_id: str, **fields: Any) -> dict[str, Any]:
        limits = {"display_name": 80, "business_name": 120, "bio": 2000, "location": 120, "website": 200, "phone": 40}
        sets, vals = [], []
        for k, v in fields.items():
            if k == "roles":
                roles = [r for r in (v or []) if r in ROLES]
                if not roles:
                    raise ValueError("Pick at least one role.")
                sets.append("roles=?")
                vals.append(json.dumps(roles))
            elif k in limits:
                v = (v or "").strip()[: limits[k]]
                if k == "display_name" and not v:
                    raise ValueError("Name can't be empty.")
                if k == "website" and v and not re.match(r"^https?://", v):
                    v = "https://" + v
                sets.append(f"{k}=?")
                vals.append(v or None)
        if sets:
            with self.db() as c:
                c.execute(f"UPDATE profiles SET {', '.join(sets)}, updated=? WHERE user_id=?", (*vals, now(), user_id))
        p = self.profile(user_id, private=True)
        assert p
        return p

    # ---- field access --------------------------------------------------------
    def grant(self, field_id: str, user_id: str, role: str) -> None:
        with self.db() as c:
            c.execute(
                "INSERT INTO field_access (field_id, user_id, role, created) VALUES (?,?,?,?) "
                "ON CONFLICT(field_id, user_id) DO UPDATE SET role=excluded.role",
                (field_id, user_id, role, now()),
            )

    def revoke(self, field_id: str, user_id: str) -> None:
        with self.db() as c:
            c.execute("DELETE FROM field_access WHERE field_id=? AND user_id=? AND role!='owner'", (field_id, user_id))

    def access(self, field_id: str, user_id: str) -> str | None:
        with self.db() as c:
            row = c.execute("SELECT role FROM field_access WHERE field_id=? AND user_id=?", (field_id, user_id)).fetchone()
        return row["role"] if row else None

    def fields_for(self, user_id: str) -> dict[str, str]:
        with self.db() as c:
            return {r["field_id"]: r["role"] for r in c.execute("SELECT field_id, role FROM field_access WHERE user_id=?", (user_id,))}

    def field_members(self, field_id: str) -> list[dict[str, Any]]:
        with self.db() as c:
            rows = c.execute(
                "SELECT fa.user_id, fa.role, p.display_name, u.email FROM field_access fa "
                "JOIN profiles p ON p.user_id=fa.user_id JOIN users u ON u.id=fa.user_id WHERE fa.field_id=? ORDER BY fa.role='owner' DESC, p.display_name",
                (field_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def owner_of(self, field_id: str) -> str | None:
        with self.db() as c:
            row = c.execute("SELECT user_id FROM field_access WHERE field_id=? AND role='owner'", (field_id,)).fetchone()
        return row["user_id"] if row else None

    def claim_unowned(self, field_ids: list[str], user_id: str) -> list[str]:
        """Give fields that predate accounts to the first account (the farm's admin)."""
        claimed = []
        for fid in field_ids:
            if self.owner_of(fid) is None:
                self.grant(fid, user_id, "owner")
                claimed.append(fid)
        return claimed

    # ---- reports -------------------------------------------------------------
    def report(self, reporter_id: str, kind: str, target_id: str, reason: str) -> None:
        with self.db() as c:
            c.execute(
                "INSERT INTO reports (reporter_id, target_kind, target_id, reason, created) VALUES (?,?,?,?,?)",
                (reporter_id, kind[:20], target_id[:80], reason.strip()[:1000], now()),
            )


def google_config() -> dict[str, str] | None:
    cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    return {"client_id": cid, "client_secret": secret} if cid and secret else None
