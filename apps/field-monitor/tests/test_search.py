"""Map search: coordinates, addresses, places, and the user's own fields."""

import httpx
import pytest

from fieldmonitor.search import Geocoder, match_fields, parse_coords, search, suggest
from tests.test_api import client, signup  # noqa: F401  (fixture)


def square(lon, lat, d=0.005):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


FIELDS = [
    {"id": "north-80", "name": "North 80", "crop": "corn", "geometry": square(-98.35, 40.92)},
    {"id": "home-pivot", "name": "Home Pivot", "crop": "soybeans", "geometry": square(-98.33, 40.92)},
    {"id": "far", "name": "Sandhills Quarter", "crop": "corn", "geometry": square(-100.5, 42.0)},
]


class FakeHTTP:
    def __init__(self, census=None, nominatim=None, photon=None, fail=()):
        self.census, self.nominatim, self.photon, self.fail, self.calls = census or [], nominatim or [], photon or [], fail, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        host = "census" if "census.gov" in url else "photon" if "photon" in url else "nominatim"
        self.calls.append((host, params))
        assert headers and "User-Agent" in headers
        if host in self.fail:
            raise httpx.ConnectError("down")
        body = ({"result": {"addressMatches": self.census}} if host == "census"
                else {"type": "FeatureCollection", "features": self.photon} if host == "photon" else self.nominatim)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def geo(**kw):
    g = Geocoder("test-agent", http_get=FakeHTTP(**kw))
    g._last = -10  # no rate-limit sleep in tests
    return g


@pytest.mark.parametrize("text,expected", [
    ("40.9236, -98.3420", (40.9236, -98.342)),
    ("40.9236 N, 98.3420 W", (40.9236, -98.342)),
    ("-98.342, 40.92", (40.92, -98.342)),                    # lon, lat (GIS order)
    ("40°55'25\"N 98°20'31\"W", (40.923611, -98.341944)),
    ("1234 Road 12, Aurora NE", None),
    ("200, 300", None),
])
def test_parse_coords(text, expected):
    got = parse_coords(text)
    assert (got is None) if expected is None else got == pytest.approx(expected, abs=1e-5)


def test_coordinates_find_the_field_they_fall_in_without_network():
    g = geo(fail=("census", "nominatim"))
    out = search("40.9225, -98.3475", FIELDS, g)
    assert out["fields"][0]["id"] == "north-80" and out["fields"][0]["match"] == "inside"
    assert {f["id"] for f in out["fields"]} == {"north-80", "home-pivot"}  # neighbor is ~1 mi away
    assert out["places"][0]["fields_near"][0] == "north-80" and g.http_get.calls == []


def test_missing_minus_sign_offers_the_us_point():
    out = search("40.9225, 98.3475", FIELDS, geo())
    assert out["places"][1]["lon"] == -98.3475 and out["fields"][0]["id"] == "north-80"


def test_street_address_uses_census_then_lists_nearby_fields():
    g = geo(census=[{"matchedAddress": "1234 RD 12, AURORA, NE, 68818", "coordinates": {"x": -98.334, "y": 40.928}}])
    out = search("1234 Road 12, Aurora NE", FIELDS, g)
    assert out["places"][0]["source"] == "US Census" and [c[0] for c in g.http_get.calls] == ["census"]
    assert out["fields"][0]["id"] == "home-pivot" and out["fields"][0]["match"] == "near"
    assert out["fields"][0]["distance_mi"] < 0.5


def test_place_name_falls_back_to_nominatim_us_only():
    g = geo(nominatim=[{"display_name": "Aurora, Hamilton County, Nebraska", "lat": "40.867", "lon": "-98.004",
                        "boundingbox": ["40.85", "40.88", "-98.03", "-97.98"]}])
    out = search("Aurora NE", FIELDS, g)
    assert out["places"][0]["bbox"] == [[40.85, -98.03], [40.88, -97.98]]
    assert g.http_get.calls[0][1]["countrycodes"] == "us"
    assert out["fields"] == []  # nothing within 3 miles


def test_field_name_search_skips_geocoding():
    g = geo()
    out = search("north 80", FIELDS, g)
    assert out["fields"][0] == {**out["fields"][0], "id": "north-80", "match": "name"} and g.http_get.calls == []


def test_geocoder_outage_still_returns_name_matches():
    out = search("Pivot road", FIELDS, geo(fail=("census", "nominatim")))
    assert out["error"] and "Coordinates still work" in out["error"]
    assert out["fields"] == []
    out = search("Pivot", FIELDS, geo(fail=("nominatim",)))
    assert [f["id"] for f in out["fields"]] == ["home-pivot"] and out["error"]


def test_match_fields_ignores_broken_geometry():
    assert match_fields("x", [{"id": "bad", "name": "x", "geometry": {"type": "Polygon", "coordinates": []}}], []) == []


