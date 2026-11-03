"""New event kinds (step 4): irrigation water sample, manure sample, soil test sampling date."""

import ag_inputs
from test_baseline import ev, setup_season


def post(c, sid, payload, when="2026-06-01T12:00:00"):
    return c.post(f"/api/seasons/{sid}/events", json={"occurred_at": when, "payload": payload})


def test_irrigation_water_sample(client):
    sid = setup_season(client)
    e = ev(client, sid, "2026-06-01T12:00:00", {"kind": "irrigation_water_sample", "nitrate_n_ppm": 9.5, "source": "Well 2"})
    assert e["payload"]["nitrate_n_ppm"] == 9.5
    assert post(client, sid, {"kind": "irrigation_water_sample"}).status_code == 422


def test_manure_sample_units_follow_form(client):
    sid = setup_season(client)
    ok = {"kind": "manure_sample", "animal": "swine", "form": "liquid", "unit": "lb_per_1000_gal", "total_n": 35, "ammonium_n": 25}
    assert post(client, sid, ok).status_code == 201
    assert post(client, sid, {**ok, "unit": "lb_per_ton"}).status_code == 422
    assert post(client, sid, {**ok, "ammonium_n": 40}).status_code == 422


def test_soil_test_needs_sampling_date(client):
    sid = setup_season(client)
    base = {"kind": "soil_test", "nitrate_n_ppm": 12, "depth_in": 24}
    r = post(client, sid, base)
    assert r.status_code == 422 and "sampled_on" in r.text
    r = post(client, sid, {**base, "sampled_on": "2026-04-02"})
    assert r.status_code == 201 and r.json()["payload"]["sampled_on"] == "2026-04-02"
    assert post(client, sid, {**base, "sampled_on": "2999-01-01"}).status_code == 422


def test_new_kinds_are_draftable_and_in_union():
    assert {"irrigation_water_sample", "manure_sample"} <= set(ag_inputs.FORM_TYPES)
    assert ag_inputs.FORM_TYPES == ag_inputs.EVENT_KINDS


def test_context_shape_unchanged_by_new_soil_fields(client):
    sid = setup_season(client)
    ev(client, sid, "2026-04-05T12:00:00", {"kind": "soil_test", "sampled_on": "2026-04-01", "ph": 6.1})
    ev(client, sid, "2026-06-01T12:00:00", {"kind": "irrigation_water_sample", "nitrate_n_ppm": 9.5})
    ev(client, sid, "2026-06-02T12:00:00", {"kind": "manure_sample", "animal": "beef", "form": "solid", "unit": "lb_per_ton", "total_n": 12})
    ctx = client.get(f"/api/seasons/{sid}/diagnosis-context", params={"as_of": "2026-07-01T00:00:00"}).json()
    assert "sampled_on" not in ctx["latest_soil_test"]
    assert set(ctx) == {"season", "as_of", "days_since_planting", "growth_stage", "stand_count", "last_14_days",
                        "nitrogen_applied_season_lb_per_ac", "recent_fertilizer", "recent_foliar",
                        "scouting_latest_by_target", "latest_soil_test"}
