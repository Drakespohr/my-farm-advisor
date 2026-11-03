"""Disease pressure (0-100) per field: recent weather + nearby scouting reports + crop stage.

These are transparent, simplified rules built from published extension guidance on the
weather each disease favors. They are NOT the validated forecasting tools (e.g. Tarspotter,
Sporecaster); use them to decide where and when to scout, then follow local extension
thresholds before spraying.

For each disease:
  favorable day = a day with at least `wet_hours` hours that are both in the disease's
                  temperature range and wet (RH >= 90% or rain). Forecast days count half.
  weather  = favorable days in the window / days for "full" pressure (capped at 1), times
             `weather_cap` (diseases that need spores blown in, like southern rust, can't reach
             the top on weather alone).
  reports  = sum over nearby reports of severity x distance decay x age decay (capped at 1).
  pressure = 100 x (1 - (1 - weather) x (1 - reports)) x stage, so either signal raises it
             and both together push toward 100.
  stage    = from the field's latest canopy NDVI: < 0.4 (not established / harvested) 0.3,
             0.4-0.7 (before canopy closure) 0.6, >= 0.7 1.0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .weather import Hour

LEVELS = [(70, "High"), (40, "Moderate"), (0, "Low")]
SEVERITY = {"trace": 0.5, "moderate": 0.8, "severe": 1.0}
REPORT_DAYS = 21


@dataclass(frozen=True)
class Disease:
    key: str
    name: str
    crop: str  # "corn" or "soybean"
    t_min: float  # deg C
    t_max: float
    wet_hours: int  # wet hours in range needed for a "favorable day"
    window_days: int
    full_days: float
    weather_cap: float
    report_radius_km: float
    watch: str  # what to look for when scouting
    note: str = ""
    needs_closed_canopy: bool = False


DISEASES: dict[str, Disease] = {d.key: d for d in [
    Disease("southern_rust", "Southern rust", "corn", 24, 33, 6, 14, 6, 0.6, 250,
            "Small orange pustules, mostly on the upper leaf surface of upper leaves",
            "Spores blow in from the south; nearby confirmed reports matter as much as weather"),
    Disease("tar_spot", "Tar spot", "corn", 16, 24, 7, 30, 18, 0.85, 160,
            "Raised black specks that don't rub off, on leaves and husks"),
    Disease("gray_leaf_spot", "Gray leaf spot", "corn", 22, 30, 11, 14, 6, 0.85, 120,
            "Narrow rectangular tan-gray lesions bounded by leaf veins, starting low in the canopy"),
    Disease("nclb", "Northern corn leaf blight", "corn", 18, 27, 6, 14, 7, 0.85, 120,
            "Long cigar-shaped gray-green lesions, 1 to 6 inches"),
    Disease("white_mold", "White mold", "soybean", 15, 27, 8, 30, 12, 0.85, 100,
            "White cottony growth and bleached stems inside the canopy during and after flowering",
            "Only matters once the canopy closes", needs_closed_canopy=True),
    Disease("frogeye", "Frogeye leaf spot", "soybean", 25, 30, 8, 14, 6, 0.85, 100,
            "Small round gray spots with a reddish-purple border on upper leaves"),
]}


def crop_kind(crop: str | None) -> str | None:
    c = (crop or "").lower()
    if "corn" in c or "maize" in c:
        return "corn"
    if "soy" in c or "bean" in c:
        return "soybean"
    return None


def level(score: float) -> str:
    return next(name for cut, name in LEVELS if score >= cut)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2)))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    ang = math.degrees(math.atan2((lon2 - lon1) * math.cos(math.radians(lat1)), lat2 - lat1)) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]


def favorable_days(d: Disease, hours: list[Hour], end_iso: str) -> tuple[float, int, int]:
    """(weighted favorable days in window, observed favorable days, forecast favorable days)."""
    start = (datetime.fromisoformat(end_iso) - timedelta(days=d.window_days)).isoformat()
    per_day: dict[tuple[str, bool], int] = {}
    for h in hours:
        if h.temp_c is None or not (d.t_min <= h.temp_c <= d.t_max) or not h.wet:
            continue
        if not h.forecast and not (start <= h.time <= end_iso):
            continue
        k = (h.time[:10], h.forecast)
        per_day[k] = per_day.get(k, 0) + 1
    obs = sum(1 for (day, fc), n in per_day.items() if not fc and n >= d.wet_hours)
    fc = sum(1 for (day, f), n in per_day.items() if f and n >= d.wet_hours)
    return obs + 0.5 * fc, obs, fc


def report_pressure(d: Disease, lat: float, lon: float, reports: list[dict[str, Any]], today: date) -> tuple[float, list[dict[str, Any]]]:
    near = []
    total = 0.0
    for r in reports:
        if r.get("disease") != d.key:
            continue
        km = _km(lat, lon, r["lat"], r["lon"])
        age = (today - date.fromisoformat(r["observed"])).days
        if km > d.report_radius_km or not (0 <= age <= REPORT_DAYS):
            continue
        w = SEVERITY.get(r.get("severity", "moderate"), 0.8) * (1 - km / d.report_radius_km) * (1 - age / (REPORT_DAYS + 1))
        total += w
        near.append({**r, "km": round(km), "miles": round(km * 0.621), "age_days": age, "direction": _bearing(lat, lon, r["lat"], r["lon"])})
    near.sort(key=lambda r: (r["km"], r["age_days"]))
    return min(1.0, total), near


def field_pressure(
    *,
    lat: float,
    lon: float,
    crop: str | None,
    hours: list[Hour],
    reports: list[dict[str, Any]],
    canopy_ndvi: float | None,
    as_of: date | None = None,
) -> dict[str, Any]:
    kind = crop_kind(crop)
    today = as_of or datetime.now(timezone.utc).date()
    past = [h.time for h in hours if not h.forecast]
    end_iso = max(past) if past else datetime.combine(today, datetime.max.time(), timezone.utc).isoformat()
    established = canopy_ndvi is not None and canopy_ndvi >= 0.4
    results = []
    for d in DISEASES.values():
        if kind and d.crop != kind:
            continue
        weighted, obs_d, fc_d = favorable_days(d, hours, end_iso)
        weather = min(1.0, weighted / d.full_days) * d.weather_cap
        rep, near = report_pressure(d, lat, lon, reports, today)
        raw = 1 - (1 - weather) * (1 - rep)
        ndvi = canopy_ndvi or 0.0
        stage = 0.3 if ndvi < 0.4 else 0.6 if ndvi < 0.7 else 1.0
        if d.needs_closed_canopy and ndvi < 0.75:
            stage = min(stage, 0.4)
        score = round(100 * raw * stage)
        points = [f"{obs_d} of the last {d.window_days} days favored it ({d.wet_hours}+ wet hours at "
                  f"{round(d.t_min * 9 / 5 + 32)}–{round(d.t_max * 9 / 5 + 32)}°F)" + (f"; {fc_d} more in the forecast" if fc_d else "")]
        if near:
            n = near[0]
            points.append(f"Reported {n['miles']} mi {n['direction']}, {n['age_days']} days ago ({n['severity']})"
                          + (f"; {len(near) - 1} more nearby" if len(near) > 1 else ""))
        elif reports is not None:
            points.append("No reports nearby in the last 3 weeks")
        if not established:
            points.append("Crop not established or harvested: pressure discounted")
        elif stage < 1:
            points.append("Canopy not closed yet: pressure discounted")
        results.append({
            "key": d.key, "name": d.name, "crop": d.crop, "score": score, "level": level(score),
            "parts": {"weather": round(weather * 100), "reports": round(rep * 100)},
            "favorable_days": obs_d, "forecast_days": fc_d, "reports_nearby": len(near),
            "nearest_report": near[0] if near else None, "points": points, "watch": d.watch, "note": d.note,
        })
    results.sort(key=lambda r: -r["score"])
    top = results[0] if results else None
    return {
        "score": top["score"] if top else None,
        "level": top["level"] if top else None,
        "top": top["name"] if top else None,
        "crop_kind": kind,
        "diseases": results,
        "as_of": today.isoformat(),
        "method": "Simplified weather + nearby-report rules from extension guidance; scout before acting.",
    }