def test_search_endpoint_only_returns_my_fields(client):  # noqa: F811
    c, store = client
    gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "North 80"}, "geometry": square(-98.35, 40.92)}]}
    assert c.post("/api/fields", json={"geojson": gj, "auto_sync": False}).status_code == 201
    c.app.state.geocoder.http_get = FakeHTTP(fail=("census", "nominatim"))
    hit = c.get("/api/search", params={"q": "40.9225, -98.3475"}).json()
    assert hit["fields"][0]["name"] == "North 80" and hit["fields"][0]["match"] == "inside"

    other = c.__class__(c.app, headers={"X-Requested-With": "fieldmonitor"})
    signup(other, "neighbor@example.com", "Ned Neighbor")
    assert other.get("/api/search", params={"q": "40.9225, -98.3475"}).json()["fields"] == []
    assert c.get("/api/geocode", params={"q": "40.9, -98.3"}).json()[0]["lat"] == 40.9


PHOTON_AURORA = [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-98.004, 40.867]},
     "properties": {"name": "Aurora", "county": "Hamilton County", "state": "Nebraska", "countrycode": "US",
                    "extent": [-98.03, 40.88, -97.98, 40.85]}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-104.8, 39.7]},
     "properties": {"name": "Aurora", "state": "Ontario", "countrycode": "CA"}},  # dropped: not US
]


def test_suggest_places_from_photon_us_only_and_cached():
    g = geo(photon=PHOTON_AURORA)
    out = suggest("Auro", FIELDS, g, near=(40.92, -98.34))
    assert out["source"] == "OpenStreetMap"
    assert [(i["main"], i["secondary"]) for i in out["items"]] == [("Aurora", "Hamilton County, Nebraska")]
    assert out["items"][0]["bbox"] == [[40.85, -98.03], [40.88, -97.98]]
    (host, params), = g.http_get.calls
    assert host == "photon" and params["bbox"] and params["lat"] == 40.92
    suggest("auro", FIELDS, g, near=(40.92, -98.34))
    assert len(g.http_get.calls) == 1  # cached
    assert all(c[0] != "nominatim" for c in g.http_get.calls)  # Nominatim forbids autocomplete


def test_suggest_fields_and_coordinates_without_network():
    g = geo(fail=("photon", "census", "nominatim"))
    out = suggest("pi", FIELDS, g)  # 2 chars: fields only, no network
    assert [i["main"] for i in out["items"]] == ["Home Pivot"] and g.http_get.calls == []
    out = suggest("40.9225, -98.3475", FIELDS, g)
    assert out["items"][0]["type"] == "coordinates" and out["items"][1]["id"] == "north-80"
    assert suggest("40.92,", FIELDS, g)["items"] == [] and g.http_get.calls == []  # half-typed: wait, no geocoding
    out = suggest("Sandhills", FIELDS, g)
    assert out["items"][0]["id"] == "far" and out["error"]  # address service down: fields still suggested


# ---- Google Places (fake HTTP; the real key lives only in the owner's environment) -------

GOOGLE_AUTOCOMPLETE = {"suggestions": [
    {"placePrediction": {"placeId": f"ChIJplace{i:04d}xyz", "text": {"text": f"{1200 + i} Road 12, Aurora, NE, USA"},
                         "structuredFormat": {"mainText": {"text": f"{1200 + i} Road 12"}, "secondaryText": {"text": "Aurora, NE, USA"}}}}
    for i in range(6)
] + [{"queryPrediction": {"text": {"text": "road 12 near me"}}}]}


class FakeGoogle:
    def __init__(self, reject_region=False, fail=False):
        self.posts, self.gets, self.reject_region, self.fail = [], [], reject_region, fail

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        req = httpx.Request("POST", url)
        if self.fail:
            raise httpx.ConnectError("down")
        if self.reject_region and "includedRegionCodes" in json:
            return httpx.Response(400, json={"error": {"message": "Unknown name includedRegionCodes"}}, request=req)
        return httpx.Response(200, json=GOOGLE_AUTOCOMPLETE, request=req)

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, params, headers))
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "id": "ChIJplace0000xyz", "formattedAddress": "1200 Road 12, Aurora, NE 68818, USA",
            "location": {"latitude": 40.93, "longitude": -98.0},
            "viewport": {"low": {"latitude": 40.92, "longitude": -98.01}, "high": {"latitude": 40.94, "longitude": -97.99}}})


def gplaces(fake, limit=1000):
    from fieldmonitor.search import GooglePlaces
    return GooglePlaces("test-key", daily_limit=limit, http_post=fake.post, http_get=fake.get)


