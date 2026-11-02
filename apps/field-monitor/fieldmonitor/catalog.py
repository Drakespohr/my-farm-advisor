"""Sentinel-2 L2A scene discovery.

Primary source is the Earth Search STAC API (Element 84, no account needed).
If the API is unreachable, falls back to listing the public `sentinel-cogs` S3
bucket that the API indexes. Each scene folder there holds the same STAC item JSON,
so both paths yield identical metadata.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from xml.etree import ElementTree

import httpx
import mgrs
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

log = logging.getLogger(__name__)

STAC_URL = os.environ.get("FIELD_MONITOR_STAC_URL", "https://earth-search.aws.element84.com/v1")
COLLECTION = "sentinel-2-l2a"
S3_BUCKET_URL = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
S3_PREFIX = "sentinel-s2-l2a-cogs"

_ITEM_ID = re.compile(r"^(S2[ABCD])_(\d{1,2}[A-Z]{3})_(\d{8})_(\d+)_L2A$")


@dataclass(frozen=True)
class Scene:
    id: str
    datetime: str
    platform: str
    mgrs_tile: str
    cloud_cover: float | None
    epsg: int | None
    assets: dict[str, dict[str, Any]]
    self_href: str | None
    source: str  # "stac-api" or "s3-listing"
    # Earth Search sets this when it has already removed the L2A BOA offset
    # (baseline >= 04.00) from the pixel values. Its raster:bands metadata still lists
    # offset -0.1, so applying that offset again would push reflectance negative.
    boa_offset_applied: bool = False

    @property
    def date(self) -> str:
        return self.datetime[:10]

    @property
    def sequence(self) -> int:
        m = _ITEM_ID.match(self.id)
        return int(m.group(4)) if m else 0

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "datetime": self.datetime,
            "date": self.date,
            "platform": self.platform,
            "mgrs_tile": self.mgrs_tile,
            "cloud_cover": self.cloud_cover,
            "thumbnail": (self.assets.get("thumbnail") or {}).get("href"),
            "source": self.source,
        }


def scene_from_item(item: dict[str, Any], source: str, self_href: str | None = None) -> Scene:
    props = item.get("properties", {})
    tile = props.get("grid:code", "").replace("MGRS-", "") or "".join(
        str(props.get(k, "")) for k in ("mgrs:utm_zone", "mgrs:latitude_band", "mgrs:grid_square")
    )
    if self_href is None:
        self_href = next((lnk["href"] for lnk in item.get("links", []) if lnk.get("rel") == "self"), None)
    return Scene(
        id=item["id"],
        datetime=props.get("datetime", ""),
        platform=props.get("platform", ""),
        mgrs_tile=tile,
        cloud_cover=props.get("eo:cloud_cover"),
        epsg=props.get("proj:epsg"),
        assets=item.get("assets", {}),
        self_href=self_href,
        source=source,
        boa_offset_applied=bool(props.get("earthsearch:boa_offset_applied", False)),
    )


def _client() -> httpx.Client:
    # Honors HTTPS_PROXY and SSL_CERT_FILE from the environment.
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)


def search_stac(geom: BaseGeometry, start: date, end: date, max_cloud: float, limit: int = 500) -> list[Scene]:
    body: dict[str, Any] = {
        "collections": [COLLECTION],
        "intersects": mapping(geom),
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "limit": 100,
    }
    scenes: list[Scene] = []
    url = f"{STAC_URL}/search"
    with _client() as client:
        while url and len(scenes) < limit:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            scenes.extend(scene_from_item(f, "stac-api") for f in data.get("features", []))
            nxt = next((lnk for lnk in data.get("links", []) if lnk.get("rel") == "next"), None)
            if not nxt:
                break
            url = nxt["href"]
            body = nxt.get("body", body)
    return scenes


def mgrs_tiles_for(geom: BaseGeometry) -> list[str]:
    """MGRS 100 km tiles touched by the geometry's vertices and centroid."""
    conv = mgrs.MGRS()
    pts: list[tuple[float, float]] = [(geom.centroid.x, geom.centroid.y)]
    minx, miny, maxx, maxy = geom.bounds
    pts += [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
    tiles: list[str] = []
    for lon, lat in pts:
        t = conv.toMGRS(lat, lon, MGRSPrecision=0)
        if t not in tiles:
            tiles.append(t)
    return tiles


def _months(start: date, end: date) -> Iterable[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _list_prefixes(client: httpx.Client, prefix: str) -> list[str]:
    out: list[str] = []
    token: str | None = None
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    while True:
        params = {"list-type": "2", "prefix": prefix, "delimiter": "/"}
        if token:
            params["continuation-token"] = token
        resp = client.get(S3_BUCKET_URL + "/", params=params)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        out += [p.text or "" for p in root.findall("s3:CommonPrefixes/s3:Prefix", ns)]
        token_el = root.find("s3:NextContinuationToken", ns)
        if token_el is None or not token_el.text:
            return out
        token = token_el.text


def search_s3(geom: BaseGeometry, start: date, end: date, max_cloud: float) -> list[Scene]:
    with _client() as client:
        candidates: list[str] = []
        for tile in mgrs_tiles_for(geom):
            zone, band, square = tile[:-3], tile[-3], tile[-2:]
            for y, m in _months(start, end):
                for p in _list_prefixes(client, f"{S3_PREFIX}/{int(zone)}/{band}/{square}/{y}/{m}/"):
                    item_id = p.rstrip("/").rsplit("/", 1)[-1]
                    mt = _ITEM_ID.match(item_id)
                    if mt and start <= datetime.strptime(mt.group(3), "%Y%m%d").date() <= end:
                        candidates.append(p)

        def fetch(prefix: str) -> Scene | None:
            item_id = prefix.rstrip("/").rsplit("/", 1)[-1]
            href = f"{S3_BUCKET_URL}/{prefix}{item_id}.json"
            try:
                r = client.get(href)
                r.raise_for_status()
                return scene_from_item(r.json(), "s3-listing", self_href=href)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("skipping %s: %s", href, exc)
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            scenes = [s for s in pool.map(fetch, candidates) if s is not None]
    return [s for s in scenes if s.cloud_cover is None or s.cloud_cover <= max_cloud]


def dedupe(scenes: list[Scene], preferred_tile: str | None) -> list[Scene]:
    """One scene per acquisition date.

    Earth Search can hold reprocessed duplicates (`_0_` vs `_1_` sequence) and a field
    near a tile edge appears in two overlapping tiles on the same pass. Keep the newest
    processing of the tile that contains the field centroid.
    """
    best: dict[str, Scene] = {}
    for s in scenes:
        cur = best.get(s.date)
        if cur is None:
            best[s.date] = s
            continue
        rank_new = (s.mgrs_tile == preferred_tile, s.sequence, -(s.cloud_cover or 0))
        rank_cur = (cur.mgrs_tile == preferred_tile, cur.sequence, -(cur.cloud_cover or 0))
        if rank_new > rank_cur:
            best[s.date] = s
    return sorted(best.values(), key=lambda s: s.datetime)


def search_scenes(geometry: dict[str, Any], start: date, end: date, max_cloud: float = 60.0) -> tuple[list[Scene], str]:
    """Return (deduplicated scenes oldest-first, source used)."""
    geom = shape(geometry)
    try:
        scenes = search_stac(geom, start, end, max_cloud)
        source = "stac-api"
    except httpx.HTTPError as exc:
        log.warning("STAC search failed (%s); falling back to S3 listing", exc)
        scenes = search_s3(geom, start, end, max_cloud)
        source = "s3-listing"
    tiles = mgrs_tiles_for(geom)
    return dedupe(scenes, tiles[0] if tiles else None), source


def load_scene(href: str) -> Scene:
    with _client() as client:
        r = client.get(href)
        r.raise_for_status()
        source = "s3-listing" if href.startswith(S3_BUCKET_URL) else "stac-api"
        return scene_from_item(r.json(), source, self_href=href)
