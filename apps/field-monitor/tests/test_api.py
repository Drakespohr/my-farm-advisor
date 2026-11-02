import numpy as np
import pytest
from fastapi.testclient import TestClient

from fieldmonitor.api import create_app
from fieldmonitor.indices import INDICES
from fieldmonitor.analysis import field_stats
from tests.conftest import FIELD, ndvi_field, synthetic_grid


def add_obs(store, field_id, date, arr, qa=None, valid=None, clear_fraction=1.0):
    from fieldmonitor import PROCESSING_VERSION

    t, crs = synthetic_grid()
    valid = np.ones_like(arr, bool) if valid is None else valid
    arrays = {k: arr for k in INDICES}
    arrays.update(valid=valid, field=np.ones_like(arr, bool))
    meta = {
        "id": f"{date}_S2X_v{PROCESSING_VERSION}", "field_id": field_id, "scene_id": f"S2X_{date}", "date": date,
        "datetime": f"{date}T17:00:00Z", "status": "clear", "field_clear_fraction": clear_fraction,
        "stats": {k: field_stats(arr[valid]) for k in INDICES}, "bounds": [[0, 0], [1, 1]], "qa": qa or {},
        "grid": {"crs": crs, "transform": list(t)[:6], "shape": list(arr.shape), "resolution_m": 10},
        "processing_version": PROCESSING_VERSION,
    }
    store.write_observation(field_id, meta["id"], meta, arrays, {"ndvi": b"\x89PNG"})
    return meta["id"]


@pytest.fixture()
def client(store, monkeypatch):
    # No network in tests: syncs started by field creation find no scenes.
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    return TestClient(create_app(store, refresh_hours=0)), store


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


def test_new_field_starts_sync_and_redraw_versions(client):
    c, store = client
    r = c.post("/api/fields", json={"name": "North 80", "geojson": FIELD}).json()
    fid = r["created"][0]
    assert len(r["sync_jobs"]) == 1
    syncer = c.app.state.syncer
    syncer.wait(syncer.jobs[r["sync_jobs"][0]])
    st = next(f for f in c.get("/api/fields").json() if f["id"] == fid)["sync"]
    assert st["last_synced"] and st["last_reason"] == "new-field"

    smaller = {"type": "Polygon", "coordinates": [[[-93.62, 42.02], [-93.61, 42.02], [-93.61, 42.028], [-93.62, 42.028], [-93.62, 42.02]]]}
    rd = c.post(f"/api/fields/{fid}/redraw", json={"geojson": smaller}).json()
    assert rd["field"]["id"] == f"{fid}-v2" and rd["field"]["replaces"] == fid
    old = c.get(f"/api/fields/{fid}").json()
    assert old["archived"] and old["replaced_by"] == f"{fid}-v2"
    assert c.post(f"/api/fields/{fid}/redraw", json={"geojson": smaller}).status_code == 409


def test_import_kml_file(client):
    import base64

    c, _ = client
    kml = KML_DJI.encode()
    r = c.post("/api/fields", json={"file_name": "mission.kml", "file_b64": base64.b64encode(kml).decode(), "auto_sync": False})
    assert r.status_code == 201 and r.json()["created"] == ["east-pivot-" + r.json()["created"][0].split("-")[-1]]
    bad = c.post("/api/fields", json={"file_name": "x.kml", "file_b64": base64.b64encode(b"<kml/>").decode()})
    assert bad.status_code == 400


def test_geocode_accepts_coordinates_without_network(client):
    c, _ = client
    hits = c.get("/api/geocode?q=41.9, -93.6").json()
    assert hits[0]["lat"] == 41.9 and hits[0]["lon"] == -93.6


KML_DJI = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document><Folder><Placemark>
    <name>East Pivot</name>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -93.62,42.02,0 -93.608,42.02,0 -93.608,42.029,0 -93.62,42.029,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark></Folder></Document>
</kml>"""
