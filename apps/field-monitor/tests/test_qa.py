import numpy as np
import pytest

from fieldmonitor import qa
from tests.conftest import FIELD
from tests.test_api import add_obs


def field_at(level, seed=0, shape=(60, 60)):
    rng = np.random.default_rng(seed)
    return (level + rng.normal(0, 0.01, shape)).astype(np.float32)


def ring(ndvi):
    return {"ring": {"n": 500, "clear_fraction": 1.0, "ndvi": ndvi}}


@pytest.fixture()
def fid(store):
    rec, _ = store.create_field(FIELD, "f")
    return rec["id"]


def run(store, fid):
    return qa.assess(store, fid, "ndvi")


def test_haze_date_is_suspect_and_its_dip_never_alerts(store, fid):
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.8))
    add_obs(store, fid, "2025-07-06", field_at(0.70, 2), qa={**ring(0.7), "haze": "suspect", "haze_reasons": ["aerosol optical thickness 0.53"]})
    add_obs(store, fid, "2025-07-11", field_at(0.86, 3), qa=ring(0.8))
    res = run(store, fid)
    by_date = {o["date"]: o for o in res["observations"]}
    assert by_date["2025-07-06"]["qa_status"] == "suspect"
    assert by_date["2025-07-06"]["qa_flags"][0]["code"] == "haze"
    assert res["alerts"] == []


def test_one_pass_dip_that_reverts_is_transient(store, fid):
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.8))
    add_obs(store, fid, "2025-07-06", field_at(0.65, 2), qa=ring(0.6))  # missed shadow
    add_obs(store, fid, "2025-07-11", field_at(0.86, 3), qa=ring(0.8))
    res = run(store, fid)
    mid = next(o for o in res["observations"] if o["date"] == "2025-07-06")
    assert mid["qa_status"] == "suspect" and mid["qa_flags"][0]["code"] == "transient"
    assert res["alerts"] == []


def test_latest_drop_is_pending_until_next_pass(store, fid):
    add_obs(store, fid, "2025-06-20", field_at(0.80, 0), qa=ring(0.8))
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.8))
    add_obs(store, fid, "2025-07-06", field_at(0.65, 2), qa=ring(0.8))
    a = run(store, fid)["alerts"]
    assert [(x["kind"], x["status"], x["severity"]) for x in a] == [("decline", "pending", "pending")]


def test_confirmed_field_specific_decline_is_high(store, fid):
    add_obs(store, fid, "2025-06-20", field_at(0.80, 0), qa=ring(0.8))
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.8))
    add_obs(store, fid, "2025-07-06", field_at(0.62, 2), qa=ring(0.8))
    add_obs(store, fid, "2025-07-11", field_at(0.63, 3), qa=ring(0.81))
    add_obs(store, fid, "2025-07-20", field_at(0.90, 4), qa=ring(0.85))  # later regrowth keeps it out of "senescence"
    a = [x for x in run(store, fid)["alerts"] if x["kind"] == "decline"]
    assert a[0]["status"] == "confirmed" and a[0]["scope"] == "field" and a[0]["severity"] == "high"


def test_regional_decline_attributed_to_neighbors(store, fid):
    add_obs(store, fid, "2025-06-20", field_at(0.80, 0), qa=ring(0.8))
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.84))
    add_obs(store, fid, "2025-07-06", field_at(0.70, 2), qa=ring(0.68))
    add_obs(store, fid, "2025-07-11", field_at(0.71, 3), qa=ring(0.69))
    add_obs(store, fid, "2025-07-25", field_at(0.88, 4), qa=ring(0.86))
    a = [x for x in run(store, fid)["alerts"] if x["kind"] == "decline"]
    assert a[0]["scope"] == "regional" and a[0]["severity"] == "medium"


def test_compares_only_pixels_clear_on_both_dates(store, fid):
    # West half is genuinely weaker; on the second date only the west half is clear.
    a = field_at(0.85, 1)
    a[:, :30] = 0.70
    b = a.copy()
    valid_b = np.zeros_like(b, bool)
    valid_b[:, :35] = True
    add_obs(store, fid, "2025-06-25", field_at(0.82, 0), qa=ring(0.8))
    add_obs(store, fid, "2025-07-01", a, qa=ring(0.8))
    add_obs(store, fid, "2025-07-06", b, qa=ring(0.8), valid=valid_b, clear_fraction=0.58)
    # Field-median comparison would show a false 0.13 drop; paired comparison shows none.
    assert [x for x in run(store, fid)["alerts"] if x["kind"] == "decline"] == []
    part = next(o for o in run(store, fid)["observations"] if o["date"] == "2025-07-06")
    assert any(fl["code"] == "partial" for fl in part["qa_flags"])


def test_low_overlap_pair_is_skipped(store, fid):
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa=ring(0.8))
    v = np.zeros((60, 60), bool)
    v[:, :20] = True
    add_obs(store, fid, "2025-07-06", field_at(0.6, 2), qa=ring(0.8), valid=v, clear_fraction=0.33)
    assert run(store, fid)["alerts"] == []


def test_post_peak_decline_is_senescence(store, fid):
    add_obs(store, fid, "2025-08-01", field_at(0.92, 1), qa=ring(0.9))
    add_obs(store, fid, "2025-09-07", field_at(0.86, 2), qa=ring(0.85))
    add_obs(store, fid, "2025-09-22", field_at(0.31, 3), qa=ring(0.40))
    add_obs(store, fid, "2025-09-27", field_at(0.26, 4), qa=ring(0.33))
    a = run(store, fid)["alerts"]
    sen = [x for x in a if x["kind"] == "senescence"]
    assert sen and sen[0]["severity"] == "info" and sen[0]["scope"] == "regional"


def test_sync_reprocesses_stale_versions(store, monkeypatch):
    from fieldmonitor.sync import Syncer

    rec, _ = store.create_field(FIELD, "f")
    old = {"id": "2025-07-01_S2A_X_v0", "datetime": "2025-07-01T17:00:00Z", "date": "2025-07-01", "scene_id": "S2A_X",
           "status": "clear", "processing_version": "0", "provenance": {"scene_href": "https://example/x.json"}}
    store.write_observation(rec["id"], old["id"], old, {}, {})
    processed = []
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a: ([], "test"))
    monkeypatch.setattr("fieldmonitor.sync.load_scene", lambda href: type("S", (), {"id": "S2A_X", "href": href})())
    monkeypatch.setattr("fieldmonitor.sync.process_observation", lambda st, f, sc: processed.append(sc.id) or {"status": "clear"})
    s = Syncer(store, refresh_hours=12)
    assert rec["id"] in s.due_fields()
    s.wait(s.start(rec["id"], "manual"))
    assert processed == ["S2A_X"]


def test_decline_without_neighbor_data_does_not_crash(store, fid):
    add_obs(store, fid, "2025-07-01", field_at(0.85, 1), qa={})
    add_obs(store, fid, "2025-07-06", field_at(0.62, 2), qa={})
    a = run(store, fid)["alerts"]
    assert a and a[0]["scope"] == "unknown"
