"""Restricted-use validation and record-keeping deadlines (step 1)."""

import datetime as dt

import pytest
from pydantic import ValidationError

import ag_inputs
from ag_inputs import FoliarApplication, record_due_by

BASE = {"kind": "foliar", "product": "Example 4EC", "purpose": "insecticide", "rate": 8, "rate_unit": "fl_oz/ac"}
WHEN = dt.datetime(2026, 7, 1, 9, 30)


def test_restricted_use_requires_epa_number():
    with pytest.raises(ValidationError, match="require epa_reg_number"):
        FoliarApplication(**BASE, restricted_use=True)
    ok = FoliarApplication(**BASE, restricted_use=True, epa_reg_number="100-1070")
    assert ok.applicator_type == "private"


def test_commercial_and_noncommercial_require_temperature():
    for t in ("commercial", "noncommercial"):
        with pytest.raises(ValidationError, match="require temp_f"):
            FoliarApplication(**BASE, applicator_type=t)
        assert FoliarApplication(**BASE, applicator_type=t, temp_f=72).temp_f == 72
    assert FoliarApplication(**BASE).temp_f is None  # private, non-RUP: optional


@pytest.mark.parametrize("bad", ["1001070", "100-", "EPA 100-1070", "100-1070-"])
def test_epa_number_format(bad):
    with pytest.raises(ValidationError):
        FoliarApplication(**BASE, epa_reg_number=bad)


@pytest.mark.parametrize("bad", ["7:30", "24:00", "07:60", "0730"])
def test_start_time_format(bad):
    with pytest.raises(ValidationError):
        FoliarApplication(**BASE, start_time=bad)
    assert FoliarApplication(**BASE, start_time="07:30").start_time == "07:30"


def test_record_due_by_rules():
    assert record_due_by("foliar", {"restricted_use": True, "applicator_type": "private"}, WHEN) == WHEN + dt.timedelta(days=14)
    assert record_due_by("foliar", {"applicator_type": "commercial"}, WHEN) == WHEN + dt.timedelta(hours=48)
    assert record_due_by("foliar", {"applicator_type": "noncommercial", "restricted_use": True}, WHEN) == WHEN + dt.timedelta(hours=48)
    assert record_due_by("foliar", {"applicator_type": "private", "restricted_use": False}, WHEN) is None
    assert record_due_by("foliar", {}, WHEN) is None  # records saved before these fields existed
    assert record_due_by("fertilizer", {"restricted_use": True}, WHEN) is None


def _season(c):
    farm = c.post("/api/farms", json={"name": "Farm"}).json()["id"]
    f = c.post("/api/fields", json={"farm_id": farm, "name": "F", "centroid_lat": 41, "centroid_lon": -98}).json()
    return c.post(f"/api/fields/{f['id']}/seasons", json={"year": 2026, "crop": "corn"}).json()["id"]


def test_add_event_reports_deadline_and_lateness(client):
    sid = _season(client)
    now = ag_inputs._utcnow()
    rup = {**BASE, "restricted_use": True, "epa_reg_number": "100-1070", "start_time": "07:30", "area_treated_ac": 80, "location": "North 80"}
    on_time = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": (now - dt.timedelta(days=3)).isoformat(), "payload": rup, "recorded_by": "J. Smith"}).json()
    assert on_time["recorded_late"] is False and on_time["recorded_by"] == "J. Smith"
    assert dt.datetime.fromisoformat(on_time["record_due_by"]) == dt.datetime.fromisoformat(on_time["occurred_at"]) + dt.timedelta(days=14)
    late = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": (now - dt.timedelta(days=20)).isoformat(), "payload": rup}).json()
    assert late["recorded_late"] is True
    comm = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": (now - dt.timedelta(hours=60)).isoformat(),
                                                           "payload": {**BASE, "applicator_type": "commercial", "temp_f": 70}}).json()
    assert comm["recorded_late"] is True
    plain = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": now.isoformat(), "payload": {**BASE}}).json()
    assert "record_due_by" not in plain and "recorded_late" not in plain
    bad = client.post(f"/api/seasons/{sid}/events", json={"occurred_at": now.isoformat(), "payload": {**BASE, "restricted_use": True}})
    assert bad.status_code == 422
