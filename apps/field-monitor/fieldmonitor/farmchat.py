"""Farm team chat: one group conversation per farm, open to everyone on the farm's team.

Direct (one-to-one) messages live in social.py's conversations; this module adds the farm group
thread and the rule that direct messages stay within farm teams (`shares_farm`).

Reading needs a current farm role: someone removed from the team loses the group chat. Messages
have no edit or delete endpoint; each person's read position is kept so unread counts work.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .accounts import Accounts, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS farm_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id TEXT NOT NULL REFERENCES farms(id),
  sender_id TEXT NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS farm_messages_by_farm ON farm_messages(farm_id, id);
CREATE TABLE IF NOT EXISTS farm_message_reads (
  farm_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  last_read_id INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (farm_id, user_id)
);
"""

PAGE = 200  # messages returned per request (newest PAGE, oldest first)


class ChatIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


def shares_farm(accounts: Accounts, a: str, b: str) -> bool:
    """True when two people are on at least one farm team together."""
    with accounts.db() as c:
        row = c.execute("SELECT 1 FROM farm_members x JOIN farm_members y ON x.farm_id=y.farm_id "
                        "WHERE x.user_id=? AND y.user_id=? LIMIT 1", (a, b)).fetchone()
    return row is not None


def teammates(accounts: Accounts, user_id: str) -> list[dict[str, Any]]:
    """Everyone who shares a farm with `user_id` (not themselves), with the farms they share."""
    with accounts.db() as c:
        rows = c.execute(
            "SELECT y.user_id, p.display_name, u.email, f.name AS farm_name FROM farm_members x "
            "JOIN farm_members y ON y.farm_id=x.farm_id AND y.user_id!=x.user_id "
            "JOIN farms f ON f.id=x.farm_id JOIN profiles p ON p.user_id=y.user_id JOIN users u ON u.id=y.user_id "
            "WHERE x.user_id=? ORDER BY p.display_name, f.name", (user_id,)).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = out.setdefault(r["user_id"], {"user_id": r["user_id"], "display_name": r["display_name"] or r["email"], "farms": []})
        d["farms"].append(r["farm_name"])
    return list(out.values())


class FarmChat:
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts
        with accounts.db() as c:
            c.executescript(SCHEMA)

    def post(self, farm_id: str, sender_id: str, body: str) -> int:
        text = body.strip()
        if not text:
            raise ValueError("Message is empty.")
        with self.accounts.db() as c:
            cur = c.execute("INSERT INTO farm_messages (farm_id, sender_id, body, created) VALUES (?,?,?,?)",
                            (farm_id, sender_id, text, now()))
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def messages(self, farm_id: str, after: int = 0, limit: int = PAGE) -> list[dict[str, Any]]:
        with self.accounts.db() as c:
            rows = c.execute(
                "SELECT m.id, m.sender_id, m.body, m.created, p.display_name AS sender_name FROM farm_messages m "
                "LEFT JOIN profiles p ON p.user_id=m.sender_id WHERE m.farm_id=? AND m.id>? ORDER BY m.id DESC LIMIT ?",
                (farm_id, after, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def mark_read(self, farm_id: str, user_id: str, last_id: int) -> None:
        with self.accounts.db() as c:
            c.execute("INSERT INTO farm_message_reads (farm_id, user_id, last_read_id) VALUES (?,?,?) "
                      "ON CONFLICT(farm_id, user_id) DO UPDATE SET last_read_id=MAX(last_read_id, excluded.last_read_id)",
                      (farm_id, user_id, last_id))

    def threads(self, user_id: str) -> list[dict[str, Any]]:
        """One group thread per farm the person is on, with last message and unread count."""
        with self.accounts.db() as c:
            rows = c.execute(
                "SELECT f.id AS farm_id, f.name AS farm_name, "
                " (SELECT COUNT(*) FROM farm_members t WHERE t.farm_id=f.id) AS members, "
                " (SELECT body FROM farm_messages WHERE farm_id=f.id ORDER BY id DESC LIMIT 1) AS last_body, "
                " (SELECT created FROM farm_messages WHERE farm_id=f.id ORDER BY id DESC LIMIT 1) AS updated, "
                " (SELECT COUNT(*) FROM farm_messages fm LEFT JOIN farm_message_reads r ON r.farm_id=f.id AND r.user_id=? "
                "   WHERE fm.farm_id=f.id AND fm.sender_id!=? AND fm.id > COALESCE(r.last_read_id,0)) AS unread "
                "FROM farms f JOIN farm_members m ON m.farm_id=f.id WHERE m.user_id=? ORDER BY f.name",
                (user_id, user_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    def unread(self, user_id: str) -> int:
        return sum(t["unread"] for t in self.threads(user_id))


def make_router(accounts: Accounts, chat: FarmChat, require_user: Callable[..., dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    def member_or_404(farm_id: str, user: dict[str, Any]) -> None:
        if not accounts.farm_role(farm_id, user["id"]):
            raise HTTPException(404, "Unknown farm")

    @r.get("/api/farm-chats")
    def threads(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        """Group threads for your farms, plus teammates you can message directly."""
        return {"threads": chat.threads(user["id"]), "teammates": teammates(accounts, user["id"])}

    @r.get("/api/farms/{farm_id}/chat")
    def read(farm_id: str, after: int = Query(0, ge=0), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        member_or_404(farm_id, user)
        farm = accounts.get_farm(farm_id) or {}
        msgs = chat.messages(farm_id, after)
        if msgs:
            chat.mark_read(farm_id, user["id"], msgs[-1]["id"])
        return {"farm_id": farm_id, "farm_name": farm.get("name"), "members": accounts.farm_members(farm_id),
                "messages": msgs, "me": user["id"]}

    @r.post("/api/farms/{farm_id}/chat", status_code=201)
    def send(farm_id: str, body: ChatIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        member_or_404(farm_id, user)
        try:
            chat.post(farm_id, user["id"], body.body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return read(farm_id, 0, user)

    return r
