"""Opening farm survey: answers tailor the planting and stand count reminders."""

from datetime import date

import pytest

from fieldmonitor import survey
from fieldmonitor.planting import timing
from tests.test_farms import add_field, app, person  # noqa: F401  (fixture)
from tests.test_planting import entry, series


def test_validation_drops_unknowns_and_questions_that_do_not_apply():
    a = survey.validate({"crops": ["corn"], "corn_start": "2027-04-20", "soy_start": "05-01", "bogus": 1, "tillage": "no_till"})
    assert a == {"crops": ["corn"], "corn_start": "04-20", "tillage": "no_till"}  # soybean question doesn't apply
    with pytest.raises(ValueError, match="Which crops"):
        survey.validate({"tillage": "no_till"})
    with pytest.raises(ValueError, match="Unknown choice"):
        survey.validate({"crops": ["corn"], "stand_counts": "daily"})
    with pytest.raises(ValueError, match="04-20"):
        survey.validate({"crops": ["corn"], "corn_start": "April"})


def test_preferences_mapping():
    p = survey.preferences(survey.validate({"crops": ["corn", "soybeans"], "start_trigger": "soil_temp", "corn_start": "04-25",
                                            "corn_finish": "05-20", "corn_soil_f": "50", "soy_soil_f": "60", "stand_counts": "never",
                                            "reminder_hour": "6"}))
    assert p["corn"] == {"start": "04-25", "finish": "05-20", "soil_f": 50.0} and p["soybean"] == {"soil_f": 60.0}
    assert p["stand_counts"] == "never" and p["reminder_hour"] == "6" and survey.preferences(None) == {}


WARM = series("2027-04-20", [44, 47, 50, 51, 53, 54, 55], today=date(2027, 4, 24))


def test_soil_farmer_planting_earlier_than_usual_is_told_so():
    t = timing("corn", "NE", [], WARM, date(2027, 4, 24), {"trigger": "soil_temp", "corn": {"start": "05-01"}})
    assert t["status"] == "window_open" and "earlier than your usual May 1" in t["headline"] and t["tailored"]
    assert not any("planting dates" in x for x in t["todo"])  # their dates fill in for the TODO state calendar


def test_calendar_farmer_window_opens_on_their_date():
    prefs = {"trigger": "calendar", "corn": {"start": "04-28"}}
    assert timing("corn", "NE", [], WARM, date(2027, 4, 24), prefs)["status"] == "too_early"
    cold = series("2027-04-25", [55, 55, 55, 55, 44, 43], today=date(2027, 4, 28))
    t = timing("corn", "NE", [], cold, date(2027, 4, 28), prefs)
    assert t["status"] == "window_open" and "your usual start date Apr 28" in t["headline"] and "Cold soil ahead" in t["headline"]


def test_their_soil_threshold_and_finish_date():
    t = timing("soybeans", "NE", [], WARM, date(2027, 4, 24), {"soybean": {"soil_f": 60.0}})
    assert t["status"] == "waiting" and "opens at 60°F" in t["headline"]
    late = timing("corn", "NE", [], WARM, date(2027, 5, 25), {"corn": {"start": "04-20", "finish": "05-20"}})
    assert late["status"] == "late" and "your usual corn finish date (May 20)" in late["headline"]


def test_no_stand_count_habit_means_no_nagging():
    days = series("2027-05-01", [60] * 40, tmax=80, tmin=60)
    log = [entry("planting", "2027-05-01", population_k_per_ac=34)]
    t = timing("corn", "NE", log, days, date(2027, 6, 1), {"stand_counts": "never"})
    assert t["status"] == "stand_count_optional" and t["priority"] == 0


def test_survey_api_roles_history_and_brief_banner(app):  # noqa: F811
    owner, _ = person(app, "olive@x.co", "Olive Owner")
    add_field(owner, "North 80")
    farm = owner.get("/api/farms").json()[0]["id"]
    assert owner.get("/api/brief").json()["survey_needed"] == [{"farm_id": farm, "name": "Olive Owner's farm"}]
    qs = owner.get("/api/survey/questions").json()
    assert qs[0]["id"] == "crops" and any(q["type"] == "monthday" for q in qs)
    assert owner.get(f"/api/farms/{farm}/survey").json()["latest"] is None
    r = owner.put(f"/api/farms/{farm}/survey", json={"answers": {"crops": ["corn"], "corn_start": "04-25", "stand_counts": "always"}})
    assert r.status_code == 200 and r.json()["latest"]["answers"]["corn_start"] == "04-25"
    owner.put(f"/api/farms/{farm}/survey", json={"answers": {"crops": ["corn"], "corn_start": "04-28"}})
    assert owner.get(f"/api/farms/{farm}/survey").json()["latest"]["answers"]["corn_start"] == "04-28"  # newest wins
    with app.state.surveys.accounts.db() as c:
        assert c.execute("SELECT COUNT(*) FROM farm_surveys").fetchone()[0] == 2  # history kept
    assert owner.get("/api/brief").json()["survey_needed"] == []
    agro, _ = person(app, "ana@x.co", "Ana Agronomist")
    owner.post(f"/api/farms/{farm}/members", json={"email": "ana@x.co", "role": "agronomist"})
    assert agro.get(f"/api/farms/{farm}/survey").status_code == 200
    assert agro.put(f"/api/farms/{farm}/survey", json={"answers": {"crops": ["corn"]}}).status_code == 403
    stranger, _ = person(app, "ned@x.co", "Ned")
    assert stranger.get(f"/api/farms/{farm}/survey").status_code == 404
    assert owner.put(f"/api/farms/{farm}/survey", json={"answers": {}}).status_code == 422


def test_new_farmer_sees_the_survey_before_any_field(app):  # noqa: F811
    c, _ = person(app, "nora@x.co", "Nora New")
    b = c.get("/api/brief").json()
    assert b["fields"] == [] and b["survey_needed"][0]["name"] == "Nora New's farm"
