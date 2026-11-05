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
# Roles on a farm, most to least. For now only "owner" manages fields; the others can view.
FARM_ROLES = ("owner", "operator", "applicator", "scout", "viewer")
ROLE_RANK = {r: len(FARM_ROLES) - i for i, r in enumerate(FARM_ROLES)}
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
CREATE TABLE IF NOT EXISTS farms (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS farm_members (
  farm_id TEXT NOT NULL REFERENCES farms(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'applicator', 'scout', 'viewer')),
  created TEXT NOT NULL,
  PRIMARY KEY (farm_id, user_id)
);
CREATE INDEX IF NOT EXISTS farm_members_by_user ON farm_members(user_id);
CREATE TABLE IF NOT EXISTS farm_fields (
  field_id TEXT PRIMARY KEY,
  farm_id TEXT NOT NULL REFERENCES farms(id),
  added TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS farm_fields_by_farm ON farm_fields(farm_id);
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
        self.migrate_to_farms()
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

    # ---- farms -----------------------------------------------------------------
    # A farm holds fields; people get one role on the whole farm. What each role may do beyond
    # "owner manages, everyone else reads" is decided later (see FARM_ROLES).
    def create_farm(self, name: str, owner_id: str) -> dict[str, Any]:
        fid = new_id("farm")
        with self.db() as c:
            c.execute("INSERT INTO farms (id, name, created_by, created) VALUES (?,?,?,?)", (fid, name.strip()[:120], owner_id, now()))
            c.execute("INSERT INTO farm_members (farm_id, user_id, role, created) VALUES (?,?, 'owner', ?)", (fid, owner_id, now()))
        return self.get_farm(fid)  # type: ignore[return-value]

    def get_farm(self, farm_id: str) -> dict[str, Any] | None:
        with self.db() as c:
            row = c.execute("SELECT * FROM farms WHERE id=?", (farm_id,)).fetchone()
        return dict(row) if row else None

    def rename_farm(self, farm_id: str, name: str) -> dict[str, Any]:
        with self.db() as c:
            c.execute("UPDATE farms SET name=? WHERE id=?", (name.strip()[:120], farm_id))
        return self.get_farm(farm_id)  # type: ignore[return-value]

    def farms_for(self, user_id: str) -> list[dict[str, Any]]:
        with self.db() as c:
            rows = c.execute(
                "SELECT f.*, m.role, (SELECT COUNT(*) FROM farm_fields ff WHERE ff.farm_id=f.id) AS field_count "
                "FROM farms f JOIN farm_members m ON m.farm_id=f.id WHERE m.user_id=? ORDER BY m.role='owner' DESC, f.name",
                (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def farm_role(self, farm_id: str, user_id: str) -> str | None:
        with self.db() as c:
            row = c.execute("SELECT role FROM farm_members WHERE farm_id=? AND user_id=?", (farm_id, user_id)).fetchone()
        return row["role"] if row else None

    def farm_members(self, farm_id: str) -> list[dict[str, Any]]:
        with self.db() as c:
            rows = c.execute(
                "SELECT m.user_id, m.role, m.created, p.display_name, u.email FROM farm_members m "
                "JOIN profiles p ON p.user_id=m.user_id JOIN users u ON u.id=m.user_id WHERE m.farm_id=? "
                "ORDER BY m.role='owner' DESC, p.display_name", (farm_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_farm_member(self, farm_id: str, user_id: str, role: str) -> None:
        if role not in FARM_ROLES:
            raise ValueError(f"role must be one of {', '.join(FARM_ROLES)}")
        with self.db() as c:
            cur = c.execute("SELECT role FROM farm_members WHERE farm_id=? AND user_id=?", (farm_id, user_id)).fetchone()
            if cur and cur["role"] == "owner" and role != "owner" and self._owner_count(c, farm_id) == 1:
                raise ValueError("A farm needs at least one owner.")
            c.execute("INSERT INTO farm_members (farm_id, user_id, role, created) VALUES (?,?,?,?) "
                      "ON CONFLICT(farm_id, user_id) DO UPDATE SET role=excluded.role", (farm_id, user_id, role, now()))

    def remove_farm_member(self, farm_id: str, user_id: str) -> None:
        with self.db() as c:
            cur = c.execute("SELECT role FROM farm_members WHERE farm_id=? AND user_id=?", (farm_id, user_id)).fetchone()
            if cur and cur["role"] == "owner" and self._owner_count(c, farm_id) == 1:
                raise ValueError("A farm needs at least one owner.")
            c.execute("DELETE FROM farm_members WHERE farm_id=? AND user_id=?", (farm_id, user_id))

    @staticmethod
    def _owner_count(c: sqlite3.Connection, farm_id: str) -> int:
        return int(c.execute("SELECT COUNT(*) FROM farm_members WHERE farm_id=? AND role='owner'", (farm_id,)).fetchone()[0])

    def default_farm(self, user_id: str) -> dict[str, Any]:
        """The user's first farm they own, created on first need ("<name>'s farm")."""
        owned = [f for f in self.farms_for(user_id) if f["role"] == "owner"]
        if owned:
            return min(owned, key=lambda f: f["created"])
        p = self.profile(user_id, private=True) or {}
        return self.create_farm(f"{p.get('display_name') or 'My'}'s farm", user_id)

    def farm_of(self, field_id: str) -> str | None:
        with self.db() as c:
            row = c.execute("SELECT farm_id FROM farm_fields WHERE field_id=?", (field_id,)).fetchone()
        return row["farm_id"] if row else None

    def farm_field_ids(self, farm_id: str) -> list[str]:
        with self.db() as c:
            return [r["field_id"] for r in c.execute("SELECT field_id FROM farm_fields WHERE farm_id=? ORDER BY added", (farm_id,))]

    def add_field_to_farm(self, field_id: str, farm_id: str) -> None:
        """Put a field on a farm (moves it if it was on another farm)."""
        with self.db() as c:
            c.execute("INSERT INTO farm_fields (field_id, farm_id, added) VALUES (?,?,?) "
                      "ON CONFLICT(field_id) DO UPDATE SET farm_id=excluded.farm_id, added=excluded.added", (field_id, farm_id, now()))

    def migrate_to_farms(self) -> int:
        """One-time, repeat-safe: each field owner's fields go onto their default farm.
        One-field shares stay one-field shares, so nobody gains access to other fields."""
        with self.db() as c:
            rows = c.execute("SELECT fa.field_id, fa.user_id FROM field_access fa LEFT JOIN farm_fields ff ON ff.field_id=fa.field_id "
                             "WHERE fa.role='owner' AND ff.field_id IS NULL ORDER BY fa.created").fetchall()
        for r in rows:
            self.add_field_to_farm(r["field_id"], self.default_farm(r["user_id"])["id"])
        return len(rows)

    # ---- field access --------------------------------------------------------
    # A person's role on a field is the higher of (a) their role on the field's farm and (b) a
    # one-field share. Legacy per-field "owner" rows only count for fields not yet on a farm.
    def grant(self, field_id: str, user_id: str, role: str) -> None:
        if role == "owner":
            if self.farm_of(field_id) is None:
                self.add_field_to_farm(field_id, self.default_farm(user_id)["id"])
            return
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
        return self.fields_for(user_id, only=field_id).get(field_id)

    def fields_for(self, user_id: str, only: str | None = None) -> dict[str, str]:
        out: dict[str, str] = {}

        def put(fid: str, role: str) -> None:
            if fid not in out or ROLE_RANK[role] > ROLE_RANK[out[fid]]:
                out[fid] = role

        flt, args = (" AND ff.field_id=?", [only]) if only else ("", [])
        with self.db() as c:
            for r in c.execute("SELECT ff.field_id, m.role FROM farm_fields ff JOIN farm_members m ON m.farm_id=ff.farm_id "
                               f"WHERE m.user_id=?{flt}", [user_id, *args]):
                put(r["field_id"], r["role"])
            flt2 = " AND fa.field_id=?" if only else ""
            for r in c.execute("SELECT fa.field_id, fa.role, ff.farm_id FROM field_access fa "
                               f"LEFT JOIN farm_fields ff ON ff.field_id=fa.field_id WHERE fa.user_id=?{flt2}", [user_id, *args]):
                if r["role"] == "owner" and r["farm_id"] is not None:
                    continue  # the farm decides ownership now
                put(r["field_id"], r["role"])
        return out

    def field_members(self, field_id: str) -> list[dict[str, Any]]:
        """Everyone who can see the field: farm members (via='farm') and one-field shares (via='field')."""
        out: dict[str, dict[str, Any]] = {}
        farm = self.farm_of(field_id)
        if farm:
            for m in self.farm_members(farm):
                out[m["user_id"]] = {"user_id": m["user_id"], "role": m["role"], "display_name": m["display_name"],
                                     "email": m["email"], "via": "farm"}
        with self.db() as c:
            rows = c.execute(
                "SELECT fa.user_id, fa.role, p.display_name, u.email FROM field_access fa "
                "JOIN profiles p ON p.user_id=fa.user_id JOIN users u ON u.id=fa.user_id WHERE fa.field_id=?", (field_id,)).fetchall()
        for r in rows:
            if r["role"] == "owner" and farm:
                continue
            if r["user_id"] not in out or ROLE_RANK[r["role"]] > ROLE_RANK[out[r["user_id"]]["role"]]:
                out[r["user_id"]] = {**dict(r), "via": "field"}
        return sorted(out.values(), key=lambda m: (-ROLE_RANK[m["role"]], m["display_name"] or ""))

    def owner_of(self, field_id: str) -> str | None:
        farm = self.farm_of(field_id)
        with self.db() as c:
            if farm:
                row = c.execute("SELECT user_id FROM farm_members WHERE farm_id=? AND role='owner' ORDER BY created LIMIT 1",
                                (farm,)).fetchone()
            else:
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
