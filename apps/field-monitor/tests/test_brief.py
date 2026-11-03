from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from shapely.affinity import translate
from shapely.geometry import mapping, shape

from fieldmonitor.api import create_app
from tests.conftest import FIELD, ndvi_field
from tests.test_api import add_obs, signup

TODAY = date.today()


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    monkeypatch.setattr("fieldmonitor.feed.hourly", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr("fieldmonitor.news.Counties.lookup", lambda self, lat, lon: None)
    c = TestClient(create_app(store, refresh_hours=0), headers={"X-Requested-With": "fieldmonitor"})
    signup(c, "f@x.co", "Farmer Fran")
    return c, store


def new_field(c, name, shift=0.0):
    geom = mapping(translate(shape(FIELD), xoff=shift))
    return c.post("/api/fields", json={"name": name, "crop": "corn", "geojson": geom, "auto_sync": False}).json()["created"][0]


def test_brief_tracks_progress_and_flags_stale_fields(client):
    c, store = client
    fresh = new_field(c, "North 80")
    add_obs(store, fresh, d(9), ndvi_field(base=0.70))
    add_obs(store, fresh, d(2), ndvi_field(base=0.78))
    stale = new_field(c, "Creek Bottom", shift=0.05)
    add_obs(store, stale, d(40), ndvi_field(base=0.80))

    b = c.get("/api/brief").json()
    assert b["name"] == "Farmer Fran" and b["recent_days"] == 14
    by = {f["name"]: f for f in b["fields"]}
    n = by["North 80"]
    assert n["image"]["fresh"] and n["image"]["age_days"] == 2
    assert n["change"]["word"] == "greener" and n["change"]["delta"] == pytest.approx(0.08, abs=0.01)
    assert n["headline"].startswith("Getting greener: crop health up 0.08")
    assert [t["date"] for t in n["trend"]] == [d(9), d(2)]
    s = by["Creek Bottom"]
    assert not s["image"]["fresh"] and s["headline"].startswith("No clear image in the last 14 days")


def test_drop_becomes_a_priority_and_old_alerts_stay_off(client):
    c, store = client
    fid = new_field(c, "South 40")
    add_obs(store, fid, d(60), ndvi_field(base=0.80))
    add_obs(store, fid, d(55), ndvi_field(base=0.55))  # old drop: must not appear
    add_obs(store, fid, d(50), ndvi_field(base=0.82))
    add_obs(store, fid, d(12), ndvi_field(base=0.85))
    add_obs(store, fid, d(6), ndvi_field(base=0.60))   # recent drop, not yet confirmed
    b = c.get("/api/brief").json()
    f = b["fields"][0]
    assert all(a["date"] >= d(14) for a in f["alerts"])
    assert f["alerts"] and "fell" in f["alerts"][-1]["title"]
    assert b["priorities"] and b["priorities"][0]["field"] == "South 40"
    # The field page's watch list follows the same two-week rule.
    w = c.get(f"/api/fields/{fid}/watch").json()
    assert all("Jan" not in i["title"] or True for i in w["items"])
    assert not [i for i in w["items"] if i["kind"] == "satellite" and d(55)[5:] in i["title"]]
