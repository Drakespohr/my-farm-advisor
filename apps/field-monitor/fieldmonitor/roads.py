"""Local road index for instant crossroads search ("250th and Waverly Road").

Public road services (Overpass) are too slow and unreliable to search while typing, so we keep our
own index of every corner where two named roads meet, per county, built from the US Census
Bureau's TIGER/Line "All Roads" files (public domain; every public road with the names on the
signs, e.g. "N 250th St", "Waverly Rd"):

    https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_<state+county FIPS>_roads.zip

- Counties around the user's fields are downloaded once in the background (a few MB each) and
  turned into a small SQLite table of crossings. After that, lookups are local: milliseconds.
- TIGER road segments are split at every intersection, so a corner is simply a point where segment
  ends of two differently named roads meet (overpasses don't share a node, so they never count).
- Matching is by words after normalizing abbreviations (Road->rd, County->co, North->n), dropping
  a leading direction and a trailing road type. Numbers must match exactly ("250" never matches
  "25"); words may be a prefix while typing or slightly misspelled ("waverley" ~ "Waverly").

Set FIELD_MONITOR_ROAD_INDEX=0 to turn background downloads off.
"""

from __future__ import annotations

import difflib
import io
import logging
import math
import os
import re
import sqlite3
import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx

log = logging.getLogger(__name__)

TIGER_URL = os.getenv("FIELD_MONITOR_TIGER_URL",
                      "https://www2.census.gov/geo/tiger/TIGER{year}/ROADS/tl_{year}_{fips}_roads.zip")
TIGER_YEARS = (2024, 2023)
NEAR_MILES = 30          # counties within this distance of a field (or the search point) are indexed
RETRY_FAILED_S = 6 * 3600

