"""Yield estimates: field-count formulas, satellite canopy signal, farm calibration, API, brief."""

from datetime import date

import pytest

from fieldmonitor import yields
from tests.conftest import ndvi_field
from tests.test_brief import area, client, new_field  # noqa: F401  (fixture)
from tests.test_api import add_obs


# ---- field counts (extension formulas) ------------------------------------------------

def test_corn_count_matches_the_yield_component_method():
    # 30 ears in 1/1000 acre (17.42 ft of 30" row), 16 rows x 35 kernels, 90,000 kernels/bu
    r = yields.count_estimate("corn", {"ears": [29, 31], "kernel_rows": [16, 16], "kernels_per_row": [34, 36]})
    assert r["parts"]["ears_per_ac"] == pytest.approx(30_000, rel=0.002)
    assert r["bu_ac"] == pytest.approx(30_000 * 560 / 90_000, rel=0.003)  # ~186.7
    assert (r["low"], r["high"]) == (round(r["bu_ac"] - 20), round(r["bu_ac"] + 20))  # Purdue: +/-20 bu
    good_fill = yields.count_estimate("corn", {"ears": 30, "kernel_rows": 16, "kernels_per_row": 35, "kernels_per_bu": 80_000})
    assert good_fill["bu_ac"] > r["bu_ac"]


def test_soybean_count_matches_ohio_state_example():
    # OSU: 110 plants per 1/1000 ac, 42 pods/plant, 2.5 seeds/pod, 3,000 seeds/lb -> 64.2 bu/ac
    r = yields.count_estimate("soybeans", {"plants": 110, "pods_per_plant": 42})
    assert r["bu_ac"] == pytest.approx(64.2, abs=0.2)


def test_wheat_count_matches_nebguide_example():
    # NebGuide G1429: 30 heads/ft, 22 kernels/head, 12" rows -> (30 x 22 / 12) x 0.48 = 26.4 bu/ac.
    # The 0.48 factor equals 18,150 kernels/lb in the general formula.
    r = yields.count_estimate("winter wheat", {"heads": 30, "row_ft": 1, "row_in": 12, "kernels_per_head": 22,
                                               "kernels_per_lb": 18_150})
    assert r["bu_ac"] == pytest.approx(26.4, abs=0.1)


@pytest.mark.parametrize("crop,inputs,msg", [
    ("corn", {"ears": 30, "kernel_rows": 16, "kernels_per_row": 35, "kernels_per_bu": 200_000}, "65,000"),
    ("corn", {"ears": 30, "kernel_rows": 16}, "Missing"),
    ("corn", {"ears": -3, "kernel_rows": 16, "kernels_per_row": 35}, "no negatives"),
    ("soybeans", {"plants": 110, "pods_per_plant": 40, "pods": 3}, "Unexpected"),
    ("sorghum", {"heads": 3}, "corn, soybeans, and wheat"),
])
def test_count_validation(crop, inputs, msg):
    with pytest.raises(ValueError, match=msg):
        yields.count_estimate(crop, inputs)


# ---- satellite signal and calibration ------------------------------------------------

def o(day, field, area_med, qa_status="ok"):
    return {"date": day, "status": "clear", "qa_status": qa_status, "stats": {"ndvi": {"median": field}},
            "qa": {"area": {"ndvi_median": area_med}}}


SEASON_2025 = [o("2025-05-20", 0.30, 0.25), o("2025-06-25", 0.70, 0.66), o("2025-07-15", 0.88, 0.80),
               o("2025-08-01", 0.89, 0.82), o("2025-08-10", 0.95, 0.70, qa_status="suspect"),
               o("2025-09-05", 0.60, 0.55)]


def test_canopy_signal_uses_peak_window_and_skips_bad_images():
    s = yields.canopy_signal(SEASON_2025, 2025)
    assert s["stage"] == "peak" and s["window"] == ["2025-07-15", "2025-08-01"] and s["images"] == 2
    assert s["rel"] == pytest.approx((0.88 + 0.89) / (0.80 + 0.82), abs=1e-3)
    early = yields.canopy_signal(SEASON_2025[:4], 2025)
    assert early["stage"] == "early"  # area hasn't come down from its peak yet
    assert yields.canopy_signal(SEASON_2025, 2024)["stage"] == "none"


