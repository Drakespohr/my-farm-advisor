import numpy as np
import pytest

from fieldmonitor import analysis
from tests.conftest import ndvi_field, synthetic_grid


def test_field_stats_basic():
    s = analysis.field_stats(np.array([0.1, 0.2, 0.3, np.nan]))
    assert s["n"] == 3 and s["median"] == pytest.approx(0.2) and s["mean"] == pytest.approx(0.2)
    assert analysis.field_stats(np.array([np.nan]))["median"] is None


def test_hotspot_found_where_planted():
    arr = ndvi_field()
    arr[20:30, 40:50] = 0.4  # a 1 ha drowned-out patch
    valid = np.ones_like(arr, bool)
    t, crs = synthetic_grid()
    spots = analysis.hotspots(arr, valid, t, crs)
    assert len(spots) == 1
    h = spots[0]
    assert h["area_ha"] == pytest.approx(1.0, abs=0.05)
    assert h["deficit"] > 0.3
    # The GPS point must lie within the planted patch (cols 40-50, rows 20-30).
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(h["lon"], h["lat"])
    col, row = ~t * (x, y)
    assert 40 <= col <= 50 and 20 <= row <= 30


def test_no_hotspots_on_uniform_field():
    arr = np.full((40, 40), 0.8, np.float32)
    t, crs = synthetic_grid()
    assert analysis.hotspots(arr, np.ones_like(arr, bool), t, crs) == []


def test_small_patches_are_ignored():
    arr = ndvi_field()
    arr[5:7, 5:7] = 0.3  # 0.04 ha
    t, crs = synthetic_grid()
    assert analysis.hotspots(arr, np.ones_like(arr, bool), t, crs, min_area_ha=0.2) == []


def test_zones_equal_area_and_ordered():
    arr = ndvi_field()
    t, crs = synthetic_grid()
    z = analysis.management_zones(arr, np.ones_like(arr, bool), t, crs, 3)
    shares = [zz["share"] for zz in z["zones"]]
    means = [zz["mean"] for zz in z["zones"]]
    assert all(abs(s - 1 / 3) < 0.06 for s in shares)
    assert means == sorted(means)
    assert z["geojson"]["features"]


def test_zones_refuse_tiny_input():
    t, crs = synthetic_grid()
    with pytest.raises(ValueError):
        analysis.management_zones(np.ones((3, 3), np.float32), np.ones((3, 3), bool), t, crs, 3)


def test_standardized_mean_requires_majority_coverage():
    a = ndvi_field(seed=1)
    b = ndvi_field(seed=2) - 0.3  # different absolute level, same pattern
    va = np.ones_like(a, bool)
    vb = np.ones_like(b, bool)
    vb[:, :10] = False
    mean, valid = analysis.standardized_mean([a, b], [va, vb])
    assert valid.all()  # covered by at least half of the dates everywhere
    assert abs(np.nanmean(mean)) < 0.2


def test_change_detects_decline():
    before = ndvi_field()
    after = before.copy()
    after[:, :30] -= 0.2
    c = analysis.change(before, after, np.ones_like(before, bool), 0.1)
    assert c["declined_share"] == pytest.approx(0.5, abs=0.01)
    assert c["improved_share"] == 0


def obs(d, med, cv=0.05):
    return {"date": d, "stats": {"ndvi": {"median": med, "cv": cv}}}


def test_midseason_drop_is_an_alert():
    series = [obs("2025-06-20", 0.7), obs("2025-07-01", 0.85), obs("2025-07-10", 0.6, 0.2), obs("2025-07-20", 0.9)]
    kinds = [(a["kind"], a["severity"]) for a in analysis.timeseries_alerts(series)]
    assert ("decline", "high") in kinds and ("patchiness", "medium") in kinds


def test_post_peak_drop_without_recovery_is_senescence():
    series = [obs("2025-07-20", 0.92), obs("2025-09-07", 0.86), obs("2025-09-14", 0.44), obs("2025-09-22", 0.31)]
    alerts = analysis.timeseries_alerts(series)
    assert alerts and all(a["kind"] in ("senescence", "patchiness") for a in alerts)
    assert {a["severity"] for a in alerts if a["kind"] == "senescence"} == {"info"}


def test_bare_soil_noise_is_not_flagged():
    assert analysis.timeseries_alerts([obs("2025-05-01", 0.2, 0.3), obs("2025-05-10", 0.15, 0.45)]) == []
