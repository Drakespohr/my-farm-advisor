"""Map search: find your fields by name, address, or coordinates.

- Coordinates: decimal ("40.9236, -98.3420"), with hemispheres ("40.9236 N, 98.3420 W"),
  or degrees-minutes-seconds ("40°55'25\"N 98°20'31\"W"). Longitude-first pairs
  ("-98.342, 40.92") are swapped when only that reading is valid.
- Addresses and places: the US Census Bureau geocoder (good for rural street addresses, no key)
  for anything that starts with a house number, then OpenStreetMap Nominatim for towns, roads,
  and landmarks (identified with a User-Agent and kept under 1 request/second, per its policy).
- Fields: the signed-in user's fields whose name matches, plus fields that contain or sit near
  each found point, so "the address of the farmhouse" leads straight to the fields around it.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Iterable, Optional

import httpx
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

log = logging.getLogger(__name__)

NEAR_MILES = 3.0
PHOTON_URL = "https://photon.komoot.io/api/"
US_BBOX = "-125.0,24.0,-66.5,49.5"  # contiguous US (minLon,minLat,maxLon,maxLat)
SUGGEST_CACHE_SECONDS = 24 * 3600
SUGGEST_CACHE_SIZE = 512
MAX_FIELDS = 8
_MI_PER_DEG_LAT = 69.0

_NUM = r"[-+]?\d+(?:\.\d+)?"
_DECIMAL = re.compile(rf"^\s*({_NUM})\s*°?\s*([NS])?\s*[,;\s]\s*({_NUM})\s*°?\s*([EW])?\s*$", re.I)
_DMS_PART = re.compile(r"(\d+(?:\.\d+)?)\s*[°d:\s]\s*(?:(\d+(?:\.\d+)?)\s*['′m:\s]\s*)?(?:(\d+(?:\.\d+)?)\s*(?:\"|″|''|s)?\s*)?([NSEW])", re.I)


def parse_coords(text: str) -> Optional[tuple[float, float]]:
    """(lat, lon) from common coordinate formats, or None if the text isn't coordinates."""
    m = _DECIMAL.match(text)
    if m:
        a, ha, b, hb = float(m.group(1)), (m.group(2) or "").upper(), float(m.group(3)), (m.group(4) or "").upper()
        if ha in ("E", "W") or hb in ("N", "S"):  # hemispheres say longitude came first
            a, ha, b, hb = b, hb, a, ha
        lat = -abs(a) if ha == "S" else a
        lon = -abs(b) if hb == "W" else b
        if _valid(lat, lon):
            return lat, lon
        if not ha and not hb and _valid(lon, lat):  # "lon, lat" as pasted from GeoJSON/GIS
            return lon, lat
        return None
    parts = _DMS_PART.findall(text)
    if len(parts) == 2:
        vals = {}
        for deg, mins, secs, hemi in parts:
            v = float(deg) + float(mins or 0) / 60 + float(secs or 0) / 3600
            hemi = hemi.upper()
            vals["lat" if hemi in "NS" else "lon"] = -v if hemi in "SW" else v
        if set(vals) == {"lat", "lon"} and _valid(vals["lat"], vals["lon"]):
            return vals["lat"], vals["lon"]
    return None


def _valid(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def looks_like_address(text: str) -> bool:
    return bool(re.match(r"^\s*\d+[A-Za-z]?\s+\S", text)) and not parse_coords(text)


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * _MI_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat2 - lat1) * _MI_PER_DEG_LAT, dx)


def _bbox(geom: Any) -> list[list[float]]:
    minx, miny, maxx, maxy = geom.bounds
    return [[miny, minx], [maxy, maxx]]


