"""Field-level statistics, management zones, scouting hotspots, and change detection."""

from __future__ import annotations

from typing import Any

import numpy as np
from affine import Affine
from rasterio.features import geometry_mask, shapes
from shapely.geometry import mapping, shape

from .raster import GRID, to_crs

M2_PER_HA = 10_000.0
HA_TO_AC = 2.471053814671653


def field_stats(values: np.ndarray) -> dict[str, float | int | None]:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {"n": 0, "mean": None, "median": None, "std": None, "cv": None, "p10": None, "p90": None, "min": None, "max": None}
    mean = float(v.mean())
    std = float(v.std())
    p10, med, p90 = (float(x) for x in np.percentile(v, [10, 50, 90]))
    return {
        "n": int(v.size),
        "mean": round(mean, 4),
        "median": round(med, 4),
        "std": round(std, 4),
        "cv": round(std / abs(mean), 4) if abs(mean) > 1e-6 else None,
        "p10": round(p10, 4),
        "p90": round(p90, 4),
        "min": round(float(v.min()), 4),
        "max": round(float(v.max()), 4),
    }


def _area_ha(pixels: int) -> float:
    return pixels * GRID * GRID / M2_PER_HA


def _majority_filter(classes: np.ndarray, valid: np.ndarray, n_classes: int) -> np.ndarray:
    """3x3 majority filter to remove salt-and-pepper speckle from zone maps."""
    h, w = classes.shape
    counts = np.zeros((n_classes + 1, h, w), dtype=np.int16)
    padded = np.pad(np.where(valid, classes, 0), 1)
    vpad = np.pad(valid, 1)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            c = padded[dy : dy + h, dx : dx + w]
            v = vpad[dy : dy + h, dx : dx + w]
            for k in range(1, n_classes + 1):
                counts[k] += (c == k) & v
    out = counts[1:].argmax(axis=0).astype(np.uint8) + 1
    return np.where(valid, out, 0).astype(np.uint8)


def _polygons_wgs84(mask_classes: np.ndarray, valid: np.ndarray, transform: Affine, crs: str) -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for geom, val in shapes(mask_classes.astype(np.uint8), mask=valid, connectivity=8, transform=transform):
        g = to_crs(shape(geom), crs, "EPSG:4326")
        feats.append({"type": "Feature", "properties": {"value": int(val)}, "geometry": mapping(g)})
    return feats


