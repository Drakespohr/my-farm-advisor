"""Admin commands run by someone with database access (not exposed over HTTP).

    python manage.py create-farm --name "Smith Farms" --owner-email jane@example.com
    python manage.py assign-fields --farm-id 1            # all fields that have no farm yet
    python manage.py assign-fields --farm-id 1 --field-id 4 --field-id 7

The owner must have signed in to the app once (that's what creates their user record).
Fields created before farms existed have no farm and can't be seen by anyone until assigned.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from ag_inputs import FarmRow, FieldRow, MembershipRow, SessionLocal, UserRow, _utcnow


def create_farm(name: str, owner_email: str) -> int:
    with SessionLocal() as db:
        owner = db.scalars(select(UserRow).where(UserRow.email == owner_email)).first()
        if owner is None:
            sys.exit(f"No user with email {owner_email}; they need to sign in once first.")
        farm = FarmRow(name=name, created_at=_utcnow())
        db.add(farm)
        db.flush()
        db.add(MembershipRow(farm_id=farm.id, user_id=owner.id, role="owner", created_at=_utcnow()))
        db.commit()
        return farm.id


def assign_fields(farm_id: int, field_ids: list[int] | None) -> list[int]:
    with SessionLocal() as db:
        if db.get(FarmRow, farm_id) is None:
            sys.exit(f"No farm {farm_id}")
        stmt = select(FieldRow).where(FieldRow.farm_id.is_(None))
        if field_ids:
            stmt = select(FieldRow).where(FieldRow.id.in_(field_ids))
        changed = []
        for f in db.scalars(stmt):
            if f.farm_id not in (None, farm_id):
                print(f"skip field {f.id}: already belongs to farm {f.farm_id}", file=sys.stderr)
                continue
            f.farm_id = farm_id
            changed.append(f.id)
        db.commit()
        return changed


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="manage.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-farm")
    c.add_argument("--name", required=True)
    c.add_argument("--owner-email", required=True)
    a = sub.add_parser("assign-fields")
    a.add_argument("--farm-id", type=int, required=True)
    a.add_argument("--field-id", type=int, action="append")
    args = p.parse_args(argv)
    if args.cmd == "create-farm":
        print(f"created farm {create_farm(args.name, args.owner_email)}")
    else:
        print(f"assigned fields {assign_fields(args.farm_id, args.field_id)} to farm {args.farm_id}")


if __name__ == "__main__":
    main()
