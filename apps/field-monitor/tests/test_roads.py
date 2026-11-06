"""Local road index (US Census TIGER) for instant crossroads search."""

import io
import time
import zipfile

import pytest
import shapefile

from fieldmonitor import roads
from fieldmonitor.intersections import Intersections
from fieldmonitor.search import search, suggest
from tests.test_intersections import FakeOverpass
from tests.test_search import FIELDS, geo


def tiger_zip(segments):
    """A TIGER-style roads zip (FULLNAME + polylines)."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYLINE)
    w.field("LINEARID", "C", 22)
    w.field("FULLNAME", "C", 100)
    for i, (name, pts) in enumerate(segments):
        w.line([[list(p) for p in pts]])
        w.record(str(i), name)
    w.close()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for ext, buf in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"tl_2024_31109_roads.{ext}", buf.getvalue())
    return out.getvalue()


# Lancaster County, NE-like grid. Segments are split at every corner, as in TIGER.
SEGMENTS = [
    ("N 250th St", [(-96.50, 40.85), (-96.50, 40.92)]),
    ("N 250th St", [(-96.50, 40.92), (-96.50, 41.00)]),
    ("Waverly Rd", [(-96.60, 40.92), (-96.50, 40.92)]),
    ("Waverly Rd", [(-96.50, 40.92), (-96.40, 40.92)]),
    ("N 25th St", [(-96.70, 40.85), (-96.70, 40.95)]),          # "25th" must never match "250th"
    ("US Hwy 6", [(-96.55, 40.88), (-96.45, 40.88)]),           # bridge over 250th: no shared node, no corner
    ("Bluff Rd", [(-96.60, 40.85), (-96.50, 40.85)]),
    ("Bluff Rd", [(-96.50, 40.85), (-96.40, 40.85)]),
]


class Counties:
    def near(self, lat, lon, km):
        return [{"fips": "31109", "name": "Lancaster", "state": "NE"}]


@pytest.fixture()
def index(tmp_path):
    ix = roads.RoadIndex(tmp_path, Counties())
    ix.build("31109", raw=tiger_zip(SEGMENTS), source="test")
    return ix


def test_tokens_normalize_like_people_type():
    assert roads.tokens("N 250th St") == ["250"] and roads.tokens("250th street") == ["250"]
    assert roads.tokens("Waverly Rd") == ["waverly"] == roads.tokens("waverly road")
    assert roads.tokens("County Road 12") == ["co", "rd", "12"] == roads.tokens("Co Rd 12")
    assert roads.tokens("US Hwy 6") == ["us", "hwy", "6"]


def test_match_score_numbers_exact_words_forgiving():
    assert roads.match_score(["250"], ["250"]) == 1
    assert roads.match_score(["250"], ["25"]) == 0 and roads.match_score(["25"], ["250"]) == 0
    assert roads.match_score(["waverley"], ["waverly"]) > 0.7          # misspelled
    assert roads.match_score(["wav"], ["waverly"], partial=True) > 0.8  # still typing
    assert roads.match_score(["wav"], ["waverly"]) == 0
    assert roads.match_score(["hwy", "6"], ["us", "hwy", "6"]) > 0.8


def test_corners_come_from_shared_segment_ends_only(index):
    with index._db() as c:
        pairs = {frozenset((r["a_name"], r["b_name"])) for r in c.execute("SELECT * FROM crossings")}
    assert {"N 250th St", "Waverly Rd"} in pairs and {"Bluff Rd", "N 250th St"} in pairs
    assert not any("US Hwy 6" in p for p in pairs)  # the bridge isn't an intersection
    assert index.status("31109")["status"] == "ready"


def test_find_is_exact_fast_and_forgiving(index):
    t0 = time.perf_counter()
    out, pending = index.find("250th", "waverly", (40.9, -96.5))
    assert (time.perf_counter() - t0) < 0.2 and pending == []
    assert out[0]["main"] == "N 250th St & Waverly Rd" and (out[0]["lat"], out[0]["lon"]) == (40.92, -96.5)
    assert "Lancaster County, NE" in out[0]["secondary"] and out[0]["source"] == "US Census TIGER"
    assert index.find("waverley rd", "250th street", (40.9, -96.5))[0][0]["lat"] == 40.92   # swapped + misspelled
    assert index.find("250th", "wav", (40.9, -96.5), partial=True)[0][0]["lat"] == 40.92   # while typing
    assert index.find("25th", "waverly", (40.9, -96.5))[0] == []                            # 25th never matches 250th


def test_unindexed_county_is_queued_not_waited_on(tmp_path, monkeypatch):
    ix = roads.RoadIndex(tmp_path, Counties())
    monkeypatch.setenv("FIELD_MONITOR_ROAD_INDEX", "0")  # don't actually start a download thread
    out, pending = ix.find("250th", "waverly", (40.9, -96.5))
    assert out == [] and pending == ["31109"]


def test_download_tries_years_and_records_errors(tmp_path):
    import httpx
    urls = []

    def get(url, **k):
        urls.append(url)
        if "2024" in url:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, content=tiger_zip(SEGMENTS), request=httpx.Request("GET", url))

    ix = roads.RoadIndex(tmp_path, Counties(), http_get=get)
    raw, url = ix.download("31109")
    assert "TIGER2023" in url and len(urls) == 2
    assert ix.build("31109", raw=raw) > 0


def test_search_uses_the_local_index_first_and_skips_slow_services(index):
    fake = FakeOverpass()
    finder = Intersections("t", http_post=fake)
    finder.road_index = index
    fields = FIELDS + [{"id": "corner", "name": "Corner Quarter", "geometry": {"type": "Polygon", "coordinates": [[
        [-96.505, 40.921], [-96.5, 40.921], [-96.5, 40.926], [-96.505, 40.926], [-96.505, 40.921]]]}}]
    out = suggest("250th and waverly", fields, geo(), near=(40.9, -96.5), intersections=finder)
    assert out["items"][0]["main"] == "N 250th St & Waverly Rd" and fake.calls == []  # no Overpass at all
    assert any(i.get("id") == "corner" for i in out["items"])
    res = search("250th & Waverly", fields, geo(), finder, near=(40.9, -96.5))
    assert res["places"][0]["source"] == "US Census TIGER" and res["fields"][0]["id"] == "corner"
    typing = suggest("25th and waverly", FIELDS, geo(), near=(40.9, -96.5), intersections=finder)
    assert all("25th" not in (i.get("main") or "") or "250th" in i["main"] for i in typing["items"]) and fake.calls == []
