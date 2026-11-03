"""Login, profiles, field sharing, marketplace listings, and messages."""

from __future__ import annotations

import os
import secrets
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .accounts import ROLE_LABELS, ROLES, SESSION_COOKIE, SESSION_DAYS, Accounts, google_config, new_id, now
from .store import Store

LISTING_KINDS = {
    "service": "Service offered",
    "product": "Product for sale",
    "crop": "Crop for sale",
    "wanted": "Wanted",
}
CATEGORIES = [
    "Scouting & agronomy", "Spraying", "Drone services", "Soil sampling", "Planting", "Harvesting",
    "Trucking", "Seed", "Fertilizer", "Crop protection", "Equipment", "Grain & crops", "Hay & forage", "Other",
]


class RegisterIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)
    name: str = Field(max_length=80)
    roles: list[str] = Field(default_factory=lambda: ["farmer"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class ProfileIn(BaseModel):
    display_name: str | None = None
    business_name: str | None = None
    roles: list[str] | None = None
    bio: str | None = None
    location: str | None = None
    website: str | None = None
    phone: str | None = None


class ShareIn(BaseModel):
    email: str = Field(max_length=320)


class ListingIn(BaseModel):
    kind: str
    category: str
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=3, max_length=4000)
    price: str | None = Field(None, max_length=80)
    location: str | None = Field(None, max_length=120)


class ListingPatch(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=120)
    description: str | None = Field(None, min_length=3, max_length=4000)
    price: str | None = Field(None, max_length=80)
    location: str | None = Field(None, max_length=120)
    status: str | None = None


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class StartConversationIn(MessageIn):
    to_user_id: str
    listing_id: str | None = None


class ReportIn(BaseModel):
    kind: str
    target_id: str
    reason: str = Field(min_length=3, max_length=1000)


def make_router(
    accounts: Accounts,
    store: Store,
    require_user: Callable[..., dict[str, Any]],
    field_summary: Callable[[str], dict[str, Any] | None],
) -> APIRouter:
    r = APIRouter()

    def set_session(resp: Response, request: Request, user_id: str) -> None:
        secure = request.url.scheme == "https" or os.environ.get("FIELD_MONITOR_COOKIE_SECURE") == "1"
        resp.set_cookie(
            SESSION_COOKIE, accounts.create_session(user_id), max_age=SESSION_DAYS * 86400,
            httponly=True, samesite="lax", secure=secure, path="/",
        )

    def after_login(user: dict[str, Any]) -> None:
        # Fields created before accounts existed belong to the first (admin) account.
        if user.get("is_admin"):
            claimed = accounts.claim_unowned([f["id"] for f in store.list_fields(include_archived=True)], user["id"])
            if claimed:
                store.log("fields.claimed", user_id=user["id"], field_ids=claimed)

    def me_payload(user: dict[str, Any]) -> dict[str, Any]:
        return {"user": user, "profile": accounts.profile(user["id"], private=True), "unread": unread_count(user["id"])}

    # ---- auth ----------------------------------------------------------------
    @r.get("/api/auth/config")
    def auth_config() -> dict[str, Any]:
        return {"google": google_config() is not None, "roles": ROLE_LABELS}

    @r.post("/api/auth/register", status_code=201)
    def register(body: RegisterIn, request: Request, response: Response) -> dict[str, Any]:
        if not accounts.login_limiter.allow(f"reg:{request.client.host if request.client else '-'}"):
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        try:
            user = accounts.register(body.email, body.password, body.name, body.roles)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from None
        store.log("user.registered", user_id=user["id"])
        after_login(user)
        set_session(response, request, user["id"])
        return me_payload(user)

    @r.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        ip = request.client.host if request.client else "-"
        if not (accounts.login_limiter.allow(f"ip:{ip}") and accounts.login_limiter.allow(f"em:{body.email.lower()}")):
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        user = accounts.authenticate(body.email, body.password)
        if not user:
            raise HTTPException(401, "Wrong email or password.")
        after_login(user)
        set_session(response, request, user["id"])
        return me_payload(user)

    @r.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        accounts.end_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    def google_redirect_uri(request: Request) -> str:
        base = os.environ.get("FIELD_MONITOR_PUBLIC_URL") or str(request.base_url).rstrip("/")
        return f"{base}/api/auth/google/callback"

    @r.get("/api/auth/google/start")
    def google_start(request: Request) -> RedirectResponse:
        cfg = google_config()
        if not cfg:
            raise HTTPException(404, "Google sign-in isn't set up on this server.")
        state = secrets.token_urlsafe(24)
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": cfg["client_id"], "redirect_uri": google_redirect_uri(request), "response_type": "code",
            "scope": "openid email profile", "state": state, "prompt": "select_account",
        })
        resp = RedirectResponse(url)
        resp.set_cookie("fm_oauth_state", state, max_age=600, httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return resp

    @r.get("/api/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        cfg = google_config()
        expected = request.cookies.get("fm_oauth_state")
        if not cfg or not code or not expected or not secrets.compare_digest(state, expected):
            return RedirectResponse("/#/login?error=google")
        try:
            tok = httpx.post("https://oauth2.googleapis.com/token", data={
                "code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                "redirect_uri": google_redirect_uri(request), "grant_type": "authorization_code",
            }, timeout=15)
            tok.raise_for_status()
            info = httpx.get("https://openidconnect.googleapis.com/v1/userinfo",
                             headers={"Authorization": f"Bearer {tok.json()['access_token']}"}, timeout=15)
            info.raise_for_status()
            data = info.json()
        except (httpx.HTTPError, KeyError, ValueError):
            return RedirectResponse("/#/login?error=google")
        if not data.get("email_verified") or not data.get("sub") or not data.get("email"):
            return RedirectResponse("/#/login?error=google-email")
        user = accounts.google_login(data["sub"], data["email"], data.get("name", ""))
        after_login(user)
        resp = RedirectResponse("/#/fields")
        resp.delete_cookie("fm_oauth_state")
        set_session(resp, request, user["id"])
        return resp

    @r.get("/api/me")
    def me(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return me_payload(user)

    @r.patch("/api/me/profile")
    def update_profile(body: ProfileIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        try:
            return accounts.update_profile(user["id"], **body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @r.get("/api/users/{user_id}")
    def public_profile(user_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        p = accounts.profile(user_id)
        if not p:
            raise HTTPException(404, "No such user.")
        showcase = []
        for fid, role in accounts.fields_for(user_id).items():
            if role != "owner":
                continue
            try:
                f = store.get_field(fid)
            except KeyError:
                continue
            if f.get("showcase") and not f.get("archived"):
                s = field_summary(fid)
                showcase.append({"name": f["name"], "crop": f.get("crop"), "area_ac": f["area_ac"],
                                 "score": (s or {}).get("score"), "label": (s or {}).get("label"), "as_of": (s or {}).get("as_of")})
        return {"profile": p, "listings": list_listings(user=user_id, viewer=user), "showcase": showcase, "is_me": user_id == user["id"]}

    # ---- field sharing ---------------------------------------------------------
    def owner_or_403(field_id: str, user: dict[str, Any]) -> None:
        if accounts.access(field_id, user["id"]) != "owner":
            raise HTTPException(403, "Only the field's owner can change sharing.")

    @r.get("/api/fields/{field_id}/members")
    def members(field_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        if not accounts.access(field_id, user["id"]):
            raise HTTPException(404, "Unknown field")
        return accounts.field_members(field_id)

    @r.post("/api/fields/{field_id}/members", status_code=201)
    def share(field_id: str, body: ShareIn, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        owner_or_403(field_id, user)
        other = accounts.get_user_by_email(body.email)
        if not other:
            raise HTTPException(404, "No account with that email. Ask them to sign up first.")
        if other["id"] == user["id"]:
            raise HTTPException(400, "That's you.")
        accounts.grant(field_id, other["id"], "viewer")
        store.log("field.shared", field_id=field_id, by=user["id"], to=other["id"])
        return accounts.field_members(field_id)

    @r.delete("/api/fields/{field_id}/members/{member_id}")
    def unshare(field_id: str, member_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        if member_id != user["id"]:
            owner_or_403(field_id, user)  # anyone may remove themselves
        accounts.revoke(field_id, member_id)
        store.log("field.unshared", field_id=field_id, by=user["id"], removed=member_id)
        return accounts.field_members(field_id)

    # ---- listings --------------------------------------------------------------
    def listing_row(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["kind_label"] = LISTING_KINDS.get(d["kind"], d["kind"])
        return d

    def list_listings(*, viewer: dict[str, Any], user: str | None = None, kind: str | None = None,
                      category: str | None = None, q: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = ("SELECT l.*, p.display_name AS seller_name, p.business_name AS seller_business, p.roles AS seller_roles "
               "FROM listings l JOIN profiles p ON p.user_id=l.user_id WHERE 1=1")
        args: list[Any] = []
        if user:
            sql += " AND l.user_id=?"
            args.append(user)
            if user != viewer["id"]:
                sql += " AND l.status='active'"
        else:
            sql += " AND l.status='active'"
        if kind:
            sql += " AND l.kind=?"
            args.append(kind)
        if category:
            sql += " AND l.category=?"
            args.append(category)
        if q:
            sql += " AND (l.title LIKE ? OR l.description LIKE ? OR l.location LIKE ?)"
            like = f"%{q.strip()[:80]}%"
            args += [like, like, like]
        sql += " ORDER BY l.created DESC LIMIT ?"
        args.append(limit)
        with accounts.db() as c:
            return [listing_row(x) for x in c.execute(sql, args).fetchall()]

    @r.get("/api/market/meta")
    def market_meta() -> dict[str, Any]:
        return {"kinds": LISTING_KINDS, "categories": CATEGORIES, "roles": ROLE_LABELS}

    @r.get("/api/listings")
    def listings(kind: str | None = None, category: str | None = None, q: str | None = Query(None, max_length=80),
                 mine: bool = False, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        return list_listings(viewer=user, user=user["id"] if mine else None, kind=kind, category=category, q=q)

    def validate_listing(kind: str | None, category: str | None) -> None:
        if kind is not None and kind not in LISTING_KINDS:
            raise HTTPException(400, f"kind must be one of {', '.join(LISTING_KINDS)}")
        if category is not None and category not in CATEGORIES:
            raise HTTPException(400, "Unknown category")

    @r.post("/api/listings", status_code=201)
    def create_listing(body: ListingIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        validate_listing(body.kind, body.category)
        lid = new_id("l")
        with accounts.db() as c:
            c.execute(
                "INSERT INTO listings (id, user_id, kind, category, title, description, price, location, status, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?)",
                (lid, user["id"], body.kind, body.category, body.title.strip(), body.description.strip(),
                 (body.price or "").strip() or None, (body.location or "").strip() or None, now(), now()),
            )
        return get_listing(lid, user)

    @r.get("/api/listings/{listing_id}")
    def get_listing(listing_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        with accounts.db() as c:
            row = c.execute(
                "SELECT l.*, p.display_name AS seller_name, p.business_name AS seller_business, p.roles AS seller_roles "
                "FROM listings l JOIN profiles p ON p.user_id=l.user_id WHERE l.id=?", (listing_id,)).fetchone()
        if not row or (row["status"] != "active" and row["user_id"] != user["id"]):
            raise HTTPException(404, "Listing not found")
        d = listing_row(row)
        d["is_mine"] = d["user_id"] == user["id"]
        return d

    @r.patch("/api/listings/{listing_id}")
    def update_listing(listing_id: str, body: ListingPatch, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        cur = get_listing(listing_id, user)
        if not cur["is_mine"]:
            raise HTTPException(403, "Not your listing")
        changes = body.model_dump(exclude_none=True)
        if "status" in changes and changes["status"] not in ("active", "closed"):
            raise HTTPException(400, "status must be active or closed")
        if changes:
            cols = ", ".join(f"{k}=?" for k in changes)
            with accounts.db() as c:
                c.execute(f"UPDATE listings SET {cols}, updated=? WHERE id=?", (*[str(v).strip() for v in changes.values()], now(), listing_id))
        return get_listing(listing_id, user)

    # ---- messages --------------------------------------------------------------
    def unread_count(user_id: str) -> int:
        with accounts.db() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                "LEFT JOIN conversation_reads r ON r.conversation_id=cv.id AND r.user_id=? "
                "WHERE (cv.a_id=? OR cv.b_id=?) AND m.sender_id!=? AND m.id > COALESCE(r.last_read_id, 0)",
                (user_id, user_id, user_id, user_id)).fetchone()
        return int(row[0])

    def conv_or_404(conv_id: str, user: dict[str, Any]) -> dict[str, Any]:
        with accounts.db() as c:
            row = c.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not row or user["id"] not in (row["a_id"], row["b_id"]):
            raise HTTPException(404, "Conversation not found")
        return dict(row)

    def add_message(conv_id: str, sender_id: str, body: str) -> None:
        with accounts.db() as c:
            c.execute("INSERT INTO messages (conversation_id, sender_id, body, created) VALUES (?,?,?,?)", (conv_id, sender_id, body.strip(), now()))
            c.execute("UPDATE conversations SET updated=? WHERE id=?", (now(), conv_id))

    @r.get("/api/conversations")
    def conversations(user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        uid = user["id"]
        with accounts.db() as c:
            rows = c.execute(
                "SELECT cv.*, l.title AS listing_title, "
                " (SELECT body FROM messages WHERE conversation_id=cv.id ORDER BY id DESC LIMIT 1) AS last_body, "
                " (SELECT COUNT(*) FROM messages m LEFT JOIN conversation_reads r ON r.conversation_id=cv.id AND r.user_id=? "
                "   WHERE m.conversation_id=cv.id AND m.sender_id!=? AND m.id > COALESCE(r.last_read_id,0)) AS unread "
                "FROM conversations cv LEFT JOIN listings l ON l.id=cv.listing_id WHERE cv.a_id=? OR cv.b_id=? ORDER BY cv.updated DESC",
                (uid, uid, uid, uid)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            other = d["b_id"] if d["a_id"] == uid else d["a_id"]
            p = accounts.profile(other) or {}
            d.update(other_id=other, other_name=p.get("display_name"), other_business=p.get("business_name"))
            out.append(d)
        return out

    @r.post("/api/conversations", status_code=201)
    def start_conversation(body: StartConversationIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        if body.to_user_id == user["id"]:
            raise HTTPException(400, "You can't message yourself.")
        if not accounts.get_user(body.to_user_id):
            raise HTTPException(404, "No such user.")
        if body.listing_id:
            get_listing(body.listing_id, user)
        a, b = sorted([user["id"], body.to_user_id])
        with accounts.db() as c:
            row = c.execute("SELECT id FROM conversations WHERE a_id=? AND b_id=? AND listing_id IS ?", (a, b, body.listing_id)).fetchone()
            conv_id = row["id"] if row else new_id("c")
            if not row:
                c.execute("INSERT INTO conversations (id, a_id, b_id, listing_id, created, updated) VALUES (?,?,?,?,?,?)",
                          (conv_id, a, b, body.listing_id, now(), now()))
        add_message(conv_id, user["id"], body.body)
        return {"id": conv_id}

    @r.get("/api/conversations/{conv_id}/messages")
    def thread(conv_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        conv = conv_or_404(conv_id, user)
        with accounts.db() as c:
            msgs = [dict(m) for m in c.execute("SELECT id, sender_id, body, created FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))]
            if msgs:
                c.execute(
                    "INSERT INTO conversation_reads (conversation_id, user_id, last_read_id) VALUES (?,?,?) "
                    "ON CONFLICT(conversation_id, user_id) DO UPDATE SET last_read_id=excluded.last_read_id",
                    (conv_id, user["id"], msgs[-1]["id"]))
        other = conv["b_id"] if conv["a_id"] == user["id"] else conv["a_id"]
        p = accounts.profile(other) or {}
        listing = None
        if conv["listing_id"]:
            with accounts.db() as c:
                lr = c.execute("SELECT id, title, status FROM listings WHERE id=?", (conv["listing_id"],)).fetchone()
            listing = dict(lr) if lr else None
        return {"id": conv_id, "other": {"id": other, "name": p.get("display_name"), "business": p.get("business_name")},
                "listing": listing, "messages": msgs, "me": user["id"]}

    @r.post("/api/conversations/{conv_id}/messages", status_code=201)
    def reply(conv_id: str, body: MessageIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        conv_or_404(conv_id, user)
        add_message(conv_id, user["id"], body.body)
        return thread(conv_id, user)

    @r.post("/api/reports", status_code=201)
    def report(body: ReportIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, bool]:
        if body.kind not in ("listing", "user", "message"):
            raise HTTPException(400, "kind must be listing, user, or message")
        accounts.report(user["id"], body.kind, body.target_id, body.reason)
        store.log("report.created", by=user["id"], kind=body.kind, target=body.target_id)
        return {"ok": True}

    return r


__all__ = ["make_router", "LISTING_KINDS", "CATEGORIES", "ROLES"]