def test_calibration_recovers_farm_slope_and_reports_loo_error():
    pts = [{"expected": 200, "rel": r, "harvest": 200 * (1 + 2.0 * (r - 1))} for r in (0.92, 0.97, 1.0, 1.04, 1.08)]
    cal = yields.calibrate(pts)
    assert cal["k"] == pytest.approx(2.0, abs=0.01) and cal["n"] == 5 and cal["loo_rmse_bu"] == pytest.approx(0, abs=0.1)
    assert yields.calibrate(pts[:2]) is None


def test_estimate_prefers_harvest_then_fresh_count_then_satellite():
    field = {"id": "f", "crop": "corn"}
    exp = {"kind": "expected", "year": 2025, "bu_ac": 200}
    s = yields.estimate(field, SEASON_2025, [exp], 2025, today=date(2025, 9, 10))
    rel = s["signal"]["rel"]
    assert s["method"] == "satellite" and s["confidence"] == "rough" and s["bu_ac"] == round(200 * rel)
    assert s["low"] == round(200 * rel * 0.8) and "count" in s["next_steps"] and "record_harvests" in s["next_steps"]

    count = {"kind": "count", "year": 2025, "bu_ac": 190.0, "low": 170, "high": 210, "date": "2025-09-01"}
    assert yields.estimate(field, SEASON_2025, [exp, count], 2025, today=date(2025, 9, 10))["method"] == "count"
    stale = yields.estimate(field, SEASON_2025, [exp, count], 2025, today=date(2025, 12, 1))
    assert stale["method"] == "satellite"  # count older than 45 days: satellite leads again

    cal = {"k": 2.0, "n": 5, "loo_rmse_bu": 9.0}
    c = yields.estimate(field, SEASON_2025, [exp], 2025, cal, today=date(2025, 9, 10))
    assert c["method"] == "satellite_calibrated" and c["bu_ac"] == round(200 * (1 + 2 * (rel - 1)))
    assert c["high"] - c["low"] in (17, 18, 19)

    done = yields.estimate(field, SEASON_2025, [exp, {"kind": "harvest", "year": 2025, "bu_ac": 214}], 2025)
    assert done["method"] == "harvest" and done["bu_ac"] == 214

    rel_only = yields.estimate(field, SEASON_2025, [], 2025)
    assert rel_only["bu_ac"] is None and "greener than cropland nearby" in rel_only["label"] and "set_expected" in rel_only["next_steps"]
    assert yields.estimate({"id": "x", "crop": "alfalfa"}, SEASON_2025, [exp], 2025)["next_steps"] == ["set_crop"]


# ---- API and daily brief ---------------------------------------------------------------

def seed_2025(store, fid, field_ndvi):
    for day, fn, am in (("2025-06-20", field_ndvi - 0.15, 0.60), ("2025-07-15", field_ndvi, 0.80),
                        ("2025-08-01", field_ndvi, 0.80), ("2025-09-10", 0.45, 0.50)):
        add_obs(store, fid, day, ndvi_field(base=fn), qa=area(am))


