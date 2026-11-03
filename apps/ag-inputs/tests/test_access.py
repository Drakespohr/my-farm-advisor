"""Multi-user access: roles, append-only corrections, drafts (step 2)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import ag_inputs
from tests.conftest import as_user

FOLIAR = {"kind": "foliar", "product": "Example 4EC", "purpose": "insecticide", "rate": 8, "rate_unit": "fl_oz/ac"}


def user(subject):
    return TestClient(ag_inputs.app, headers=as_user(subject, email=f"{subject}@example.com"))


def farm_with(client, roles):
    """Owner creates a farm, field, season; adds one member per role. Returns (season_id, clients)."""
    farm = client.post("/api/farms", json={"name": "Farm"}).json()["id"]
    clients = {}
    for role in roles:
        c = user(role)
        c.get("/api/farms")  # signing in once creates the user
        assert client.post(f"/api/farms/{farm}/members", json={"email": f"{role}@example.com", "role": role}).status_code == 201
        clients[role] = c
    field = client.post("/api/fields", json={"farm_id": farm, "name": "F", "centroid_lat": 41, "centroid_lon": -98}).json()["id"]
    season = client.post(f"/api/fields/{field}/seasons", json={"year": 2026, "crop": "corn"}).json()["id"]
    return farm, field, season, clients


def event(c, sid, payload=None, **extra):
    return c.post(f"/api/seasons/{sid}/events", json={"occurred_at": "2026-06-01T08:00:00", "payload": payload or {"kind": "rainfall", "amount_in": 1, "source": "estimate"}, **extra})


def test_auth_required_and_disabled_by_default(db_schema, monkeypatch):
    assert TestClient(ag_inputs.app).get("/api/farms").status_code == 401
    import auth

    monkeypatch.setenv("AG_AUTH_MODE", "")
    auth.set_auth_provider(None)
    try:
        r = TestClient(ag_inputs.app, headers=as_user("x")).get("/api/farms")
        assert r.status_code == 401 and "not configured" in r.json()["detail"]
    finally:
        monkeypatch.setenv("AG_AUTH_MODE", "dev")
        auth.set_auth_provider(None)


def test_roles(client):
    farm, field, sid, u = farm_with(client, ["operator", "applicator", "scout", "viewer"])
    # Everyone can read.
    for c in u.values():
        assert c.get(f"/api/seasons/{sid}/events").status_code == 200
        assert c.get(f"/api/seasons/{sid}/diagnosis-context").status_code == 200
    # Writers can log events; viewers can't.
    for role in ("operator", "applicator", "scout"):
        assert event(u[role], sid).status_code == 201
    assert event(u["viewer"], sid).status_code == 403
    assert u["viewer"].put(f"/api/seasons/{sid}/drafts/rainfall", json={"content": {}}).status_code == 403
    # Only owners/operators create fields and seasons.
    assert u["operator"].post(f"/api/fields/{field}/seasons", json={"year": 2027, "crop": "soybeans"}).status_code == 201
    for role in ("applicator", "scout", "viewer"):
        assert u[role].post(f"/api/fields/{field}/seasons", json={"year": 2028, "crop": "wheat"}).status_code == 403
        assert u[role].post("/api/fields", json={"farm_id": farm, "name": "X", "centroid_lat": 41, "centroid_lon": -98}).status_code == 403
    # Only owners manage members, and a farm keeps at least one owner.
    assert u["operator"].post(f"/api/farms/{farm}/members", json={"email": "viewer@example.com", "role": "owner"}).status_code == 403
    me = next(m for m in client.get(f"/api/farms/{farm}/members").json() if m["role"] == "owner")
    assert client.post(f"/api/farms/{farm}/members", json={"email": "owner@example.com", "role": "viewer"}).status_code == 409
    assert client.delete(f"/api/farms/{farm}/members/{me['user_id']}").status_code == 409


def test_non_members_see_nothing(client):
    farm, field, sid, _ = farm_with(client, [])
    stranger = user("stranger")
    for path in (f"/api/seasons/{sid}/events", f"/api/seasons/{sid}/diagnosis-context", f"/api/fields/{field}/seasons",
                 f"/api/farms/{farm}/fields", f"/api/seasons/{sid}/drafts"):
        assert stranger.get(path).status_code == 404, path
    assert event(stranger, sid).status_code == 404
    assert stranger.get("/api/farms").json() == []


def test_corrections_are_new_events(client):
    _, _, sid, _ = farm_with(client, [])
    first = event(client, sid, {"kind": "irrigation", "amount_in": 1.0, "method": "pivot"}).json()
    fix = event(client, sid, {"kind": "irrigation", "amount_in": 1.5, "method": "pivot"}, supersedes_id=first["id"]).json()
    assert fix["supersedes_id"] == first["id"]
    rows = client.get(f"/api/seasons/{sid}/events").json()
    assert [(r["id"], r["superseded_by"]) for r in rows] == [(first["id"], fix["id"]), (fix["id"], None)]
    assert [r["id"] for r in client.get(f"/api/seasons/{sid}/events?include_superseded=false").json()] == [fix["id"]]
    ctx = client.get(f"/api/seasons/{sid}/diagnosis-context?as_of=2026-06-02T00:00:00").json()
    assert ctx["last_14_days"]["irrigation_in"] == 1.5  # the correction replaces the original, not adds to it
    # Correct the latest version only, same kind only.
    assert event(client, sid, {"kind": "irrigation", "amount_in": 2, "method": "pivot"}, supersedes_id=first["id"]).status_code == 409
    assert event(client, sid, {"kind": "rainfall", "amount_in": 2, "source": "estimate"}, supersedes_id=fix["id"]).status_code == 422
    assert event(client, sid, supersedes_id=999999).status_code == 404
    # No update/delete endpoints exist.
    assert client.delete(f"/api/seasons/{sid}/events/{first['id']}").status_code in (404, 405)
    assert client.put(f"/api/seasons/{sid}/events/{first['id']}", json={}).status_code in (404, 405)


def test_orm_refuses_to_edit_or_delete_events(client):
    _, _, sid, _ = farm_with(client, [])
    eid = event(client, sid).json()["id"]
    with Session(ag_inputs.engine) as s:
        row = s.get(ag_inputs.EventRow, eid)
        row.payload = {"kind": "rainfall", "amount_in": 9, "source": "estimate"}
        with pytest.raises(ValueError, match="append-only"):
            s.commit()
        s.rollback()
        s.delete(s.get(ag_inputs.EventRow, eid))
        with pytest.raises(ValueError, match="append-only"):
            s.commit()


def test_database_triggers_block_update_and_delete(tmp_path):
    url = f"sqlite:///{tmp_path / 'trig.db'}"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes.update(url=url, skip_logging=True)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO fields (name, centroid_lat, centroid_lon) VALUES ('F', 41, -98)"))
        conn.execute(text("INSERT INTO seasons (field_id, year, crop) VALUES (1, 2026, 'corn')"))
        conn.execute(text("INSERT INTO field_events (season_id, kind, occurred_at, payload, created_at) "
                          "VALUES (1, 'rainfall', '2026-06-01', '{}', '2026-06-01')"))
    for sql in ("UPDATE field_events SET kind='x'", "DELETE FROM field_events"):
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(sql))


def test_drafts_per_user_form_and_season(client):
    farm, field, sid, u = farm_with(client, ["scout", "applicator"])
    scout, app = u["scout"], u["applicator"]
    assert scout.put(f"/api/seasons/{sid}/drafts/scouting", json={"content": {"target": "tar spot", "incidence_pct": 5}}).status_code == 200
    assert app.put(f"/api/seasons/{sid}/drafts/foliar", json={"content": {"product": "Example 4EC"}}).status_code == 200
    # Saving again replaces the same draft.
    scout.put(f"/api/seasons/{sid}/drafts/scouting", json={"content": {"target": "tar spot", "incidence_pct": 8}})
    assert scout.get(f"/api/seasons/{sid}/drafts/scouting").json()["content"]["incidence_pct"] == 8
    # Each user only loads their own draft, but everyone sees who's working on what.
    assert app.get(f"/api/seasons/{sid}/drafts/scouting").status_code == 404
    listed = app.get(f"/api/seasons/{sid}/drafts").json()
    assert {(d["user_name"], d["form_type"]) for d in listed} == {("Scout", "scouting"), ("Applicator", "foliar")}
    assert [d for d in listed if d["form_type"] == "scouting"][0].get("content") is None
    assert [d for d in listed if d["form_type"] == "foliar"][0]["content"] == {"product": "Example 4EC"}
    # Discard.
    assert scout.delete(f"/api/seasons/{sid}/drafts/scouting").status_code == 204
    assert scout.get(f"/api/seasons/{sid}/drafts/scouting").status_code == 404
    assert scout.put(f"/api/seasons/{sid}/drafts/not_a_form", json={"content": {}}).status_code == 422


def test_legacy_fields_are_kept_and_can_be_assigned(client):
    import manage

    with Session(ag_inputs.engine) as s:  # a field from before farms existed
        s.add(ag_inputs.FieldRow(name="Legacy", centroid_lat=41, centroid_lon=-98))
        s.commit()
    client.get("/api/farms")  # owner signs in once
    farm = manage.create_farm("Smith Farms", "owner@example.com")
    assert client.get(f"/api/farms/{farm}/fields").json() == []
    assert manage.assign_fields(farm, None) == [1]
    assert [f["name"] for f in client.get(f"/api/farms/{farm}/fields").json()] == ["Legacy"]
