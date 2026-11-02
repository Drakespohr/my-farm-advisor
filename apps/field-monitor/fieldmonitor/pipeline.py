"""Process one field x one Sentinel-2 scene into a stored observation."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from . import PROCESSING_VERSION, __version__
from .analysis import field_stats
from .catalog import Scene
from .indices import INDICES, REQUIRED_BANDS, compute_index
from .raster import SCL_CLEAR, SCL_CLOUDY, read_field_stack
from .render import colorize, to_web_png, true_color
from .store import Store, git_commit, now_iso

log = logging.getLogger(__name__)

DEFAULTS = {
    "inner_buffer_m": 10.0,
    "cloud_dilation_px": 2,
    "min_clear_fraction": 0.5,
}


# Haze thresholds, calibrated on a 2025 Iowa season (field OSM_998713335): clear days had
# AOT 0.06-0.15 and blue reflectance over dense canopy 0.025-0.045; a visibly hazy day had
# AOT 0.53, and a thin-cloud day missed by the SCL mask had blue-over-canopy 0.096.
HAZE_AOT = {"caution": 0.25, "suspect": 0.40}
HAZE_BLUE_CANOPY = {"caution": 0.060, "suspect": 0.075}
RING_MIN_PIXELS = 50


def _median(a: np.ndarray) -> float | None:
    a = a[np.isfinite(a)]
    return round(float(np.median(a)), 4) if a.size else None


def observation_qa(stack: Any) -> dict[str, Any]:
    """Atmospheric quality and neighbor-ring statistics for one clear observation."""
    b = stack.bands
    valid = stack.valid
    ndvi = compute_index("ndvi", b)
    canopy = valid & (ndvi > 0.6)
    aot = _median(b["aot"][valid]) if "aot" in b else None
    blue = _median(b["blue"][canopy]) if canopy.sum() >= RING_MIN_PIXELS else None

    level, reasons = "none", []
    for lvl in ("suspect", "caution"):
        if aot is not None and aot > HAZE_AOT[lvl]:
            reasons.append(f"aerosol optical thickness {aot:.2f} (clear days ≈0.06–0.15)")
        if blue is not None and blue > HAZE_BLUE_CANOPY[lvl]:
            reasons.append(f"blue reflectance over canopy {blue:.3f} (clear ≈0.03)")
        if reasons:
            level = lvl
            break

    ring = stack.ring_mask & stack.clear_mask
    ring_n = int(ring.sum())
    ring_stats: dict[str, Any] = {"n": ring_n, "clear_fraction": round(ring_n / max(1, int(stack.ring_mask.sum())), 3)}
    if ring_n >= RING_MIN_PIXELS:
        for key in INDICES:
            ring_stats[key] = _median(compute_index(key, b)[ring])
    return {"aot": aot, "blue_canopy": blue, "haze": level, "haze_reasons": reasons, "ring": ring_stats}


def process_observation(store: Store, field: dict[str, Any], scene: Scene, **params: Any) -> dict[str, Any]:
    """Compute and store every index for `field` on `scene`. Idempotent per processing version."""
    p = {**DEFAULTS, **params}
    obs_id = store.obs_id(scene.id, scene.date)
    try:
        return store.get_observation(field["id"], obs_id)
    except KeyError:
        pass

    bands = (*REQUIRED_BANDS, "aot") if "aot" in scene.assets else REQUIRED_BANDS
    stack = read_field_stack(
        scene,
        field["geometry"],
        bands,
        inner_buffer_m=p["inner_buffer_m"],
        cloud_dilation_px=p["cloud_dilation_px"],
        min_clear_fraction=p["min_clear_fraction"],
    )
    if not stack.field_mask.any():
        raise ValueError(f"Field {field['id']} does not overlap any pixels in {scene.id}.")

    clear = stack.clear_fraction
    status = "clear" if clear >= p["min_clear_fraction"] else "cloudy"
    pngs: dict[str, bytes] = {}
    arrays: dict[str, np.ndarray] = {}
    stats: dict[str, Any] = {}

    tc_png, bounds = to_web_png(
        true_color(stack.bands["red"], stack.bands["green"], stack.bands["blue"]), stack.transform, stack.crs
    )
    pngs["truecolor"] = tc_png

    qa: dict[str, Any] = {}
    if status == "clear":
        valid = stack.valid
        qa = observation_qa(stack)
        arrays["valid"] = valid
        arrays["field"] = stack.field_mask
        for key, idx in INDICES.items():
            arr = compute_index(key, stack.bands)
            arr = np.where(valid, arr, np.nan).astype(np.float32)
            arrays[key] = arr
            stats[key] = field_stats(arr[valid])
            lo, hi = idx.display_range
            png, _ = to_web_png(colorize(arr, valid, idx.colormap, lo, hi), stack.transform, stack.crs)
            pngs[key] = png

    meta = {
        "id": obs_id,
        "field_id": field["id"],
        "field_geometry_hash": field["geometry_hash"],
        "scene_id": scene.id,
        "date": scene.date,
        "datetime": scene.datetime,
        "platform": scene.platform,
        "mgrs_tile": scene.mgrs_tile,
        "scene_cloud_cover": scene.cloud_cover,
        "field_clear_fraction": round(clear, 4),
        "field_cloud_fraction": round(stack.cloud_fraction, 4),
        "status": status,
        "stats": stats,
        "qa": qa,
        "bounds": bounds,
        "grid": {
            "crs": stack.crs,
            "transform": list(stack.transform)[:6],
            "shape": list(stack.shape),
            "resolution_m": 10,
        },
        "provenance": {
            "collection": "sentinel-2-l2a",
            "scene_href": scene.self_href,
            "scene_source": scene.source,
            "boa_offset_applied_upstream": scene.boa_offset_applied,
            "assets": {k: scene.assets[k]["href"] for k in (*sorted(stack.bands), "scl")},
            "processing_version": PROCESSING_VERSION,
            "app_version": __version__,
            "git_commit": git_commit(),
            "processed_at": now_iso(),
            "params": {**p, "scl_clear_classes": list(SCL_CLEAR), "scl_cloud_classes": list(SCL_CLOUDY)},
        },
        "processing_version": PROCESSING_VERSION,
    }
    try:
        store.write_observation(field["id"], obs_id, meta, arrays, pngs)
    except FileExistsError:
        return store.get_observation(field["id"], obs_id)
    return meta
