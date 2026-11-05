"""Crossroads search: "250th and Waverly Road" -> where the two roads cross."""

import re

import httpx
import pytest

from fieldmonitor.intersections import Intersections, crossings, name_regex, parse
from fieldmonitor.search import search, suggest
from tests.test_search import FIELDS, client, geo, square  # noqa: F401  (fixture)


def way(wid, name, coords, ref=None):
    tags = {"highway": "residential", "name": name}
    if ref:
        tags["ref"] = ref
    return {"type": "way", "id": wid, "tags": tags, "geometry": [{"lat": la, "lon": lo} for la, lo in coords]}


# A small grid near Waverly, NE: N 250th St runs north-south at lon -96.50; Waverly Rd east-west at lat 40.92.
GRID = {"elements": [
    way(1, "North 250th Street", [(40.80, -96.50), (40.92, -96.50)]),
    way(2, "North 250th Street", [(40.92, -96.50), (41.00, -96.50)]),            # second segment, same corner
    way(3, "Waverly Road", [(40.92, -96.60), (40.92, -96.40)]),
    way(4, "North 2500th Street", [(40.80, -96.30), (41.00, -96.30)]),           # must not match "250th"
    way(5, "250th Street", [(41.50, -96.52), (41.60, -96.52)]),                  # same name, another county
    way(6, "Waverly Road", [(41.55, -96.70), (41.55, -96.40)]),
    way(7, "North 148th Street", [(40.80, -96.55), (40.9198, -96.55)]),          # T-junction stops ~22 m short
]}


class FakeOverpass:
    def __init__(self, data=GRID, fail=False):
        self.data, self.fail, self.calls = data, fail, []

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls.append(data["data"])
        assert "User-Agent" in headers
        if self.fail:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=self.data, request=httpx.Request("POST", url))


def finder(fake=None):
    ix = Intersections("test-agent", http_post=fake or FakeOverpass())
    ix._last = -10
    return ix


@pytest.mark.parametrize("q,expected", [
    ("250th and Waverly Road", ("250th", "Waverly Road")),
    ("250th & waverly rd", ("250th", "waverly rd")),
    ("Hwy 6 / 148th", ("Hwy 6", "148th")),
    ("148th at Waverly", ("148th", "Waverly")),
    ("North 80", None),
    ("Smith Farm", None),
])
def test_parse(q, expected):
    assert parse(q) == expected


def test_name_regex_matches_like_people_type():
    rx = re.compile(name_regex("250th"), re.I)
    assert rx.search("North 250th Street") and rx.search("250th Rd") and not rx.search("North 2500th Street")
    rx = re.compile(name_regex("250"), re.I)
    assert rx.search("N 250th St") and not rx.search("2500th St")
    rx = re.compile(name_regex("waverly road"), re.I)
    assert rx.search("Waverly Road") and rx.search("Waverly Rd") and not rx.search("Waverlyton Road")
    assert re.compile(name_regex("wav", partial_last=True), re.I).search("Waverly Road")  # typing
    assert name_regex("road") and name_regex("N") is not None


def test_crossing_nearest_first_and_deduplicated():
    out = crossings(GRID, name_regex("250th"), name_regex("waverly road"), near=(40.9, -96.5))
    assert [(o["lat"], o["lon"]) for o in out] == [(40.92, -96.5), (41.55, -96.52)]  # one per corner, nearest first
    assert out[0]["main"] == "North 250th Street & Waverly Road" and out[0]["kind"] == "intersection"
    assert out[0]["miles"] < 2


def test_t_junction_drawn_short_still_counts():
    out = crossings(GRID, name_regex("148th"), name_regex("waverly"), near=(40.9, -96.55))
    assert len(out) == 1 and out[0]["lat"] == pytest.approx(40.9199, abs=0.0002) and out[0]["lon"] == pytest.approx(-96.55)


