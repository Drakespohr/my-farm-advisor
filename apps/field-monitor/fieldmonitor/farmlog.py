"""The farm and field log: one shared, append-only table of everything done on the farm.

Each entry: farm, optional field, when it happened, activity kind, and validated details
(see records.py). Entries are never edited or deleted (enforced by database triggers): a
correction is a new entry that points at the one it replaces (`supersedes_id`), and a mistaken
entry is withdrawn with a "void" correction. Reads show the current version by default.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from . import records
from .accounts import Accounts, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS log_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id TEXT NOT NULL REFERENCES farms(id),
  field_id TEXT,
  occurred_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  details TEXT NOT NULL,
  voided INTEGER NOT NULL DEFAULT 0,
  supersedes_id INTEGER REFERENCES log_events(id),
  source TEXT NOT NULL DEFAULT 'manual',
  recorded_by TEXT NOT NULL REFERENCES users(id),
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS log_by_farm ON log_events(farm_id, occurred_at);
CREATE INDEX IF NOT EXISTS log_by_field ON log_events(field_id, occurred_at);
CREATE UNIQUE INDEX IF NOT EXISTS log_one_correction ON log_events(supersedes_id) WHERE supersedes_id IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS log_no_update BEFORE UPDATE ON log_events
  BEGIN SELECT RAISE(ABORT, 'log_events is append-only: add a correction instead'); END;
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log_events
  BEGIN SELECT RAISE(ABORT, 'log_events is append-only: add a correction instead'); END;
"""

WRITE_ROLES = ("owner", "operator", "applicator", "agronomist")
SOURCES = ("manual", "chat", "photo", "import")


class LogError(ValueError):
    """A request the log can't accept (bad kind, wrong farm, already corrected, ...)."""


def _naive_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


