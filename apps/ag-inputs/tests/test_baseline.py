"""Behavior of the original ag_inputs.py, kept stable by every later change.

The diagnosis-context test is a golden snapshot: its output shape must not change.
"""

import datetime as dt

AS_OF = "2026-07-20T12:00:00"


def setup_season(c):
    f = c.post("/api/fields", json={"name": "North 80", "centroid_lat": 41.1, "centroid_lon": -98.0, "acres": 80}).json()
    s = c.post(f"/api/fields/{f['id']}/seasons", json={"year": 2026, "crop": "corn", "planting_date": "2026-05-01"}).json()
    return s["id"]


def ev(c, sid, when, payload):
    r = c.post(f"/api/seasons/{sid}/events", json={"occurred_at": when, "payload": payload})
    assert r.status_code == 201, r.text
    return r.json()


def test_field_season_event_roundtrip(client):
    sid = setup_season(client)
    e = ev(client, sid, "2026-05-01T08:00:00", {"kind": "planting", "population_k_per_ac": 34})
    assert set(e) >= {"id", "season_id", "kind", "occurred_at", "payload"}
    assert client.get(f"/api/seasons/{sid}/events?kind=planting").json()[0]["payload"]["population_k_per_ac"] == 34


def test_validation_rejects_bad_payloads(client):
    sid = setup_season(client)
    bad = [
        {"kind": "soil_moisture", "depth_in": 12},  # needs a reading
        {"kind": "irrigation", "amount_in": 1, "method": "bucket"},
        {"kind": "unknown"},
    ]
    for p in bad:
        assert client.post(f"/api/seasons/{sid}/events", json={"occurred_at": "2026-06-01T00:00:00", "payload": p}).status_code == 422
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
    assert client.post(f"/api/seasons/{sid}/events", json={"occurred_at": future, "payload": {"kind": "rainfall", "amount_in": 1, "source": "estimate"}}).status_code == 422


GOLDEN = {
    "season": {"year": 2026, "crop": "corn", "variety": None, "planting_date": "2026-05-01"},
    "as_of": "2026-07-20T12:00:00",
    "days_since_planting": 80,
    "growth_stage": {"stage": "V12", "days_ago": 2},
    "stand_count": {"plants_per_ac": 33000.0, "days_ago": 50},
    "last_14_days": {"rainfall_in": 1.2, "irrigation_in": 1.0, "total_water_in": 2.2},
    "nitrogen_applied_season_lb_per_ac": 179.6,
    "recent_fertilizer": [
        {"product": "Anhydrous ammonia", "method": "pre_plant", "rate_lb_per_ac": 180.0, "n_lb_per_ac": 147.6, "days_ago": 96},
        {"product": "UAN 32%", "method": "side_dress", "rate_lb_per_ac": 100.0, "n_lb_per_ac": 32.0, "days_ago": 20},
    ],
    "recent_foliar": [
        {"product": "Fungicide X", "purpose": "fungicide", "active_ingredient": None, "rate": 10.5, "rate_unit": "fl_oz/ac",
         "target": "tar spot", "growth_stage": "V12", "temp_f": 78.0, "wind_mph": 6.0, "days_ago": 1},
    ],
    "scouting_latest_by_target": {
        "tar spot": {"category": "disease", "incidence_pct": 5.0, "severity_pct": None, "pattern": "patchy", "growth_stage": "V12", "days_ago": 3},
    },
    "latest_soil_test": {"depth_in": 6.0, "ph": 6.4, "nitrate_n_ppm": 12.0, "days_ago": 110},
}


def test_diagnosis_context_golden(client):
    sid = setup_season(client)
    ev(client, sid, "2026-04-01T12:00:00", {"kind": "soil_test", "ph": 6.4, "nitrate_n_ppm": 12})
    ev(client, sid, "2026-04-15T12:00:00", {"kind": "fertilizer", "product": "Anhydrous ammonia", "method": "pre_plant", "rate_lb_per_ac": 180, "n_pct": 82})
    ev(client, sid, "2026-05-31T12:00:00", {"kind": "stand_count", "plants_per_ac": 33000})
    ev(client, sid, "2026-06-30T12:00:00", {"kind": "fertilizer", "product": "UAN 32%", "method": "side_dress", "rate_lb_per_ac": 100, "n_pct": 32})
    ev(client, sid, "2026-06-01T12:00:00", {"kind": "rainfall", "amount_in": 3.0, "source": "on_farm_gauge"})  # outside 14 days
    ev(client, sid, "2026-07-10T12:00:00", {"kind": "rainfall", "amount_in": 1.2, "source": "on_farm_gauge"})
    ev(client, sid, "2026-07-12T12:00:00", {"kind": "irrigation", "amount_in": 1.0, "method": "pivot"})
    ev(client, sid, "2026-07-17T12:00:00", {"kind": "scouting", "target": "tar spot", "category": "disease", "incidence_pct": 5, "pattern": "patchy", "growth_stage": "V12"})
    ev(client, sid, "2026-07-18T12:00:00", {"kind": "growth_stage", "stage": "V12"})
    ev(client, sid, "2026-07-19T12:00:00", {"kind": "foliar", "product": "Fungicide X", "purpose": "fungicide", "rate": 10.5, "rate_unit": "fl_oz/ac",
                                             "target": "tar spot", "growth_stage": "V12", "temp_f": 78, "wind_mph": 6})
    ev(client, sid, "2026-07-21T12:00:00", {"kind": "rainfall", "amount_in": 9.9, "source": "estimate"})  # after as_of: excluded
    got = client.get(f"/api/seasons/{sid}/diagnosis-context?as_of={AS_OF}").json()
    assert got == GOLDEN