def test_overpass_query_cache_and_spacing():
    fake = FakeOverpass()
    ix = finder(fake)
    first = ix.find("250th and Waverly Rd", (40.9, -96.5))
    assert first[0]["lat"] == 40.92 and len(fake.calls) == 1
    q = fake.calls[0]
    assert 'way["highway"]["name"~"(^| )250(st|nd|rd|th)?( |$)",i]' in q and "40.6500,-96.83" in q  # nearby box first
    ix.find("250TH AND WAVERLY ROAD", (40.9, -96.5))
    assert len(fake.calls) == 1  # cached
    assert ix.find("148th and waverly", (40.9, -96.55), partial=True) == []  # too soon after the last call: skipped while typing


def test_suggest_and_search_show_crossing_and_nearby_fields():
    fields = FIELDS + [{"id": "corner", "name": "Corner Quarter", "geometry": square(-96.505, 40.921)}]
    out = suggest("250th and waverly", fields, geo(), near=(40.9, -96.5), intersections=finder())
    assert out["items"][0]["main"] == "North 250th Street & Waverly Road"
    assert any(i.get("id") == "corner" for i in out["items"])  # your field at that corner
    res = search("250th & Waverly Road", fields, geo(fail=("census", "nominatim")), finder(), near=None)  # fields' center
    assert res["places"][0]["kind"] == "intersection" and res["fields"][0]["id"] == "corner"
    plain = suggest("North 8", FIELDS, geo(), intersections=finder())
    assert plain["items"][0]["main"] == "North 80"  # not an intersection: normal suggestions


def test_outage_falls_back_quietly():
    out = suggest("250th and waverly", FIELDS, geo(photon=[]), near=(40.9, -96.5), intersections=finder(FakeOverpass(fail=True)))
    assert out["items"] == [] or all(i.get("kind") != "intersection" for i in out["items"])


def test_api_passes_map_center(client):  # noqa: F811
    c, _ = client
    c.app.state.intersections = finder()
    r = c.get("/api/search", params={"q": "250th and Waverly", "lat": 40.9, "lon": -96.5}).json()
    assert r["places"][0]["name"] == "North 250th Street & Waverly Road"
    c.app.state.intersections._last = -10  # as if the user typed a moment later (requests are spaced 0.6 s)
    s = c.get("/api/search/suggest", params={"q": "250th and Waverly", "lat": 40.9, "lon": -96.5}).json()
    assert s["items"][0]["kind"] == "intersection"


def test_widens_the_search_only_when_nothing_crosses_nearby():
    empty = {"elements": []}

    class TwoStage(FakeOverpass):
        def __call__(self, url, data=None, headers=None, timeout=None):
            self.calls.append(data["data"])
            body = empty if len(self.calls) == 1 else GRID
            return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    fake = TwoStage()
    out = finder(fake).find("250th and Waverly", (40.9, -96.5))
    assert out and len(fake.calls) == 2 and "40.3000" in fake.calls[1]  # second, wider box


def test_falls_back_to_the_next_overpass_server(monkeypatch):
    import fieldmonitor.intersections as ix
    monkeypatch.setattr(ix, "OVERPASS_URLS", ["https://busy.example/api", "https://backup.example/api"])
    seen = []

    def post(url, data=None, headers=None, timeout=None):
        seen.append(url)
        if "busy" in url:
            return httpx.Response(504, text="<html>timeout</html>", request=httpx.Request("POST", url))
        return httpx.Response(200, json=GRID, request=httpx.Request("POST", url))

    out = finder(post).find("250th and Waverly", (40.9, -96.5))
    assert out[0]["lat"] == 40.92 and seen == ["https://busy.example/api", "https://backup.example/api"]


def test_search_never_errors_and_reports_why(client):  # noqa: F811
    c, _ = client

    class Broken:
        def find(self, *a, **k):
            raise RuntimeError("weird geometry")

    c.app.state.intersections = Broken()
    r = c.get("/api/search/suggest", params={"q": "250th and Waverly", "lat": 40.9, "lon": -96.5})
    assert r.status_code == 200
    full = c.get("/api/search", params={"q": "250th and Waverly", "lat": 40.9, "lon": -96.5})
    assert full.status_code == 200


