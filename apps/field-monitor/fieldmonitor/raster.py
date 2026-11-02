"""Read field-sized windows of Sentinel-2 COG bands onto a common 10 m grid."""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from .catalog import Scene

# Scene Classification Layer (Sen2Cor) classes treated as clear observations.
#   4 vegetation, 5 not-vegetated (bare soil), 6 water, 7 unclassified.
# Excluded: 0 nodata, 1 saturated/defective, 2 dark/topographic shadow,
#   3 cloud shadow, 8/9 cloud medium/high probability, 10 thin cirrus, 11 snow/ice.
SCL_CLEAR = (4, 5, 6, 7)
SCL_CLOUDY = (3, 8, 9, 10)

GRID = 10.0  # output resolution, metres
ALIGN = 20.0  # snap windows to the 20 m grid so 10 m and 20 m bands line up exactly

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}
_ca = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
if _ca:
    GDAL_ENV["CURL_CA_BUNDLE"] = _ca


@dataclass
class FieldStack:
    """Band reflectances and masks for one field on one date, on a 10 m UTM grid."""

    bands: dict[str, np.ndarray]  # float32 surface reflectance, NaN = nodata
    scl: np.ndarray  # uint8 scene classification
    field_mask: np.ndarray  # True inside the field (after inner buffer)
    clear_mask: np.ndarray  # True where SCL is clear and data present
    ring_mask: np.ndarray  # True in the neighbor ring outside the field (see read_field_stack)
    transform: Affine
    crs: str

    @property
    def shape(self) -> tuple[int, int]:
        return self.field_mask.shape

    @property
    def valid(self) -> np.ndarray:
        return self.field_mask & self.clear_mask

    @property
    def clear_fraction(self) -> float:
        n = int(self.field_mask.sum())
        return float(self.valid.sum()) / n if n else 0.0

    @property
    def cloud_fraction(self) -> float:
        n = int(self.field_mask.sum())
        cloudy = self.field_mask & np.isin(self.scl, SCL_CLOUDY)
        return float(cloudy.sum()) / n if n else 0.0


def to_crs(geom: BaseGeometry, src: str, dst: str) -> BaseGeometry:
    t = Transformer.from_crs(src, dst, always_xy=True)
    return shp_transform(t.transform, geom)


def _dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(iterations):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _snapped_bounds(bounds: tuple[float, float, float, float], origin: tuple[float, float]) -> tuple[float, float, float, float]:
    x0, y0 = origin
    minx, miny, maxx, maxy = bounds
    left = x0 + math.floor((minx - x0) / ALIGN) * ALIGN
    right = x0 + math.ceil((maxx - x0) / ALIGN) * ALIGN
    top = y0 - math.floor((y0 - maxy) / ALIGN) * ALIGN
    bottom = y0 - math.ceil((y0 - miny) / ALIGN) * ALIGN
    return left, bottom, right, top


def _band_scaling(asset: dict[str, Any]) -> tuple[float, float, float | None]:
    rb = (asset.get("raster:bands") or [{}])[0]
    return float(rb.get("scale", 1.0)), float(rb.get("offset", 0.0)), rb.get("nodata", 0)


def _read_asset(href: str, bounds: tuple[float, float, float, float], out_shape: tuple[int, int], resampling: Resampling) -> np.ndarray:
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        win = from_bounds(*bounds, transform=src.transform)
        win = Window(round(win.col_off), round(win.row_off), round(win.width), round(win.height))
        inside = (
            win.col_off >= 0
            and win.row_off >= 0
            and win.col_off + win.width <= src.width
            and win.row_off + win.height <= src.height
        )
        return src.read(1, window=win, out_shape=out_shape, resampling=resampling, boundless=not inside, fill_value=0)


def read_field_stack(
    scene: Scene,
    geometry_wgs84: dict[str, Any],
    bands: tuple[str, ...],
    inner_buffer_m: float = 10.0,
    context_m: float = 160.0,
    cloud_dilation_px: int = 2,
    min_clear_fraction: float | None = None,
    ring_m: tuple[float, float] = (30.0, 150.0),
) -> FieldStack:
    """Read `bands` + SCL for the field's bounding box (plus `context_m`) at 10 m.

    `inner_buffer_m` shrinks the field polygon before masking so mixed edge pixels
    (roads, headlands, tree lines) don't bias field statistics.
    `cloud_dilation_px` grows cloud/shadow masks to catch fuzzy cloud edges that
    Sen2Cor tends to under-classify.
    If `min_clear_fraction` is set, SCL is read first; when the field is too cloudy
    only the true-color bands are fetched (enough to show the user why it was skipped).
    `ring_m` (inner, outer) defines a neighbor ring outside the field. Comparing change
    inside the field with change in the ring separates field-specific problems from
    regional ones (weather, haze, area-wide maturity).
    """
    crs = f"EPSG:{scene.epsg}" if scene.epsg else None
    ref_href = scene.assets["red"]["href"]
    with rasterio.Env(**GDAL_ENV), rasterio.open(ref_href) as ref:
        crs = crs or ref.crs.to_string()
        origin = (ref.transform.c, ref.transform.f)

    field_utm = to_crs(shape(geometry_wgs84), "EPSG:4326", crs)
    left, bottom, right, top = _snapped_bounds(field_utm.buffer(context_m).bounds, origin)
    width, height = int(round((right - left) / GRID)), int(round((top - bottom) / GRID))
    transform = Affine(GRID, 0.0, left, 0.0, -GRID, top)
    bnds = (left, bottom, right, top)

    def load(key: str) -> tuple[str, np.ndarray]:
        asset = scene.assets[key]
        is_scl = key == "scl"
        raw = _read_asset(asset["href"], bnds, (height, width), Resampling.nearest if is_scl else Resampling.bilinear)
        if is_scl:
            return key, raw.astype(np.uint8)
        scale, offset, nodata = _band_scaling(asset)
        if scene.boa_offset_applied:
            offset = 0.0
        arr = raw.astype(np.float32) * scale + offset
        if nodata is not None:
            arr[raw == nodata] = np.nan
        return key, arr

    inner = field_utm.buffer(-inner_buffer_m) if inner_buffer_m > 0 else field_utm
    if inner.is_empty or inner.area < 4 * GRID * GRID:
        inner = field_utm  # tiny field: don't buffer it away
    field_mask = ~geometry_mask([mapping(inner)], out_shape=(height, width), transform=transform, all_touched=False)
    ring_geom = field_utm.buffer(ring_m[1]).difference(field_utm.buffer(ring_m[0]))
    ring_mask = ~geometry_mask([mapping(ring_geom)], out_shape=(height, width), transform=transform, all_touched=False)

    scl = load("scl")[1]
    cloudy = _dilate(np.isin(scl, SCL_CLOUDY), cloud_dilation_px)
    scl_clear = np.isin(scl, SCL_CLEAR) & ~cloudy
    if min_clear_fraction is not None and field_mask.any():
        if (scl_clear & field_mask).sum() / field_mask.sum() < min_clear_fraction:
            bands = ("red", "green", "blue")

    keys = list(dict.fromkeys(bands))
    with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
        loaded = dict(pool.map(load, keys))

    have_data = np.all([np.isfinite(a) for a in loaded.values()], axis=0)
    clear_mask = scl_clear & have_data

    return FieldStack(
        bands=loaded, scl=scl, field_mask=field_mask, clear_mask=clear_mask, ring_mask=ring_mask, transform=transform, crs=crs
    )
