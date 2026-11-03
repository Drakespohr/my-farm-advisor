"""Area baseline: how cropland around a field looks on the same date.

Cropland comes from ESA WorldCover 2021 (10 m, class 40 = cropland, CC-BY 4.0), read straight
from the public COGs. For each clear image, NDVI is sampled on cropland pixels within
`AREA_RADIUS_M` of the field (excluding the field and a 30 m margin), so the field can be
ranked against nearby farmland instead of trees, roads, or towns.
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from .catalog import Scene
from .indices import compute_index
from .raster import GDAL_ENV, read_field_stack

log = logging.getLogger(__name__)

AREA_RADIUS_M = 2000.0
AREA_MARGIN_M = 30.0
MIN_AREA_PIXELS = 200  # 2 ha of clear cropland
WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
CROPLAND = 40
QUANTILES = list(range(0, 101, 5))


def worldcover_tiles(west: float, south: float, east: float, north: float) -> list[str]:
    tiles = []
    for lat in range(int(math.floor(south / 3) * 3), int(math.floor(north / 3) * 3) + 1, 3):
        for lon in range(int(math.floor(west / 3) * 3), int(math.floor(east / 3) * 3) + 1, 3):
            tiles.append(f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}")
    return tiles


def cropland_mask(crs: str, transform: Affine, shape: tuple[int, int], cache_dir: Path | None = None) -> np.ndarray:
    """Boolean cropland mask on the given grid (cached on disk; land cover doesn't change per image)."""
    key = hashlib.sha1(f"{crs}|{tuple(transform)[:6]}|{shape}".encode()).hexdigest()[:16]
    path = cache_dir / f"worldcover_{key}.npy" if cache_dir else None
    if path and path.exists():
        return np.load(path)
    h, w = shape
    left, top = transform * (0, 0)
    right, bottom = transform * (w, h)
    west, south, east, north = transform_bounds(crs, "EPSG:4326", left, bottom, right, top)
    out = np.zeros(shape, dtype=bool)
    with rasterio.Env(**GDAL_ENV):
        for tile in worldcover_tiles(west, south, east, north):
            try:
                with rasterio.open(WORLDCOVER_URL.format(tile=tile)) as src, WarpedVRT(
                    src, crs=crs, transform=transform, width=w, height=h, resampling=Resampling.nearest, nodata=0
                ) as vrt:
                    out |= vrt.read(1) == CROPLAND
            except rasterio.errors.RasterioIOError as exc:  # ocean tiles don't exist
                log.info("worldcover tile %s unavailable: %s", tile, exc)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, out)
    return out


def area_baseline(scene: Scene, geometry_wgs84: dict[str, Any], cache_dir: Path | None = None) -> dict[str, Any]:
    stack = read_field_stack(
        scene, geometry_wgs84, ("red", "nir"),
        inner_buffer_m=0.0, context_m=AREA_RADIUS_M + 20, cloud_dilation_px=2,
        ring_m=(AREA_MARGIN_M, AREA_RADIUS_M),
    )
    crop = cropland_mask(stack.crs, stack.transform, stack.shape, cache_dir)
    sample = stack.ring_mask & stack.clear_mask & crop
    n = int(sample.sum())
    out: dict[str, Any] = {
        "radius_m": AREA_RADIUS_M,
        "cropland_px": int((stack.ring_mask & crop).sum()),
        "n": n,
        "source": "ESA WorldCover 2021 cropland",
    }
    if n >= MIN_AREA_PIXELS:
        ndvi = compute_index("ndvi", stack.bands)[sample]
        ndvi = ndvi[np.isfinite(ndvi)]
        if ndvi.size >= MIN_AREA_PIXELS:
            out["ndvi_q"] = [round(float(v), 4) for v in np.percentile(ndvi, QUANTILES)]
            out["ndvi_median"] = out["ndvi_q"][10]
    return out


def percentile_of(value: float, quantiles: list[float]) -> float:
    """Where `value` falls in a distribution given by 5%-step quantiles (0-100)."""
    q = np.asarray(quantiles, dtype=float)
    if value <= q[0]:
        return 0.0
    if value >= q[-1]:
        return 100.0
    return float(np.interp(value, q, QUANTILES))
