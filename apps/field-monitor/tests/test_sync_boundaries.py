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
