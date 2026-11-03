"""Hourly weather near a field: temperature, dew point, humidity, rain, and a "leaf wet" flag.

Providers (all return the same `Hour` records):

- ``nws`` (default, US): National Weather Service API, public domain. Recent station
  observations (about a week) plus the hourly forecast. Observations are stored locally
  as they arrive, so a 30-day history builds up on its own.
- ``open-meteo`` (opt-in via FIELD_MONITOR_WEATHER=open-meteo): 30 days of history plus a
  7-day forecast in one call. Its free API is for NON-COMMERCIAL use only; a commercial
  deployment needs an Open-Meteo subscription.
- ``isd``: NOAA Global Hourly (ISD) on AWS Open Data, public domain. Used automatically
  for past dates (backtesting earlier seasons). The AWS mirror currently ends in 2025.

Leaf wetness isn't measured at weather stations, so an hour counts as "wet" when
RH >= 90% or it rained (>= 0.25 mm).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from . import __version__

log = logging.getLogger(__name__)

UA = {"User-Agent": f"overacre-field-monitor/{__version__} (github.com/drakespohr/my-farm-advisor)", "Accept": "application/geo+json"}
ISD_HISTORY = "https://noaa-isd-pds.s3.amazonaws.com/isd-history.csv"
ISD_YEAR = "https://noaa-global-hourly-pds.s3.amazonaws.com/{year}/{station}.csv"
TTL_S = 3 * 3600


@dataclass
class Hour:
    time: str  # ISO 8601 UTC, top of the hour
    temp_c: float | None
    dew_c: float | None
    rh: float | None
    precip_mm: float | None
    forecast: bool = False

    @property
    def wet(self) -> bool:
        if self.precip_mm is not None and self.precip_mm >= 0.25:
            return True
        return self.rh is not None and self.rh >= 90


def rh_from(temp_c: float, dew_c: float) -> float:
    """Relative humidity (%) from temperature and dew point (Magnus formula)."""
    a, b = 17.625, 243.04
    return max(0.0, min(100.0, 100 * math.exp(a * dew_c / (b + dew_c) - a * temp_c / (b + temp_c))))


def _hour_key(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0).isoformat()


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2)))


class WeatherStore:
    """Local cache: HTTP responses with TTL and accumulated hourly observations per station."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(
                "CREATE TABLE IF NOT EXISTS obs (station TEXT, time TEXT, temp_c REAL, dew_c REAL, rh REAL, precip_mm REAL, PRIMARY KEY (station, time));"
                "CREATE TABLE IF NOT EXISTS http (url TEXT PRIMARY KEY, fetched REAL, body TEXT);"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, client: httpx.Client, url: str, ttl: float = TTL_S) -> str:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT fetched, body FROM http WHERE url=?", (url,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return row[1]
        r = client.get(url)
        r.raise_for_status()
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO http (url, fetched, body) VALUES (?,?,?)", (url, time.time(), r.text))
        return r.text

    def save_obs(self, station: str, hours: list[Hour]) -> None:
        with self._lock, self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO obs VALUES (?,?,?,?,?,?)",
                [(station, h.time, h.temp_c, h.dew_c, h.rh, h.precip_mm) for h in hours],
            )

    def load_obs(self, station: str, start: str, end: str) -> list[Hour]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT time, temp_c, dew_c, rh, precip_mm FROM obs WHERE station=? AND time>=? AND time<=? ORDER BY time",
                (station, start, end),
            ).fetchall()
        return [Hour(*r) for r in rows]


# ---- National Weather Service ------------------------------------------------------
def _nws_val(p: dict[str, Any], key: str) -> float | None:
    v = (p.get(key) or {}).get("value")
    return float(v) if v is not None else None


def parse_nws_observations(data: dict[str, Any]) -> list[Hour]:
    by_hour: dict[str, Hour] = {}
    for f in data.get("features", []):
        p = f.get("properties", {})
        if not p.get("timestamp"):
            continue
        t, d = _nws_val(p, "temperature"), _nws_val(p, "dewpoint")
        rh = _nws_val(p, "relativeHumidity")
        if rh is None and t is not None and d is not None:
            rh = rh_from(t, d)
        rain = _nws_val(p, "precipitationLastHour")  # mm
        key = _hour_key(p["timestamp"])
        if key not in by_hour or by_hour[key].temp_c is None:
            by_hour[key] = Hour(key, t, d, rh, rain)
    return sorted(by_hour.values(), key=lambda h: h.time)


