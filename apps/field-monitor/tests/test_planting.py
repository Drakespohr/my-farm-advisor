"""Planting window and stand count reminders."""

from datetime import date, timedelta

import pytest

from fieldmonitor.planting import Day, gdd, parse_open_meteo, timing
from tests.test_farms import add_field, app, person  # noqa: F401  (fixture)


def series(start, soils, tmax=75, tmin=55, today=None):
    """Daily records from `start`; days after `today` are forecast."""
    d0 = date.fromisoformat(start)
    out = []
    for i, s in enumerate(soils):
        d = d0 + timedelta(days=i)
        out.append(Day(d.isoformat(), s, tmax, tmin, forecast=today is not None and d > today))
    return out


def entry(kind, when, **details):
    return {"kind": kind, "occurred_at": f"{when}T12:00:00", "details": details}


def test_gdd_uses_corn_method():
    assert gdd(80, 60) == 20 and gdd(95, 40) == (86 + 50) / 2 - 50 and gdd(None, 50) == 0


def test_window_opens_after_three_warm_days_and_a_warm_forecast():
    today = date(2027, 4, 24)
    days = series("2027-04-20", [44, 47, 50, 51, 53, 54, 55], today=today)  # 3 observed >= 50 (to the 24th), forecast 54, 55
    t = timing("corn", "NE", [], days, today)
    assert t["status"] == "window_open" and t["priority"] == 2 and t["tasks"] == [{"kind": "planting_window", "state": "open"}]
    assert t["soil"]["today_f"] == 53 and t["soil"]["threshold_f"] == 50
    cold = series("2027-04-20", [44, 47, 50, 51, 53, 46, 44], today=today)
    assert timing("corn", "NE", [], cold, today)["status"] == "cold_snap"
    cool = series("2027-04-20", [44, 45, 46, 47, 48, 49, 50], today=today)
    w = timing("soybeans", "NE", [], cool, today)
    assert w["status"] == "waiting" and "48°F" in w["headline"]


def test_off_season_and_wheat_and_no_crop():
    assert timing("corn", "NE", [], [], date(2026, 11, 5))["status"] == "off_season"
    wheat = timing("winter wheat", "NE", [], [], date(2026, 9, 15))
    assert wheat["status"] == "calendar" and "TODO" in wheat["headline"]
    assert timing("alfalfa", "NE", [], [], date(2027, 4, 25))["status"] == "no_crop"
    assert any("NE corn planting dates" in t for t in timing("corn", "NE", [], [], date(2027, 4, 25))["todo"])


def test_corn_emergence_estimate_drives_stand_count_reminder():
    planted = "2027-05-01"
    days = series(planted, [60] * 40, tmax=80, tmin=60)  # 20 GDD/day -> 115 GDD on day 6 (May 6)
    log = [entry("planting", planted, population_k_per_ac=34)]
    t = timing("corn", "NE", log, days, date(2027, 5, 10))
    assert t["emergence"] == {"date": "2027-05-06", "estimated": True}
    assert t["status"] == "emerging" and t["tasks"][0]["due"] == "2027-05-20" and t["tasks"][0]["state"] == "upcoming"
    due = timing("corn", "NE", log, days, date(2027, 5, 22))
    assert due["status"] == "stand_count_due" and due["priority"] == 2
    late = timing("corn", "NE", log, days, date(2027, 6, 1))
    assert late["status"] == "stand_count_overdue" and late["priority"] == 3  # repeats until a count is logged
    assert timing("corn", "NE", log, days, date(2027, 7, 10))["status"] == "stand_count_missed"  # stops nagging
    assert timing("corn", "NE", log + [entry("harvest", "2027-06-05", yield_bu_per_ac=1)], days, date(2027, 6, 6))["priority"] == 0
    done = timing("corn", "NE", log + [entry("stand_count", "2027-06-02", plants_per_ac=32500)], days, date(2027, 6, 3))
    assert done["status"] == "stand_counted" and done["priority"] == 0 and "32,500" in done["headline"]


def test_logged_emergence_wins_and_soybean_fallback():
    planted = "2027-05-10"
    days = series(planted, [60] * 40, tmax=80, tmin=60)
    log = [entry("planting", planted, population_k_per_ac=140), entry("growth_stage", "2027-05-20", stage="VE")]
    t = timing("corn", "NE", log, days, date(2027, 5, 21))
    assert t["emergence"] == {"date": "2027-05-20", "estimated": False} and t["tasks"][0]["due"] == "2027-06-03"
    soy = timing("soybeans", "NE", log[:1], days, date(2027, 5, 15))
    assert soy["tasks"][0]["due"] == "2027-05-31" and "log emergence" in soy["headline"]  # 21 days after planting


def test_parse_open_meteo_daily_soil_means():
    data = {"utc_offset_seconds": -18000,
            "hourly": {"time": ["2027-04-24T00:00", "2027-04-24T12:00", "2027-04-25T00:00", "2027-04-26T00:00"],
                       "soil_temperature_6cm": [48.0, 52.0, 51.0, None]},
            "daily": {"time": ["2027-04-24", "2027-04-25", "2027-04-26"], "temperature_2m_max": [70, 72, 74],
                      "temperature_2m_min": [45, 47, 49]}}
    days = parse_open_meteo(data, today=date(2027, 4, 25))
    assert [(d.date, d.soil_f, d.forecast) for d in days] == [("2027-04-24", 50.0, False), ("2027-04-25", 51.0, False),
                                                              ("2027-04-26", None, True)]


class FakeSoil:
    def __init__(self, days):
        self.days, self.calls = days, 0

    def daily(self, lat, lon):
        self.calls += 1
        return self.days


def test_timing_api_and_brief_use_the_field_log(app, monkeypatch):  # noqa: F811
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    fid = add_field(owner, "North 80")
    farm = owner.get("/api/farms").json()[0]["id"]
    today = date.today()
    start = today - timedelta(days=30)  # planted 30 days ago -> overdue, still within the reminder period
    app.state.soil_weather = FakeSoil(series(start.isoformat(), [60] * 37, tmax=80, tmin=60, today=today))
    owner.post(f"/api/farms/{farm}/log", json={"kind": "planting", "field_id": fid, "occurred_at": f"{start}T12:00:00Z",
                                               "details": {"population_k_per_ac": 34}})
    t = owner.get(f"/api/fields/{fid}/timing").json()
    assert t["status"] == "stand_count_overdue" and t["planted_on"] == start.isoformat() and t["sources"]
    b = owner.get("/api/brief").json()
    card = b["fields"][0]
    assert card["timing"]["status"] == "stand_count_overdue" and card["headline"].startswith("Stand count overdue")
    assert b["priorities"][0]["text"].startswith("Stand count overdue")
    from datetime import datetime, timezone
    r = owner.post(f"/api/farms/{farm}/log", json={"kind": "stand_count", "field_id": fid,
                   "occurred_at": datetime.now(timezone.utc).isoformat(), "details": {"plants_per_ac": 33000}})
    assert r.status_code == 201, r.text
    assert owner.get(f"/api/fields/{fid}/timing").json()["status"] == "stand_counted"


def test_timing_survives_weather_outage(app):  # noqa: F811
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    fid = add_field(owner, "North 80")
    t = owner.get(f"/api/fields/{fid}/timing").json()  # conftest makes the feed fail
    assert t["status"] in ("off_season", "no_soil_data", "calendar") and t["soil"] is None
    assert owner.get("/api/brief").status_code == 200
