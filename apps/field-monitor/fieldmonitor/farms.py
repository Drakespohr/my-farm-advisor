"""Farms and team roles: a farm holds fields, and people get one role on the whole farm.

Roles: owner, operator, applicator, agronomist, viewer. For now, owners manage the farm (name, team,
fields) and everyone else can see its fields; what each role may change is decided later.
Fields can still be shared one at a time with an outside person (e.g. an agronomist); that only
opens that field, never the rest of the farm.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .accounts import FARM_ROLES, Accounts
from .store import Store

ROLE_LABELS = {
    "owner": "Owner — manages the farm, team, and fields",
    "operator": "Operator — runs day-to-day work",
    "applicator": "Applicator — applies fertilizer and sprays",
    "agronomist": "Agronomist — scouts fields, adds and corrects log records, advises",
    "viewer": "Viewer — read only",
}


class FarmIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class MemberIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    role: str = Field(..., pattern="^(owner|operator|applicator|agronomist|viewer)$")


class MoveFieldsIn(BaseModel):
    field_ids: list[str] = Field(..., min_length=1, max_length=500)


def make_router(accounts: Accounts, store: Store, require_user: Callable[..., dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    def farm_or_404(farm_id: str, user: dict[str, Any], owner: bool = False) -> tuple[dict[str, Any], str]:
        farm = accounts.get_farm(farm_id)
        role = accounts.farm_role(farm_id, user["id"]) if farm else None
        if not farm or not role:
            raise HTTPException(404, "Unknown farm")
        if owner and role != "owner":
            raise HTTPException(403, "Only a farm owner can do that.")
        return farm, role

    def farm_view(farm: dict[str, Any], role: str) -> dict[str, Any]:
        return {"id": farm["id"], "name": farm["name"], "my_role": role, "created": farm["created"],
                "field_ids": accounts.farm_field_ids(farm["id"])}

    @r.get("/api/farms/roles")
    def roles(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return {"roles": list(FARM_ROLES), "labels": ROLE_LABELS}

    @r.get("/api/farms")
    def list_farms(user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        farms = accounts.farms_for(user["id"])
        if not farms:  # every account starts with its own (empty) farm
            accounts.default_farm(user["id"])
            farms = accounts.farms_for(user["id"])
        return [farm_view(f, f["role"]) for f in farms]

    @r.post("/api/farms", status_code=201)
    def create_farm(body: FarmIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        farm = accounts.create_farm(body.name, user["id"])
        store.log("farm.created", farm_id=farm["id"], by=user["id"])
        return farm_view(farm, "owner")

    @r.patch("/api/farms/{farm_id}")
    def rename_farm(farm_id: str, body: FarmIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        farm_or_404(farm_id, user, owner=True)
        return farm_view(accounts.rename_farm(farm_id, body.name), "owner")

    @r.get("/api/farms/{farm_id}/members")
    def members(farm_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        farm_or_404(farm_id, user)
        return accounts.farm_members(farm_id)

    @r.post("/api/farms/{farm_id}/members", status_code=201)
    def set_member(farm_id: str, body: MemberIn, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        """Add someone to the team or change their role. They need an account first."""
        farm_or_404(farm_id, user, owner=True)
        other = accounts.get_user_by_email(body.email.strip())
        if not other:
            raise HTTPException(404, "No account with that email. Ask them to sign up first.")
        try:
            accounts.set_farm_member(farm_id, other["id"], body.role)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        store.log("farm.member_set", farm_id=farm_id, by=user["id"], member=other["id"], role=body.role)
        return accounts.farm_members(farm_id)

    @r.delete("/api/farms/{farm_id}/members/{member_id}")
    def remove_member(farm_id: str, member_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        farm_or_404(farm_id, user, owner=member_id != user["id"])  # anyone may leave; owners remove others
        try:
            accounts.remove_farm_member(farm_id, member_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        store.log("farm.member_removed", farm_id=farm_id, by=user["id"], member=member_id)
        return accounts.farm_members(farm_id) if accounts.farm_role(farm_id, user["id"]) else []

    @r.post("/api/farms/{farm_id}/fields")
    def move_fields(farm_id: str, body: MoveFieldsIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        """Move fields you own onto this farm (you must own both the fields and the farm)."""
        farm, _ = farm_or_404(farm_id, user, owner=True)
        for fid in body.field_ids:
            if accounts.access(fid, user["id"]) != "owner":
                raise HTTPException(403, f"You don't own field {fid!r}.")
        for fid in body.field_ids:
            accounts.add_field_to_farm(fid, farm_id)
        store.log("farm.fields_moved", farm_id=farm_id, by=user["id"], field_ids=body.field_ids)
        return farm_view(accounts.get_farm(farm_id), "owner")  # type: ignore[arg-type]

    return r