def management_zones(
    arr: np.ndarray, valid: np.ndarray, transform: Affine, crs: str, n_zones: int = 3
) -> dict[str, Any]:
    """Split the field into equal-area zones by index quantile (zone 1 = lowest).

    Zones are relative to this field, so they highlight within-field variability.
    Multi-date input (a mean of standardized index maps) gives far more stable zones
    than any single date.
    """
    n_zones = max(2, min(n_zones, 7))
    vals = arr[valid & np.isfinite(arr)]
    if vals.size < n_zones * 10:
        raise ValueError("Not enough clear pixels to build zones.")
    edges = np.percentile(vals, np.linspace(0, 100, n_zones + 1)[1:-1])
    ok = valid & np.isfinite(arr)
    classes = np.where(ok, np.digitize(arr, edges) + 1, 0).astype(np.uint8)
    classes = _majority_filter(classes, ok, n_zones)

    zones = []
    total = int(ok.sum())
    for k in range(1, n_zones + 1):
        m = classes == k
        st = field_stats(arr[m])
        ha = _area_ha(int(m.sum()))
        zones.append(
            {
                "zone": k,
                "area_ha": round(ha, 2),
                "area_ac": round(ha * HA_TO_AC, 2),
                "share": round(int(m.sum()) / total, 3) if total else 0,
                "mean": st["mean"],
            }
        )
    features = _polygons_wgs84(classes, classes > 0, transform, crs)
    for f in features:
        f["properties"] = {"zone": f["properties"]["value"]}
    p10, p90 = np.percentile(vals, [10, 90])
    return {
        "classes": classes,
        "spread_p10_p90": round(float(p90 - p10), 4),
        "breaks": [round(float(e), 4) for e in edges],
        "zones": zones,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


def hotspots(
    arr: np.ndarray,
    valid: np.ndarray,
    transform: Affine,
    crs: str,
    *,
    z: float = 1.5,
    min_delta: float = 0.05,
    min_area_ha: float = 0.2,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Contiguous low-value patches worth scouting, with a GPS point inside each.

    A pixel is "low" if it sits more than max(z * std, min_delta) below the field
    median. Patches smaller than `min_area_ha` are dropped as noise.
    """
    ok = valid & np.isfinite(arr)
    vals = arr[ok]
    if vals.size < 20:
        return []
    med, std = float(np.median(vals)), float(vals.std())
    threshold = med - max(z * std, min_delta)
    low = ok & (arr < threshold)
    if not low.any():
        return []

    out: list[dict[str, Any]] = []
    min_px = max(1, int(round(min_area_ha * M2_PER_HA / (GRID * GRID))))
    for geom, _ in shapes(low.astype(np.uint8), mask=low, connectivity=8, transform=transform):
        poly = shape(geom)
        n_px = int(round(poly.area / (GRID * GRID)))
        if n_px < min_px:
            continue
        mask = low & geometry_mask([geom], out_shape=arr.shape, transform=transform, invert=True)
        mean = float(np.nanmean(arr[mask])) if mask.any() else float("nan")
        pt = to_crs(poly.representative_point(), crs, "EPSG:4326")
        ha = _area_ha(n_px)
        out.append(
            {
                "lat": round(pt.y, 6),
                "lon": round(pt.x, 6),
                "area_ha": round(ha, 2),
                "area_ac": round(ha * HA_TO_AC, 2),
                "mean": round(mean, 4),
                "deficit": round(med - mean, 4),
                "geometry": mapping(to_crs(poly, crs, "EPSG:4326")),
                # Internal (stripped before JSON): exact pixels and UTM shape for diagnosis.
                "_mask": mask,
                "_poly": poly,
            }
        )
    out.sort(key=lambda h: h["area_ha"] * h["deficit"], reverse=True)
    for i, h in enumerate(out[:limit], start=1):
        h["rank"] = i
    return out[:limit]


def standardized_mean(arrays: list[np.ndarray], valids: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Average of per-date z-scores; the basis for multi-date (stable) zones."""
    zs = []
    for a, v in zip(arrays, valids):
        ok = v & np.isfinite(a)
        vals = a[ok]
        if vals.size < 20 or vals.std() < 1e-6:
            continue
        zs.append(np.where(ok, (a - vals.mean()) / vals.std(), np.nan))
    if not zs:
        raise ValueError("No usable dates for multi-date zones.")
    stack = np.stack(zs)
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(stack, axis=0)
        count = np.isfinite(stack).sum(axis=0)
    # Require a pixel be clear on at least half the dates.
    valid = count >= max(1, len(zs) // 2)
    return mean.astype(np.float32), valid


def change(
    before: np.ndarray, after: np.ndarray, valid: np.ndarray, decline_threshold: float = 0.1
) -> dict[str, Any]:
    ok = valid & np.isfinite(before) & np.isfinite(after)
    diff = np.where(ok, after - before, np.nan).astype(np.float32)
    n = int(ok.sum())
    declined = int((ok & (diff <= -decline_threshold)).sum())
    improved = int((ok & (diff >= decline_threshold)).sum())
    return {
        "diff": diff,
        "valid": ok,
        "stats": field_stats(diff[ok]),
        "declined_share": round(declined / n, 3) if n else None,
        "improved_share": round(improved / n, 3) if n else None,
        "declined_ha": round(_area_ha(declined), 2),
        "improved_ha": round(_area_ha(improved), 2),
        "threshold": decline_threshold,
    }


def public(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop internal keys (masks, shapes) so hotspots can be returned as JSON."""
    return [{k: v for k, v in h.items() if not k.startswith("_")} for h in spots]