class FarmLog:
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts
        with accounts.db() as c:
            c.executescript(SCHEMA)

    def add(self, *, farm_id: str, user_id: str, kind: str, details: dict[str, Any], occurred_at: dt.datetime,
            field_id: str | None = None, supersedes_id: int | None = None, void: bool = False,
            source: str = "manual") -> dict[str, Any]:
        if source not in SOURCES:
            raise LogError(f"source must be one of {', '.join(SOURCES)}")
        when = _naive_utc(occurred_at)
        if when > _naive_utc(dt.datetime.now(dt.timezone.utc)) + dt.timedelta(minutes=5):
            raise LogError("occurred_at is in the future")
        if field_id is not None and self.accounts.farm_of(field_id) != farm_id:
            raise LogError("That field isn't on this farm.")
        if supersedes_id is not None:
            orig = self.get(supersedes_id)
            if not orig or orig["farm_id"] != farm_id:
                raise LogError("The entry being corrected isn't on this farm.")
            if orig["kind"] != kind:
                raise LogError(f"A correction must keep the same kind ({orig['kind']}).")
            if orig["superseded_by"] is not None:
                raise LogError("That entry was already corrected; correct the newest version instead.")
            if field_id is None:
                field_id = orig["field_id"]
        elif void:
            raise LogError("Void is only for correcting an existing entry.")
        if kind not in records.KINDS:
            raise LogError(f"Unknown kind {kind!r}")
        if field_id is None and kind not in records.FARM_LEVEL_KINDS:
            raise LogError(f"{records.LABELS[kind]} entries belong to a field.")
        clean = {} if void else self._validate(kind, details)
        with self.accounts.db() as c:
            try:
                cur = c.execute(
                    "INSERT INTO log_events (farm_id, field_id, occurred_at, kind, details, voided, supersedes_id, source, recorded_by, recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (farm_id, field_id, when.isoformat(), kind, json.dumps(clean), int(void), supersedes_id, source, user_id, now()))
            except sqlite3.IntegrityError:
                raise LogError("That entry was already corrected; correct the newest version instead.") from None
        return self.get(int(cur.lastrowid))  # type: ignore[return-value]

    @staticmethod
    def _validate(kind: str, details: dict[str, Any]) -> dict[str, Any]:
        try:
            return records.validate(kind, details)
        except ValidationError as exc:
            msgs = [f"{'.'.join(str(p) for p in e['loc'][1:]) or 'details'}: {e['msg']}" for e in exc.errors()]
            raise LogError("; ".join(msgs)) from None

    def get(self, entry_id: int) -> dict[str, Any] | None:
        with self.accounts.db() as c:
            row = c.execute(self._SELECT + " WHERE e.id=?", (entry_id,)).fetchone()
        return self._out(row) if row else None

    _SELECT = ("SELECT e.*, p.display_name AS recorded_by_name, "
               "(SELECT n.id FROM log_events n WHERE n.supersedes_id=e.id) AS superseded_by "
               "FROM log_events e LEFT JOIN profiles p ON p.user_id=e.recorded_by")

    def list(self, *, farm_id: str, field_ids: list[str] | None = None, kind: str | None = None, year: int | None = None,
             include_history: bool = False, include_farm_level: bool = True, limit: int = 500) -> list[dict[str, Any]]:
        """Newest first. `field_ids` limits to those fields (plus farm-level entries if asked)."""
        sql, args = self._SELECT + " WHERE e.farm_id=?", [farm_id]
        if field_ids is not None:
            marks = ",".join("?" * len(field_ids)) or "NULL"
            sql += f" AND (e.field_id IN ({marks})" + (" OR e.field_id IS NULL)" if include_farm_level else ")")
            args += field_ids
        if kind:
            sql += " AND e.kind=?"
            args.append(kind)
        if year:
            sql += " AND substr(e.occurred_at, 1, 4)=?"
            args.append(str(year))
        sql += " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?"
        args.append(limit)
        with self.accounts.db() as c:
            rows = [self._out(r) for r in c.execute(sql, args)]
        if not include_history:
            rows = [r for r in rows if r["superseded_by"] is None and not r["voided"]]
        return rows

    @staticmethod
    def _out(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["details"] = json.loads(d["details"])
        d["voided"] = bool(d["voided"])
        d["label"] = records.LABELS.get(d["kind"], d["kind"])
        due = records.record_due_by(d["kind"], d["details"], dt.datetime.fromisoformat(d["occurred_at"])) if not d["voided"] else None
        if due:
            d["record_due_by"] = due.isoformat()
            d["recorded_late"] = _naive_utc(dt.datetime.fromisoformat(d["recorded_at"])) > due
        return d


# ---- API -------------------------------------------------------------------------------

class EntryIn(BaseModel):
    kind: str = Field(..., max_length=40)
    occurred_at: dt.datetime
    field_id: str | None = Field(None, max_length=120)
    details: dict[str, Any] = Field(default_factory=dict)
    supersedes_id: int | None = Field(None, ge=1, description="Correct this entry (same kind)")
    void: bool = Field(False, description="With supersedes_id: withdraw a mistaken entry")
    source: str = Field("manual", pattern="^(manual|chat|photo|import)$")


def make_router(accounts: Accounts, log: FarmLog, require_user: Callable[..., dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    def member_or_404(farm_id: str, user: dict[str, Any]) -> str:
        role = accounts.farm_role(farm_id, user["id"])
        if not role:
            raise HTTPException(404, "Unknown farm")
        return role

    @r.get("/api/log/kinds")
    def kinds(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        """Activity kinds with their JSON schemas (for the log forms)."""
        return {"kinds": list(records.KINDS), "farm_level": list(records.FARM_LEVEL_KINDS), "schemas": records.schemas()}

    @r.get("/api/farms/{farm_id}/log")
    def farm_log(farm_id: str, field_id: str | None = None, kind: str | None = None,
                 year: int | None = Query(None, ge=1990, le=2100), history: bool = False,
                 user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        """Everything on the farm (farm-level and every field), newest first."""
        member_or_404(farm_id, user)
        fids = [field_id] if field_id else None
        return log.list(farm_id=farm_id, field_ids=fids, kind=kind, year=year, include_history=history,
                        include_farm_level=field_id is None)

    @r.post("/api/farms/{farm_id}/log", status_code=201)
    def add_entry(farm_id: str, body: EntryIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        role = member_or_404(farm_id, user)
        if role not in WRITE_ROLES:
            raise HTTPException(403, "Viewers can read the log but not add to it.")
        try:
            return log.add(farm_id=farm_id, user_id=user["id"], kind=body.kind, details=body.details,
                           occurred_at=body.occurred_at, field_id=body.field_id, supersedes_id=body.supersedes_id,
                           void=body.void, source=body.source)
        except LogError as exc:
            raise HTTPException(422, str(exc)) from None

    @r.get("/api/fields/{field_id}/log")
    def field_log(field_id: str, kind: str | None = None, year: int | None = Query(None, ge=1990, le=2100),
                  history: bool = False, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        """One field's log. Also works for people the field was shared with on its own."""
        if not accounts.access(field_id, user["id"]):
            raise HTTPException(404, f"Unknown field {field_id!r}")
        farm = accounts.farm_of(field_id)
        if not farm:
            return []
        return log.list(farm_id=farm, field_ids=[field_id], kind=kind, year=year, include_history=history,
                        include_farm_level=False)

    return r
