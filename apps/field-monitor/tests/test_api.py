import numpy as np
import pytest
from fastapi.testclient import TestClient

from fieldmonitor.api import create_app
from fieldmonitor.indices import INDICES
from fieldmonitor.analysis import field_stats
from tests.conftest import FIELD, ndvi_field, synthetic_grid


def add_obs(store, field_id, date, arr):
    t, crs = synthetic_grid()
    valid = np.ones_like(arr, bool)
    arrays = {k: arr for k in INDICES}
    arrays.update(valid=valid, field=valid)
    meta = {
        "id": f"{date}_S2X_v1", "field_id": field_id, "scene_id": f"S2X_{date}", "date": date,
        "datetime": f"{date}T17:00:00Z", "status": "clear", "field_clear_fraction": 1.0,
        "stats": {k: field_stats(arr) for k in INDICES}, "bounds": [[0, 0], [1, 1]],
        "grid": {"crs": crs, "transform": list(t)[:6], "shape": list(arr.shape), "resolution_m": 10},
        "processing_version": "1",
    }
    store.write_observation(field_id, meta["id"], meta, arrays, {"ndvi": b"\x89PNG"})
    return meta["id"]


@pytest.fixture()
def client(store):
    return TestClient(create_app(store)), store


def test_create_and_list_fields(client):
    c, _ = client
    r = c.post("/api/fields", json={"name": "North 80", "geojson": FIELD})
    assert r.status_code == 201 and len(r.json()["created"]) == 1
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"field_id": "B2"}, "geometry": FIELD}]}
    r = c.post("/api/fields", json={"geojson": fc})
    assert r.json()["created"] == ["b2"]
    assert {f["id"] for f in c.get("/api/fields").json()} >= {"b2"}
    assert c.post("/api/fields", json={"geojson": {"type": "Point", "coordinates": [0, 0]}}).status_code == 400


def test_analysis_endpoints(client):
    c, store = client
    fid = c.post("/api/fields", json={"name": "f", "geojson": FIELD}).json()["created"][0]
    a = ndvi_field()
    b = a.copy()
    b[10:25, 10:25] -= 0.3
    o1 = add_obs(store, fid, "2025-07-01", a)
    o2 = add_obs(store, fid, "2025-07-11", b)

    obs = c.get(f"/api/fields/{fid}/observations").json()
    assert [o["id"] for o in obs] == [o1, o2]

    h = c.get(f"/api/fields/{fid}/observations/{o2}/hotspots?index=ndvi").json()
    assert h["hotspots"] and h["hotspots"][0]["area_ha"] > 1.5

    z = c.get(f"/api/fields/{fid}/zones?index=ndvi&n=3").json()
    assert z["mode"] == "multi-date" and len(z["zones"]) == 3 and z["png"].startswith("data:image/png")

    zs = c.get(f"/api/fields/{fid}/zones?index=ndvi&n=3&obs_id={o1}").json()
    assert zs["mode"] == "single-date"

    ch = c.get(f"/api/fields/{fid}/change?before={o1}&after={o2}&index=ndvi").json()
    assert ch["declined_share"] > 0.05 and ch["decline_hotspots"]

    csv = c.get(f"/api/fields/{fid}/timeseries.csv").text.splitlines()
    assert csv[0].startswith("field_id,date") and len(csv) == 3

    assert c.get(f"/api/fields/{fid}/observations/{o1}/ndvi.png").status_code == 200
    assert c.get(f"/api/fields/{fid}/zones?index=bogus").status_code == 400
    assert c.get("/api/fields/nope").status_code == 404


def test_static_ui_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "Field Monitor" in r.text
    assert c.get("/vendor/leaflet/leaflet.js").status_code == 200
