from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from fieldmonitor import disease
from fieldmonitor.api import create_app
from fieldmonitor.weather import Hour, parse_isd_csv, parse_nws_forecast, parse_nws_observations, parse_open_meteo, rh_from, summary
from tests.conftest import FIELD
from tests.test_api import add_obs, signup
from tests.conftest import ndvi_field

END = date(2025, 8, 15)


def hours(days, temp, rh, end=END, wet_hours_per_day=24):
    out = []
    start = datetime.combine(end - timedelta(days=days - 1), datetime.min.time(), timezone.utc)
    for d in range(days):
        for hh in range(24):
            t = start + timedelta(days=d, hours=hh)
            out.append(Hour(t.isoformat(), temp, temp - 1, rh if hh < wet_hours_per_day else 60, 0.0))
    return out


def test_rh_from_dewpoint():
    assert rh_from(20, 20) == pytest.approx(100, abs=0.5)
    assert 40 < rh_from(30, 15) < 45


def test_warm_wet_weather_raises_southern_rust_not_tar_spot():
    p = disease.field_pressure(lat=40, lon=-93, crop="corn", hours=hours(14, 28, 95), reports=[], canopy_ndvi=0.9, as_of=END)
    by = {d["key"]: d for d in p["diseases"]}
    assert by["southern_rust"]["score"] == 60  # weather alone capped: rust spores must blow in
    assert by["tar_spot"]["score"] == 0        # 28 C is too warm for tar spot
    assert {d["crop"] for d in p["diseases"]} == {"corn"}


def test_dry_weather_is_low_and_canopy_gates_pressure():
    dry = disease.field_pressure(lat=40, lon=-93, crop="corn", hours=hours(30, 20, 50), reports=[], canopy_ndvi=0.9, as_of=END)
    assert dry["score"] == 0 and dry["level"] == "Low"
    wet = hours(30, 20, 95)
    full = disease.field_pressure(lat=40, lon=-93, crop="corn", hours=wet, reports=[], canopy_ndvi=0.9, as_of=END)
    early = disease.field_pressure(lat=40, lon=-93, crop="corn", hours=wet, reports=[], canopy_ndvi=0.5, as_of=END)
    bare = disease.field_pressure(lat=40, lon=-93, crop="corn", hours=wet, reports=[], canopy_ndvi=0.2, as_of=END)
    assert full["score"] > early["score"] > bare["score"]


def test_nearby_reports_push_pressure_up_and_decay():
    base = dict(lat=40.0, lon=-93.0, crop="corn", hours=hours(14, 28, 95), canopy_ndvi=0.9, as_of=END)
    near = [{"disease": "southern_rust", "severity": "severe", "lat": 39.8, "lon": -93.0, "observed": "2025-08-13"}]
    far = [{"disease": "southern_rust", "severity": "severe", "lat": 37.0, "lon": -93.0, "observed": "2025-08-13"}]
    old = [{"disease": "southern_rust", "severity": "severe", "lat": 39.8, "lon": -93.0, "observed": "2025-07-01"}]
    sr = lambda r: next(d for d in disease.field_pressure(reports=r, **base)["diseases"] if d["key"] == "southern_rust")  # noqa: E731
    with_near = sr(near)
    assert with_near["score"] > 85 and with_near["level"] == "High"
    assert "14 mi S" in with_near["points"][1]
    assert sr(far)["score"] == sr(old)["score"] == 60


def test_soybean_field_only_gets_soybean_diseases():
    p = disease.field_pressure(lat=40, lon=-93, crop="Soybeans", hours=[], reports=[], canopy_ndvi=0.8, as_of=END)
    assert {d["crop"] for d in p["diseases"]} == {"soybean"}


