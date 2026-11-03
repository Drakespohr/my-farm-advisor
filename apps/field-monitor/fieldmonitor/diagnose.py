"""Explain a hotspot in plain language: what's different there and what might cause it.

Rule-based and deliberately hedged. Satellite data shows *where* and *how* a spot differs,
not *why*. The causes are ranked possibilities to check on the ground, not diagnoses.

Signals used for each hotspot (all compared with the field average on the same date):
- NDVI (crop health/biomass), NDRE (chlorophyll/nitrogen), NDMI (water in the crop),
  NDWI (standing water);
- shape: along the field edge, or a long straight strip;
- history: low in the same spot most of the season, or new since the last good image;
- crop stage: early (thin canopy) or after the seasonal peak.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from shapely.geometry.base import BaseGeometry

LABELS = {
    "ndvi": "Crop health (NDVI)",
    "ndre": "Nitrogen / chlorophyll (NDRE)",
    "ndmi": "Water in the crop (NDMI)",
    "ndwi": "Surface water (NDWI)",
}

EDGE_M = 20.0  # within this distance of the boundary counts as "along the edge"
LINEAR_ASPECT = 4.0
LINEAR_MAX_WIDTH_M = 40.0


def _avg(a: np.ndarray) -> float | None:
    v = a[np.isfinite(a)]
    return float(v.mean()) if v.size else None


def _values(arrays: dict[str, np.ndarray], mask: np.ndarray, valid: np.ndarray) -> dict[str, dict[str, float]]:
    out = {}
    for k in LABELS:
        if k not in arrays:
            continue
        spot, field = _avg(arrays[k][mask]), _avg(arrays[k][valid])
        if spot is None or field is None:
            continue
        out[k] = {"spot": round(spot, 3), "field": round(field, 3), "diff": round(spot - field, 3)}
    return out


def _shape(poly: BaseGeometry, field_utm: BaseGeometry | None) -> dict[str, Any]:
    edge = bool(field_utm is not None and poly.distance(field_utm.boundary) <= EDGE_M)
    rect = poly.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    sides = sorted({round(float(np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i])), 1) for i in range(3)})
    short, long_ = (sides[0], sides[-1]) if len(sides) > 1 else (sides[0], sides[0])
    linear = short > 0 and long_ / short >= LINEAR_ASPECT and short <= LINEAR_MAX_WIDTH_M
    return {"edge": edge, "linear": bool(linear), "length_m": round(long_), "width_m": round(short)}


def _history(mask: np.ndarray, history: list[tuple[str, dict[str, np.ndarray]]]) -> dict[str, Any]:
    """How this spot compared with the field on earlier good dates with established crop."""
    rows = []
    for date, arr in history:
        v = arr["valid"]
        m = mask & v
        if m.sum() < max(5, int(0.5 * mask.sum())):
            continue
        field = float(np.nanmedian(arr["ndvi"][v]))
        if field < 0.4:
            continue  # bare soil / emergence: differences mostly reflect soil color
        rows.append((date, float(np.nanmedian(arr["ndvi"][m])) - field))
    if not rows:
        return {"kind": "unknown", "dates": 0}
    low = [d for d, diff in rows if diff <= -0.05]
    last_date, last_diff = rows[-1]
    if len(rows) >= 3 and len(low) / len(rows) >= 0.6:
        return {"kind": "persistent", "dates": len(rows), "low_dates": len(low)}
    if last_diff > -0.03:
        return {"kind": "new", "dates": len(rows), "since": last_date}
    return {"kind": "recurring", "dates": len(rows), "low_dates": len(low)}


def explain(
    spot: dict[str, Any],
    arrays: dict[str, np.ndarray],
    field_utm: BaseGeometry | None,
    history: list[tuple[str, dict[str, np.ndarray]]],
    stage: str,
) -> dict[str, Any]:
    """`stage` is "early", "mid", or "late" (after the seasonal peak)."""
    mask, valid = spot["_mask"], arrays["valid"]
    vals = _values(arrays, mask, valid)
    shp = _shape(spot["_poly"], field_utm)
    hist = _history(mask, history)

    ndvi = vals.get("ndvi")
    ndre, ndmi, ndwi = vals.get("ndre"), vals.get("ndmi"), vals.get("ndwi")
    rel = lambda v: v["diff"] / abs(v["field"]) if v and abs(v["field"]) > 0.05 else 0.0  # noqa: E731

    causes: list[tuple[int, str, str]] = []
    # NDWI rises whenever canopy thins (less NIR), so only values above 0, actual open
    # water, count. Waterlogged soil under a canopy isn't visible from orbit.
    water = bool(ndwi and ndwi["spot"] > 0)
    if water:
        causes.append((6, "Standing water or waterlogged soil: drown-out, poor drainage, or a tile problem",
                       "Look for ponding, yellow or dead plants in low ground, and crusted soil"))
    if shp["linear"]:
        causes.append((6, "Straight-line strip: planter, sprayer, or fertilizer skip/overlap, or a tile line",
                       "Walk the strip; see if it follows equipment passes or rows"))
    if ndmi and ndmi["diff"] <= -0.06 and not water:
        causes.append((5, "Less water in the leaves: thinner or water-stressed crop (light soil, compaction, short roots, irrigation gap)",
                       "Dig to check root depth and soil moisture; look for rolled or gray leaves"))
    if stage == "early":
        causes.append((5, "Thin, late, or uneven stand: emergence trouble, crusting, planter skips, or seedling disease",
                       "Count plants in 1/1000 acre and compare with a good area"))
    if ndre and ndvi and stage != "early" and rel(ndre) <= 1.2 * rel(ndvi) and rel(ndre) < -0.05 and not (ndmi and ndmi["diff"] <= -0.06):
        causes.append((5, "Less chlorophyll than the rest: possible nitrogen shortage or N loss on wet ground",
                       "Look for yellowing in a V from the leaf tip on lower leaves; consider a tissue test"))
    if hist["kind"] == "persistent":
        causes.append((5, "Low in the same spot most of the season: likely soil (low ground, eroded knoll, drainage, compaction)",
                       "Compare with soil and yield maps; may deserve its own management zone"))
    elif hist["kind"] == "new" and stage != "late":
        causes.append((5, f"New since {hist['since']}: check for hail, herbicide injury or drift, insects, disease, or animals",
                       "Look at leaves for damage patterns and check whether neighboring fields show it too"))
    if shp["edge"]:
        causes.append((3, "Along the field edge: end-row compaction, headland traffic, tree shade, or wildlife",
                       "Compare soil firmness and plant size with a spot farther inside"))
    if stage == "late":
        causes.append((3, "Maturing or drying down earlier than the rest of the field",
                       "Normal near harvest; if early, check for stalk rot or lodging"))
    if not causes:
        causes.append((1, "General low vigor; the satellite can't narrow it down", "Scout the spot and compare with a healthy area"))
    causes.sort(key=lambda c: -c[0])

    if ndvi:
        pct = abs(ndvi["diff"]) / abs(ndvi["field"]) * 100 if abs(ndvi["field"]) > 0.05 else None
        summary = (f"NDVI here is {abs(ndvi['diff']):.2f} {'below' if ndvi['diff'] < 0 else 'above'} the field average "
                   f"({ndvi['spot']:.2f} vs {ndvi['field']:.2f}" + (f", about {pct:.0f}% {'lower' if ndvi['diff'] < 0 else 'higher'})" if pct else ")"))
    else:
        summary = "This spot reads lower than the rest of the field."

    pattern = []
    if shp["linear"]:
        pattern.append(f"Long, narrow strip (~{shp['length_m']} × {shp['width_m']} m)")
    if shp["edge"]:
        pattern.append("Along the field edge")
    if hist["kind"] == "persistent":
        pattern.append(f"Low on {hist['low_dates']} of {hist['dates']} earlier images")
    elif hist["kind"] == "new":
        pattern.append(f"Not low on {hist['since']}; new since then")
    if water:
        pattern.append("Water detected on the surface")

    return {
        "summary": summary,
        "values": [{"key": k, "label": LABELS[k], **v} for k, v in vals.items()],
        "pattern": pattern,
        "causes": [{"cause": c, "check": chk} for _, c, chk in causes[:3]],
        "history": hist,
        "shape": shp,
    }
