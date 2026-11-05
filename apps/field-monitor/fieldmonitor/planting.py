"""Planting window and stand count reminders, per field.

Inputs per field: its centroid (soil temperature and air temperature from the weather feed), its
crop, its state (for the planting calendar), and its log (planting, emergence, stand counts).

Rules (sources in SOURCES; anything not verified is TODO and never guessed):
- Corn and soybeans: the planting window opens when 2-inch soil temperature has been at or above
  50°F for the last 3 days and the next 2 forecast days stay at or above 50°F ("50°F and rising",
  UNL CropWatch / Iowa State). Some advise 60°F for soybeans; the threshold is configurable.
- Corn emergence: about 115 GDD (base 50°F, cap 86°F) after planting (Purdue, Nielsen).
- Stand count: due 14 to 21 days after emergence (product spec), repeats until one is logged, and
  stops 30 days after it went overdue or once harvest is logged (too late to inform replanting).
  Logged emergence (growth stage VE/VC or later) always wins over the estimate.
- Soybeans have no published GDD-to-emergence figure here (TODO), so without a logged emergence
  the stand count reminder starts 21 days after planting.
- Winter wheat is planted by calendar date, not soil temperature; the state dates are TODO.

Soil temperature comes from Open-Meteo's modeled 6 cm (2.4 in) soil temperature. Its free API is
for non-commercial use: set OPEN_METEO_API_KEY (commercial plan) before serving customers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

log = logging.getLogger(__name__)

TODO = "TODO: confirm with your state extension planting guide"

SOURCES = [
    {"label": "UNL CropWatch: corn and soybean planting considerations", "url": "https://cropwatch.unl.edu/2019/corn-and-soybean-planting-considerations/"},
    {"label": "Iowa State: soil temperature and planting conditions", "url": "https://crops.extension.iastate.edu/post/soil-temperature-and-planting-conditions-what-watch-spring"},
    {"label": "Purdue: the process of emergence in corn", "url": "https://ag.purdue.edu/news/department/agry/kernel-news/2023/05/2023-corn-emergence.html"},
]


@dataclass(frozen=True)
class CropRule:
    soil_f: Optional[float]                 # 2-inch soil temp that opens the window; None = calendar only
    season_months: tuple[int, int]          # months the window can open (inclusive)
    emergence_gdd: Optional[float] = None   # GDD (base 50/cap 86 °F) from planting to emergence
    stand_count_days: tuple[int, int] = (14, 21)  # after emergence
    fallback_stand_count_after_planting: Optional[int] = None  # when emergence can't be estimated
    todo: tuple[str, ...] = ()


RULES: dict[str, CropRule] = {
    "corn": CropRule(soil_f=50.0, season_months=(3, 6), emergence_gdd=115.0),
    "soybean": CropRule(soil_f=50.0, season_months=(4, 6), fallback_stand_count_after_planting=21,
                        todo=("Soybean days or GDD from planting to emergence", "Whether to use 50°F or 60°F for soybeans")),
    "wheat": CropRule(soil_f=None, season_months=(8, 11),
                      todo=("Winter wheat planting dates by state (e.g. Hessian fly-free date)",)),
}

# Calendar limits by state (two-letter code) and crop: {"earliest": "MM-DD", "latest": "MM-DD"}.
# All None until verified; the soil-temperature rule works without them.
STATE_CALENDAR: dict[str, dict[str, dict[str, Optional[str]]]] = {
    st: {"corn": {"earliest": None, "latest": None}, "soybean": {"earliest": None, "latest": None},
         "wheat": {"earliest": None, "latest": None}}
    for st in ("NE", "IA", "KS", "SD", "MN", "MO", "IL", "IN", "OH", "ND", "WI", "CO")
}

STAND_COUNT_EXPIRES_DAYS = 30  # stop the overdue reminder this long after it went overdue (or at harvest)
RISING_DAYS = 3     # observed days at or above the threshold
FORECAST_DAYS = 2   # forecast days that must stay at or above it


@dataclass
class Day:
    date: str
    soil_f: Optional[float]
    tmax_f: Optional[float]
    tmin_f: Optional[float]
    forecast: bool = False


def gdd(tmax_f: Optional[float], tmin_f: Optional[float]) -> float:
    """Corn growing degree days (base 50°F, both temps capped to 50–86°F)."""
    if tmax_f is None or tmin_f is None:
        return 0.0
    hi = min(max(tmax_f, 50.0), 86.0)
    lo = min(max(tmin_f, 50.0), 86.0)
    return (hi + lo) / 2 - 50.0


# ---- weather -----------------------------------------------------------------------------

class SoilWeather:
    """Daily 2-inch soil temperature and air max/min (past ~3 months + 7-day forecast) from Open-Meteo.

    Uses the commercial endpoint when OPEN_METEO_API_KEY is set. Responses are cached for 3 hours
    per ~1 km location. `http_get` is injectable for tests.
    """

    FREE_URL = "https://api.open-meteo.com/v1/forecast"
    PAID_URL = "https://customer-api.open-meteo.com/v1/forecast"
    TTL = 3 * 3600

    def __init__(self, api_key: Optional[str] = None, http_get: Callable[..., httpx.Response] = httpx.get) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPEN_METEO_API_KEY", "").strip() or None
        self.http_get = http_get
        self._cache: dict[tuple[float, float], tuple[float, list[Day]]] = {}
        self._lock = threading.Lock()

    def daily(self, lat: float, lon: float) -> list[Day]:
        key = (round(lat, 2), round(lon, 2))
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.TTL:
                return hit[1]
        params: dict[str, Any] = {
            "latitude": key[0], "longitude": key[1], "hourly": "soil_temperature_6cm",
            "daily": "temperature_2m_max,temperature_2m_min", "temperature_unit": "fahrenheit",
            "timezone": "auto", "past_days": 92, "forecast_days": 7,
        }
        if self.api_key:
            params["apikey"] = self.api_key
        r = self.http_get(self.PAID_URL if self.api_key else self.FREE_URL, params=params, timeout=10.0)
        r.raise_for_status()
        days = parse_open_meteo(r.json())
        with self._lock:
            self._cache[key] = (time.monotonic(), days)
        return days


def parse_open_meteo(data: dict[str, Any], today: Optional[date] = None) -> list[Day]:
    hourly = data.get("hourly") or {}
    soil: dict[str, list[float]] = {}
    for t, v in zip(hourly.get("time", []), hourly.get("soil_temperature_6cm", [])):
        if v is not None:
            soil.setdefault(t[:10], []).append(float(v))
    daily = data.get("daily") or {}
    today_s = (today or _local_today(data)).isoformat()
    out = []
    times = daily.get("time", [])
    tmaxs = list(daily.get("temperature_2m_max") or []) + [None] * len(times)
    tmins = list(daily.get("temperature_2m_min") or []) + [None] * len(times)
    for d, tmax, tmin in zip(times, tmaxs, tmins):
        vals = soil.get(d)
        out.append(Day(date=d, soil_f=round(sum(vals) / len(vals), 1) if vals else None, tmax_f=tmax, tmin_f=tmin,
                       forecast=d > today_s))
    return out


def _local_today(data: dict[str, Any]) -> date:
    offset = int(data.get("utc_offset_seconds") or 0)
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).date()


# ---- timing ------------------------------------------------------------------------------

def _entries(log_entries: list[dict[str, Any]], kind: str, year: int) -> list[dict[str, Any]]:
    return sorted((e for e in log_entries if e["kind"] == kind and e["occurred_at"].startswith(str(year))),
                  key=lambda e: e["occurred_at"])


def _emerged_entry(log_entries: list[dict[str, Any]], year: int, after: str) -> Optional[dict[str, Any]]:
    for e in _entries(log_entries, "growth_stage", year):
        st = (e["details"].get("stage") or "").upper()
        if e["occurred_at"][:10] >= after and (st in ("VE", "VC") or st.startswith(("V", "R"))):
            return e
    return None


def _md(d: date) -> str:
    return d.strftime("%m-%d")


def _nice(d: str | date) -> str:
    dd = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{dd:%b} {dd.day}"  # portable (no %-d on Windows)


def timing(crop: Optional[str], state: Optional[str], log_entries: list[dict[str, Any]], days: Optional[list[Day]],
           today: Optional[date] = None) -> dict[str, Any]:
    """What to do next on this field and when. Returns status, headline, priority (0-3), and tasks."""
    from .yields import crop_kind  # same crop names as the yield estimate

    today = today or date.today()
    kind = crop_kind(crop)
    out: dict[str, Any] = {"crop": kind, "state": state, "year": today.year, "status": "unknown", "priority": 0,
                           "headline": "", "tasks": [], "soil": None, "gdd_since_planting": None, "todo": []}
    if kind not in RULES:
        out.update(status="no_crop", headline="Set the crop (corn, soybeans, or wheat) for planting and stand count reminders.")
        return out
    rule = RULES[kind]
    out["todo"] = list(rule.todo)
    cal = (STATE_CALENDAR.get(state or "", {}) or {}).get(kind) or {}
    if not cal.get("earliest"):
        out["todo"].append(f"{state or 'Your state'} {kind} planting dates ({TODO})")

    observed = [d for d in (days or []) if not d.forecast]
    forecast = [d for d in (days or []) if d.forecast]
    if days:
        last = next((d for d in reversed(observed) if d.soil_f is not None), None)
        out["soil"] = {"today_f": last.soil_f if last else None, "date": last.date if last else None,
                       "threshold_f": rule.soil_f, "next_days": [{"date": d.date, "soil_f": d.soil_f} for d in forecast[:FORECAST_DAYS]]}

    year = today.year
    plantings = _entries(log_entries, "planting", year)
    if not plantings:
        return _before_planting(out, kind, rule, cal, observed, forecast, today)

    planted = plantings[-1]["occurred_at"][:10]
    out["planted_on"] = planted
    if days:
        since = [d for d in days if d.date >= planted]
        out["gdd_since_planting"] = round(sum(gdd(d.tmax_f, d.tmin_f) for d in since if not d.forecast), 0)

    counts = [e for e in _entries(log_entries, "stand_count", year) if e["occurred_at"][:10] >= planted]
    if counts:
        out.update(status="stand_counted", priority=0,
                   headline=f"Stand count logged {_nice(counts[-1]['occurred_at'][:10])}: "
                            f"{counts[-1]['details'].get('plants_per_ac', 0):,.0f} plants/ac.")
        return out

    emerged = _emerged_entry(log_entries, year, planted)
    emergence: Optional[str] = emerged["occurred_at"][:10] if emerged else None
    estimated = False
    if not emergence and rule.emergence_gdd and days:
        total = 0.0
        for d in days:
            if d.date < planted:
                continue
            total += gdd(d.tmax_f, d.tmin_f)
            if total >= rule.emergence_gdd:
                emergence, estimated = d.date, True
                break
    out["emergence"] = {"date": emergence, "estimated": estimated} if emergence else None

    if emergence:
        start = date.fromisoformat(emergence) + timedelta(days=rule.stand_count_days[0])
        late = date.fromisoformat(emergence) + timedelta(days=rule.stand_count_days[1])
        basis = f"{'expected ' if estimated else ''}emergence {_nice(emergence)}"
    elif rule.fallback_stand_count_after_planting:
        start = date.fromisoformat(planted) + timedelta(days=rule.fallback_stand_count_after_planting)
        late = start + timedelta(days=7)
        basis = f"planting {_nice(planted)} (log emergence for a better date)"
    else:
        out.update(status="planted", priority=0,
                   headline=f"Planted {_nice(planted)}. Waiting for emergence"
                            + (f" (about {rule.emergence_gdd:.0f} GDD; {out['gdd_since_planting']:.0f} so far)." if rule.emergence_gdd and out["gdd_since_planting"] is not None else "."))
        return out

    task = {"kind": "stand_count", "due": start.isoformat(), "late_after": late.isoformat()}
    harvested = _entries(log_entries, "harvest", year)
    if harvested or today > late + timedelta(days=STAND_COUNT_EXPIRES_DAYS):
        # Counts only help replant decisions early on; stop reminding once that chance has passed.
        out.update(status="stand_count_missed", priority=0, headline="No stand count was logged this season.")
        return out
    if today < start:
        emerged_now = bool(emergence) and date.fromisoformat(emergence) <= today
        out.update(status="emerging" if emerged_now else "planted", priority=0,
                   headline=f"Stand count due {_nice(start)} (2–3 weeks after {basis}).")
        task["state"] = "upcoming"
    elif today <= late:
        out.update(status="stand_count_due", priority=2,
                   headline=f"Stand count due now (2–3 weeks after {basis}). Log it to clear this reminder.")
        task["state"] = "due"
    else:
        out.update(status="stand_count_overdue", priority=3,
                   headline=f"Stand count overdue since {_nice(late)}. Count now to judge replant decisions.")
        task["state"] = "overdue"
    out["tasks"].append(task)
    return out


def _before_planting(out: dict[str, Any], kind: str, rule: CropRule, cal: dict[str, Optional[str]], observed: list[Day],
                     forecast: list[Day], today: date) -> dict[str, Any]:
    m0, m1 = rule.season_months
    crop_word = {"corn": "Corn", "soybean": "Soybean", "wheat": "Wheat"}[kind]
    if not (m0 <= today.month <= m1):
        if rule.soil_f is None:
            out.update(status="off_season", headline=f"{crop_word} planting is by calendar date this fall ({TODO}).")
        else:
            out.update(status="off_season",
                       headline=f"{crop_word} planting window opens in spring once 2-inch soil holds {rule.soil_f:.0f}°F.")
        return out
    if cal.get("earliest") and _md(today) < cal["earliest"]:
        out.update(status="too_early", headline=f"Before the earliest recommended {crop_word.lower()} planting date ({cal['earliest']}).")
        return out
    if cal.get("latest") and _md(today) > cal["latest"]:
        out.update(status="late", priority=2, headline=f"Past the recommended {crop_word.lower()} planting date ({cal['latest']}); plant as soon as conditions allow.")
        out["tasks"].append({"kind": "planting_window", "state": "late"})
        return out
    if rule.soil_f is None:
        out.update(status="calendar", priority=1, headline=f"{crop_word} planting season: follow your state's dates ({TODO}).")
        return out
    recent = [d.soil_f for d in observed[-RISING_DAYS:] if d.soil_f is not None]
    ahead = [d.soil_f for d in forecast[:FORECAST_DAYS] if d.soil_f is not None]
    thr = rule.soil_f
    if len(recent) < RISING_DAYS:
        out.update(status="no_soil_data", headline="Soil temperature unavailable right now; check before planting.")
        return out
    now_f = recent[-1]
    if all(v >= thr for v in recent) and ahead and all(v >= thr for v in ahead):
        out.update(status="window_open", priority=2,
                   headline=f"Planting window open: 2-inch soil {now_f:.0f}°F and holding above {thr:.0f}°F.")
        out["tasks"].append({"kind": "planting_window", "state": "open"})
    elif now_f >= thr and ahead and min(ahead) < thr:
        out.update(status="cold_snap", priority=1,
                   headline=f"Soil is {now_f:.0f}°F but a cold snap is coming ({min(ahead):.0f}°F). Hold off planting.")
    else:
        out.update(status="waiting", priority=0, headline=f"Soil {now_f:.0f}°F at 2 inches; planting window opens at {thr:.0f}°F.")
    return out
