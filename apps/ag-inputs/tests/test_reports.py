"""Season CSV export and N summary (step 5)."""

import csv
import io

import nrd_rules
from conftest import as_user
from test_baseline import ev


def season(c, nrd=None):
    farm = c.post("/api/farms", json={"name": "Test Farm"}).json()["id"]
    body = {"farm_id": farm, "name": "South Pivot", "centroid_lat": 40.9, "centroid_lon": -98.3, "acres": 125}
    if nrd:
        body["nrd"] = nrd
    f = c.post("/api/fields", json=body).json()
    return farm, c.post(f"/api/fields/{f['id']}/seasons", json={"year": 2026, "crop": "corn", "planting_date": "2026-05-01"}).json()["id"]


def load(c, sid):
    ev(c, sid, "2026-04-20T12:00:00", {"kind": "soil_test", "sampled_on": "2026-04-15", "nitrate_n_ppm": 8, "depth_in": 24})
    ev(c, sid, "2026-04-25T12:00:00", {"kind": "fertilizer", "product": "Anhydrous ammonia", "method": "pre_plant", "rate_lb_per_ac": 150, "n_pct": 82})
    ev(c, sid, "2026-05-01T08:00:00", {"kind": "planting", "population_k_per_ac": 34})
    side = ev(c, sid, "2026-06-20T12:00:00", {"kind": "fertilizer", "product": "UAN 32%", "method": "side_dress", "rate_lb_per_ac": 100, "n_pct": 32})
    ev(c, sid, "2026-07-01T12:00:00", {"kind": "irrigation", "amount_in": 1.0, "method": "pivot"})
    ev(c, sid, "2026-07-08T12:00:00", {"kind": "irrigation", "amount_in": 1.0, "method": "pivot"})
    ev(c, sid, "2026-07-02T12:00:00", {"kind": "irrigation_water_sample", "nitrate_n_ppm": 10})
    ev(c, sid, "2026-07-03T12:00:00", {"kind": "rainfall", "amount_in": 0.8, "source": "on_farm_gauge"})
    ev(c, sid, "2026-07-20T09:30:00", {"kind": "foliar", "product": "=HYPERLINK(\"x\")", "purpose": "insecticide", "rate": 1.9,
                                       "rate_unit": "fl_oz/ac", "restricted_use": True, "epa_reg_number": "100-1295"})
    ev(c, sid, "2026-07-21T12:00:00", {"kind": "scouting", "target": "tar spot", "category": "disease", "incidence_pct": 5, "pattern": "patchy"})
    ev(c, sid, "2026-07-22T12:00:00", {"kind": "growth_stage", "stage": "R1"})  # not in the export
    ev(c, sid, "2026-10-10T12:00:00", {"kind": "harvest", "yield_bu_per_ac": 230})
    return side


def test_csv_export(client):
    _, sid = season(client)
    side = load(client, sid)
    # Correct the side-dress rate; the export uses the correction.
    c = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": "2026-06-20T12:00:00", "supersedes_id": side["id"],
                    "payload": {"kind": "fertilizer", "product": "UAN 32%", "method": "side_dress", "rate_lb_per_ac": 125, "n_pct": 32}}).json()
    r = client.get(f"/api/seasons/{sid}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="South_Pivot_2026.csv"' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["section"] for x in rows] == ["planting", "irrigation", "irrigation", "rainfall", "fertilizer", "fertilizer",
                                            "foliar", "scouting", "soil_test", "irrigation_water_sample", "harvest"]
    fert = [x for x in rows if x["section"] == "fertilizer"]
    assert [x["n_lb_per_ac"] for x in fert] == ["123.0", "40.0"]
    assert fert[1]["event_id"] == str(c["id"]) and fert[1]["supersedes_id"] == str(side["id"])
    foliar = next(x for x in rows if x["section"] == "foliar")
    assert foliar["epa_reg_number"] == "100-1295" and foliar["restricted_use"] == "True"
    assert foliar["record_due_by"] == "2026-08-03T09:30:00" and foliar["recorded_late"] == "True"  # logged in Nov for a July application
    assert foliar["product"].startswith("'=")  # formula neutralized
    assert all(x["field_name"] == "South Pivot" and x["year"] == "2026" for x in rows)
    soil = next(x for x in rows if x["section"] == "soil_test")
    assert '"sampled_on": "2026-04-15"' in soil["details"] and '"depth_in": 24' in soil["details"]


def test_export_requires_membership(client):
    _, sid = season(client)
    assert client.get(f"/api/seasons/{sid}/export.csv", headers=as_user("stranger")).status_code == 404


def test_n_summary_without_target(client):
    _, sid = season(client, nrd="central_platte")
    load(client, sid)
    s = client.get(f"/api/seasons/{sid}/n-summary").json()
    assert s["fertilizer_n_lb_per_ac"] == 155.0 and s["fertilizer_n_lb_total"] == 19375
    assert s["irrigation_water_n_lb_per_ac_estimate"] == 4.5  # 2 in x 10 ppm x 0.2265
    assert s["fertilizer_n_lb_per_bu"] == round(155 / 230, 3)
    assert s["nue_target_lb_n_per_bu"] is None and s["comparison"]["status"] == "no_target"


def test_n_summary_compares_with_configured_target(client, monkeypatch):
    _, sid = season(client, nrd="lower_loup")
    load(client, sid)
    import dataclasses
    test_cfg = dataclasses.replace(nrd_rules.NRDS["lower_loup"], nue_target_lb_n_per_bu=0.6)
    monkeypatch.setitem(nrd_rules.NRDS, "lower_loup", test_cfg)
    s = client.get(f"/api/seasons/{sid}/n-summary").json()
    assert s["nue_target_lb_n_per_bu"] == 0.6 and s["comparison"]["status"] == "above_target"
