from fieldmonitor.area import percentile_of
from fieldmonitor.score import field_score

Q_AREA = [0.5 + 0.02 * i for i in range(21)]  # area NDVI 0.50 .. 0.90, median 0.70


def obs(date, ndvi, cv=0.06, area_shift=0.0, qa_status="ok"):
    q = [v + area_shift for v in Q_AREA]
    return {"date": date, "status": "clear", "qa_status": qa_status,
            "stats": {"ndvi": {"median": ndvi, "cv": cv}},
            "qa": {"area": {"ndvi_q": q, "ndvi_median": q[10]}}}


def test_percentile_of():
    assert percentile_of(0.70, Q_AREA) == 50 and percentile_of(0.4, Q_AREA) == 0 and percentile_of(0.95, Q_AREA) == 100


def test_strong_even_field_scores_high():
    s = field_score([obs("2025-07-01", 0.86), obs("2025-07-10", 0.88), obs("2025-07-20", 0.89)])
    assert s["score"] >= 80 and s["label"] == "Excellent" and "Greener than" in s["points"][0]


def test_weak_patchy_field_scores_low():
    s = field_score([obs("2025-07-01", 0.60, cv=0.2), obs("2025-07-20", 0.55, cv=0.22)])
    assert s["score"] < 50 and s["label"] == "Needs attention"
    assert any("Patchy" in p for p in s["points"])


def test_falling_behind_area_lowers_pace():
    s = field_score([obs("2025-06-25", 0.80), obs("2025-07-20", 0.70, area_shift=0.05)])
    assert s["parts"]["pace"] < 30 and any("Falling behind" in p for p in s["points"])


def test_suspect_and_off_season_dates():
    s = field_score([obs("2025-07-01", 0.85), obs("2025-07-10", 0.30, qa_status="suspect")])
    assert s["detail"]["dates"] == ["2025-07-01"]
    off = obs("2025-10-20", 0.25, area_shift=-0.4)  # area median 0.30: off-season
    s2 = field_score([obs("2025-07-01", 0.85), off])
    assert s2["status"] == "paused" and s2["points"][0].startswith("Season over")
    # A long gap with no images at all is "off-season" rather than "season over".
    s3 = field_score([obs("2025-05-01", 0.85), obs("2025-07-20", 0.86)], as_of="2025-09-30")
    assert s3["status"] == "paused" and s3["points"][0].startswith("Off-season")


def test_no_area_data():
    assert field_score([])["score"] is None


def test_harvest_freezes_score_instead_of_failing():
    season = [obs("2025-07-20", 0.88), obs("2025-08-10", 0.89), obs("2025-09-07", 0.86)]
    harvest = obs("2025-09-22", 0.31, cv=0.2, area_shift=-0.35)  # area median 0.35: area is maturing too
    s = field_score(season + [harvest])
    assert s["status"] == "paused" and s["as_of"] == "2025-09-07" and s["score"] >= 70
    assert s["points"][0].startswith("Season over")
