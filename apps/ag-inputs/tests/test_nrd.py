"""NRD configs and the season check (step 3). Configs are unverified; tests use test configs for logic."""

import dataclasses
import datetime as dt
from types import SimpleNamespace as NS

import pytest

import nrd_rules
from nrd_rules import NRDS, NRDConfig, Restriction, check_season

SEASON = NS(crop="corn", planting_date=None)


def ev(i, kind, when, **payload):
    return NS(id=i, kind=kind, occurred_at=when, payload={"kind": kind, **payload}, supersedes_id=payload.pop("_supersedes", None))


def test_all_requested_nrds_exist_and_are_unverified():
    assert {"upper_big_blue", "central_platte", "lower_loup", "lower_elkhorn",
            "upper_republican", "middle_republican", "lower_republican"} <= set(NRDS)
    for cfg in NRDS.values():
        assert cfg.verified is False and cfg.todo, cfg.key
        assert cfg.nue_target_lb_n_per_bu is None  # no invented targets
        assert all(k in nrd_rules.DATA_ITEMS for k in cfg.required_items)


def test_unconfigured_nrd_reports_todo_not_compliance():
    out = check_season(SEASON, [], "lower_loup")
    assert out["requirements_configured"] is False and out["missing"] == [] and out["todo"]


def test_missing_items_with_a_test_config():
    cfg = NRDConfig("test", "Test NRD", required_items=("planting_date", "fertilizer_n", "irrigation_water_nitrate", "yield"))
    events = [ev(1, "fertilizer", dt.datetime(2026, 4, 1), product="UAN", method="pre_plant", rate_lb_per_ac=100, n_pct=32),
              ev(2, "harvest", dt.datetime(2026, 10, 1), yield_bu_per_ac=220)]
    out = check_season(SEASON, events, "test", config=cfg)
    assert [m["key"] for m in out["missing"]] == ["planting_date", "irrigation_water_nitrate"]
    # A corrected-away record doesn't count.
    corrected = events + [ev(3, "harvest", dt.datetime(2026, 10, 2), yield_bu_per_ac=0, _supersedes=2)]
    assert [m["key"] for m in check_season(SEASON, corrected, "test", config=cfg)["missing"]] == ["planting_date", "irrigation_water_nitrate"]


def test_central_platte_fall_n_rule_waits_for_confirmed_parameters():
    fall_n = [ev(1, "fertilizer", dt.datetime(2025, 10, 20), product="Anhydrous ammonia", method="pre_plant", rate_lb_per_ac=150, n_pct=82)]
    out = check_season(SEASON, fall_n, "central_platte", {"nrd_phase": 2, "soil_texture": "loamy sand"})
    assert out["restrictions"][0]["status"] == "cannot_evaluate"
    assert check_season(SEASON, fall_n, "central_platte", {"nrd_phase": 1})["restrictions"][0]["status"] == "not_applicable"
    # With parameters filled in (as they will be once confirmed), the checker works.
    r = NRDS["central_platte"].restrictions[0]
    test_r = dataclasses.replace(r, params={**r.params, "fall_window": ("09-01", "12-31"), "sandy_textures": ("sand", "loamy sand")})
    cfg = dataclasses.replace(NRDS["central_platte"], restrictions=(test_r,))
    hit = check_season(SEASON, fall_n, "central_platte", {"nrd_phase": 3, "soil_texture": "loamy sand"}, config=cfg)["restrictions"][0]
    assert hit["status"] == "violation" and hit["event_ids"] == [1]
    loam = check_season(SEASON, fall_n, "central_platte", {"nrd_phase": 3, "soil_texture": "silt loam"}, config=cfg)["restrictions"][0]
    assert loam["status"] == "ok"
    spring = [ev(2, "fertilizer", dt.datetime(2026, 4, 20), product="UAN", method="pre_plant", rate_lb_per_ac=100, n_pct=32)]
    assert check_season(SEASON, spring, "central_platte", {"nrd_phase": 2, "soil_texture": "sand"}, config=cfg)["restrictions"][0]["status"] == "ok"
    assert check_season(SEASON, fall_n, "central_platte", {}, config=cfg)["restrictions"][0]["status"] == "missing_field_info"


def test_nrd_check_endpoint(client, farm_id):
    f = client.post("/api/fields", json={"farm_id": farm_id, "name": "F", "centroid_lat": 41, "centroid_lon": -98,
                                         "nrd": "central_platte", "nrd_phase": 2, "soil_texture": "loamy sand"}).json()
    sid = client.post(f"/api/fields/{f['id']}/seasons", json={"year": 2026, "crop": "corn"}).json()["id"]
    out = client.get(f"/api/seasons/{sid}/nrd-check").json()
    assert out["nrd"] == "central_platte" and out["restrictions"][0]["status"] == "cannot_evaluate"
    assert client.get(f"/api/seasons/{sid}/nrd-check?nrd=nope").status_code == 422
    assert client.post("/api/fields", json={"farm_id": farm_id, "name": "G", "centroid_lat": 41, "centroid_lon": -98, "nrd": "made_up"}).status_code == 422
    assert client.post("/api/fields", json={"farm_id": farm_id, "name": "G", "centroid_lat": 41, "centroid_lon": -98, "soil_texture": "gravel"}).status_code == 422
