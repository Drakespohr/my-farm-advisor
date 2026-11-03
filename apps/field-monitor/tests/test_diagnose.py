import numpy as np
from shapely.geometry import box

from fieldmonitor import analysis, diagnose
from tests.conftest import synthetic_grid

SHAPE = (60, 60)


def base(seed=0):
    rng = np.random.default_rng(seed)
    n = lambda lvl: (lvl + rng.normal(0, 0.005, SHAPE)).astype(np.float32)  # noqa: E731
    return {"ndvi": n(0.85), "ndre": n(0.6), "ndmi": n(0.35), "ndwi": n(-0.6), "valid": np.ones(SHAPE, bool), "field": np.ones(SHAPE, bool)}


def field_utm():
    t, _ = synthetic_grid()
    x0, y0 = t.c, t.f
    return box(x0, y0 - 600, x0 + 600, y0)


def spot_of(arrays):
    t, crs = synthetic_grid()
    spots = analysis.hotspots(arrays["ndvi"], arrays["valid"], t, crs)
    assert spots, "expected a hotspot"
    return spots[0]


def causes(res):
    return " ".join(c["cause"] for c in res["causes"]).lower()


def test_summary_reports_ndvi_below_field_average():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.6
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    ndvi = next(v for v in res["values"] if v["key"] == "ndvi")
    assert ndvi["spot"] < ndvi["field"] and ndvi["diff"] < -0.2
    assert "below the field average" in res["summary"]


def test_standing_water():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.4
    a["ndwi"][20:32, 20:32] = 0.15
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert "water" in res["causes"][0]["cause"].lower()


def test_linear_strip_points_to_equipment():
    a = base()
    a["ndvi"][5:55, 30:32] = 0.6  # 500 m x 20 m
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert res["shape"]["linear"] and "straight-line" in causes(res)


def test_nitrogen_when_chlorophyll_drops_more_than_biomass():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.75  # -12%
    a["ndre"][20:32, 20:32] = 0.42  # -30%
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert "nitrogen" in causes(res)


def test_dry_spot_is_water_stress_not_nitrogen():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.7
    a["ndre"][20:32, 20:32] = 0.45
    a["ndmi"][20:32, 20:32] = 0.2
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert "less water in the leaves" in causes(res) and "nitrogen" not in causes(res)


def test_persistent_vs_new():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.6
    spot = spot_of(a)
    low_before = [(f"2025-07-0{i}", a) for i in range(1, 5)]
    assert diagnose.explain(spot, a, field_utm(), low_before, "mid")["history"]["kind"] == "persistent"
    fine_before = [("2025-07-01", base(1))]
    res = diagnose.explain(spot, a, field_utm(), fine_before, "mid")
    assert res["history"]["kind"] == "new" and "new since 2025-07-01" in causes(res)


def test_edge_spot():
    a = base()
    a["ndvi"][0:10, 20:35] = 0.6
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert res["shape"]["edge"] and "edge" in causes(res)


def test_early_season_points_to_stand():
    a = base()
    for k, lvl in (("ndvi", 0.35), ("ndre", 0.2)):
        a[k] = np.full(SHAPE, lvl, np.float32) + np.random.default_rng(3).normal(0, 0.005, SHAPE).astype(np.float32)
    a["ndvi"][20:32, 20:32] = 0.2
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "early")
    assert "stand" in causes(res)


def test_thin_canopy_is_not_called_water():
    a = base()
    a["ndvi"][20:32, 20:32] = 0.6
    a["ndwi"][20:32, 20:32] = -0.5  # higher than field (-0.6) only because NIR dropped
    res = diagnose.explain(spot_of(a), a, field_utm(), [], "mid")
    assert "water" not in res["causes"][0]["cause"].lower().split(":")[0]
    assert not any("Water detected" in p for p in res["pattern"])