def parse_nws_forecast(data: dict[str, Any]) -> list[Hour]:
    out = []
    for p in data.get("properties", {}).get("periods", []):
        temp = p.get("temperature")
        if isinstance(temp, dict):
            temp = temp.get("value")
        if temp is not None and p.get("temperatureUnit", "F") == "F":
            temp = (float(temp) - 32) * 5 / 9
        dew = _nws_val(p, "dewpoint")
        rh = _nws_val(p, "relativeHumidity")
        pop = _nws_val(p, "probabilityOfPrecipitation") or 0
        out.append(Hour(_hour_key(p["startTime"]), temp, dew, rh, 1.0 if pop >= 60 else 0.0, forecast=True))
    return out


def nws_hours(lat: float, lon: float, ws: WeatherStore, days: int) -> tuple[list[Hour], dict[str, Any]]:
    with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as c:
        point = json.loads(ws.get(c, f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", ttl=30 * 86400))["properties"]
        stations = json.loads(ws.get(c, point["observationStations"], ttl=30 * 86400))["features"]
        station = stations[0]["properties"]["stationIdentifier"]
        obs = parse_nws_observations(json.loads(ws.get(c, f"https://api.weather.gov/stations/{station}/observations?limit=500")))
        ws.save_obs(f"nws:{station}", obs)
        try:
            fc = parse_nws_forecast(json.loads(ws.get(c, point["forecastHourly"])))
        except (httpx.HTTPError, KeyError, ValueError) as exc:  # forecasts fail more often than observations
            log.warning("NWS forecast unavailable: %s", exc)
            fc = []
    now = datetime.now(timezone.utc)
    hist = ws.load_obs(f"nws:{station}", (now - timedelta(days=days)).isoformat(), now.isoformat())
    return hist + [h for h in fc if h.time > now.isoformat()], {"provider": "nws", "station": station}


# ---- Open-Meteo (opt-in; non-commercial free tier) -------------------------------------
def parse_open_meteo(data: dict[str, Any], now_iso: str) -> list[Hour]:
    h = data.get("hourly", {})
    out = []
    for i, t in enumerate(h.get("time", [])):
        iso = _hour_key(t + "+00:00" if len(t) == 16 else t)
        out.append(Hour(iso, h["temperature_2m"][i], h["dew_point_2m"][i], h["relative_humidity_2m"][i],
                        h["precipitation"][i], forecast=iso > now_iso))
    return out


def open_meteo_hours(lat: float, lon: float, ws: WeatherStore, days: int) -> tuple[list[Hour], dict[str, Any]]:
    url = ("https://api.open-meteo.com/v1/forecast?latitude={:.4f}&longitude={:.4f}&timezone=UTC&past_days={}&forecast_days=7"
           "&hourly=temperature_2m,dew_point_2m,relative_humidity_2m,precipitation").format(lat, lon, min(days, 92))
    with httpx.Client(headers=UA, timeout=20) as c:
        data = json.loads(ws.get(c, url))
    return parse_open_meteo(data, datetime.now(timezone.utc).isoformat()), {"provider": "open-meteo"}


# ---- NOAA ISD on AWS (history / backtesting) ----------------------------------------
def _isd_value(field: str, scale: float = 10.0) -> float | None:
    try:
        v, q = field.split(",")[:2]
    except ValueError:
        return None
    if v.lstrip("+-").strip("9") == "" or q in ("3", "7"):  # 9999 = missing; 3/7 = erroneous
        return None
    return int(v) / scale


def parse_isd_csv(text: str, start: str, end: str) -> list[Hour]:
    by_hour: dict[str, Hour] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("REPORT_TYPE", "").strip() not in ("FM-15", "FM-16", "SAO"):
            continue
        key = _hour_key(row["DATE"] + "+00:00")
        if not (start <= key <= end):
            continue
        t, d = _isd_value(row.get("TMP", "")), _isd_value(row.get("DEW", ""))
        rain = None
        aa = row.get("AA1", "")
        if aa:
            parts = aa.split(",")
            if len(parts) >= 2 and parts[0] == "01" and parts[1] != "9999":
                rain = int(parts[1]) / 10.0
        h = Hour(key, t, d, rh_from(t, d) if t is not None and d is not None else None, rain)
        if key not in by_hour or by_hour[key].temp_c is None:
            by_hour[key] = h
    return sorted(by_hour.values(), key=lambda h: h.time)


def isd_nearest_station(lat: float, lon: float, ws: WeatherStore, on: date) -> dict[str, Any]:
    with httpx.Client(headers=UA, timeout=60) as c:
        text = ws.get(c, ISD_HISTORY, ttl=30 * 86400)
    best, best_d = None, 1e9
    stamp = on.strftime("%Y%m%d")
    for r in csv.DictReader(io.StringIO(text)):
        # The station list lags the data files by a few weeks, so allow a 90-day grace period.
        grace = (on - timedelta(days=90)).strftime("%Y%m%d")
        if not r["LAT"] or not r["LON"] or r["END"] < grace or r["BEGIN"] > stamp or r["WBAN"] == "99999":
            continue
        d = _km(lat, lon, float(r["LAT"]), float(r["LON"]))
        if d < best_d:
            best, best_d = r, d
    if not best or best_d > 80:
        raise LookupError("No NOAA station with data within 80 km for that date.")
    return {"id": f"{best['USAF']}{best['WBAN']}", "name": best["STATION NAME"].title(), "km": round(best_d, 1)}


def isd_hours(lat: float, lon: float, ws: WeatherStore, end: date, days: int) -> tuple[list[Hour], dict[str, Any]]:
    st = isd_nearest_station(lat, lon, ws, end)
    start_dt = datetime.combine(end - timedelta(days=days), datetime.min.time(), timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), timezone.utc)
    hours: list[Hour] = []
    with httpx.Client(headers=UA, timeout=60) as c:
        for year in sorted({start_dt.year, end_dt.year}):
            text = ws.get(c, ISD_YEAR.format(year=year, station=st["id"]), ttl=7 * 86400)
            hours += parse_isd_csv(text, start_dt.isoformat(), end_dt.isoformat())
    if not hours:
        raise LookupError(f"No hourly data from {st['name']} for that period.")
    return hours, {"provider": "noaa-isd", "station": st["name"], "station_km": st["km"]}


def hourly(lat: float, lon: float, ws: WeatherStore, as_of: date | None = None, days: int = 30) -> tuple[list[Hour], dict[str, Any]]:
    """Hourly weather for the `days` before `as_of` (default: now, plus forecast)."""
    today = datetime.now(timezone.utc).date()
    if as_of is not None and as_of < today - timedelta(days=7):
        return isd_hours(lat, lon, ws, as_of, days)
    provider = os.environ.get("FIELD_MONITOR_WEATHER", "nws")
    if provider == "open-meteo":
        return open_meteo_hours(lat, lon, ws, days)
    return nws_hours(lat, lon, ws, days)


def summary(hours: list[Hour], days: int = 14) -> dict[str, Any]:
    """Plain numbers for the watch list: rain, wet hours, heat over the last `days`."""
    if not hours:
        return {}
    end = max(h.time for h in hours if not h.forecast) if any(not h.forecast for h in hours) else hours[-1].time
    start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()
    past = [h for h in hours if not h.forecast and start <= h.time <= end]
    temps = [h.temp_c for h in past if h.temp_c is not None]
    days_hot = len({h.time[:10] for h in past if h.temp_c is not None and h.temp_c >= 35})
    return {
        "days": days,
        # Many small automated stations don't report rain; None means "not reported", not "dry".
        "rain_mm": round(sum(h.precip_mm for h in past if h.precip_mm is not None), 1) if any(h.precip_mm is not None for h in past) else None,
        "wet_hours": sum(1 for h in past if h.wet),
        "max_c": round(max(temps), 1) if temps else None,
        "hot_days": days_hot,
        "hours": len(past),
        "latest": end,
    }


__all__ = ["Hour", "WeatherStore", "hourly", "summary", "rh_from", "parse_nws_observations", "parse_nws_forecast",
           "parse_open_meteo", "parse_isd_csv", "asdict"]