def test_weather_parsers():
    obs = parse_nws_observations({"features": [
        {"properties": {"timestamp": "2026-07-01T05:53:00+00:00", "temperature": {"value": 22.0}, "dewpoint": {"value": 21.5},
                        "relativeHumidity": {"value": None}, "precipitationLastHour": {"value": 1.2}}}]})
    assert obs[0].time == "2026-07-01T05:00:00+00:00" and obs[0].rh > 95 and obs[0].wet
    fc = parse_nws_forecast({"properties": {"periods": [
        {"startTime": "2026-07-02T06:00:00-05:00", "temperature": 86, "temperatureUnit": "F", "dewpoint": {"value": 23.0},
         "relativeHumidity": {"value": 92}, "probabilityOfPrecipitation": {"value": 20}}]}})
    assert fc[0].forecast and fc[0].temp_c == pytest.approx(30.0) and fc[0].time == "2026-07-02T11:00:00+00:00"
    om = parse_open_meteo({"hourly": {"time": ["2026-07-01T00:00"], "temperature_2m": [20.0], "dew_point_2m": [19.0],
                                       "relative_humidity_2m": [94], "precipitation": [0.0]}}, "2026-07-02T00:00:00+00:00")
    assert om[0].rh == 94 and not om[0].forecast
    isd = parse_isd_csv(
        '"STATION","DATE","REPORT_TYPE","TMP","DEW","AA1"\n'
        '"72546904913","2025-07-24T10:15:00","FM-15","+0210,1","+0210,1","01,0005,9,5"\n'
        '"72546904913","2025-07-24T11:15:00","FM-15","+9999,9","+9999,9",""\n',
        "2025-07-24T00:00:00+00:00", "2025-07-25T00:00:00+00:00")
    assert isd[0].temp_c == 21.0 and isd[0].rh == pytest.approx(100) and isd[0].precip_mm == 0.5 and isd[1].temp_c is None
    s = summary(isd, 14)
    assert s["rain_mm"] == 0.5 and s["wet_hours"] == 1


@pytest.fixture()
def app(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    monkeypatch.setattr("fieldmonitor.feed.hourly", lambda lat, lon, ws, as_of=None, days=30: (
        hours(30, 28, 95, end=datetime.now(timezone.utc).date()), {"provider": "test", "station": "Test Airport"}))
    return create_app(store, refresh_hours=0)


def client(app):
    return TestClient(app, headers={"X-Requested-With": "fieldmonitor"})


def test_reports_posts_feed_and_watch(app, store):
    a, b, far = client(app), client(app), client(app)
    signup(a, "a@x.co", "Ann")
    signup(b, "b@x.co", "Bo", ("agronomist",))
    signup(far, "far@x.co", "Far")
    fa = a.post("/api/fields", json={"name": "North", "crop": "corn", "geojson": FIELD, "auto_sync": False}).json()["created"][0]
    add_obs(store, fa, datetime.now(timezone.utc).date().isoformat(), ndvi_field(base=0.85))
    fb = b.post("/api/fields", json={"name": "Nearby", "crop": "corn", "geojson": FIELD, "auto_sync": False, "field_id": "bo-north"}).json()["created"][0]
    assert b.post("/api/crop-reports", json={"disease": "southern_rust", "severity": "severe", "field_id": fa}).status_code == 404
    rep = b.post("/api/crop-reports", json={"disease": "southern_rust", "severity": "severe", "field_id": fb, "notes": "Upper canopy, R2"}).json()
    assert rep["lat"] == round(rep["lat"], 2)  # rounded to ~1 km for privacy
    assert b.post("/api/crop-reports", json={"disease": "bogus", "field_id": fb}).status_code == 400
    assert b.post("/api/crop-reports", json={"disease": "insect", "lat": 10.0, "lon": 10.0, "notes": "Japanese beetles"}).status_code == 201

    w = a.get(f"/api/fields/{fa}/watch").json()
    assert w["pressure"]["top"] == "Southern rust" and w["pressure"]["level"] == "High"
    assert any(i["kind"] == "weather" and "Station: Test Airport" in i["points"] for i in w["items"])

    a.post("/api/posts", json={"body": "Anyone seeing rust south of Chariton?", "field_id": fa})
    feed = a.get("/api/feed").json()
    types = {i["type"] for i in feed}
    assert {"report", "post", "field"} <= types
    near = a.get("/api/feed?scope=near").json()
    assert all(i.get("miles") is not None and i["miles"] < 100 for i in near if i["type"] in ("post", "report"))
    assert not any("beetles" in i.get("body", "") for i in near)  # the far-away report isn't "near"
    pid = next(i["id"] for i in feed if i["type"] == "report")
    assert a.post(f"/api/posts/{pid}/helpful").json() == {"helpful": 1, "mine": True}
    assert a.post(f"/api/posts/{pid}/helpful").json() == {"helpful": 0, "mine": False}
    cm = a.post(f"/api/posts/{pid}/comments", json={"body": "Seeing it here too"}).json()
    assert cm[0]["author"] == "Ann"
    assert any(i["type"] == "field" and "pressure" in i["title"] for i in feed)
    assert far.get(f"/api/fields/{fa}/watch").status_code == 404
