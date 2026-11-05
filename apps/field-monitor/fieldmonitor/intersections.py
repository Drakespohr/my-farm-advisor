"""Find road intersections like "250th and Waverly Road" near the map view or your fields.

Address services don't know rural crossroads, so we ask OpenStreetMap (Overpass API) for roads
whose name or number matches each side, near a point, and compute where they cross.

- Query forms: "A and B", "A & B", "A / B", "A at B", "A + B".
- Each side matches road names or route numbers by word, ignoring a trailing suffix (Road, Rd,
  Street, St, Ave, ...) and a leading direction (N, North, ...). The last word may be partial while
  typing ("250th and wav" finds Waverly Road). Numbers match whole ("250" ~ "250th", not "2500").
- Search area: about 35 miles around the point. Repeated road names (rural grids repeat "250th St"
  in many counties) resolve to the crossing nearest the point.
- Overpass is free with fair use: we identify the app, cache results for a day, and space requests.
  Set FIELD_MONITOR_OVERPASS_URL to use another instance (or your own).
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

import httpx
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

log = logging.getLogger(__name__)

OVERPASS_URL = os.getenv("FIELD_MONITOR_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
SPLIT = re.compile(r"\s+(?:and|at)\s+|\s*[&/+]\s*", re.I)
SUFFIXES = {"road", "rd", "street", "st", "avenue", "ave", "av", "highway", "hwy", "lane", "ln", "drive", "dr",
            "boulevard", "blvd", "trail", "trl", "parkway", "pkwy", "way", "court", "ct", "place", "pl", "circle", "cir"}
DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west", "ne", "nw", "se", "sw"}
HALF_DEG_LAT = 0.5          # search box: +/- 0.5 deg lat (~35 mi), lon scaled to match
GAP_M = 30                  # roads that stop short of each other still count as meeting within this
MIN_GAP_S = 0.6             # space Overpass requests (fair use)
CACHE_S = 24 * 3600


def parse(q: str) -> Optional[tuple[str, str]]:
    """("250th", "waverly road") from "250th and Waverly Road"; None if it isn't two roads."""
    parts = [p.strip() for p in SPLIT.split(q.strip(), maxsplit=1)]
    if len(parts) != 2 or min(len(p) for p in parts) < 2:
        return None
    return parts[0], parts[1]


