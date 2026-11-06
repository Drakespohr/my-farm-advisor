"""Farm team chat and direct messages between teammates."""

import pytest

from fieldmonitor.api import create_app
from tests.test_farms import person


@pytest.fixture()
def app(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    return create_app(store, refresh_hours=0)


def team(app):
    owner, oid = person(app, "olive@x.co", "Olive Owner")
    sam, sid = person(app, "sam@x.co", "Sam Agronomist")
    out, xid = person(app, "xena@x.co", "Xena Outsider")
    farm = owner.get("/api/farms").json()[0]["id"]
    owner.post(f"/api/farms/{farm}/members", json={"email": "sam@x.co", "role": "agronomist"})
    return farm, (owner, oid), (sam, sid), (out, xid)


def test_team_group_chat_and_unread(app):
    farm, (owner, oid), (sam, sid), (out, _) = team(app)
    t = owner.post(f"/api/farms/{farm}/chat", json={"body": "  Spraying the north 80 at 6.  "}).json()
    assert [m["body"] for m in t["messages"]] == ["Spraying the north 80 at 6."]
    assert t["messages"][0]["sender_name"] == "Olive Owner"
    assert {m["user_id"] for m in t["members"]} == {oid, sid}

    chats = sam.get("/api/farm-chats").json()
    assert chats["threads"][0]["unread"] == 1 and chats["threads"][0]["last_body"] == "Spraying the north 80 at 6."
    assert [m["user_id"] for m in chats["teammates"]] == [oid]
    assert sam.get("/api/me").json()["unread"] == 1
    sam.get(f"/api/farms/{farm}/chat")  # reading marks it read
    assert sam.get("/api/me").json()["unread"] == 0
    assert owner.get("/api/me").json()["unread"] == 0  # your own messages never count

    # 'after' returns only newer messages
    sam.post(f"/api/farms/{farm}/chat", json={"body": "Wind looks ok"})
    first = t["messages"][0]["id"]
    assert [m["body"] for m in owner.get(f"/api/farms/{farm}/chat?after={first}").json()["messages"]] == ["Wind looks ok"]

    # outsiders and empty messages
    assert out.get(f"/api/farms/{farm}/chat").status_code == 404
    assert out.post(f"/api/farms/{farm}/chat", json={"body": "hi"}).status_code == 404
    assert owner.post(f"/api/farms/{farm}/chat", json={"body": ""}).status_code == 422
    assert owner.post(f"/api/farms/{farm}/chat", json={"body": "   "}).status_code == 422


def test_removed_member_loses_group_chat(app):
    farm, (owner, _), (sam, sid), _ = team(app)
    owner.post(f"/api/farms/{farm}/chat", json={"body": "hello"})
    owner.delete(f"/api/farms/{farm}/members/{sid}")
    assert sam.get(f"/api/farms/{farm}/chat").status_code == 404
    assert all(t["farm_id"] != farm for t in sam.get("/api/farm-chats").json()["threads"])


def test_direct_messages_stay_within_farm_teams(app):
    farm, (owner, oid), (sam, sid), (out, xid) = team(app)
    # teammates: opening without a message creates an empty conversation
    conv = owner.post("/api/conversations", json={"to_user_id": sid}).json()["id"]
    assert sam.get("/api/conversations").json()[0]["id"] == conv
    owner.post(f"/api/conversations/{conv}/messages", json={"body": "Can you scout the creek field?"})
    assert sam.get("/api/me").json()["unread"] == 1
    # same pair reuses the conversation
    assert sam.post("/api/conversations", json={"to_user_id": oid, "body": "On it"}).json()["id"] == conv

    # strangers can't start a conversation (marketplace off)
    r = out.post("/api/conversations", json={"to_user_id": oid, "body": "hi"})
    assert r.status_code == 403 and "farm team" in r.json()["detail"]

    # after leaving the team, the old conversation is read-only
    owner.delete(f"/api/farms/{farm}/members/{sid}")
    assert sam.get(f"/api/conversations/{conv}/messages").status_code == 200
    assert sam.post(f"/api/conversations/{conv}/messages", json={"body": "still there?"}).status_code == 403
