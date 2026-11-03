"""Free cash grain bids from USDA AMS Market News (MyMarketNews / MARS API).

Public-domain data. Needs a free API key from a MyMarketNews account, provided to the
server as the environment variable USDA_AMS_API_KEY (never stored by the app).

How it works:
1. List all Market News reports and pick the "<State> ... Grain Bids" reports for the states
   your fields are in (override with FIELD_MONITOR_AMS_REPORTS="2850,3046" report ids).
2. Pull each report's recent rows and take the median bid per crop per day across locations.
3. Store them as "cash" prices; the board shows "USDA AMS · Iowa Daily Grain Bids".

The MARS response format was written from USDA's public descriptions without access to test
it, so the row parser is deliberately tolerant (several common field names) and
`fieldmonitor usda-check` prints exactly what came back on the first live run.
"""

from __future__ import annotations

import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = os.environ.get("FIELD_MONITOR_AMS_BASE", "https://marsapi.ams.usda.gov/services/v1.2")
STATE_FULL = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
PRICE_KEYS = ("avg_price", "price_avg", "wtd_avg_price", "weighted_avg", "avg", "bid_avg", "price", "bid", "price_mostly")
LOW_HIGH = (("price_min", "price_max"), ("price_low", "price_high"), ("low_price", "high_price"), ("bid_low", "bid_high"))
DATE_KEYS = ("report_date", "report_end_date", "published_date", "date")
COMMODITY_KEYS = ("commodity", "commodity_name", "class", "grain", "item")
EXCLUDE = re.compile(r"meal|oil|gluten|pop|hull|distill|ddg|silage|seed|futures", re.I)


def api_key() -> str | None:
    return os.environ.get("USDA_AMS_API_KEY") or None


def _client(key: str) -> httpx.Client:
    # MARS uses HTTP Basic auth with the API key as the username and an empty password.
    return httpx.Client(auth=(key, ""), timeout=30, headers={"Accept": "application/json"})


def list_reports(key: str) -> list[dict[str, Any]]:
    with _client(key) as c:
        r = c.get(f"{BASE}/reports")
        r.raise_for_status()
        data = r.json()
    rows = data if isinstance(data, list) else data.get("results", data.get("reports", []))
    out = []
    for x in rows:
        sid = x.get("slug_id") or x.get("slugId") or x.get("id")
        title = x.get("report_title") or x.get("title") or x.get("slug_name") or ""
        if sid:
            out.append({"id": str(sid), "title": str(title)})
    return out


def grain_bid_reports(reports: list[dict[str, Any]], states: list[str]) -> list[dict[str, Any]]:
    """Reports whose title mentions one of `states` (full names) and grain bids."""
    picks = []
    for r in reports:
        t = r["title"].lower()
        if "grain" in t and "bid" in t and any(s.lower() in t for s in states):
            picks.append(r)
    return picks


def _num(v: Any) -> float | None:
    try:
        f = float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def commodity_of(rec: dict[str, Any]) -> str | None:
    text = " ".join(str(rec.get(k, "")) for k in COMMODITY_KEYS)
    if not text.strip() or EXCLUDE.search(text):
        return None
    t = text.lower()
    if "soybean" in t:
        return "soybeans"
    if "corn" in t:
        return "corn"
    if "wheat" in t:
        return "wheat"
    return None


def price_of(rec: dict[str, Any]) -> float | None:
    p = next((v for k in PRICE_KEYS if (v := _num(rec.get(k))) is not None), None)
    if p is None:
        for lo_k, hi_k in LOW_HIGH:
            lo, hi = _num(rec.get(lo_k)), _num(rec.get(hi_k))
            if lo and hi:
                p = (lo + hi) / 2
                break
    if p is None:
        return None
    if p > 50:  # quoted in cents per bushel
        p /= 100
    return round(p, 4) if 1 <= p <= 50 else None


def date_of(rec: dict[str, Any]) -> str | None:
    for k in DATE_KEYS:
        v = rec.get(k)
        if not v:
            continue
        s = str(v).strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S"):
            try:
                return datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt).date().isoformat()
            except ValueError:
                continue
    return None


def daily_medians(rows: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[float, int]]:
    """{(date, commodity): (median $/bu across locations, number of quotes)}."""
    buckets: dict[tuple[str, str], list[float]] = {}
    for rec in rows:
        c, p, d = commodity_of(rec), price_of(rec), date_of(rec)
        if c and p and d:
            buckets.setdefault((d, c), []).append(p)
    return {k: (round(statistics.median(v), 4), len(v)) for k, v in buckets.items()}


def fetch_report(key: str, report_id: str, days: int = 10) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    q = f"report_begin_date={start:%m/%d/%Y}:{end:%m/%d/%Y}"
    with _client(key) as c:
        r = c.get(f"{BASE}/reports/{report_id}", params={"q": q, "allSections": "true"})
        r.raise_for_status()
        data = r.json()
    rows: list[dict[str, Any]] = []
    sections = data if isinstance(data, list) else [data]
    for sec in sections:
        if isinstance(sec, dict):
            rows += [x for x in sec.get("results", []) if isinstance(x, dict)]
    return rows


def sync_prices(prices: Any, states: list[str]) -> dict[str, Any]:
    """Fetch the matching reports and store the latest daily medians. Returns a summary."""
    key = api_key()
    if not key:
        return {"ok": False, "error": "USDA_AMS_API_KEY is not set"}
    fixed = [x.strip() for x in os.environ.get("FIELD_MONITOR_AMS_REPORTS", "").split(",") if x.strip()]
    reports = [{"id": r, "title": f"USDA AMS report {r}"} for r in fixed] or grain_bid_reports(list_reports(key), states)
    if not reports:
        return {"ok": False, "error": f"No grain bid reports found for {', '.join(states) or 'your states'}; set FIELD_MONITOR_AMS_REPORTS"}
    stored = []
    for rep in reports[:3]:
        med = daily_medians(fetch_report(key, rep["id"]))
        for (day, crop), (price, n) in sorted(med.items()):
            prices.add_if_new(crop, price, kind="cash", source=f"USDA AMS · {rep['title']}"[:80],
                              contract=f"{n} locations", as_of=f"{day}T18:00:00+00:00")
            stored.append((rep["id"], day, crop, price, n))
    return {"ok": True, "reports": reports[:3], "stored": len(stored), "latest": stored[-6:]}