ABBREV = {
    "road": "rd", "street": "st", "avenue": "ave", "av": "ave", "highway": "hwy", "hiway": "hwy", "county": "co",
    "lane": "ln", "drive": "dr", "boulevard": "blvd", "trail": "trl", "parkway": "pkwy", "court": "ct", "place": "pl",
    "circle": "cir", "north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
    "southeast": "se", "southwest": "sw", "route": "rte", "terrace": "ter",
}
TYPES = {"rd", "st", "ave", "hwy", "ln", "dr", "blvd", "trl", "pkwy", "ct", "pl", "cir", "way", "ter", "loop"}
DIRS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS counties (fips TEXT PRIMARY KEY, status TEXT NOT NULL, built_at REAL, error TEXT,
                                     segments INTEGER, crossings INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS crossings (fips TEXT NOT NULL, a_norm TEXT NOT NULL, b_norm TEXT NOT NULL,
                                      a_name TEXT NOT NULL, b_name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
CREATE INDEX IF NOT EXISTS crossings_by_fips ON crossings(fips);
"""


def tokens(name: str) -> list[str]:
    """Normalized words: 'N 250th St' -> ['250'], 'County Road 12' -> ['co', 'rd', '12'], 'Waverly Rd' -> ['waverly']."""
    words = [ABBREV.get(w, w) for w in re.findall(r"[a-z0-9]+", name.lower())]
    words = [(_ORDINAL.match(w).group(1) if _ORDINAL.match(w) else w) for w in words]
    if len(words) > 1 and words[0] in DIRS:
        words = words[1:]
    if len(words) > 1 and words[-1] in DIRS:  # "Waverly Rd E"
        words = words[:-1]
    if len(words) > 1 and words[-1] in TYPES:
        words = words[:-1]
    return words


def norm(name: str) -> str:
    return " ".join(tokens(name))


def _word_score(q: str, r: str, last_partial: bool) -> float:
    if q == r:
        return 1.0
    if q.isdigit() or r.isdigit():
        return 0.0  # numbers match exactly: 250 is never 25 or 2500
    if last_partial and r.startswith(q) and len(q) >= 2:
        return 0.9
    if len(q) >= 4:
        ratio = difflib.SequenceMatcher(None, q, r).ratio()
        if ratio >= 0.8:
            return ratio * 0.9
    return 0.0


def match_score(query: list[str], road: list[str], partial: bool = False) -> float:
    """How well typed words match a road's words (0 = no match). Every typed word must match a
    road word, in order; numbers exactly."""
    if not query or not road:
        return 0.0
    total, j = 0.0, 0
    for i, q in enumerate(query):
        last = partial and i == len(query) - 1
        best, best_j = 0.0, -1
        for k in range(j, len(road)):
            s = _word_score(q, road[k], last)
            if s > best:
                best, best_j = s, k
        if best == 0:
            return 0.0
        total += best
        j = best_j + 1
    extra = len(road) - len(query)  # unmatched road words cost a little ("us hwy 6" for "hwy 6")
    return total / len(query) - 0.05 * max(0, extra)


def crossings_from_shapes(records: Iterable[tuple[str, list[tuple[float, float]]]]) -> list[tuple[str, str, float, float]]:
    """Corners from (FULLNAME, [(lon, lat), ...]) segments: where ends of differently named roads meet."""
    ends: dict[tuple[int, int], set[str]] = defaultdict(set)
    for name, pts in records:
        if not name or len(pts) < 2:
            continue
        for lon, lat in (pts[0], pts[-1]):
            ends[(round(lon * 1e6), round(lat * 1e6))].add(name.strip())
    out = []
    for (x, y), names in ends.items():
        by_norm = {norm(n): n for n in sorted(names)}
        keys = sorted(by_norm)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                out.append((by_norm[keys[i]], by_norm[keys[j]], y / 1e6, x / 1e6))
    return out


def _miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(b[0] - a[0], dx) * 69.0


class RoadIndex:
    def __init__(self, root: Path, counties: Any, http_get: Callable[..., httpx.Response] = httpx.get,
                 user_agent: str = "overacre-field-monitor") -> None:
        self.path = root / "roads.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.counties = counties
        self.http_get = http_get
        self.user_agent = user_agent
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._worker: Optional[threading.Thread] = None
        self._mem: dict[str, list[tuple[list[str], list[str], str, str, float, float]]] = {}
        with self._db() as c:
            c.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # ---- which counties ------------------------------------------------------------------
    def counties_near(self, lat: float, lon: float, miles: float = NEAR_MILES) -> list[dict[str, Any]]:
        try:
            return self.counties.near(lat, lon, miles * 1.609344)
        except Exception as exc:  # county boundaries unavailable (offline)
            log.warning("county lookup failed: %s", exc.__class__.__name__)
            return []

    def status(self, fips: str) -> Optional[dict[str, Any]]:
        with self._db() as c:
            row = c.execute("SELECT * FROM counties WHERE fips=?", (fips,)).fetchone()
        return dict(row) if row else None

    def ensure(self, fips_list: Iterable[str]) -> list[str]:
        """Queue counties that aren't indexed yet (background download). Returns those still pending."""
        pending = []
        for fips in dict.fromkeys(fips_list):
            st = self.status(fips)
            if st and st["status"] == "ready":
                continue
            if st and st["status"] == "error" and time.time() - (st["built_at"] or 0) < RETRY_FAILED_S:
                continue
            pending.append(fips)
            with self._lock:
                if fips not in self._queue:
                    self._queue.append(fips)
        if pending and os.getenv("FIELD_MONITOR_ROAD_INDEX", "1") != "0":
            with self._lock:
                if not (self._worker and self._worker.is_alive()):
                    self._worker = threading.Thread(target=self._drain, daemon=True, name="road-index")
                    self._worker.start()
        return pending

    def ensure_near(self, points: Iterable[tuple[float, float]]) -> list[str]:
        fips = [c["fips"] for p in points for c in self.counties_near(*p) if c.get("fips")]
        return self.ensure(fips)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                fips = self._queue.pop(0)
            try:
                self.build(fips)
            except Exception as exc:
                log.warning("road index for %s failed: %s", fips, exc)
                with self._db() as c:
                    c.execute("INSERT OR REPLACE INTO counties (fips, status, built_at, error) VALUES (?, 'error', ?, ?)",
                              (fips, time.time(), str(exc)[:300]))

    # ---- build -----------------------------------------------------------------------------
    def download(self, fips: str) -> tuple[bytes, str]:
        last: Exception = RuntimeError("no TIGER year configured")
        for year in TIGER_YEARS:
            url = TIGER_URL.format(year=year, fips=fips)
            try:
                r = self.http_get(url, headers={"User-Agent": self.user_agent}, timeout=120.0, follow_redirects=True)
                r.raise_for_status()
                return r.content, url
            except httpx.HTTPError as exc:
                last = exc
        raise last

    def build(self, fips: str, raw: Optional[bytes] = None, source: str = "") -> int:
        """Download (unless `raw` is given) and index one county. Returns the number of crossings."""
        import shapefile  # pyshp

        if raw is None:
            raw, source = self.download(fips)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
        shp = next(n for n in names if n.endswith(".shp"))
        dbf = next(n for n in names if n.endswith(".dbf"))
        reader = shapefile.Reader(shp=io.BytesIO(zf.read(shp)), dbf=io.BytesIO(zf.read(dbf)))
        fields = [f[0] for f in reader.fields[1:]]
        name_i = fields.index("FULLNAME")
        segs = []
        for sr in reader.iterShapeRecords():
            name = sr.record[name_i]
            if name:
                segs.append((str(name), [(float(x), float(y)) for x, y in sr.shape.points]))
        rows = crossings_from_shapes(segs)
        with self._db() as c:
            c.execute("DELETE FROM crossings WHERE fips=?", (fips,))  # a rebuild replaces the derived index
            c.executemany("INSERT INTO crossings (fips, a_norm, b_norm, a_name, b_name, lat, lon) VALUES (?,?,?,?,?,?,?)",
                          [(fips, norm(a), norm(b), a, b, lat, lon) for a, b, lat, lon in rows])
            c.execute("INSERT OR REPLACE INTO counties (fips, status, built_at, error, segments, crossings, source) "
                      "VALUES (?, 'ready', ?, NULL, ?, ?, ?)", (fips, time.time(), len(segs), len(rows), source))
        self._mem.pop(fips, None)
        log.info("road index %s: %d segments, %d crossings", fips, len(segs), len(rows))
        return len(rows)

    # ---- search ----------------------------------------------------------------------------
    def _county_rows(self, fips: str) -> list[tuple[str, str, str, str, float, float]]:
        rows = self._mem.get(fips)
        if rows is None:
            with self._db() as c:
                rows = [(r["a_norm"], r["b_norm"], r["a_name"], r["b_name"], r["lat"], r["lon"])
                        for r in c.execute("SELECT * FROM crossings WHERE fips=?", (fips,))]
            self._mem[fips] = rows
        return rows

    def find(self, side_a: str, side_b: str, near: tuple[float, float], partial: bool = False,
             limit: int = 3) -> tuple[list[dict[str, Any]], list[str]]:
        """(crossings nearest-best first, counties still being indexed)."""
        counties = self.counties_near(*near)
        fips_list = [c["fips"] for c in counties if c.get("fips")]
        pending = self.ensure(fips_list)
        names = {c["fips"]: f"{c.get('name', '')} County, {c.get('state', '')}".strip(", ") for c in counties}
        qa, qb = tokens(side_a), tokens(side_b)
        if not qa or not qb:
            return [], pending
        hits = []
        score_a: dict[str, float] = {}
        score_b: dict[str, float] = {}

        def sa(n: str) -> float:  # each distinct road name is scored once per search
            if n not in score_a:
                score_a[n] = match_score(qa, n.split())
            return score_a[n]

        def sb(n: str) -> float:
            if n not in score_b:
                score_b[n] = match_score(qb, n.split(), partial)
            return score_b[n]

        for fips in fips_list:
            if fips in pending:
                continue
            for a_n, b_n, a_name, b_name, lat, lon in self._county_rows(fips):
                s1 = min(sa(a_n), sb(b_n)) if score_a.get(a_n, 1) and score_b.get(b_n, 1) else 0.0
                s2 = min(sa(b_n), sb(a_n)) if score_a.get(b_n, 1) and score_b.get(a_n, 1) else 0.0
                score = max(s1, s2)
                if score <= 0:
                    continue
                first, second = (a_name, b_name) if s1 >= s2 else (b_name, a_name)
                miles = _miles(near, (lat, lon))
                hits.append((score, miles, first, second, lat, lon, names.get(fips, "")))
        # Best match first; among equally good matches, nearest first.
        hits.sort(key=lambda h: (-round(h[0], 2), h[1]))
        out: list[dict[str, Any]] = []
        for score, miles, a, b, lat, lon, county in hits:
            if any(_miles((o["lat"], o["lon"]), (lat, lon)) < 0.2 for o in out):
                continue
            out.append({"type": "place", "kind": "intersection", "main": f"{a} & {b}", "name": f"{a} & {b}",
                        "secondary": " · ".join(x for x in ("Intersection", county, f"{miles:.1f} mi away") if x),
                        "lat": round(lat, 6), "lon": round(lon, 6), "bbox": None, "source": "US Census TIGER",
                        "miles": round(miles, 1), "score": round(score, 2)})
            if len(out) >= limit:
                break
        return out, pending