def name_regex(part: str, partial_last: bool = False) -> Optional[str]:
    """Overpass (POSIX) regex for one side, matched case-insensitively against name and ref."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", part.lower())]
    if words and words[0] in DIRECTIONS and len(words) > 1:
        words = words[1:]
    if len(words) > 1 and words[-1] in SUFFIXES:
        words = words[:-1]
    if not words:
        return None
    out = []
    for i, w in enumerate(words):
        last = i == len(words) - 1
        m = re.fullmatch(r"(\d+)(st|nd|rd|th)?", w)
        if m:  # 250 / 250th -> 250th, 250 (whole number)
            out.append(f"{m.group(1)}(st|nd|rd|th)?")
        else:
            out.append(re.escape(w))
        if last and not (partial_last and not m):
            out[-1] += "( |$)"
    return "(^| )" + " ".join(out)


class Intersections:
    def __init__(self, user_agent: str, http_post: Callable[..., httpx.Response] = httpx.post) -> None:
        self.user_agent = user_agent
        self.http_post = http_post
        self._lock = threading.Lock()
        self._last = 0.0
        self._cache: "OrderedDict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]]" = OrderedDict()

    def find(self, q: str, near: tuple[float, float], partial: bool = False, limit: int = 3) -> list[dict[str, Any]]:
        """Crossings of the two roads in `q`, nearest to `near` first. [] if none or not an intersection."""
        sides = parse(q)
        if not sides:
            return []
        ra, rb = name_regex(sides[0]), name_regex(sides[1], partial_last=partial)
        if not ra or not rb:
            return []
        key = (ra, rb, round(near[0], 1), round(near[1], 1))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1][:limit]
        with self._lock:
            wait = MIN_GAP_S - (time.monotonic() - self._last)
            if wait > 0:
                if partial:  # while typing, skip rather than queue up requests
                    return []
                time.sleep(wait)
            self._last = time.monotonic()
        lat, lon = near
        dlon = HALF_DEG_LAT / max(0.2, math.cos(math.radians(lat)))
        bbox = f"{lat - HALF_DEG_LAT:.4f},{lon - dlon:.4f},{lat + HALF_DEG_LAT:.4f},{lon + dlon:.4f}"
        sel = lambda rx: (f'way["highway"]["name"~"{rx}",i]({bbox});'  # noqa: E731
                          f'way["highway"]["ref"~"{rx}",i]({bbox});')
        query = f"[out:json][timeout:20];({sel(ra)}{sel(rb)});out tags geom;"
        r = self.http_post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": self.user_agent}, timeout=25.0)
        r.raise_for_status()
        out = crossings(r.json(), ra, rb, near)
        self._cache[key] = (time.monotonic(), out)
        while len(self._cache) > 256:
            self._cache.popitem(last=False)
        return out[:limit]


def _matches(rx: str, tags: dict[str, Any]) -> bool:
    pyrx = re.compile(rx, re.I)
    return any(pyrx.search(str(tags.get(k) or "")) for k in ("name", "ref"))


def crossings(data: dict[str, Any], ra: str, rb: str, near: tuple[float, float]) -> list[dict[str, Any]]:
    a_lines, b_lines, a_names, b_names = [], [], {}, {}
    for el in data.get("elements", []):
        geom = el.get("geometry") or []
        if el.get("type") != "way" or len(geom) < 2:
            continue
        line = LineString([(g["lon"], g["lat"]) for g in geom])
        tags = el.get("tags") or {}
        label = tags.get("name") or tags.get("ref") or "road"
        if _matches(ra, tags):
            a_lines.append((line, label))
        if _matches(rb, tags):
            b_lines.append((line, label))
    found: list[dict[str, Any]] = []
    deg_gap = GAP_M / 111_000
    for la, na in a_lines:
        for lb, nb in b_lines:
            if la.equals(lb):
                continue
            hit = la.intersection(lb)
            pts: list[Point] = []
            if not hit.is_empty:
                if hit.geom_type == "Point":
                    pts = [hit]
                elif hit.geom_type == "MultiPoint":
                    pts = list(hit.geoms)
                else:  # roads overlap for a stretch: use the middle of it
                    pts = [hit.representative_point()]
            elif la.distance(lb) <= deg_gap:  # T-junction drawn a few meters short
                p1, p2 = nearest_points(la, lb)
                pts = [Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)]
            for p in pts:
                found.append({"lat": round(p.y, 6), "lon": round(p.x, 6), "a": na, "b": nb})
    # One result per crossing (several way segments can meet at the same corner).
    uniq: list[dict[str, Any]] = []
    for f in sorted(found, key=lambda f: _dist(near, f)):
        if all(_dist((u["lat"], u["lon"]), f) > 0.003 for u in uniq):  # ~300 m apart
            uniq.append(f)
    return [{"type": "place", "kind": "intersection", "main": f"{u['a']} & {u['b']}",
             "secondary": f"Intersection · {round(_dist(near, u) * 69.0, 1):g} mi away",
             "name": f"{u['a']} & {u['b']}", "lat": u["lat"], "lon": u["lon"], "bbox": None, "source": "OpenStreetMap",
             "miles": round(_dist(near, u) * 69.0, 1)} for u in uniq]


def _dist(a: tuple[float, float], f: dict[str, Any]) -> float:
    dx = (f["lon"] - a[1]) * math.cos(math.radians(a[0]))
    return math.hypot(f["lat"] - a[0], dx)
