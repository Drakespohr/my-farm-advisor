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

import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Optional

import httpx
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

NEAR_MILES = 3.0
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


def _only_names(q: str, named: list[dict[str, Any]]) -> bool:
    """Skip geocoding when the query is exactly one of your field names."""
    return any(m["name"].strip().lower() == q.strip().lower() for m in named)
