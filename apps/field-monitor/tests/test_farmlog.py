"""The shared farm and field log: one append-only table, corrections instead of edits."""

import sqlite3

import pytest

from tests.test_farms import add_field, app, person  # noqa: F401  (fixture)


def setup_farm(app):  # noqa: F811
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    a, b = add_field(owner, "North 80"), add_field(owner, "Home Pivot", shift=0.05)
    farm = owner.get("/api/farms").json()[0]["id"]
    return owner, farm, a, b


def log(c, farm, **body):
    return c.post(f"/api/farms/{farm}/log", json={"occurred_at": "2026-06-20T08:00:00-05:00", **body})


def test_field_and_farm_entries_in_one_log(app):  # noqa: F811
    owner, farm, a, b = setup_farm(app)
    e1 = log(owner, farm, field_id=a, kind="fertilizer",
             details={"product": "UAN 32%", "method": "side_dress", "rate_lb_per_ac": 100, "n_pct": 32})
    assert e1.status_code == 201, e1.text
    assert e1.json()["occurred_at"] == "2026-06-20T13:00:00" and e1.json()["recorded_by_name"] == "Olive Owner"
    log(owner, farm, field_id=b, kind="scouting", details={"target": "tar spot", "category": "disease", "incidence_pct": 5, "pattern": "patchy"})
    log(owner, farm, kind="note", details={"text": "Serviced the planter", "topic": "equipment"})
    farm_view = owner.get(f"/api/farms/{farm}/log").json()
    assert [e["kind"] for e in farm_view] == ["note", "scouting", "fertilizer"]  # newest first (same time: latest entry)
    field_a = owner.get(f"/api/fields/{a}/log").json()
    assert [e["kind"] for e in field_a] == ["fertilizer"]  # field log: only that field
    assert [e["kind"] for e in owner.get(f"/api/farms/{farm}/log", params={"field_id": b}).json()] == ["scouting"]


def test_validation_and_field_rules(app):  # noqa: F811
    owner, farm, a, _ = setup_farm(app)
    r = log(owner, farm, field_id=a, kind="fertilizer", details={"product": "UAN", "method": "sideways", "rate_lb_per_ac": 100})
    assert r.status_code == 422 and "method" in r.json()["detail"]
    assert log(owner, farm, kind="planting", details={"population_k_per_ac": 34}).status_code == 422  # needs a field
    assert log(owner, farm, kind="dancing", details={}).status_code == 422
    assert log(owner, farm, kind="note", details={"text": "x"}, occurred_at="2999-01-01T00:00:00").status_code == 422
    other, _ = person(app, "ned@x.co", "Ned")
    ned_field = add_field(other, "Ned's", shift=0.3)
    assert log(owner, farm, field_id=ned_field, kind="note", details={"text": "x"}).status_code == 422  # not on this farm


def test_restricted_use_rules_and_deadline(app):  # noqa: F811
    owner, farm, a, _ = setup_farm(app)
    spray = {"product": "Insecticide X", "purpose": "insecticide", "rate": 1.9, "rate_unit": "fl_oz/ac", "restricted_use": True}
    r = log(owner, farm, field_id=a, kind="foliar", details=spray)
    assert r.status_code == 422 and "epa_reg_number" in r.json()["detail"]
    r = log(owner, farm, field_id=a, kind="foliar", details={**spray, "epa_reg_number": "100-1295"})
    e = r.json()
    assert e["record_due_by"] == "2026-07-04T13:00:00" and e["recorded_late"] is True  # 14 days, logged in November


def test_corrections_and_voids_keep_history(app):  # noqa: F811
    owner, farm, a, _ = setup_farm(app)
    orig = log(owner, farm, field_id=a, kind="irrigation", details={"amount_in": 1.0, "method": "pivot"}).json()
    fix = log(owner, farm, kind="irrigation", details={"amount_in": 1.25, "method": "pivot"}, supersedes_id=orig["id"]).json()
    assert fix["field_id"] == a  # inherits the field
    assert [e["details"]["amount_in"] for e in owner.get(f"/api/fields/{a}/log").json()] == [1.25]
    hist = owner.get(f"/api/fields/{a}/log", params={"history": True}).json()
    assert {(e["id"], e["superseded_by"]) for e in hist} == {(orig["id"], fix["id"]), (fix["id"], None)}
    assert log(owner, farm, kind="irrigation", details={"amount_in": 2, "method": "pivot"}, supersedes_id=orig["id"]).status_code == 422
    assert log(owner, farm, kind="note", details={"text": "x"}, supersedes_id=fix["id"]).status_code == 422  # kind change
    void = log(owner, farm, kind="irrigation", supersedes_id=fix["id"], void=True)
    assert void.status_code == 201 and owner.get(f"/api/fields/{a}/log").json() == []
    assert len(owner.get(f"/api/fields/{a}/log", params={"history": True}).json()) == 3
    assert log(owner, farm, kind="note", details={"text": "x"}, void=True).status_code == 422  # void needs a target


def test_database_refuses_edits_and_deletes(app):  # noqa: F811
    owner, farm, a, _ = setup_farm(app)
    log(owner, farm, kind="note", details={"text": "keep me"})
    with app.state.farm_log.accounts.db() as c:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("UPDATE log_events SET details='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("DELETE FROM log_events")


def test_roles_who_can_write_and_read(app):  # noqa: F811
    owner, farm, a, b = setup_farm(app)
    scout, _ = person(app, "sam@x.co", "Sam Scout")
    viewer, _ = person(app, "vi@x.co", "Vi Viewer")
    agro, _ = person(app, "al@x.co", "Al Agronomist")
    owner.post(f"/api/farms/{farm}/members", json={"email": "sam@x.co", "role": "scout"})
    owner.post(f"/api/farms/{farm}/members", json={"email": "vi@x.co", "role": "viewer"})
    owner.post(f"/api/fields/{a}/members", json={"email": "al@x.co"})  # one-field share
    assert log(scout, farm, field_id=a, kind="scouting", details={"target": "rootworm beetles", "category": "insect",
               "incidence_pct": 10, "pattern": "edge"}).status_code == 201
    assert log(viewer, farm, kind="note", details={"text": "hi"}).status_code == 403
    assert len(viewer.get(f"/api/farms/{farm}/log").json()) == 1
    log(owner, farm, field_id=b, kind="note", details={"text": "B only"})
    assert [e["field_id"] for e in agro.get(f"/api/fields/{a}/log").json()] == [a]
    assert agro.get(f"/api/fields/{b}/log").status_code == 404
    assert agro.get(f"/api/farms/{farm}/log").status_code == 404  # not on the team
    assert log(agro, farm, field_id=a, kind="note", details={"text": "x"}).status_code == 404


def test_kinds_endpoint_describes_forms(app):  # noqa: F811
    owner, _, _, _ = setup_farm(app)
    k = owner.get("/api/log/kinds").json()
    assert "note" in k["farm_level"] and k["schemas"]["fertilizer"]["label"] == "Fertilizer / manure applied"
    assert "rate_lb_per_ac" in k["schemas"]["fertilizer"]["properties"]
