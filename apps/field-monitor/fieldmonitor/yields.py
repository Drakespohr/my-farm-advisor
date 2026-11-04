"""Yield estimates for corn, soybeans, and wheat.

Three methods, best first. Every estimate says which one it used and how sure it is.

1. Field count (yield components): the extension methods, counted in the field.
   - Corn:     bu/ac = ears/ac x kernels/ear / kernels per bushel (default 90,000; 80,000 with
               good grain fill, up to ~100,000+ under stress). Purdue/Nielsen: about +/-20 bu/ac.
   - Soybeans: bu/ac = plants/ac x pods/plant x seeds/pod / seeds per lb / 60 (Ohio State:
               2.5 seeds/pod, 3,000 seeds/lb; 3,500 with late-season stress).
   - Wheat:    bu/ac = heads/ft2 x kernels/head x 43,560 / kernels per lb / 60 (same math as the
               Oklahoma State / Texas A&M "x 726" form; 13,000-16,000 kernels/lb is typical).
2. Satellite, calibrated on your farm: once you've recorded harvest yields for 3+ field-years of a
   crop (with an expected yield for those years), we fit how strongly yield follows canopy on your
   farm, and report the leave-one-out error.
3. Satellite, uncalibrated: your expected yield (e.g. APH) x how green the field was at peak
   canopy compared with cropland within 2 km on the same images (Sentinel-2 NDVI, QA "ok" only).
   Comparing with neighbors on the same date cancels most weather, haze, and calendar effects.
   NDVI saturates in dense canopies, so this understates differences between fields; treat it as
   a rough guide (we show +/-20%) until it's calibrated.

Research notes (why not one "formula"): published field-scale Sentinel-2 models explain roughly
25-60% of yield variation without local calibration (e.g. red-edge CIre R2 0.44 for corn,
DVI R2 0.25 for soybean, Remote Sens. 2021, 13, 872; GNDVI R2 0.48 for corn, Remote Sens.
2019, 11, 2873), and Landsat-based SCYM explains ~30-40% at pixel level. Accuracy comes from calibrating
on the farm's own yields, which is why method 2 exists.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Optional

from .disease import crop_kind as _disease_crop_kind

SQFT_PER_ACRE = 43_560
IN_SEASON_AREA_NDVI = 0.35
PEAK_SHARE = 0.90           # images where nearby cropland is within 90% of its season peak
UNCALIBRATED_BAND = 0.20    # +/-20% shown for uncalibrated satellite estimates (our assumption)
MIN_CALIBRATION_POINTS = 3
COUNT_FRESH_DAYS = 45       # a field count newer than this leads the estimate

DEFAULTS = {
    "corn": {"kernels_per_bu": 90_000, "row_in": 30, "row_ft": 17.42},
    "soybean": {"seeds_per_pod": 2.5, "seeds_per_lb": 3_000, "row_in": 30, "row_ft": 17.42},
    "wheat": {"kernels_per_head": 22, "kernels_per_lb": 15_000, "row_in": 7.5, "row_ft": 3.0},
}
# How far off a count can be. Corn per Purdue (Nielsen); soybean and wheat guides only say the
# methods are "rough", so +/-15% is our stated assumption.
COUNT_BAND = {"corn": ("abs", 20.0), "soybean": ("rel", 0.15), "wheat": ("rel", 0.15)}

SOURCES = [
    {"label": "Purdue: Estimating corn grain yield prior to harvest", "url": "https://extension.entm.purdue.edu/newsletters/pestandcrop/article/estimating-corn-grain-yield-prior-to-harvest-2"},
    {"label": "K-State: Estimating corn yield potential", "url": "https://eupdate.agronomy.ksu.edu/article/estimating-corn-yield-potential-293"},
    {"label": "Ohio State: Estimating soybean yield", "url": "https://agcrops.osu.edu/newsletter/corn-newsletter/2018-24/estimating-soybean-yield"},
    {"label": "Oklahoma State: Estimating wheat grain yield potential", "url": "https://extension.okstate.edu/fact-sheets/estimating-wheat-grain-yield-potential-2.html"},
    {"label": "UNL NebGuide G1429: Estimating winter wheat yields", "url": "https://extensionpubs.unl.edu/publication/g1429/na/html/view"},
    {"label": "Sentinel-2/Planet/WorldView corn & soybean yield variability (Remote Sens. 2021)", "url": "https://dx.doi.org/10.3390/rs13050872"},
]


def crop_kind(crop: Optional[str]) -> Optional[str]:
    c = (crop or "").lower()
    if "wheat" in c:
        return "wheat"
    return _disease_crop_kind(crop)


def _acre_fraction(row_ft: float, row_in: float) -> float:
    """Acres covered by `row_ft` feet of one row at `row_in`-inch spacing."""
    if row_ft <= 0 or row_in <= 0:
        raise ValueError("Row length and row spacing must be positive")
    return row_ft * (row_in / 12) / SQFT_PER_ACRE


def _avg(values: Any, name: str) -> float:
    vals = [float(v) for v in (values if isinstance(values, (list, tuple)) else [values]) if v is not None]
    if not vals or any(v < 0 for v in vals):
        raise ValueError(f"Enter at least one {name} (no negatives)")
    return sum(vals) / len(vals)


def corn_count(*, ears: Any, row_ft: float = 17.42, row_in: float = 30, kernel_rows: Any,
               kernels_per_row: Any, kernels_per_bu: float = 90_000) -> dict[str, Any]:
    """`ears`: ears counted in `row_ft` of row (one value per spot). Kernel rows and kernels per row
    are averaged over the ears you pulled."""
    if not 65_000 <= kernels_per_bu <= 110_000:
        raise ValueError("Kernels per bushel is usually 65,000-110,000 (90,000 is the common default)")
    ears_ac = _avg(ears, "ear count") / _acre_fraction(row_ft, row_in)
    kpe = _avg(kernel_rows, "kernel-row count") * _avg(kernels_per_row, "kernels-per-row count")
    bu = ears_ac * kpe / kernels_per_bu
    return {"bu_ac": round(bu, 1), "parts": {"ears_per_ac": round(ears_ac), "kernels_per_ear": round(kpe),
            "kernels_per_bu": kernels_per_bu}}


def soybean_count(*, plants: Any, row_ft: float = 17.42, row_in: float = 30, pods_per_plant: Any,
                  seeds_per_pod: float = 2.5, seeds_per_lb: float = 3_000) -> dict[str, Any]:
    if not 1.5 <= seeds_per_pod <= 4:
        raise ValueError("Seeds per pod is usually 2-3 (2.5 is typical)")
    if not 1_800 <= seeds_per_lb <= 5_000:
        raise ValueError("Seeds per pound is usually 2,500-3,500 (3,000 is typical)")
    plants_ac = _avg(plants, "plant count") / _acre_fraction(row_ft, row_in)
    pods = _avg(pods_per_plant, "pods-per-plant count")
    bu = plants_ac * pods * seeds_per_pod / seeds_per_lb / 60
    return {"bu_ac": round(bu, 1), "parts": {"plants_per_ac": round(plants_ac), "pods_per_plant": round(pods, 1),
            "seeds_per_pod": seeds_per_pod, "seeds_per_lb": seeds_per_lb}}


def wheat_count(*, heads: Any, row_ft: float = 3.0, row_in: float = 7.5, kernels_per_head: Any = 22,
                kernels_per_lb: float = 15_000) -> dict[str, Any]:
    if not 9_000 <= kernels_per_lb <= 22_000:
        raise ValueError("Kernels per pound is usually 13,000-16,000")
    heads_ft2 = _avg(heads, "head count") / (row_ft * row_in / 12)
    kph = _avg(kernels_per_head, "kernels-per-head count")
    bu = heads_ft2 * kph * SQFT_PER_ACRE / kernels_per_lb / 60
    return {"bu_ac": round(bu, 1), "parts": {"heads_per_ft2": round(heads_ft2, 1), "kernels_per_head": round(kph, 1),
            "kernels_per_lb": kernels_per_lb}}


COUNTERS = {"corn": corn_count, "soybean": soybean_count, "wheat": wheat_count}


def count_estimate(crop: str, inputs: dict[str, Any]) -> dict[str, Any]:
    kind = crop_kind(crop)
    if kind not in COUNTERS:
        raise ValueError("Field counts work for corn, soybeans, and wheat")
    allowed = set(COUNTERS[kind].__kwdefaults__ or {}) | {"ears", "kernel_rows", "kernels_per_row", "plants",
                                                         "pods_per_plant", "heads"}
    extra = set(inputs) - allowed
    if extra:
        raise ValueError(f"Unexpected inputs for {kind}: {', '.join(sorted(extra))}")
    try:
        out = COUNTERS[kind](**inputs)
    except TypeError as exc:
        raise ValueError(f"Missing input for {kind}: {exc}") from None
    mode, band = COUNT_BAND[kind]
    half = band if mode == "abs" else out["bu_ac"] * band
    return {**out, "crop": kind, "low": round(max(0.0, out["bu_ac"] - half)), "high": round(out["bu_ac"] + half)}


# ---- satellite ---------------------------------------------------------------------------

def _usable(o: dict[str, Any]) -> bool:
    area = (o.get("qa") or {}).get("area") or {}
    return (o.get("status") == "clear" and o.get("qa_status") == "ok" and area.get("ndvi_median") is not None
            and ((o.get("stats") or {}).get("ndvi") or {}).get("median") is not None)


def season_years(observations: list[dict[str, Any]]) -> list[int]:
    return sorted({int(o["date"][:4]) for o in observations if _usable(o)}, reverse=True)


def canopy_signal(observations: list[dict[str, Any]], year: int) -> dict[str, Any]:
    """How green the field was at peak canopy vs. cropland within 2 km (same images).

    `observations` must already be QA-classified. Returns rel (field / area NDVI), the images used,
    and the stage: 'none' (no in-season images), 'early' (area still greening up), or 'peak'.
    """
    obs = [o for o in observations if _usable(o) and o["date"].startswith(str(year))
           and o["qa"]["area"]["ndvi_median"] >= IN_SEASON_AREA_NDVI]
    if not obs:
        return {"stage": "none", "year": year, "images": 0}
    area = [o["qa"]["area"]["ndvi_median"] for o in obs]
    peak = max(area)
    window = [o for o, a in zip(obs, area) if a >= PEAK_SHARE * peak]
    peak_i = area.index(peak)
    passed = any(a < PEAK_SHARE * peak for a in area[peak_i + 1:])  # area declined after its peak
    f = sum(o["stats"]["ndvi"]["median"] for o in window) / len(window)
    a = sum(o["qa"]["area"]["ndvi_median"] for o in window) / len(window)
    return {
        "stage": "peak" if passed else "early",
        "year": year,
        "images": len(window),
        "window": [window[0]["date"], window[-1]["date"]],
        "field_ndvi": round(f, 3),
        "area_ndvi": round(a, 3),
        "rel": round(f / a, 4) if a > 0 else None,
    }


def calibrate(points: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Fit yield = expected x (1 + k x (rel - 1)) on your farm's field-years.

    k = 1 means yield moves one-for-one with relative greenness; k > 1 (typical, since NDVI
    saturates) means a field 5% greener than its neighbors yields more than 5% above expected.
    Needs MIN_CALIBRATION_POINTS points with harvest, expected yield, and a canopy signal.
    """
    pts = [p for p in points if p.get("rel") and p.get("expected") and p.get("harvest") is not None]
    if len(pts) < MIN_CALIBRATION_POINTS:
        return None

    def fit(sample: list[dict[str, Any]]) -> Optional[float]:
        num = sum(p["expected"] * (p["rel"] - 1) * (p["harvest"] - p["expected"]) for p in sample)
        den = sum((p["expected"] * (p["rel"] - 1)) ** 2 for p in sample)
        return None if den <= 1e-9 else min(max(num / den, 0.0), 6.0)

    k = fit(pts)
    if k is None:
        return None  # every field matched its neighbors exactly: nothing to learn from
    errs = []
    for i, p in enumerate(pts):  # leave-one-out error = what to expect on a new field-year
        ki = fit(pts[:i] + pts[i + 1:])
        if ki is None:
            continue
        errs.append(p["harvest"] - p["expected"] * (1 + ki * (p["rel"] - 1)))
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else None
    return {"k": round(k, 2), "n": len(pts), "loo_rmse_bu": round(rmse, 1) if rmse is not None else None}


