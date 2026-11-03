"""Streamlit page smoke tests (step 6): runs the page against the in-process API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import ag_inputs
import ui_client
from conftest import as_user

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


@pytest.fixture()
def seeded(client, farm_id, monkeypatch):
    f = client.post("/api/fields", json={"farm_id": farm_id, "name": "North 80", "centroid_lat": 41.1,
                                         "centroid_lon": -98.0, "acres": 80}).json()
    sid = client.post(f"/api/fields/{f['id']}/seasons", json={"year": 2026, "crop": "corn"}).json()["id"]
    monkeypatch.setenv("AG_AUTH_MODE", "dev")
    monkeypatch.setenv("AG_UI_DEV_USER", "owner")
    ui_client.set_client(TestClient(ag_inputs.app))
    yield client, sid
    ui_client.set_client(None)


def run(at=None):
    at = at or AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def by_label(widgets, label):
    return next(w for w in widgets if w.label == label)


def test_logs_a_restricted_use_foliar_record_with_countdown(seeded):
    client, sid = seeded
    at = run()
    at.selectbox(key="form_kind").set_value("foliar")
    at = run(at)
    at.selectbox(key="foliar.product").set_value("Other (type it)")
    at = run(at)
    at.text_input(key="foliar.product.other").set_value("Test RUP")
    at.number_input(key="foliar.rate").set_value(1.5)
    at.text_input(key="foliar.epa_reg_number").set_value("100-1295")
    at.checkbox(key="foliar.restricted_use").check()
    at = run(at)
    assert any("Record due by" in w.value for w in at.warning)  # countdown next to the RUP entry

    # The edit autosaved a draft that other users can see in the live panel.
    drafts = client.get(f"/api/seasons/{sid}/drafts").json()
    assert [d["form_type"] for d in drafts] == ["foliar"]

    by_label(at.button, "Save record").click()
    at = run(at)
    events = client.get(f"/api/seasons/{sid}/events").json()
    assert events[-1]["payload"]["epa_reg_number"] == "100-1295" and events[-1]["record_due_by"]
    assert client.get(f"/api/seasons/{sid}/drafts").json() == []  # draft cleared on save
    assert any("Saved foliar" in w.value for w in at.success)
    assert any("Test RUP" in m.value for m in at.markdown)  # listed with its deadline


def test_fertilizer_defaults_from_grade(seeded):
    client, sid = seeded
    at = run()
    at.selectbox(key="form_kind").set_value("fertilizer")
    at = run(at)
    at.selectbox(key="fertilizer.product").set_value("UAN 32%")
    at = run(at)
    assert at.number_input(key="fertilizer.n_pct").value == 32.0
    by_label(at.button, "Save record").click()
    run(at)
    p = client.get(f"/api/seasons/{sid}/events").json()[-1]["payload"]
    assert p["product"] == "UAN 32%" and p["n_pct"] == 32.0


def test_two_users_drafts_and_viewer_cannot_write(seeded, farm_id):
    client, sid = seeded
    client.get("/api/farms", headers=as_user("scout1"))
    client.get("/api/farms", headers=as_user("viewer1"))
    client.post(f"/api/farms/{farm_id}/members", json={"subject": "scout1", "role": "scout"})
    client.post(f"/api/farms/{farm_id}/members", json={"subject": "viewer1", "role": "viewer"})
    client.put(f"/api/seasons/{sid}/drafts/scouting", json={"content": {"target": "tar spot"}}, headers=as_user("scout1"))
    client.put(f"/api/seasons/{sid}/drafts/irrigation", json={"content": {"amount_in": 1}})

    at = run()
    text = " ".join(m.value for m in at.markdown)
    assert "Scout1" in text and "scouting" in text and "irrigation" in text

    at.sidebar.text_input(key="dev_user").set_value("viewer1")
    at = run(at)
    assert by_label(at.button, "Save record").disabled


def test_my_draft_loads_into_the_form(seeded):
    client, sid = seeded
    client.put(f"/api/seasons/{sid}/drafts/irrigation", json={"content": {"amount_in": 2, "method": "drip", "source": "Well 7"}})
    at = run()
    at.selectbox(key="form_kind").set_value("irrigation")
    at = run(at)
    assert at.number_input(key="irrigation.amount_in").value == 2.0
    assert at.selectbox(key="irrigation.method").value == "drip"
    assert at.text_input(key="irrigation.source.other").value == "Well 7"


def test_previous_product_carries_label_details(seeded):
    client, sid = seeded
    client.post(f"/api/seasons/{sid}/events", json={"occurred_at": "2026-07-01T10:00:00", "payload": {
        "kind": "foliar", "product": "Insecticide X", "purpose": "insecticide", "rate": 1.9, "rate_unit": "fl_oz/ac",
        "restricted_use": True, "epa_reg_number": "100-1295"}})
    at = run()
    at.selectbox(key="form_kind").set_value("foliar")
    at = run(at)
    assert at.selectbox(key="foliar.product").value == "—"  # no silent default product
    at.selectbox(key="foliar.product").set_value("Insecticide X")
    at = run(at)
    assert at.text_input(key="foliar.epa_reg_number").value == "100-1295"
    assert at.checkbox(key="foliar.restricted_use").value is True
    assert at.selectbox(key="foliar.purpose").value == "insecticide"
