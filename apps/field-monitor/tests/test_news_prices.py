from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fieldmonitor.api import create_app
from fieldmonitor.news import clean_text, parse_feed, parse_spc
from tests.conftest import FIELD
from tests.test_api import signup

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>farmdoc daily</title>
<item><title>USDA Cuts Corn Yield Estimate to 181 Bushels</title><link>https://example.org/corn-yield</link>
<description><![CDATA[<p>The November <b>Crop Production</b> report lowered&nbsp;national yield&hellip;</p>]]></description>
<pubDate>Mon, 02 Nov 2026 14:00:00 GMT</pubDate></item>
<item><title>No link item</title></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Soybean exports rise</title>
<link href="https://example.org/soy"/><updated>2026-11-01T10:00:00Z</updated><summary>Weekly export sales.</summary></entry></feed>"""
SPC = "Time,Size,Location,County,State,Lat,Lon,Comments\n2130,175,3 S CHARITON,LUCAS,IA,41.01,-93.30,Quarter to golf ball size hail. (DMX)\n0230,100,ROLLA,PHELPS,MO,37.95,-91.77,(SGF)\n"


def test_parse_rss_and_atom():
    items = parse_feed(RSS)
    assert len(items) == 1 and items[0]["title"] == "USDA Cuts Corn Yield Estimate to 181 Bushels"
    assert items[0]["summary"] == "The November Crop Production report lowered national yield…"
    assert items[0]["published"] == "2026-11-02T14:00:00+00:00"
    assert parse_feed(ATOM)[0]["url"] == "https://example.org/soy"
    assert len(clean_text("word " * 200, 50)) <= 50


def test_parse_spc_storm_reports():
    day = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    items = parse_spc(SPC, day, "Hail")
    assert items[0]["title"] == "Hail (1.75 in) reported near 3 S Chariton, Lucas County, IA"
    assert items[0]["published"].startswith("2026-07-10T21:30")
    assert items[1]["published"].startswith("2026-07-11T02:30")  # after midnight UTC belongs to the next calendar day


@pytest.fixture()
def app(store, monkeypatch):
    monkeypatch.setattr("fieldmonitor.sync.search_scenes", lambda *a, **k: ([], "test"))
    return create_app(store, refresh_hours=0)


def test_prices_board_and_admin_only(app):
    admin, member = TestClient(app, headers={"X-Requested-With": "fieldmonitor"}), TestClient(app, headers={"X-Requested-With": "fieldmonitor"})
    signup(admin, "admin@x.co", "Admin")
    signup(member, "m@x.co", "Member")
    board = member.get("/api/prices").json()
    assert [c["key"] for c in board["commodities"]] == ["corn", "soybeans", "wheat"] and board["commodities"][0]["price"] is None
    assert member.post("/api/prices", json={"commodity": "corn", "price": 4.1}).status_code == 403
    admin.post("/api/prices", json={"commodity": "corn", "price": 4.10, "source": "Chariton Co-op"})
    b = admin.post("/api/prices", json={"commodity": "corn", "price": 4.25, "source": "Chariton Co-op"}).json()
    corn = b["commodities"][0]
    assert corn["price"] == 4.25 and corn["change"] == pytest.approx(0.15) and corn["pct"] == pytest.approx(3.66, abs=0.01)
    assert corn["source"] == "Chariton Co-op" and corn["kind"] == "cash" and corn["history"] == [4.10, 4.25]
    assert admin.post("/api/prices", json={"commodity": "oats", "price": 3}).status_code == 400
    assert admin.post("/api/prices", json={"commodity": "corn", "price": 432}).status_code == 422  # cents, not dollars


def test_news_items_local_vs_national(app, store):
    c = TestClient(app, headers={"X-Requested-With": "fieldmonitor"})
    signup(c, "a@x.co", "Ann")
    c.post("/api/fields", json={"name": "North", "geojson": FIELD, "auto_sync": False})
    news = app.state.news
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    news._store({"id": "spc-hail", "name": "NOAA SPC", "scope": "local"},
                parse_spc("Time,Size,Location,County,State,Lat,Lon,Comments\n1300,125,AMES,STORY,IA,42.03,-93.62,\n"
                          "1300,125,FAR AWAY,KERN,CA,35.3,-119.0,\n", now, "Hail"))
    news._store({"id": "farmdoc", "name": "farmdoc daily", "scope": "national"},
                [{**parse_feed(RSS)[0], "published": now.isoformat()}])
    local = c.get("/api/feed?section=local").json()
    assert [i["title"] for i in local if i["type"] == "storm"] == ["Hail (1.25 in) reported near Ames, Story County, IA"]
    national = c.get("/api/feed?section=national").json()
    assert [i["type"] for i in national] == ["news"] and national[0]["url"] == "https://example.org/corn-yield"
    everything = c.get("/api/feed").json()
    assert {i["title"] for i in everything if i["type"] == "storm"} == {
        "Hail (1.25 in) reported near Ames, Story County, IA", "Hail (1.25 in) reported near Far Away, Kern County, CA"}
    assert c.get("/api/news/sources").status_code == 200  # first account is admin
