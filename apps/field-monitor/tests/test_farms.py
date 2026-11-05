"""Farms and team roles: fields belong to a farm; people get one role on the whole farm."""

import pytest
from fastapi.testclient import TestClient
from shapely.affinity import translate
from shapely.geometry import mapping, shape

from fieldmonitor.accounts import Accounts, now
from fieldmonitor.api import create_app
from tests.conftest import FIELD
from tests.test_api import signup

H = {"X-Requested-With": "fieldmonitor"}


@pytest.fixture()
def app(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    return create_app(store, refresh_hours=0)


def person(app, email, name):
    c = TestClient(app, headers=H)
    uid = signup(c, email, name)
    return c, uid


def add_field(c, name, shift=0.0, **extra):
    geom = mapping(translate(shape(FIELD), xoff=shift))
    r = c.post("/api/fields", json={"name": name, "crop": "corn", "geojson": geom, "auto_sync": False, **extra})
    assert r.status_code == 201, r.text
    return r.json()["created"][0]


def test_first_field_creates_my_farm(app):
    c, _ = person(app, "olive@x.co", "Olive Owner")
    fid = add_field(c, "North 80")
    farms = c.get("/api/farms").json()
    assert len(farms) == 1 and farms[0]["name"] == "Olive Owner's farm" and farms[0]["my_role"] == "owner"
    assert farms[0]["field_ids"] == [fid]
    f = c.get("/api/fields").json()[0]
    assert f["farm_name"] == "Olive Owner's farm" and f["my_role"] == "owner"


def test_team_member_sees_every_farm_field_with_their_role(app):
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    a, b = add_field(owner, "A"), add_field(owner, "B", shift=0.05)
    farm = owner.get("/api/farms").json()[0]["id"]
    scout, scout_id = person(app, "sam@x.co", "Sam Scout")
    assert scout.get(f"/api/fields/{a}").status_code == 404  # not on the team yet
    r = owner.post(f"/api/farms/{farm}/members", json={"email": "sam@x.co", "role": "agronomist"})
    assert r.status_code == 201 and {m["email"]: m["role"] for m in r.json()}["sam@x.co"] == "agronomist"
    seen = {f["id"]: f["my_role"] for f in scout.get("/api/fields").json()}
    assert seen == {a: "agronomist", b: "agronomist"}
    # For now only owners manage: a scout can look but not change the farm or its fields.
    assert scout.post(f"/api/fields/{a}/sync").status_code == 403
    assert scout.post(f"/api/farms/{farm}/members", json={"email": "olive@x.co", "role": "viewer"}).status_code == 403
    assert scout.patch(f"/api/farms/{farm}", json={"name": "Mine now"}).status_code == 403
    # Role changes and removal
    owner.post(f"/api/farms/{farm}/members", json={"email": "sam@x.co", "role": "applicator"})
    assert scout.get(f"/api/fields/{a}").json()["my_role"] == "applicator"
    owner.delete(f"/api/farms/{farm}/members/{scout_id}")
    assert scout.get(f"/api/fields/{a}").status_code == 404


def test_farm_keeps_at_least_one_owner(app):
    owner, owner_id = person(app, "olive@x.co", "Olive Owner")
    add_field(owner, "A")
    farm = owner.get("/api/farms").json()[0]["id"]
    assert owner.post(f"/api/farms/{farm}/members", json={"email": "olive@x.co", "role": "viewer"}).status_code == 409
    assert owner.delete(f"/api/farms/{farm}/members/{owner_id}").status_code == 409
    partner, partner_id = person(app, "pat@x.co", "Pat Partner")
    owner.post(f"/api/farms/{farm}/members", json={"email": "pat@x.co", "role": "owner"})
    assert owner.delete(f"/api/farms/{farm}/members/{owner_id}").status_code == 200  # Olive leaves; Pat owns it
    assert partner.get("/api/farms").json()[0]["my_role"] == "owner"
    assert owner.get(f"/api/farms/{farm}/members").status_code == 404


def test_unknown_people_and_farms(app):
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    add_field(owner, "A")
    farm = owner.get("/api/farms").json()[0]["id"]
    assert owner.post(f"/api/farms/{farm}/members", json={"email": "nobody@x.co", "role": "agronomist"}).status_code == 404
    other, _ = person(app, "ned@x.co", "Ned Neighbor")
    assert other.get(f"/api/farms/{farm}/members").status_code == 404
    assert owner.post(f"/api/farms/{farm}/members", json={"email": "ned@x.co", "role": "boss"}).status_code == 422


def test_second_farm_and_moving_fields(app):
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    a = add_field(owner, "A")
    home = owner.get("/api/farms").json()[0]["id"]
    rented = owner.post("/api/farms", json={"name": "Rented ground"}).json()["id"]
    b = add_field(owner, "B", shift=0.05, farm_id=rented)
    farms = {f["name"]: f["field_ids"] for f in owner.get("/api/farms").json()}
    assert farms == {"Olive Owner's farm": [a], "Rented ground": [b]}
    owner.post(f"/api/farms/{rented}/fields", json={"field_ids": [a]})
    farms = {f["name"]: set(f["field_ids"]) for f in owner.get("/api/farms").json()}
    assert farms == {"Olive Owner's farm": set(), "Rented ground": {a, b}}
    other, _ = person(app, "ned@x.co", "Ned Neighbor")
    ned_farm = other.get("/api/farms").json()[0]["id"]
    assert other.post(f"/api/farms/{ned_farm}/fields", json={"field_ids": [a]}).status_code == 403  # not his field
    assert other.post("/api/fields", json={"name": "X", "geojson": mapping(translate(shape(FIELD), xoff=0.2)),
                                           "auto_sync": False, "farm_id": home}).status_code == 403


def test_one_field_share_stays_one_field(app):
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    a, b = add_field(owner, "A"), add_field(owner, "B", shift=0.05)
    agro, _ = person(app, "al@x.co", "Al Agronomist")
    owner.post(f"/api/fields/{a}/members", json={"email": "al@x.co"})
    assert [f["id"] for f in agro.get("/api/fields").json()] == [a]  # not B
    members = {m["email"]: (m["role"], m["via"]) for m in owner.get(f"/api/fields/{a}/members").json()}
    assert members == {"olive@x.co": ("owner", "farm"), "al@x.co": ("viewer", "field")}


def test_redraw_stays_on_the_farm(app):
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    a = add_field(owner, "A")
    farm = owner.get("/api/farms").json()[0]["id"]
    scout, _ = person(app, "sam@x.co", "Sam Scout")
    owner.post(f"/api/farms/{farm}/members", json={"email": "sam@x.co", "role": "agronomist"})
    agro, _ = person(app, "al@x.co", "Al Agronomist")
    owner.post(f"/api/fields/{a}/members", json={"email": "al@x.co"})
    new = owner.post(f"/api/fields/{a}/redraw", json={"geojson": mapping(translate(shape(FIELD), xoff=0.001))}).json()["field"]["id"]
    assert set(owner.get("/api/farms").json()[0]["field_ids"]) == {a, new}
    assert scout.get(f"/api/fields/{new}").json()["my_role"] == "agronomist"
    assert agro.get(f"/api/fields/{new}").json()["my_role"] == "viewer"


def test_existing_owners_and_shares_migrate_without_widening_access(tmp_path):
    db = tmp_path / "app.db"
    acc = Accounts(db)
    olive = acc.register("olive@x.co", "correct horse 1", "Olive Owner", ["farmer"])
    al = acc.register("al@x.co", "correct horse 1", "Al Agronomist", ["agronomist"])
    with acc.db() as c:  # data as it looked before farms existed
        c.execute("DELETE FROM farm_fields")
        c.execute("DELETE FROM farm_members")
        c.execute("DELETE FROM farms")
        for fid, uid, role in (("a", olive["id"], "owner"), ("b", olive["id"], "owner"), ("a", al["id"], "viewer")):
            c.execute("INSERT INTO field_access (field_id, user_id, role, created) VALUES (?,?,?,?)", (fid, uid, role, now()))
    acc2 = Accounts(db)  # app restart runs the migration
    farms = acc2.farms_for(olive["id"])
    assert len(farms) == 1 and set(acc2.farm_field_ids(farms[0]["id"])) == {"a", "b"}
    assert acc2.fields_for(olive["id"]) == {"a": "owner", "b": "owner"}
    assert acc2.fields_for(al["id"]) == {"a": "viewer"}  # still only the one shared field
    assert Accounts(db).migrate_to_farms() == 0  # repeat-safe


def test_old_scout_role_becomes_agronomist(tmp_path):
    import sqlite3
    db = tmp_path / "app.db"
    acc = Accounts(db)
    olive = acc.register("olive@x.co", "correct horse 1", "Olive Owner", ["farmer"])
    sam = acc.register("sam@x.co", "correct horse 1", "Sam Scout", ["farmer"])
    farm = acc.create_farm("Home", olive["id"])["id"]
    with acc.db() as c:  # rebuild the table the way the previous version created it
        c.executescript("""
            DROP TABLE farm_members;
            CREATE TABLE farm_members (farm_id TEXT NOT NULL REFERENCES farms(id), user_id TEXT NOT NULL REFERENCES users(id),
              role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'applicator', 'scout', 'viewer')),
              created TEXT NOT NULL, PRIMARY KEY (farm_id, user_id));
        """)
        c.execute("INSERT INTO farm_members VALUES (?,?,?,?)", (farm, olive["id"], "owner", now()))
        c.execute("INSERT INTO farm_members VALUES (?,?,?,?)", (farm, sam["id"], "scout", now()))
    acc2 = Accounts(db)
    assert acc2.farm_role(farm, sam["id"]) == "agronomist" and acc2.farm_role(farm, olive["id"]) == "owner"
    acc2.set_farm_member(farm, sam["id"], "agronomist")  # new CHECK accepts it
    with pytest.raises(sqlite3.IntegrityError):
        with acc2.db() as c:
            c.execute("UPDATE farm_members SET role='scout' WHERE user_id=?", (sam["id"],))
    Accounts(db)  # repeat-safe