def test_page_files_are_revalidated_and_health_endpoint(client, monkeypatch):  # noqa: F811
    c, _ = client
    assert c.get("/app.js").headers["cache-control"] == "no-cache"
    import fieldmonitor.search as fs
    monkeypatch.setitem(fs._HEALTH, "value", None)
    monkeypatch.setattr(fs.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("blocked")))
    monkeypatch.setattr(fs.httpx, "get", lambda url, **k: httpx.Response(200, request=httpx.Request("GET", url)))
    h = c.get("/api/search/health").json()
    assert h["crossroads (OpenStreetMap Overpass)"] == {"ok": False, "error": "ConnectError"}
    assert h["place suggestions (Photon)"] == {"ok": True}


# ---- fallback when Overpass can't be reached: Photon's typo-tolerant road search ---------------

def photon_road(name, ext, county="Lancaster County", place=None):
    """A Photon road feature; ext = [minLon, maxLat, maxLon, minLat]."""
    lon, lat = (ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "osm_key": "highway", "osm_value": "unclassified", "extent": ext,
                           "county": county, "state": "Nebraska", "countrycode": "US", **({"city": place} if place else {})}}


ROADS = {
    "250th": [photon_road("North 250th Street", [-96.501, 41.00, -96.499, 40.80])],          # runs north-south
    "waverly": [photon_road("Waverly Road", [-96.60, 40.921, -96.40, 40.919]),                # runs east-west
                photon_road("Waverly Road", [-96.70, 41.551, -96.40, 41.549], county="Saunders County")],
}


class FakePhotonRoads:
    def __init__(self, roads=ROADS):
        self.roads, self.calls = roads, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        q = params["q"].lower()
        feats = [f for k, v in self.roads.items() if k[:4] in q or q[:4] in k for f in v] if params.get("osm_tag") == "highway" else [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-96.53, 40.92]},
             "properties": {"name": "Waverly", "county": "Lancaster County", "state": "Nebraska", "countrycode": "US"}}]
        return httpx.Response(200, json={"features": feats}, request=httpx.Request("GET", url))


def photon_geo(roads=ROADS):
    from fieldmonitor.search import Geocoder
    return Geocoder("test-agent", http_get=FakePhotonRoads(roads))


def test_overpass_unreachable_falls_back_to_approximate_grid_crossing():
    down = finder(FakeOverpass(fail=True))
    out = suggest("250th and waverley", FIELDS, photon_geo(), near=(40.9, -96.5), intersections=down)  # misspelled
    first = out["items"][0]
    assert first["kind"] == "intersection" and first["approximate"]
    assert (first["lat"], first["lon"]) == (pytest.approx(40.92), pytest.approx(-96.5))
    assert "Crossroads (approximate)" in first["secondary"] and "Nebraska" in first["secondary"]
    # The Saunders County Waverly Rd doesn't reach 250th there: no false crossing.
    assert all(i["lat"] != pytest.approx(41.55) for i in out["items"] if i.get("kind") == "intersection")


def test_no_crossing_still_offers_the_closest_roads_and_towns():
    roads = {"250th": ROADS["250th"], "waverly": [photon_road("Waverly Road", [-97.60, 40.921, -97.40, 40.919])]}  # far apart
    res = search("250th & waverly", FIELDS, photon_geo(roads), finder(FakeOverpass(fail=True)), near=(40.9, -96.5))
    kinds = [p.get("kind") for p in res["places"]]
    assert "intersection" not in kinds and kinds.count("road") == 2 and res["error"] is None
    assert any(p["main"] == "Waverly" for p in res["places"])  # the town, too
    assert res["places"][0]["secondary"].startswith("Road · Lancaster County, Nebraska")


def test_after_an_overpass_failure_typing_skips_it_for_a_while():
    fake = FakeOverpass(fail=True)
    ix = finder(fake)
    with pytest.raises(httpx.ConnectError):
        ix.find("250th and Waverly", (40.9, -96.5))
    n = len(fake.calls)
    from fieldmonitor.intersections import OverpassDown
    with pytest.raises(OverpassDown):
        ix.find("148th and Waverly", (40.9, -96.5))
    assert len(fake.calls) == n  # no new request: straight to the fallback