def test_google_gives_five_suggestions_with_session_and_bias():
    fake = FakeGoogle()
    out = suggest("1200 road 12", FIELDS, geo(), near=(40.9, -98.3), google=gplaces(fake), session_token="abc12345-session")
    assert out["source"] == "Google" and len(out["items"]) == 5
    assert out["items"][0] == {"type": "place", "place_id": "ChIJplace0000xyz", "main": "1200 Road 12",
                               "secondary": "Aurora, NE, USA", "name": "1200 Road 12, Aurora, NE, USA", "source": "Google"}
    url, body, headers = fake.posts[0]
    assert url.endswith("places:autocomplete") and headers["X-Goog-Api-Key"] == "test-key"
    assert body["input"] == "1200 road 12" and body["sessionToken"] == "abc12345-session"
    assert body["includedRegionCodes"] == ["us"] and body["locationBias"]["circle"]["center"] == {"latitude": 40.9, "longitude": -98.3}


def test_fields_take_at_most_two_of_five_slots():
    fields = FIELDS + [{"id": "road-12-north", "name": "Road 12 North", "geometry": square(-98.2, 40.9)},
                       {"id": "road-12-south", "name": "Road 12 South", "geometry": square(-98.2, 40.8)},
                       {"id": "old-road-12", "name": "Old Road 12", "geometry": square(-98.2, 40.7)}]
    out = suggest("road 12", fields, geo(), google=gplaces(FakeGoogle()))
    kinds = [i["type"] for i in out["items"]]
    assert kinds == ["field", "field", "place", "place", "place"]
    assert [i["main"] for i in out["items"][:2]] == ["Road 12 North", "Road 12 South"]  # "starts with" first


def test_google_details_ends_session_and_returns_location():
    fake = FakeGoogle()
    d = gplaces(fake).details("ChIJplace0000xyz", "abc12345-session")
    url, params, headers = fake.gets[0]
    assert url.endswith("/places/ChIJplace0000xyz") and params == {"sessionToken": "abc12345-session"}
    assert headers["X-Goog-FieldMask"] == "id,formattedAddress,location,viewport"
    assert (d["lat"], d["lon"]) == (40.93, -98.0) and d["bbox"] == [[40.92, -98.01], [40.94, -97.99]]
    with pytest.raises(ValueError):
        gplaces(fake).details("../../etc", None)


def test_google_region_filter_retry_limit_and_fallback():
    fake = FakeGoogle(reject_region=True)
    g = gplaces(fake)
    assert len(suggest("1200 road", FIELDS, geo(), google=g)["items"]) == 5
    assert "includedRegionCodes" not in fake.posts[-1][1]  # retried without the filter
    # Daily cap: the 2nd call falls back to OpenStreetMap instead of spending more.
    capped = gplaces(FakeGoogle(), limit=1)
    assert suggest("aurora a", FIELDS, geo(photon=PHOTON_AURORA), google=capped)["source"] == "Google"
    assert suggest("aurora b", FIELDS, geo(photon=PHOTON_AURORA), google=capped)["source"] == "OpenStreetMap"
    down = suggest("aurora", FIELDS, geo(photon=PHOTON_AURORA), google=gplaces(FakeGoogle(fail=True)))
    assert down["source"] == "OpenStreetMap" and down["items"][0]["main"] == "Aurora"


def test_suggest_endpoint(client):  # noqa: F811
    c, _ = client
    gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "North 80"}, "geometry": square(-98.35, 40.92)}]}
    c.post("/api/fields", json={"geojson": gj, "auto_sync": False})
    c.app.state.geocoder.http_get = FakeHTTP(photon=PHOTON_AURORA)
    r = c.get("/api/search/suggest", params={"q": "nor", "lat": 40.9, "lon": -98.3}).json()
    assert r["items"][0]["main"] == "North 80" and r["items"][0]["type"] == "field"
    assert c.get("/api/search/suggest", params={"q": "x", "lat": 200}).status_code == 422
    assert c.get("/api/search/suggest", params={"q": "nor", "session": "bad token!"}).status_code == 422
    assert c.get("/api/search/place", params={"id": "ChIJplace0000xyz"}).status_code == 404  # no key configured
    c.app.state.google_places = gplaces(FakeGoogle())
    r = c.get("/api/search/suggest", params={"q": "1200 road", "session": "abc12345-session"}).json()
    assert r["source"] == "Google" and len(r["items"]) == 5
    p = c.get("/api/search/place", params={"id": r["items"][0]["place_id"], "session": "abc12345-session"}).json()
    assert p["lat"] == 40.93


def test_house_number_alone_asks_for_addresses():
    fake = FakeGoogle()
    out = suggest("120", FIELDS, geo(), google=gplaces(fake))
    assert len(out["items"]) == 5 and fake.posts  # "120" is a house number, not coordinates
    fake2 = FakeGoogle()
    assert suggest("40.9", FIELDS, geo(), google=gplaces(fake2))["items"] == [] and not fake2.posts
