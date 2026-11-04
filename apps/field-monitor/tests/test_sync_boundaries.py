import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from fieldmonitor import boundaries
from fieldmonitor.sync import INITIAL_DAYS, OVERLAP_DAYS, Syncer
from tests.conftest import FIELD
from tests.test_api import KML_DJI


@pytest.fixture()
def syncer(store, monkeypatch):
    calls = []

    def fake_search(geom, start, end, max_cloud):
        calls.append((start, end))
        return [], "test"

    monkeypatch.setattr("fieldmonitor.sync.search_scenes", fake_search)
    s = Syncer(store, refresh_hours=12)
    s.calls = calls
    return s


def test_first_sync_backfills_a_year_then_overlaps(store, syncer):
    rec, _ = store.create_field(FIELD, "f")
    syncer.wait(syncer.start(rec["id"], "new-field"))
    start, end = syncer.calls[0]
    assert end == date.today() and (end - start).days == INITIAL_DAYS
    s2, _ = syncer.window_for(rec["id"])
    assert (date.today() - s2).days == OVERLAP_DAYS


def test_one_job_per_field(store, syncer, monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a: (gate.wait(5), ([], "test"))[1])
    rec, _ = store.create_field(FIELD, "f")
    j1 = syncer.start(rec["id"], "manual")
    j2 = syncer.start(rec["id"], "scheduled")
    assert j1 is j2
    gate.set()
    syncer.wait(j1)
    assert j1.state == "done"


def test_due_fields_respect_refresh_interval(store, syncer):
    rec, _ = store.create_field(FIELD, "f")
    assert syncer.due_fields() == [rec["id"]]
    syncer.wait(syncer.start(rec["id"], "manual"))
    assert syncer.due_fields() == []
    later = datetime.now(timezone.utc) + timedelta(hours=13)
    assert syncer.due_fields(later) == [rec["id"]]


def test_archived_fields_are_not_scheduled(store, syncer):
    rec, _ = store.create_field(FIELD, "f")
    store.update_field_meta(rec["id"], archived=True)
    assert syncer.due_fields() == []


def test_parse_dji_kml_and_kmz():
    feats = boundaries.parse_kml(KML_DJI)
    assert len(feats) == 1
    geom, props = feats[0]
    assert props["name"] == "East Pivot"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("wpmz/template.kml", KML_DJI)
    assert boundaries.parse_file("Mission.KMZ", buf.getvalue())[0][1]["name"] == "East Pivot"


def test_kml_waypoint_route_is_rejected_with_help():
    route = '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><LineString><coordinates>0,0 1,1</coordinates></LineString></Placemark></kml>'
    with pytest.raises(ValueError, match="polygon"):
        boundaries.parse_kml(route)


def test_unsupported_file_type():
    with pytest.raises(ValueError):
        boundaries.parse_file("field.shp", b"")


# ---- new fields get the newest imagery first, without waiting for the scheduler -------

def _scenes(n):
    from fieldmonitor.catalog import Scene
    return [Scene(id=f"S2B_{i:02d}", datetime=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T17:00:00Z", platform="sentinel-2b",
                  mgrs_tile="14TNL", cloud_cover=5.0, epsg=32614, assets={}, self_href=None, source="test") for i in range(n)]


def test_new_field_processes_newest_image_first(store, monkeypatch):
    order = []
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a: (_scenes(12), "test"))
    monkeypatch.setattr("fieldmonitor.sync.process_observation",
                        lambda st, f, sc: order.append(sc.datetime) or {"status": "clear"})
    s = Syncer(store, refresh_hours=0)
    s._pool = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=1)  # deterministic order
    rec, _ = store.create_field(FIELD, "f")
    job = s.start(rec["id"], "new-field")
    s.wait(job)
    assert job.state == "done" and job.total == 12
    assert order == sorted(order, reverse=True)  # newest first


def test_bulk_import_queues_every_field_even_with_scheduler_off(store, monkeypatch):
    from shapely.affinity import translate
    from shapely.geometry import mapping, shape
    synced = []
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda geom, *a: (synced.append(geom) or [], "test"))
    s = Syncer(store, refresh_hours=0)  # no periodic scheduler at all
    ids = [store.create_field(mapping(translate(shape(FIELD), xoff=0.02 * i)), f"f{i}")[0]["id"] for i in range(3)]
    assert s.enqueue(ids) == ids and s.enqueue(ids[:1]) == []  # no duplicates
    import time
    t0 = time.monotonic()
    while len(synced) < 3 and time.monotonic() - t0 < 10:
        time.sleep(0.05)
    assert len(synced) == 3
    t0 = time.monotonic()
    while any(s.status(i)["queued"] for i in ids) and time.monotonic() - t0 < 5:
        time.sleep(0.05)
    assert all(s.status(i)["last_synced"] and not s.status(i)["queued"] for i in ids)


def test_scheduler_handles_never_synced_fields_first(store, syncer):
    from shapely.affinity import translate
    from shapely.geometry import mapping, shape
    old, _ = store.create_field(FIELD, "old")
    syncer.wait(syncer.start(old["id"], "manual"))
    new, _ = store.create_field(mapping(translate(shape(FIELD), xoff=0.05)), "new")
    later = datetime.now(timezone.utc) + timedelta(hours=13)
    assert syncer.due_fields(later) == [new["id"], old["id"]]


def test_api_queues_fields_beyond_the_parallel_limit(store, monkeypatch):
    from fastapi.testclient import TestClient
    from shapely.affinity import translate
    from shapely.geometry import mapping, shape
    from fieldmonitor.api import BULK_SYNC_LIMIT, create_app
    from tests.test_api import signup
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    c = TestClient(create_app(store, refresh_hours=0), headers={"X-Requested-With": "fieldmonitor"})
    signup(c, "bulk@x.co", "Bulk Bo")
    feats = [{"type": "Feature", "properties": {"name": f"F{i}"}, "geometry": mapping(translate(shape(FIELD), xoff=0.02 * i))}
             for i in range(BULK_SYNC_LIMIT + 2)]
    r = c.post("/api/fields", json={"geojson": {"type": "FeatureCollection", "features": feats}}).json()
    assert len(r["sync_jobs"]) == BULK_SYNC_LIMIT and len(r["queued"]) == 2
