"""News: national ag headlines, local storm and crop reports, and county names for locations.

Sources (all fetched by the server, cached, and never fatal when they fail):
- RSS/Atom feeds (national/extension news). Only titles, short summaries, and links are shown;
  every item links back to the publisher.
- NOAA Storm Prediction Center local storm reports (hail, wind), public domain. Turned into
  local items like "Hail (1.75 in) reported near Chariton, Lucas County, IA", shown only to people
  with a field within 100 miles (on every News tab).

The default source list lives in DEFAULT_SOURCES and can be replaced with
$FIELD_MONITOR_DATA/news_sources.json. Sources marked "verified": false were written without
network access to check them; their health shows at GET /api/news/sources.

County names come from US Census county boundaries (public domain), cached locally.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from shapely import STRtree
from shapely.geometry import Point, shape

from . import __version__
from .accounts import Accounts, now

log = logging.getLogger(__name__)

UA = {"User-Agent": f"overacre-news/{__version__}"}
COUNTIES_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"  # US Census 2010 cartographic boundaries
STATE_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "72": "PR",
}
STATE_NAMES = {v: k for k, v in STATE_ABBR.items()}

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {"id": "spc-hail", "kind": "spc", "name": "NOAA Storm Prediction Center (hail)", "scope": "local", "verified": False,
     "urls": ["https://www.spc.noaa.gov/climo/reports/yesterday_filtered_hail.csv",
              "https://www.spc.noaa.gov/climo/reports/today_filtered_hail.csv"], "event": "Hail"},
    {"id": "spc-wind", "kind": "spc", "name": "NOAA Storm Prediction Center (wind)", "scope": "local", "verified": False,
     "urls": ["https://www.spc.noaa.gov/climo/reports/yesterday_filtered_wind.csv",
              "https://www.spc.noaa.gov/climo/reports/today_filtered_wind.csv"], "event": "Damaging wind"},
    {"id": "farmdoc", "kind": "rss", "name": "farmdoc daily (University of Illinois)", "scope": "national", "verified": False,
     "urls": ["https://farmdocdaily.illinois.edu/feed"]},
    {"id": "usda", "kind": "rss", "name": "USDA news releases", "scope": "national", "verified": False,
     "urls": ["https://www.usda.gov/rss/latest-releases.xml"]},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL, scope TEXT NOT NULL,
  title TEXT NOT NULL, summary TEXT, url TEXT, lat REAL, lon REAL, place TEXT, published TEXT NOT NULL, fetched TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS news_items_pub ON news_items(published);
CREATE TABLE IF NOT EXISTS news_sources (
  id TEXT PRIMARY KEY, last_try TEXT, last_ok TEXT, last_error TEXT, items INTEGER DEFAULT 0
);
"""


# ---- counties -------------------------------------------------------------------------
class Counties:
    def __init__(self, cache_dir: Path) -> None:
        self.path = cache_dir / "counties.geojson"
        self._tree: STRtree | None = None
        self._props: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._tree is not None:
            return
        with self._lock:
            if self._tree is not None:
                return
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                r = httpx.get(COUNTIES_URL, headers=UA, timeout=60, follow_redirects=True)
                r.raise_for_status()
                self.path.write_bytes(r.content)
            data = json.loads(self.path.read_text())
            geoms = []
            for f in data["features"]:
                p = f["properties"]
                geoms.append(shape(f["geometry"]))
                self._props.append({"fips": f.get("id") or p.get("GEO_ID", "")[-5:], "name": p["NAME"],
                                    "lsad": p.get("LSAD", "County"), "state": STATE_ABBR.get(p["STATE"], p["STATE"])})
            self._tree = STRtree(geoms)
            self._geoms = geoms

    def near(self, lat: float, lon: float, radius_km: float) -> list[dict[str, Any]]:
        """Counties within `radius_km` of a point (the one containing it first)."""
        self._load()
        assert self._tree is not None
        import math
        dlat = radius_km / 111.0
        dlon = dlat / max(0.2, math.cos(math.radians(lat)))
        pt = Point(lon, lat)
        from shapely.geometry import box as _box
        hits = []
        for i in self._tree.query(_box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)):
            g = self._geoms[int(i)]
            d = g.distance(pt)  # degrees; good enough to rank and filter at county scale
            hits.append((0.0 if g.contains(pt) else d, self._props[int(i)]))
        hits.sort(key=lambda h: h[0])
        return [p for d, p in hits if d * 111.0 <= radius_km]

    def lookup(self, lat: float, lon: float) -> dict[str, Any] | None:
        try:
            self._load()
        except Exception as exc:  # offline: locations just won't get county names
            log.warning("county boundaries unavailable: %s", exc)
            return None
        pt = Point(lon, lat)
        assert self._tree is not None
        for i in self._tree.query(pt):
            if self._geoms[int(i)].contains(pt):
                p = self._props[int(i)]
                label = p["name"] if p["lsad"] in ("city", "") else f"{p['name']} {p['lsad']}"
                return {**p, "label": f"{label}, {p['state']}"}
        return None


# ---- parsing --------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")


