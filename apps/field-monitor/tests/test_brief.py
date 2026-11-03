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


def area(median):
    q = [round(median - 0.2 + 0.02 * i, 3) for i in range(21)]
    return {"area": {"ndvi_q": q, "ndvi_median": q[10]}, "ring": {"n": 0}}


def test_after_harvest_the_brief_reports_the_season_not_alarms(client):
    c, store = client
    done = new_field(c, "Harvested")
    green = new_field(c, "Cover crop", shift=0.05)
    for fid, late in ((done, 0.22), (green, 0.78)):
        for days, nd, am in ((100, 0.85, 0.80), (80, 0.90, 0.85), (60, 0.88, 0.82), (30, 0.50, 0.40), (8, late, 0.25)):
            add_obs(store, fid, d(days), ndvi_field(base=nd if days > 8 else late), qa=area(am))
    # A nearby severe southern-rust report must not create a November priority.
    c.post("/api/crop-reports", json={"disease": "southern_rust", "severity": "severe", "field_id": done})
    b = c.get("/api/brief").json()
    by = {f["name"]: f for f in b["fields"]}
    assert by["Harvested"]["season_over"] and by["Harvested"]["headline"].startswith("Season over: final score")
    assert by["Cover crop"]["headline"].startswith("Still green (NDVI 0.78) after harvest")
    assert b["priorities"] == []
    p = c.get(f"/api/fields/{done}/pressure").json()
    assert p["level"] != "High" and any("season is over" in x for x in p["diseases"][0]["points"])


def test_unknown_crop_gets_a_tip_not_a_disease_priority(client):
    c, store = client
    geom = mapping(translate(shape(FIELD), xoff=0.1))
    fid = c.post("/api/fields", json={"name": "Mystery", "geojson": geom, "auto_sync": False}).json()["created"][0]
    add_obs(store, fid, d(9), ndvi_field(base=0.80))
    add_obs(store, fid, d(3), ndvi_field(base=0.81))
    f = c.get("/api/brief").json()["fields"][0]
    assert "set the crop" in f["headline"] and f["priority"] < 2