def _latest(records: list[dict[str, Any]], kind: str, year: int | None = None) -> Optional[dict[str, Any]]:
    rs = [r for r in records if r["kind"] == kind and (year is None or r["year"] == year)]
    return rs[-1] if rs else None


def expected_for(records: list[dict[str, Any]], year: int) -> Optional[dict[str, Any]]:
    """Expected yield for the year, else the most recent one entered (APH rarely changes much)."""
    return _latest(records, "expected", year) or _latest(records, "expected")


def estimate(field: dict[str, Any], observations: list[dict[str, Any]], records: list[dict[str, Any]],
             year: int, calibration: Optional[dict[str, Any]] = None, today: Optional[date] = None) -> dict[str, Any]:
    """The field's yield estimate for `year`, with the method, range, and what would improve it."""
    today = today or date.today()
    kind = crop_kind(field.get("crop"))
    sig = canopy_signal(observations, year)
    exp = expected_for(records, year)
    harvest = _latest(records, "harvest", year)
    count = _latest(records, "count", year)
    out: dict[str, Any] = {"year": year, "crop": kind, "unit": "bu/ac", "signal": sig, "expected": exp,
                           "harvest": harvest, "count": count, "calibration": calibration, "next_steps": []}

    if harvest:
        out.update(method="harvest", bu_ac=harvest["bu_ac"], low=harvest["bu_ac"], high=harvest["bu_ac"],
                   confidence="measured", label=f"Harvested: {harvest['bu_ac']:g} bu/ac")
        return out
    if kind is None:
        out.update(method=None, bu_ac=None, label="Set the crop (corn, soybeans, or wheat) to estimate yield")
        out["next_steps"].append("set_crop")
        return out

    sat: Optional[dict[str, Any]] = None
    if sig.get("rel") and exp:
        k = calibration["k"] if calibration else 1.0
        bu = exp["bu_ac"] * (1 + k * (sig["rel"] - 1))
        if calibration and calibration.get("loo_rmse_bu") is not None:
            half, conf = calibration["loo_rmse_bu"], "calibrated"
        else:
            half, conf = bu * UNCALIBRATED_BAND, "rough"
        if sig["stage"] == "early":
            half *= 1.5  # before peak canopy the picture can still change a lot
        sat = {"bu_ac": round(bu), "low": round(max(0, bu - half)), "high": round(bu + half), "confidence": conf,
               "method": "satellite_calibrated" if calibration else "satellite"}
    out["satellite"] = sat

    count_fresh = count and (today - date.fromisoformat(count["date"])).days <= COUNT_FRESH_DAYS
    if count and (count_fresh or not sat):
        out.update(method="count", bu_ac=count["bu_ac"], low=count["low"], high=count["high"], confidence="field count",
                   label=f"{count['bu_ac']:g} bu/ac from your field count ({count['date']})")
    elif sat:
        stage = " so far (before peak canopy)" if sig["stage"] == "early" else ""
        out.update(method=sat["method"], bu_ac=sat["bu_ac"], low=sat["low"], high=sat["high"], confidence=sat["confidence"],
                   label=f"About {sat['bu_ac']} bu/ac ({sat['low']}–{sat['high']}){stage}")
    elif sig.get("rel"):
        pct = (sig["rel"] - 1) * 100
        out.update(method="relative", bu_ac=None, confidence="relative",
                   label=f"Canopy {abs(pct):.0f}% {'greener' if pct >= 0 else 'less green'} than cropland nearby at peak"
                   if abs(pct) >= 1 else "Canopy on par with cropland nearby at peak")
    else:
        out.update(method=None, bu_ac=None,
                   label="Waiting for in-season images" if sig["stage"] == "none" else "Not enough images yet")

    if not exp:
        out["next_steps"].append("set_expected")
    if not count_fresh and sig["stage"] != "none":
        out["next_steps"].append("count")
    if not calibration:
        out["next_steps"].append("record_harvests")
    return out


def farm_calibration_points(items: list[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]],
                            kind: str) -> list[dict[str, Any]]:
    """Calibration points from (field, classified observations, yield records) for one crop."""
    pts = []
    for field, obs, records in items:
        for h in (r for r in records if r["kind"] == "harvest"):
            if crop_kind(h.get("crop") or field.get("crop")) != kind:
                continue
            exp = expected_for(records, h["year"])
            sig = canopy_signal(obs, h["year"])
            if exp and sig.get("rel") and sig["stage"] == "peak":
                pts.append({"field_id": field["id"], "year": h["year"], "harvest": h["bu_ac"],
                            "expected": exp["bu_ac"], "rel": sig["rel"]})
    return pts
