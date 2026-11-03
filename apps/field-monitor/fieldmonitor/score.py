"""Field score (0-100): how this field is doing compared with cropland around it.

Built from good-quality (QA "ok") images during the growing season, i.e. when nearby
cropland has a real canopy (area median NDVI >= 0.35):

- Vigor vs. area (60%): the field's median NDVI as a percentile of NDVI across cropland
  within 2 km, averaged over the latest (up to) 3 in-season images.
- Evenness (25%): within-field variability; CV <= 0.05 scores 100, >= 0.25 scores 0.
- Keeping pace (15%): the field's NDVI change minus the area's change over the last
  ~2-6 weeks; matching the area scores 70, +0.05 ahead 100, 0.10 behind 0.

The season ends once nearby cropland falls below 75% of its own seasonal peak (maturity and
harvest across the area). After that, or with no in-season image for 45 days, the last
in-season score is shown as paused, so a harvested field isn't reported as failing.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .area import percentile_of
from .text import nice_date

IN_SEASON_AREA_NDVI = 0.35
SEASON_END_SHARE = 0.75  # area below 75% of its peak after the peak = maturity/harvest
RECENT_DAYS = 45
WEIGHTS = {"vigor": 0.60, "evenness": 0.25, "pace": 0.15}
LABELS = [(80, "Excellent"), (65, "Good"), (50, "Fair"), (0, "Needs attention")]


def _days(a: str, b: str) -> int:
    return int((np.datetime64(b) - np.datetime64(a)).astype(int))


def _usable(o: dict[str, Any]) -> bool:
    area = (o.get("qa") or {}).get("area") or {}
    return (
        o.get("status") == "clear"
        and o.get("qa_status") == "ok"
        and bool(area.get("ndvi_q"))
        and (o["stats"].get("ndvi") or {}).get("median") is not None
    )


def label(score: float) -> str:
    return next(name for cut, name in LABELS if score >= cut)


def field_score(observations: list[dict[str, Any]], as_of: str | None = None) -> dict[str, Any]:
    """`observations` must already be QA-classified. Returns score, parts, and plain bullets."""
    obs = [o for o in observations if _usable(o) and (as_of is None or o["date"] <= as_of)]
    area = [o["qa"]["area"]["ndvi_median"] for o in obs]
    peak_i = int(np.argmax(area)) if area else 0
    peak = area[peak_i] if area else 0.0
    season = [
        o for i, o in enumerate(obs)
        if area[i] >= IN_SEASON_AREA_NDVI and (i <= peak_i or area[i] >= SEASON_END_SHARE * peak)
    ]
    if not season:
        return {"score": None, "status": "no-data", "points": ["Not enough in-season images yet"]}
    latest_any = obs[-1]["date"]
    last = season[-1]
    # Season over: the area has passed its peak and dropped off since our last in-season image.
    season_over = obs[-1]["date"] > last["date"] and obs.index(last) >= peak_i and area[-1] < SEASON_END_SHARE * peak
    paused = season_over or _days(last["date"], as_of or latest_any) > RECENT_DAYS

    recent = [o for o in season if _days(o["date"], last["date"]) <= 21][-3:]
    pcts = [percentile_of(o["stats"]["ndvi"]["median"], o["qa"]["area"]["ndvi_q"]) for o in recent]
    vigor = float(np.mean(pcts))

    cvs = [o["stats"]["ndvi"]["cv"] for o in recent if o["stats"]["ndvi"].get("cv") is not None]
    cv = float(np.mean(cvs)) if cvs else None
    evenness = None if cv is None else float(np.clip((0.25 - cv) / 0.20 * 100, 0, 100))

    pace, pace_delta, pace_from = None, None, None
    earlier = [o for o in season if 14 <= _days(o["date"], last["date"]) <= 42]
    if earlier:
        ref = earlier[-1]
        field_change = last["stats"]["ndvi"]["median"] - ref["stats"]["ndvi"]["median"]
        area_change = last["qa"]["area"]["ndvi_median"] - ref["qa"]["area"]["ndvi_median"]
        pace_delta = field_change - area_change
        pace_from = ref["date"]
        pace = float(np.interp(pace_delta, [-0.10, 0.0, 0.05], [0, 70, 100]))

    parts = {"vigor": vigor, "evenness": evenness, "pace": pace}
    have = {k: v for k, v in parts.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in have)
    score = round(sum(WEIGHTS[k] * v for k, v in have.items()) / total_w)

    points = [f"Greener than {vigor:.0f}% of cropland within 1.2 miles"]
    if cv is not None:
        points.append("Even across the field" if cv <= 0.08 else "Some uneven areas" if cv <= 0.15 else "Patchy: check the Scout tab")
    if pace_delta is not None:
        points.append(
            "Keeping pace with the area" if abs(pace_delta) < 0.03
            else f"Gaining on the area since {nice_date(pace_from)}" if pace_delta > 0
            else f"Falling behind the area since {nice_date(pace_from)}"
        )
    if paused:
        points.insert(0, f"Season over: final score from {nice_date(last['date'])}" if season_over else f"Off-season: last score from {nice_date(last['date'])}")

    return {
        "score": score,
        "label": label(score),
        "status": "paused" if paused else "current",
        "as_of": last["date"],
        "parts": {k: (round(v) if v is not None else None) for k, v in parts.items()},
        "detail": {
            "field_ndvi": last["stats"]["ndvi"]["median"],
            "area_ndvi": last["qa"]["area"]["ndvi_median"],
            "cv": round(cv, 3) if cv is not None else None,
            "pace_delta": round(pace_delta, 3) if pace_delta is not None else None,
            "dates": [o["date"] for o in recent],
        },
        "points": points,
        "method": "60% crop health vs. cropland within 1.2 miles, 25% evenness, 15% keeping pace with the area",
    }
