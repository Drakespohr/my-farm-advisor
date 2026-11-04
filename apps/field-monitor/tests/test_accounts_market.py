import numpy as np
import pytest
from fastapi.testclient import TestClient

from fieldmonitor.accounts import hash_password, verify_password
from fieldmonitor.api import create_app
from tests.conftest import FIELD
from tests.test_api import add_obs, signup

H = {"X-Requested-With": "fieldmonitor"}


@pytest.fixture()
def app(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    monkeypatch.setenv("FIELD_MONITOR_MARKETPLACE", "1")  # these tests cover the marketplace itself
    return create_app(store, refresh_hours=0)


def client(app):
    return TestClient(app, headers=H)


def test_password_hashing():
    h = hash_password("a long password")
    assert verify_password("a long password", h) and not verify_password("wrong password!", h)
    assert "a long password" not in h


def test_login_required_and_csrf(app):
    anon = TestClient(app)
    assert anon.get("/api/fields").status_code == 401
    assert anon.post("/api/auth/register", json={"email": "a@b.co", "password": "x" * 12, "name": "A"}).status_code == 403
    c = client(app)
    signup(c, "a@b.co", "A")
    assert c.get("/api/me").json()["profile"]["display_name"] == "A"
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "a@b.co", "password": "nope nope nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "A@B.co", "password": "correct horse 1"}).status_code == 200


def test_registration_validation(app):
    c = client(app)
    assert c.post("/api/auth/register", json={"email": "bad", "password": "x" * 12, "name": "A"}).status_code == 400
    assert c.post("/api/auth/register", json={"email": "a@b.co", "password": "short", "name": "A"}).status_code == 400
    signup(c, "a@b.co", "A")
    assert c.post("/api/auth/register", json={"email": "a@b.co", "password": "x" * 12, "name": "B"}).status_code == 409


def test_fields_are_private_and_sharing_is_read_only(app, store):
    owner, other = client(app), client(app)
    signup(owner, "owner@x.co", "Owner")
    signup(other, "agro@x.co", "Agro", ("agronomist",))
    fid = owner.post("/api/fields", json={"name": "North", "geojson": FIELD, "auto_sync": False}).json()["created"][0]
    assert other.get(f"/api/fields/{fid}").status_code == 404
    assert [f["id"] for f in other.get("/api/fields").json()] == []
    assert owner.post(f"/api/fields/{fid}/members", json={"email": "nobody@x.co"}).status_code == 404
    members = owner.post(f"/api/fields/{fid}/members", json={"email": "agro@x.co"}).json()
    assert {m["role"] for m in members} == {"owner", "viewer"}
    assert other.get(f"/api/fields/{fid}").json()["my_role"] == "viewer"
    assert other.patch(f"/api/fields/{fid}", json={"name": "Mine now"}).status_code == 403
    assert other.post(f"/api/fields/{fid}/members", json={"email": "owner@x.co"}).status_code == 403
    # Re-importing the same field id can't take it over...
    r = other.post("/api/fields", json={"name": "North", "field_id": fid, "geojson": FIELD, "auto_sync": False})
    assert r.status_code == 400 and other.get(f"/api/fields/{fid}").json()["my_role"] == "viewer"
    # ...but the same land under a new name is a separate, independent field (e.g. landlord and tenant).
    own = other.post("/api/fields", json={"name": "Rented 80", "geojson": FIELD, "auto_sync": False}).json()["created"][0]
    assert own != fid and other.get(f"/api/fields/{own}").json()["my_role"] == "owner"


def test_first_account_claims_fields_from_before_accounts(app, store):
    rec, _ = store.create_field(FIELD, "Legacy")
    c = client(app)
    signup(c, "first@x.co", "First")
    assert [f["id"] for f in c.get("/api/fields").json()] == [rec["id"]]
    second = client(app)
    signup(second, "second@x.co", "Second")
    assert second.get("/api/fields").json() == []


def test_listings_messages_and_profile(app):
    seller, buyer = client(app), client(app)
    sid = signup(seller, "drone@x.co", "Sky Ag", ("service",))
    bid = signup(buyer, "farm@x.co", "Farm Co")
    assert seller.post("/api/listings", json={"kind": "service", "category": "Bogus", "title": "Drone", "description": "abc"}).status_code == 400
    lst = seller.post("/api/listings", json={"kind": "service", "category": "Drone services", "title": "Drone spraying",
                                             "description": "Up to 300 ac/day", "price": "$12/ac", "location": "Story County, IA"}).json()
    found = buyer.get("/api/listings?q=drone").json()
    assert [x["id"] for x in found] == [lst["id"]] and found[0]["seller_name"] == "Sky Ag"
    assert buyer.patch(f"/api/listings/{lst['id']}", json={"status": "closed"}).status_code == 403

    conv = buyer.post("/api/conversations", json={"to_user_id": sid, "listing_id": lst["id"], "body": "Available June 10?"}).json()["id"]
    again = buyer.post("/api/conversations", json={"to_user_id": sid, "listing_id": lst["id"], "body": "Also 80 ac of beans"}).json()["id"]
    assert again == conv
    assert seller.get("/api/me").json()["unread"] == 2
    t = seller.get(f"/api/conversations/{conv}/messages").json()
    assert [m["body"] for m in t["messages"]] == ["Available June 10?", "Also 80 ac of beans"] and t["listing"]["title"] == "Drone spraying"
    assert seller.get("/api/me").json()["unread"] == 0
    seller.post(f"/api/conversations/{conv}/messages", json={"body": "Yes, booked."})
    assert buyer.get("/api/conversations").json()[0]["unread"] == 1
    outsider = client(app)
    signup(outsider, "x@x.co", "X")
    assert outsider.get(f"/api/conversations/{conv}/messages").status_code == 404

    seller.patch("/api/me/profile", json={"business_name": "Sky Ag LLC", "bio": "Licensed drone applicator", "website": "skyag.example"})
    prof = buyer.get(f"/api/users/{sid}").json()
    assert prof["profile"]["business_name"] == "Sky Ag LLC" and prof["profile"]["website"] == "https://skyag.example"
    assert "email" not in prof["profile"] and len(prof["listings"]) == 1
    seller.patch(f"/api/listings/{lst['id']}", json={"status": "closed"})
    assert buyer.get("/api/listings").json() == []
    assert buyer.post("/api/reports", json={"kind": "listing", "target_id": lst["id"], "reason": "spam"}).status_code == 201


def test_showcase_shows_score_not_map(app, store):
    c = client(app)
    uid = signup(c, "o@x.co", "O")
    fid = c.post("/api/fields", json={"name": "North", "geojson": FIELD, "auto_sync": False}).json()["created"][0]
    c.patch(f"/api/fields/{fid}", json={"showcase": True})
    prof = c.get(f"/api/users/{uid}").json()
    assert prof["showcase"][0]["name"] == "North" and "geometry" not in prof["showcase"][0]


def test_marketplace_is_off_by_default(app, monkeypatch):
    monkeypatch.delenv("FIELD_MONITOR_MARKETPLACE")
    c = client(app)
    r = c.post("/api/auth/register", json={"email": "m@example.com", "password": "correct horse 1", "name": "M", "roles": ["farmer"]})
    assert r.status_code == 201
    assert c.get("/api/health").json()["features"] == {"marketplace": False}
    assert c.get("/api/listings").status_code == 404
    assert c.post("/api/listings", json={"kind": "service", "category": "Spraying", "title": "Custom spraying", "description": "Ground rig, 120 ft boom"}).status_code == 404
    assert c.get("/api/feed?section=market").status_code == 200
    me = c.get("/api/me").json()["user"]["id"]
    assert c.get(f"/api/users/{me}").json()["listings"] == []