def match_fields(query: str, fields: Iterable[dict[str, Any]], points: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Fields matching by name, containing a point, or within NEAR_MILES of one (closest first)."""
    q = query.strip().lower()
    out: dict[str, dict[str, Any]] = {}
    for f in fields:
        try:
            geom = shape(f["geometry"])
        except Exception:  # a broken geometry shouldn't break search
            continue
        if geom.is_empty:
            continue
        base = {"id": f["id"], "name": f.get("name") or f["id"], "crop": f.get("crop"),
                "archived": bool(f.get("archived")), "bbox": _bbox(geom)}
        best: Optional[dict[str, Any]] = None
        if q and q in (f.get("name") or "").lower():
            best = {**base, "match": "name", "distance_mi": None}
        for lat, lon in points:
            if geom.covers(Point(lon, lat)):
                best = {**base, "match": "inside", "distance_mi": 0.0}
                break
            # Distance to the nearest edge, not the centroid, so big fields aren't penalized.
            nearest = nearest_points(geom, Point(lon, lat))[0]
            d = miles_between(lat, lon, nearest.y, nearest.x)
            if d <= NEAR_MILES and (best is None or best["match"] == "near" and d < best["distance_mi"]):
                best = {**base, "match": "near", "distance_mi": round(d, 2)}
        if best:
            out[f["id"]] = best
    rank = {"inside": 0, "name": 1, "near": 2}
    return sorted(out.values(), key=lambda r: (rank[r["match"]], r["distance_mi"] or 0, r["name"].lower()))[:MAX_FIELDS]


class Geocoder:
    """Address/place lookup. `http_get` is injectable for tests (no network)."""

    def __init__(self, user_agent: str, http_get: Callable[..., httpx.Response] = httpx.get,
                 countries: str = "us") -> None:
        self.user_agent = user_agent
        self.http_get = http_get
        self.countries = countries
        self._lock = threading.Lock()
        self._last = 0.0
        self._suggest_cache: "OrderedDict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]]" = OrderedDict()

    def census(self, q: str) -> list[dict[str, Any]]:
        r = self.http_get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": q, "benchmark": "Public_AR_Current", "format": "json"},
            headers={"User-Agent": self.user_agent}, timeout=10.0,
        )
        r.raise_for_status()
        hits = (r.json().get("result") or {}).get("addressMatches") or []
        return [{"name": h.get("matchedAddress", q), "lat": float(h["coordinates"]["y"]), "lon": float(h["coordinates"]["x"]),
                 "bbox": None, "kind": "address", "source": "US Census"} for h in hits[:5]]

    def nominatim(self, q: str) -> list[dict[str, Any]]:
        with self._lock:  # Nominatim policy: max 1 request/second
            wait = 1.0 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        params = {"q": q, "format": "jsonv2", "limit": 6}
        if self.countries:
            params["countrycodes"] = self.countries
        r = self.http_get("https://nominatim.openstreetmap.org/search", params=params,
                          headers={"User-Agent": self.user_agent}, timeout=10.0)
        r.raise_for_status()
        out = []
        for hit in r.json():
            bb = hit.get("boundingbox")
            out.append({"name": hit.get("display_name", ""), "lat": float(hit["lat"]), "lon": float(hit["lon"]),
                        "bbox": [[float(bb[0]), float(bb[2])], [float(bb[1]), float(bb[3])]] if bb else None,
                        "kind": "place", "source": "OpenStreetMap"})
        return out

    def photon(self, q: str, near: Optional[tuple[float, float]] = None) -> list[dict[str, Any]]:
        """Type-ahead place suggestions from Photon (OpenStreetMap data, built for autocomplete).
        Nominatim's usage policy forbids autocomplete, so suggestions never go there."""
        key = (q.strip().lower(), tuple(round(v, 1) for v in near) if near else None)
        hit = self._suggest_cache.get(key)
        if hit and time.monotonic() - hit[0] < SUGGEST_CACHE_SECONDS:
            self._suggest_cache.move_to_end(key)
            return hit[1]
        params: dict[str, Any] = {"q": q, "limit": 6, "lang": "en"}
        if near:  # rank places near the map view first
            params["lat"], params["lon"] = round(near[0], 3), round(near[1], 3)
        if self.countries.strip().lower() == "us":
            params["bbox"] = US_BBOX
        r = self.http_get(PHOTON_URL, params=params, headers={"User-Agent": self.user_agent}, timeout=5.0)
        r.raise_for_status()
        out = []
        for f in r.json().get("features", []):
            pr = f.get("properties") or {}
            if self.countries and pr.get("countrycode") and pr["countrycode"].lower() not in self.countries.lower().split(","):
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            ext = pr.get("extent")  # [minLon, maxLat, maxLon, minLat]
            out.append({"name": _photon_label(pr), "lat": float(lat), "lon": float(lon),
                        "bbox": [[ext[3], ext[0]], [ext[1], ext[2]]] if ext and len(ext) == 4 else None,
                        "kind": "address" if pr.get("housenumber") else "place", "source": "OpenStreetMap"})
        self._suggest_cache[key] = (time.monotonic(), out)
        while len(self._suggest_cache) > SUGGEST_CACHE_SIZE:
            self._suggest_cache.popitem(last=False)
        return out

    def lookup(self, q: str) -> tuple[list[dict[str, Any]], Optional[str]]:
        """(places, error message or None). Tries Census first for street addresses."""
        errors = []
        if looks_like_address(q):
            try:
                hits = self.census(q)
                if hits:
                    return hits, None
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(f"US Census ({exc.__class__.__name__})")
        try:
            return self.nominatim(q), None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            errors.append(f"OpenStreetMap ({exc.__class__.__name__})")
        return [], "Address search is unavailable right now: " + ", ".join(errors) + ". Coordinates still work."


def search(q: str, fields: list[dict[str, Any]], geocoder: Geocoder) -> dict[str, Any]:
    coords = parse_coords(q)
    error = None
    if coords:
        lat, lon = coords
        places = [{"name": f"{lat:.6f}, {lon:.6f}", "lat": lat, "lon": lon, "bbox": None, "kind": "coordinates", "source": None}]
        if 0 < lon and -125 <= -lon <= -66 and 24 <= lat <= 50 and not re.search(r"[EW]", q, re.I):
            # "40.92, 98.34" usually means 98.34 W: offer the US point too.
            places.append({"name": f"{lat:.6f}, {-lon:.6f} (west, if you dropped the minus sign)", "lat": lat, "lon": -lon,
                           "bbox": None, "kind": "coordinates", "source": None})
    else:
        # A name match on your own fields doesn't need the network.
        if _only_names(q, match_fields(q, fields, [])):
            places = []
        else:
            places, error = geocoder.lookup(q)
    points = [(p["lat"], p["lon"]) for p in places[:3]]
    for p in places:
        p["fields_near"] = [m["id"] for m in match_fields("", fields, [(p["lat"], p["lon"])])]
    return {"query": q, "coordinates": coords is not None, "fields": match_fields(q, fields, points),
            "places": places, "error": error}


def _photon_label(pr: dict[str, Any]) -> str:
    first = " ".join(x for x in (pr.get("housenumber"), pr.get("street")) if x) if pr.get("street") else None
    name = pr.get("name")
    parts = [name, first] if name and first and name != first else [name or first]
    place = pr.get("city") or pr.get("town") or pr.get("village") or pr.get("district")
    state = pr.get("state")
    parts += [place if place != (name or first) else None, pr.get("county") if not place else None, state]
    return ", ".join(dict.fromkeys(p for p in parts if p)) or "Unnamed place"


MAX_SUGGESTIONS = 5
MAX_FIELD_SUGGESTIONS = 2  # when addresses are also available, fields take at most 2 of the 5 slots


class GooglePlaces:
    """Google Places API (New) autocomplete + place details, called from the server so the key
    stays private. Read the key from GOOGLE_MAPS_API_KEY; never store it in code.

    Billing: autocomplete requests are grouped into a session by `session_token` (a UUID the
    browser makes per search). Picking a suggestion calls Place Details with the same token, which
    ends the session; the next search uses a new token. A daily request cap protects the bill;
    past it, suggestions fall back to the free OpenStreetMap service.
    """

    AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
    DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
    DETAILS_FIELDS = "id,formattedAddress,location,viewport"

    def __init__(self, api_key: str, daily_limit: int = 1000, region: str = "us",
                 http_post: Callable[..., httpx.Response] = httpx.post,
                 http_get: Callable[..., httpx.Response] = httpx.get) -> None:
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.region = region
        self.http_post = http_post
        self.http_get = http_get
        self._lock = threading.Lock()
        self._day = ""
        self._count = 0
        self._region_filter_ok = True  # dropped if Google rejects includedRegionCodes

    def _spend(self) -> bool:
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self.daily_limit:
                return False
            self._count += 1
            return True

    @property
    def used_today(self) -> int:
        return self._count if self._day == time.strftime("%Y-%m-%d") else 0

    def autocomplete(self, q: str, session_token: Optional[str], near: Optional[tuple[float, float]]) -> list[dict[str, Any]]:
        if not self._spend():
            raise QuotaExceeded("Daily Google suggestion limit reached")
        body: dict[str, Any] = {"input": q, "languageCode": "en"}
        if session_token:
            body["sessionToken"] = session_token
        if self.region and self._region_filter_ok:
            body["includedRegionCodes"] = [self.region]
        if near:
            body["locationBias"] = {"circle": {"center": {"latitude": near[0], "longitude": near[1]}, "radius": 50000.0}}
        headers = {"X-Goog-Api-Key": self.api_key, "Content-Type": "application/json"}
        r = self.http_post(self.AUTOCOMPLETE_URL, json=body, headers=headers, timeout=5.0)
        if r.status_code == 400 and "includedRegionCodes" in body:
            # Older/changed API versions may reject the region filter: retry once without it.
            self._region_filter_ok = False
            body.pop("includedRegionCodes")
            r = self.http_post(self.AUTOCOMPLETE_URL, json=body, headers=headers, timeout=5.0)
        r.raise_for_status()
        out = []
        for sug in r.json().get("suggestions", []):
            pp = sug.get("placePrediction")
            if not pp or not pp.get("placeId"):
                continue  # query predictions ("pizza near…") have no place to jump to
            sf = pp.get("structuredFormat") or {}
            full = (pp.get("text") or {}).get("text") or ""
            main = (sf.get("mainText") or {}).get("text") or full.split(",")[0]
            secondary = (sf.get("secondaryText") or {}).get("text") or ", ".join(p.strip() for p in full.split(",")[1:])
            out.append({"type": "place", "place_id": pp["placeId"], "main": main, "secondary": secondary,
                        "name": full or f"{main}, {secondary}", "source": "Google"})
        return out

    def details(self, place_id: str, session_token: Optional[str]) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,300}", place_id):
            raise ValueError("Bad place id")
        params = {"sessionToken": session_token} if session_token else None
        r = self.http_get(self.DETAILS_URL.format(place_id=place_id), params=params, timeout=5.0,
                          headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": self.DETAILS_FIELDS})
        r.raise_for_status()
        d = r.json()
        loc = d["location"]
        vp = d.get("viewport") or {}
        lo, hi = vp.get("low"), vp.get("high")
        bbox = [[lo["latitude"], lo["longitude"]], [hi["latitude"], hi["longitude"]]] if lo and hi else None
        return {"name": d.get("formattedAddress") or "", "lat": float(loc["latitude"]), "lon": float(loc["longitude"]),
                "bbox": bbox, "kind": "place", "source": "Google"}


class QuotaExceeded(Exception):
    pass


def _photon_item(p: dict[str, Any]) -> dict[str, Any]:
    main, _, rest = p["name"].partition(", ")
    return {"type": "place", "main": main, "secondary": rest, "name": p["name"], "lat": p["lat"], "lon": p["lon"],
            "bbox": p.get("bbox"), "source": p.get("source")}


def _field_item(f: dict[str, Any]) -> dict[str, Any]:
    why = {"inside": "the point is in this field", "near": f"{f.get('distance_mi')} mi away"}.get(f["match"], "")
    bits = ["Your field", f.get("crop"), why]
    return {"type": "field", "id": f["id"], "main": f["name"], "secondary": " · ".join(b for b in bits if b),
            "bbox": f["bbox"], "match": f["match"]}


def suggest(q: str, fields: list[dict[str, Any]], geocoder: Geocoder,
            near: Optional[tuple[float, float]] = None, google: Optional[GooglePlaces] = None,
            session_token: Optional[str] = None) -> dict[str, Any]:
    """The 5 most likely things you're looking for, in one list, while you type.

    Order: typed coordinates (and the field they land in), your fields whose name starts with /
    contains the text, then addresses and places (Google Places if a key is set, otherwise
    OpenStreetMap via Photon) to fill the 5 slots.
    """
    q = q.strip()
    out: dict[str, Any] = {"query": q, "items": [], "source": None, "error": None}
    if len(q) < 2:
        return out
    coords = parse_coords(q)
    if coords:
        out["items"] = [{"type": "coordinates", "main": f"{coords[0]:.6f}, {coords[1]:.6f}", "secondary": "Coordinates",
                         "name": f"{coords[0]:.6f}, {coords[1]:.6f}", "lat": coords[0], "lon": coords[1], "bbox": None}]
        out["items"] += [_field_item(f) for f in match_fields("", fields, [coords])[:MAX_SUGGESTIONS - 1]]
        return out
    ql = q.lower()
    named = [f for f in match_fields(q, fields, []) if f["match"] == "name"]
    named.sort(key=lambda f: (not f["name"].lower().startswith(ql), f["name"].lower()))  # "starts with" first
    places: list[dict[str, Any]] = []
    # "40.9" or "40.92," is coordinates mid-typing: wait. A plain number like "120" is the start of
    # a house number, so it goes to the address service like any other text.
    half_typed_coords = re.search(r"[.,°]", q) and re.fullmatch(r"[-+\d.,\s°'\"NSEWnsew]+", q)
    if len(q) >= 3 and not half_typed_coords:
        if google:
            try:
                places, out["source"] = google.autocomplete(q, session_token, near), "Google"
            except QuotaExceeded:
                log.warning("Google Places daily limit reached; using OpenStreetMap suggestions")
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                log.warning("Google Places autocomplete failed: %s", exc.__class__.__name__)
        if not out["source"]:
            try:
                places, out["source"] = [_photon_item(p) for p in geocoder.photon(q, near)], "OpenStreetMap"
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
                out["error"] = f"Address suggestions unavailable ({exc.__class__.__name__}); press Search to look it up."
    n_fields = min(len(named), MAX_FIELD_SUGGESTIONS if places else MAX_SUGGESTIONS)
    out["items"] = [_field_item(f) for f in named[:n_fields]] + places[:MAX_SUGGESTIONS - n_fields]
    return out


def _only_names(q: str, named: list[dict[str, Any]]) -> bool:
    """Skip geocoding when the query is exactly one of your field names."""
    return any(m["name"].strip().lower() == q.strip().lower() for m in named)