def test_yield_api_records_and_estimates(client):  # noqa: F811
    c, store = client
    fid = new_field(c, "North 80")
    seed_2025(store, fid, 0.86)
    r = c.post(f"/api/fields/{fid}/yield/records", json={"kind": "expected", "year": 2025, "bu_ac": 210})
    assert r.status_code == 201
    y = r.json()
    assert y["method"] == "satellite" and y["signal"]["stage"] == "peak" and 150 < y["bu_ac"] < 260
    assert y["records"][0]["kind"] == "expected"

    bad = c.post(f"/api/fields/{fid}/yield/records", json={"kind": "count", "year": 2025, "inputs": {"ears": 30}})
    assert bad.status_code == 400 and "Missing" in bad.json()["detail"]
    ok = c.post(f"/api/fields/{fid}/yield/records", json={"kind": "count", "year": 2025, "on_date": "2025-09-01",
                "inputs": {"ears": 32, "kernel_rows": 16, "kernels_per_row": 36}})
    assert ok.json()["count"]["bu_ac"] == pytest.approx(32_000 * 576 / 90_000, rel=0.01)

    prev = c.post("/api/yield/count", json={"crop": "soybeans", "inputs": {"plants": 110, "pods_per_plant": 42}})
    assert prev.json()["bu_ac"] == pytest.approx(64.2, abs=0.2)
    assert c.get("/api/yield/methods").json()["defaults"]["corn"]["kernels_per_bu"] == 90_000


def test_farm_calibration_kicks_in_after_three_harvests(client):  # noqa: F811
    c, store = client
    fids = []
    for i, nd in enumerate((0.78, 0.84, 0.90)):
        fid = new_field(c, f"F{i}", shift=0.05 * (i + 1))
        seed_2025(store, fid, nd)
        c.post(f"/api/fields/{fid}/yield/records", json={"kind": "expected", "year": 2025, "bu_ac": 200})
        fids.append(fid)
    for fid, h in zip(fids, (175, 200, 228)):
        c.post(f"/api/fields/{fid}/yield/records", json={"kind": "harvest", "year": 2025, "bu_ac": h})
    target = new_field(c, "New")
    seed_2025(store, target, 0.90)
    c.post(f"/api/fields/{target}/yield/records", json={"kind": "expected", "year": 2025, "bu_ac": 200})
    y = c.get(f"/api/fields/{target}/yield", params={"year": 2025}).json()
    assert y["method"] == "satellite_calibrated" and y["calibration"]["n"] == 3 and y["calibration"]["k"] > 1
    assert y["confidence"] == "calibrated"


def test_viewers_see_estimates_not_records_and_cannot_write(client):  # noqa: F811
    c, store = client
    fid = new_field(c, "Shared")
    c.post(f"/api/fields/{fid}/yield/records", json={"kind": "expected", "year": 2025, "bu_ac": 60, "crop": "soybeans"})
    from tests.test_api import signup
    v = c.__class__(c.app, headers={"X-Requested-With": "fieldmonitor"})
    signup(v, "agro@x.co", "Agro Al")
    c.post(f"/api/fields/{fid}/members", json={"email": "agro@x.co"})
    assert v.get(f"/api/fields/{fid}/yield").json()["records"] == []
    assert v.post(f"/api/fields/{fid}/yield/records", json={"kind": "harvest", "year": 2025, "bu_ac": 70}).status_code == 403


def test_brief_includes_yield(client):  # noqa: F811
    c, store = client
    fid = new_field(c, "Brief field")
    seed_2025(store, fid, 0.86)
    c.post(f"/api/fields/{fid}/yield/records", json={"kind": "expected", "year": 2025, "bu_ac": 200})
    b = c.get("/api/brief").json()["fields"][0]
    assert b["yield"]["year"] == 2025 and b["yield"]["method"] == "satellite" and b["yield"]["bu_ac"]


def test_yield_records_are_append_only(store):
    rec, _ = store.create_field({"type": "Polygon", "coordinates": [[[-98.35, 40.92], [-98.34, 40.92], [-98.34, 40.93], [-98.35, 40.92]]]}, "A")
    store.add_yield_record(rec["id"], {"kind": "expected", "year": 2025, "bu_ac": 200})
    store.add_yield_record(rec["id"], {"kind": "expected", "year": 2025, "bu_ac": 205})
    rs = store.list_yield_records(rec["id"])
    assert [r["bu_ac"] for r in rs] == [200, 205] and yields.expected_for(rs, 2025)["bu_ac"] == 205
    with pytest.raises(ValueError):
        store.add_yield_record(rec["id"], {"kind": "guess", "year": 2025})
