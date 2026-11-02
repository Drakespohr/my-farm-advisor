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


def process_observation(store: Store, field: dict[str, Any], scene: Scene, **params: Any) -> dict[str, Any]:
    """Compute and store every index for `field` on `scene`. Idempotent per processing version."""
    p = {**DEFAULTS, **params}
    obs_id = store.obs_id(scene.id, scene.date)
    try:
        return store.get_observation(field["id"], obs_id)
    except KeyError:
        pass

    stack = read_field_stack(
        scene,
        field["geometry"],
        REQUIRED_BANDS,
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

    if status == "clear":
        valid = stack.valid
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