def clean_text(s: str | None, limit: int = 280) -> str:
    t = html.unescape(_TAG.sub(" ", s or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _date(s: str | None) -> str:
    if s:
        for parse in (parsedate_to_datetime, lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
            try:
                d = parse(s.strip())
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except (TypeError, ValueError):
                continue
    return now()


def parse_feed(xml_text: str | bytes) -> list[dict[str, Any]]:
    """RSS 2.0 or Atom -> [{title, url, summary, published}]."""
    root = ElementTree.fromstring(xml_text)
    out = []
    for item in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
        kids = {_local(c.tag): c for c in item}
        title = clean_text((kids.get("title").text if kids.get("title") is not None else "") or "", 200)
        link_el = kids.get("link")
        url = ""
        if link_el is not None:
            url = (link_el.text or "").strip() or link_el.attrib.get("href", "")
        summ = next((kids[k].text for k in ("description", "summary", "content") if k in kids and kids[k].text), "")
        pub = next((kids[k].text for k in ("pubDate", "published", "updated", "date") if k in kids and kids[k].text), None)
        if title and url.startswith(("http://", "https://")):
            out.append({"title": title, "url": url, "summary": clean_text(summ), "published": _date(pub)})
    return out


def parse_spc(text: str, day: datetime, event: str) -> list[dict[str, Any]]:
    """SPC storm report CSV (Time,Size|Speed,Location,County,State,Lat,Lon,Comments) -> items."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat, lon = float(row["Lat"]), float(row["Lon"])
        except (KeyError, TypeError, ValueError):
            continue
        hhmm = (row.get("Time") or "0000").zfill(4)
        when = day.replace(hour=int(hhmm[:2]) % 24, minute=int(hhmm[2:]) % 60, second=0, microsecond=0)
        if when.hour < 12:  # SPC days run 12Z to 12Z
            when += timedelta(days=1)
        size = row.get("Size") or row.get("Speed") or ""
        detail = ""
        if size.isdigit() and event == "Hail":
            detail = f" ({int(size) / 100:.2f} in)"
        elif size.isdigit():
            detail = f" ({size} mph)"
        county = (row.get("County") or "").title()
        state = row.get("State") or ""
        place = f"{(row.get('Location') or '').title()}, {county} County, {state}".strip(", ")
        out.append({"title": f"{event}{detail} reported near {place}", "summary": clean_text(row.get("Comments") or ""),
                    "lat": lat, "lon": lon, "place": f"{county} County, {state}", "published": when.isoformat(), "url": None})
    return out


# ---- fetching -------------------------------------------------------------------------
class News:
    def __init__(self, accounts: Accounts, data_root: Path) -> None:
        self.accounts = accounts
        self.counties = Counties(data_root / "cache")
        self.sources_path = data_root / "news_sources.json"
        with accounts.db() as c:
            c.executescript(SCHEMA)

    def sources(self) -> list[dict[str, Any]]:
        if self.sources_path.exists():
            try:
                return json.loads(self.sources_path.read_text())
            except ValueError:
                log.warning("news_sources.json is not valid JSON; using defaults")
        return DEFAULT_SOURCES

    def _store(self, src: dict[str, Any], items: list[dict[str, Any]]) -> int:
        added = 0
        with self.accounts.db() as c:
            for it in items:
                key = it.get("url") or f"{it['title']}|{it['published']}"
                iid = "n_" + hashlib.sha1(f"{src['id']}|{key}".encode()).hexdigest()[:16]
                cur = c.execute(
                    "INSERT OR IGNORE INTO news_items (id, source_id, source_name, scope, title, summary, url, lat, lon, place, published, fetched) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (iid, src["id"], src["name"], src.get("scope", "national"), it["title"], it.get("summary"), it.get("url"),
                     it.get("lat"), it.get("lon"), it.get("place"), it["published"], now()))
                added += cur.rowcount
        return added

    def fetch_source(self, src: dict[str, Any], client: httpx.Client) -> int:
        items: list[dict[str, Any]] = []
        for url in src["urls"]:
            r = client.get(url)
            r.raise_for_status()
            if src["kind"] == "rss":
                items += parse_feed(r.content)[:30]
            elif src["kind"] == "spc":
                today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
                day = today - timedelta(days=1) if "yesterday" in url else today
                items += parse_spc(r.text, day, src.get("event", "Storm"))
        return self._store(src, items)

    def refresh(self) -> dict[str, Any]:
        results = {}
        with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
            for src in self.sources():
                err, n = None, 0
                try:
                    n = self.fetch_source(src, client)
                except Exception as exc:  # one bad source never blocks the others
                    err = f"{type(exc).__name__}: {str(exc)[:160]}"
                with self.accounts.db() as c:
                    c.execute(
                        "INSERT INTO news_sources (id, last_try, last_ok, last_error, items) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET last_try=excluded.last_try, "
                        "last_ok=COALESCE(excluded.last_ok, news_sources.last_ok), last_error=excluded.last_error, "
                        "items=news_sources.items + excluded.items",
                        (src["id"], now(), None if err else now(), err, n))
                results[src["id"]] = err or f"+{n}"
        return results

    def health(self) -> list[dict[str, Any]]:
        with self.accounts.db() as c:
            rows = {r["id"]: dict(r) for r in c.execute("SELECT * FROM news_sources")}
        return [{"id": s["id"], "name": s["name"], "scope": s.get("scope"), "verified": s.get("verified", True),
                 **{k: rows.get(s["id"], {}).get(k) for k in ("last_try", "last_ok", "last_error", "items")}} for s in self.sources()]

    def start(self, every_s: float = 2 * 3600, delay_s: float = 30) -> None:
        def loop() -> None:
            time.sleep(delay_s)
            while True:
                try:
                    self.refresh()
                except Exception:
                    log.exception("news refresh failed")
                time.sleep(every_s)
        threading.Thread(target=loop, daemon=True, name="news").start()

    def items(self, since: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.accounts.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM news_items WHERE published>=? ORDER BY published DESC LIMIT ?", (since, limit))]
