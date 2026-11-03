"""Disease/pest reports, posts, comments, the news feed, and the per-field watch list.

Reports and posts that carry a location store it rounded to 0.01 degrees (about 1 km), so
sharing a report never reveals an exact field.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from shapely.geometry import shape

from . import disease, qa
from .accounts import Accounts, new_id, now
from .news import News
from .prices import Prices
from .score import field_score
from .text import RECENT_DAYS, ago, nice_date
from .store import Store
from .weather import WeatherStore, hourly, summary

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS crop_reports (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, disease TEXT NOT NULL, crop TEXT, severity TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, observed TEXT NOT NULL, notes TEXT, source TEXT NOT NULL DEFAULT 'community',
  created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS crop_reports_obs ON crop_reports(observed);
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, report_id TEXT,
  lat REAL, lon REAL, created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_created ON posts(created);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS helpful (post_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (post_id, user_id));
CREATE TABLE IF NOT EXISTS pressure_history (
  field_id TEXT NOT NULL, day TEXT NOT NULL, score INTEGER, level TEXT, top TEXT, PRIMARY KEY (field_id, day)
);
"""
NEAR_KM = 160  # "near me" = within ~100 miles of one of my fields
EXTRA_DISEASES = {"other_disease": "Other disease", "insect": "Insect pest", "weed": "Weed escape", "hail": "Hail / storm damage"}


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2)))


def rounded(v: float) -> float:
    return round(v, 2)


class ReportIn(BaseModel):
    disease: str
    severity: str = "moderate"
    observed: date | None = None
    notes: str | None = Field(None, max_length=1000)
    field_id: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    share: bool = True


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=3000)
    field_id: str | None = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class PriceIn(BaseModel):
    commodity: str
    price: float = Field(gt=0, lt=100)
    kind: str = "cash"
    source: str = Field("Manual entry", min_length=2, max_length=80)
    contract: str | None = Field(None, max_length=20)


class Watcher:
    """Computes (and caches) disease pressure per field; records a daily history for the feed."""

    def __init__(self, store: Store, accounts: Accounts, ws: WeatherStore) -> None:
        self.store, self.accounts, self.ws = store, accounts, ws
        self._cache: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def centroid(self, field: dict[str, Any]) -> tuple[float, float]:
        c = shape(field["geometry"]).centroid
        return c.y, c.x

    def reports_near(self, lat: float, lon: float, radius_km: float = 300, days: int = 21, as_of: date | None = None) -> list[dict[str, Any]]:
        end = as_of or datetime.now(timezone.utc).date()
        start = (end - timedelta(days=days)).isoformat()
        dlat = radius_km / 111.0
        with self.accounts.db() as c:
            rows = c.execute(
                "SELECT r.*, p.display_name AS author FROM crop_reports r LEFT JOIN profiles p ON p.user_id=r.user_id "
                "WHERE r.observed>=? AND r.observed<=? AND r.lat BETWEEN ? AND ?",
                (start, end.isoformat(), lat - dlat, lat + dlat)).fetchall()
        return [dict(r) for r in rows if km(lat, lon, r["lat"], r["lon"]) <= radius_km]

    def pressure(self, field_id: str, as_of: date | None = None, ttl: float = 1800) -> dict[str, Any]:
        key = (field_id, as_of.isoformat() if as_of else None)
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        f = self.store.get_field(field_id)
        lat, lon = self.centroid(f)
        obs = [o for o in qa.classify(self.store.list_observations(field_id)) if o.get("qa_status") == "ok"]
        if as_of:
            obs = [o for o in obs if o["date"] <= as_of.isoformat()]
        canopy = obs[-1]["stats"]["ndvi"]["median"] if obs else None
        try:
            hours, meta = hourly(lat, lon, self.ws, as_of=as_of, days=30)
            weather_err = None
        except Exception as exc:  # network down, no station nearby, archive gap
            hours, meta, weather_err = [], {}, str(exc)[:200]
        reports = self.reports_near(lat, lon, as_of=as_of)
        sc = field_score(qa.classify(self.store.list_observations(field_id)), as_of.isoformat() if as_of else None)
        season_over = sc.get("status") == "paused"
        result = disease.field_pressure(lat=lat, lon=lon, crop=f.get("crop"), hours=hours, reports=reports,
                                        canopy_ndvi=canopy, as_of=as_of, season_over=season_over)
        result["season_over"] = season_over
        result.update(weather=summary(hours), weather_source=meta, weather_error=weather_err, canopy_ndvi=canopy)
        if weather_err:
            result["weather_note"] = "Weather data unavailable right now; pressure uses reports only."
        if not as_of and result["score"] is not None:
            with self.accounts.db() as c:
                c.execute("INSERT OR REPLACE INTO pressure_history (field_id, day, score, level, top) VALUES (?,?,?,?,?)",
                          (field_id, result["as_of"], result["score"], result["level"], result["top"]))
        with self._lock:
            self._cache[key] = (time.time(), result)
        return result

    def refresh_all(self) -> None:
        for f in self.store.list_fields():
            try:
                self.pressure(f["id"], ttl=0)
            except Exception:
                log.exception("pressure refresh failed for %s", f["id"])

    def start(self, every_s: float = 6 * 3600, delay_s: float = 60) -> None:
        def loop() -> None:
            time.sleep(delay_s)
            while True:
                self.refresh_all()
                time.sleep(every_s)
        threading.Thread(target=loop, daemon=True, name="disease-pressure").start()

    def watch(self, field_id: str, as_of: date | None = None) -> dict[str, Any]:
        """The short "things to watch" list shown next to the field score."""
        p = self.pressure(field_id, as_of)
        items: list[dict[str, Any]] = []
        for i, d in enumerate(p["diseases"][:3]):
            if d["level"] == "Low" and items:
                continue
            # Full detail for the top disease only; the rest get one line to keep the list short.
            pts = d["points"][:2] + ([f"Look for: {d['watch']}"] if d["level"] != "Low" else []) if i == 0 else d["points"][:1]
            items.append({"kind": "disease", "level": d["level"], "title": f"{d['name']}: {d['level'].lower()} risk ({d['score']}/100)", "points": pts})
        w = p.get("weather") or {}
        if w:
            pts = [f"{w['wet_hours']} humid or rainy hours in the last {w['days']} days"]
            if w.get("rain_mm") is not None:
                pts.append(f"{w['rain_mm'] / 25.4:.1f} in of rain")
            if w.get("hot_days"):
                pts.append(f"{w['hot_days']} day(s) at 95°F or hotter: watch for heat stress")
            src = p.get("weather_source") or {}
            if src.get("station"):
                pts.append(f"Station: {src['station']}")
            items.append({"kind": "weather", "level": "info", "title": "Weather", "points": pts})
        elif p.get("weather_note"):
            items.append({"kind": "weather", "level": "info", "title": "Weather", "points": [p["weather_note"]]})
        alerts = qa.assess(self.store, field_id, "ndvi")["alerts"]
        ref = as_of or datetime.now(timezone.utc).date()
        cutoff = (ref - timedelta(days=RECENT_DAYS)).isoformat()
        recent = [a for a in alerts if a["kind"] != "senescence" and cutoff <= a["date"] <= ref.isoformat()][-2:]
        for a in reversed(recent):
            items.append({"kind": "satellite", "level": a["severity"], "title": a.get("title") or a["message"], "points": a.get("points", [])[:2]})
        return {"pressure": {k: p[k] for k in ("score", "level", "top", "as_of", "crop_kind", "method")},
                "diseases": p["diseases"], "items": items, "weather": p.get("weather") or {},
                "weather_source": p.get("weather_source") or {}, "weather_note": p.get("weather_note")}


def make_router(
    accounts: Accounts,
    store: Store,
    watcher: Watcher,
    require_user: Callable[..., dict[str, Any]],
    field_access: Callable[[str, dict[str, Any]], dict[str, Any]],
    news: News,
    prices: Prices,
) -> APIRouter:
    with accounts.db() as c:
        c.executescript(SCHEMA)
        cols = {row[1] for row in c.execute("PRAGMA table_info(crop_reports)")}
        if "place" not in cols:
            c.execute("ALTER TABLE crop_reports ADD COLUMN place TEXT")
    r = APIRouter()
    names = {k: v.name for k, v in disease.DISEASES.items()} | EXTRA_DISEASES

    def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        if not user.get("is_admin"):
            raise HTTPException(403, "Admins only.")
        return user

    def my_points(user: dict[str, Any]) -> list[tuple[float, float]]:
        pts = []
        for fid in accounts.fields_for(user["id"]):
            try:
                pts.append(watcher.centroid(store.get_field(fid)))
            except KeyError:
                continue
        return pts

    def dist_km(pts: list[tuple[float, float]], lat: float | None, lon: float | None) -> float | None:
        if lat is None or lon is None or not pts:
            return None
        return min(km(a, b, lat, lon) for a, b in pts)

    @r.get("/api/diseases")
    def diseases_list() -> list[dict[str, Any]]:
        out = [{"key": d.key, "name": d.name, "crop": d.crop, "watch": d.watch} for d in disease.DISEASES.values()]
        return out + [{"key": k, "name": v, "crop": None, "watch": ""} for k, v in EXTRA_DISEASES.items()]

    @r.get("/api/fields/{field_id}/pressure")
    def pressure(field_id: str, as_of: date | None = None, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_access(field_id, user)
        return watcher.pressure(field_id, as_of)

    @r.get("/api/fields/{field_id}/watch")
    def watch(field_id: str, as_of: date | None = None, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_access(field_id, user)
        return watcher.watch(field_id, as_of)

    @r.post("/api/crop-reports", status_code=201)
    def create_report(body: ReportIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        if body.disease not in disease.DISEASES and body.disease not in EXTRA_DISEASES:
            raise HTTPException(400, "Unknown disease or pest type.")
        if body.severity not in disease.SEVERITY:
            raise HTTPException(400, "severity must be trace, moderate, or severe")
        observed = body.observed or datetime.now(timezone.utc).date()
        if observed > datetime.now(timezone.utc).date() or observed < datetime.now(timezone.utc).date() - timedelta(days=60):
            raise HTTPException(400, "Observed date must be within the last 60 days.")
        crop = None
        if body.field_id:
            f = field_access(body.field_id, user)
            lat, lon = watcher.centroid(f)
            crop = disease.crop_kind(f.get("crop"))
        elif body.lat is not None and body.lon is not None:
            lat, lon = body.lat, body.lon
        else:
            raise HTTPException(400, "Pick one of your fields or share your location.")
        rid = new_id("r")
        d = disease.DISEASES.get(body.disease)
        name = d.name if d else EXTRA_DISEASES[body.disease]
        county = news.counties.lookup(lat, lon)
        place = county["label"] if county else None
        with accounts.db() as c:
            c.execute("INSERT INTO crop_reports (id, user_id, disease, crop, severity, lat, lon, observed, notes, created, place) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, user["id"], body.disease, crop or (d.crop if d else None), body.severity, rounded(lat), rounded(lon),
                       observed.isoformat(), (body.notes or "").strip() or None, now(), place))
            if body.share:
                text = (body.notes or "").strip() or f"{name} ({body.severity})"
                c.execute("INSERT INTO posts (id, user_id, kind, body, report_id, lat, lon, created) VALUES (?,?,?,?,?,?,?,?)",
                          (new_id("p"), user["id"], "report", text, rid, rounded(lat), rounded(lon), now()))
        with watcher._lock:
            watcher._cache.clear()  # nearby pressures change
        store.log("crop_report.created", by=user["id"], report_id=rid, disease=body.disease)
        return {"id": rid, "lat": rounded(lat), "lon": rounded(lon), "place": place}

    @r.get("/api/crop-reports")
    def list_reports(lat: float, lon: float, radius_km: float = Query(250, le=1000), days: int = Query(21, le=90),
                     user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        rows = watcher.reports_near(lat, lon, radius_km, days)
        return [{**{k: x[k] for k in ("id", "disease", "severity", "lat", "lon", "observed", "notes", "author")},
                 "name": names.get(x["disease"], x["disease"]), "km": round(km(lat, lon, x["lat"], x["lon"]))} for x in rows]

    @r.post("/api/posts", status_code=201)
    def create_post(body: PostIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        lat = lon = None
        if body.field_id:
            lat, lon = watcher.centroid(field_access(body.field_id, user))
            lat, lon = rounded(lat), rounded(lon)
        pid = new_id("p")
        with accounts.db() as c:
            c.execute("INSERT INTO posts (id, user_id, kind, body, lat, lon, created) VALUES (?,?,?,?,?,?,?)",
                      (pid, user["id"], "post", body.body.strip(), lat, lon, now()))
        return {"id": pid}

    def post_or_404(pid: str) -> dict[str, Any]:
        with accounts.db() as c:
            row = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Post not found")
        return dict(row)

    @r.post("/api/posts/{post_id}/helpful")
    def toggle_helpful(post_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        post_or_404(post_id)
        with accounts.db() as c:
            if c.execute("SELECT 1 FROM helpful WHERE post_id=? AND user_id=?", (post_id, user["id"])).fetchone():
                c.execute("DELETE FROM helpful WHERE post_id=? AND user_id=?", (post_id, user["id"]))
                mine = False
            else:
                c.execute("INSERT INTO helpful (post_id, user_id) VALUES (?,?)", (post_id, user["id"]))
                mine = True
            n = c.execute("SELECT COUNT(*) FROM helpful WHERE post_id=?", (post_id,)).fetchone()[0]
        return {"helpful": n, "mine": mine}

    @r.get("/api/posts/{post_id}/comments")
    def comments(post_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        post_or_404(post_id)
        with accounts.db() as c:
            return [dict(x) for x in c.execute(
                "SELECT cm.id, cm.user_id, cm.body, cm.created, p.display_name AS author FROM comments cm "
                "LEFT JOIN profiles p ON p.user_id=cm.user_id WHERE cm.post_id=? ORDER BY cm.id", (post_id,))]

    @r.post("/api/posts/{post_id}/comments", status_code=201)
    def add_comment(post_id: str, body: CommentIn, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        post_or_404(post_id)
        with accounts.db() as c:
            c.execute("INSERT INTO comments (post_id, user_id, body, created) VALUES (?,?,?,?)", (post_id, user["id"], body.body.strip(), now()))
        return comments(post_id, user)

    # ---- prices & sources --------------------------------------------------------
    @r.get("/api/prices")
    def price_board(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return prices.board()

    @r.post("/api/prices", status_code=201)
    def set_price(body: PriceIn, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        try:
            prices.add(body.commodity, body.price, kind=body.kind, source=body.source.strip(), contract=body.contract)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        store.log("price.set", by=user["id"], commodity=body.commodity, price=body.price, source=body.source)
        return prices.board()

    @r.get("/api/news/sources")
    def news_sources(user: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
        return news.health()

    @r.post("/api/news/refresh")
    def news_refresh(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        return news.refresh()

    # ---- the News feed --------------------------------------------------------------
    @r.get("/api/feed")
    def feed(section: str = Query("all", pattern="^(all|local|national|community|market|fields)$"),
             user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        """Sections: all (identical for every member: everything network-wide except personal
        field alerts), local (near my fields: reports, storms, roundup), national (news),
        community (posts and reports everywhere), market (listings), fields (my field alerts)."""
        pts = my_points(user)
        items: list[dict[str, Any]] = []
        since = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).replace(microsecond=0).isoformat()
        near = lambda lat, lon: (dist_km(pts, lat, lon) or 1e9) <= NEAR_KM  # noqa: E731

        if section in ("all", "local", "community"):
            with accounts.db() as c:
                rows = c.execute(
                    "SELECT po.*, pr.display_name AS author, pr.business_name AS business, "
                    " (SELECT COUNT(*) FROM helpful h WHERE h.post_id=po.id) AS helpful, "
                    " (SELECT COUNT(*) FROM helpful h WHERE h.post_id=po.id AND h.user_id=?) AS mine, "
                    " (SELECT COUNT(*) FROM comments cm WHERE cm.post_id=po.id) AS comments, "
                    " cr.disease, cr.severity, cr.observed, cr.place "
                    "FROM posts po LEFT JOIN profiles pr ON pr.user_id=po.user_id LEFT JOIN crop_reports cr ON cr.id=po.report_id "
                    "WHERE po.created>=? ORDER BY po.created DESC LIMIT 200", (user["id"], since)).fetchall()
            for x in rows:
                x = dict(x)
                d = dist_km(pts, x["lat"], x["lon"])
                if section == "local" and not near(x["lat"], x["lon"]):
                    continue
                title = None
                if x["kind"] == "report":
                    what = names.get(x["disease"], "Crop problem")
                    verb = "reported" if x["disease"] in ("hail", "insect", "weed") else "spotted"
                    title = f"{what} {verb} in {x['place']}" if x.get("place") else f"{what} {verb}"
                items.append({"type": x["kind"], "id": x["id"], "time": x["created"], "author": x["business"] or x["author"],
                              "author_id": x["user_id"], "title": title, "body": x["body"], "miles": round(d * 0.621) if d is not None else None,
                              "helpful": x["helpful"], "mine": bool(x["mine"]), "comments": x["comments"],
                              "disease": x["disease"], "severity": x["severity"], "observed": x["observed"], "place": x.get("place")})

        storms_shown = 0
        if section in ("all", "local", "national"):
            for n in news.items(since):
                if n["scope"] == "local":
                    if section == "national" or n["lat"] is None:
                        continue
                    if section == "local" and not near(n["lat"], n["lon"]):
                        continue
                    if section == "all":
                        storms_shown += 1
                        if storms_shown > 15:  # everywhere-storms can number hundreds a day; keep the shared view readable
                            continue
                    d = dist_km(pts, n["lat"], n["lon"])
                    items.append({"type": "storm", "id": n["id"], "time": n["published"], "title": n["title"], "body": n["summary"],
                                  "source": n["source_name"], "miles": round(d * 0.621) if d is not None else None, "place": n["place"]})
                elif section != "local":
                    items.append({"type": "news", "id": n["id"], "time": n["published"], "title": n["title"], "body": n["summary"],
                                  "url": n["url"], "source": n["source_name"]})

        if section == "all" or (section == "local" and pts):
            # Weekly roundup grouped by problem and county: network-wide on "all", near my fields on "local".
            week = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
            with accounts.db() as c:
                recent = [dict(x) for x in c.execute("SELECT disease, place, lat, lon FROM crop_reports WHERE observed>=?", (week,))]
            groups: dict[str, set[str]] = {}
            counts: dict[str, int] = {}
            for x in recent:
                if section == "all" or near(x["lat"], x["lon"]):
                    nm = names.get(x["disease"], x["disease"])
                    counts[nm] = counts.get(nm, 0) + 1
                    if x["place"]:
                        groups.setdefault(nm, set()).add(x["place"].split(",")[0])
            if counts:
                pts_txt = [f"{nm}: {n} report{'s' if n > 1 else ''}" + (f" ({', '.join(sorted(groups.get(nm, []))[:3])})" if groups.get(nm) else "")
                           for nm, n in sorted(counts.items(), key=lambda kv: -kv[1])]
                title = "This week across Overacre" if section == "all" else "This week near your fields"
                items.append({"type": "roundup", "id": f"roundup:{section}:{week}", "time": now(), "title": title, "points": pts_txt})

        if section in ("all", "market"):
            with accounts.db() as c:
                for x in c.execute("SELECT l.*, p.display_name AS author, p.business_name AS business FROM listings l "
                                   "JOIN profiles p ON p.user_id=l.user_id WHERE l.status='active' AND l.created>=? "
                                   "ORDER BY l.created DESC LIMIT 50", (since,)):
                    items.append({"type": "listing", "id": x["id"], "time": x["created"], "author": x["business"] or x["author"],
                                  "author_id": x["user_id"], "title": x["title"], "body": x["description"], "price": x["price"],
                                  "location": x["location"]})

        if section == "fields":  # personal alerts stay out of the shared "all" view
            for fid in accounts.fields_for(user["id"]):
                try:
                    f = store.get_field(fid)
                except KeyError:
                    continue
                if f.get("archived"):
                    continue
                for a in qa.assess(store, fid, "ndvi")["alerts"][-2:]:
                    if a["kind"] == "senescence" or a["date"] < since[:10]:
                        continue
                    items.append({"type": "field", "id": f"{fid}:{a['date']}:{a['kind']}", "time": a["date"] + "T12:00:00+00:00",
                                  "field_id": fid, "field": f["name"], "title": a.get("title") or a["message"],
                                  "points": a.get("points", []), "severity": a["severity"]})
                with accounts.db() as c:
                    hist = c.execute("SELECT day, score, level, top FROM pressure_history WHERE field_id=? ORDER BY day DESC LIMIT 2", (fid,)).fetchall()
                order = {"Low": 0, "Moderate": 1, "High": 2}
                if hist and hist[0]["level"] in ("Moderate", "High") and (len(hist) == 1 or order[hist[0]["level"]] > order.get(hist[1]["level"], 0)):
                    items.append({"type": "field", "id": f"{fid}:{hist[0]['day']}:pressure", "time": hist[0]["day"] + "T06:00:00+00:00",
                                  "field_id": fid, "field": f["name"], "severity": "high" if hist[0]["level"] == "High" else "medium",
                                  "title": f"{hist[0]['top']} pressure is {hist[0]['level'].lower()} ({hist[0]['score']})",
                                  "points": ["Open the field's watch list for why and what to look for"]})
        roundup = [i for i in items if i["type"] == "roundup"]
        rest = sorted([i for i in items if i["type"] != "roundup"], key=lambda i: i["time"], reverse=True)
        return (roundup + rest)[:100]

    # ---- daily brief ----------------------------------------------------------------
    def field_brief(f: dict[str, Any], role: str, today: date) -> dict[str, Any]:
        obs = qa.classify(store.list_observations(f["id"]))
        ok = [o for o in obs if o.get("qa_status") == "ok"]
        cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
        latest = ok[-1] if ok else None
        age = (today - date.fromisoformat(latest["date"])).days if latest else None
        fresh = latest is not None and age is not None and age <= RECENT_DAYS
        out: dict[str, Any] = {"id": f["id"], "name": f["name"], "crop": f.get("crop"), "area_ac": f["area_ac"], "role": role,
                               "image": None, "change": None, "trend": [], "alerts": [], "pressure": None}
        if latest:
            out["image"] = {"date": latest["date"], "label": nice_date(latest["date"], today), "age_days": age, "fresh": fresh,
                            "ndvi": latest["stats"]["ndvi"]["median"]}
            prev = [o for o in ok if 5 <= (date.fromisoformat(latest["date"]) - date.fromisoformat(o["date"])).days <= 21]
            if prev:
                p0 = prev[-1]
                d = latest["stats"]["ndvi"]["median"] - p0["stats"]["ndvi"]["median"]
                out["change"] = {"since": nice_date(p0["date"], today), "delta": round(d, 3),
                                 "word": "greener" if d >= 0.03 else "less green" if d <= -0.03 else "steady"}
            # "Season so far": this calendar year's good images.
            year = str(today.year) if fresh else latest["date"][:4]
            out["trend"] = [{"date": o["date"], "v": o["stats"]["ndvi"]["median"]} for o in ok if o["date"].startswith(year)]
        sc = field_score(obs)
        out["score"] = {k: sc.get(k) for k in ("score", "label", "status", "as_of")}
        if sc.get("as_of"):
            out["score"]["as_of_label"] = nice_date(sc["as_of"], today)
        try:
            p = watcher.pressure(f["id"])
            out["pressure"] = {"score": p["score"], "level": p["level"], "top": p["top"]}
            top_d = p["diseases"][0] if p["diseases"] else None
        except Exception:
            top_d = None
        out["alerts"] = [{"date": a["date"], "title": a.get("title") or a["message"], "severity": a["severity"], "status": a.get("status")}
                         for a in qa.alerts(store, f["id"], obs, "ndvi") if a["kind"] != "senescence" and a["date"] >= cutoff][-2:]

        # One plain sentence: the most useful thing to know about this field today.
        season_over = sc.get("status") == "paused"
        crop_known = disease.crop_kind(f.get("crop")) is not None
        risk_high = crop_known and not season_over and out["pressure"] and out["pressure"]["level"] == "High" and top_d
        risk_mod = crop_known and not season_over and out["pressure"] and out["pressure"]["level"] == "Moderate" and top_d
        high = [a for a in out["alerts"] if a["severity"] == "high"]
        if high and not season_over:
            headline, priority = f"{high[-1]['title']}. Scout the low spots.", 3
        elif risk_high:
            headline, priority = f"{top_d['name']} risk is high. Look for: {top_d['watch'].lower()}.", 3
        elif out["alerts"] and not season_over:
            a = out["alerts"][-1]
            nxt = {"pending": "Waiting for the next clear image to confirm; scout if you're nearby.",
                   "confirmed": "Confirmed on the next image. Scout the low spots.",
                   "recovered": "It bounced back; no action needed."}.get(a.get("status") or "", "Check the Scout tab.")
            headline, priority = f"{a['title']}. {nxt}", 1 if a.get("status") == "recovered" else 2
        elif not latest:
            headline, priority = "Waiting for the first clear satellite image.", 1
        elif season_over:
            final = f"final score {sc['score']}/100 ({sc['label']}), {nice_date(sc['as_of'], today)}" if sc.get("score") is not None else "no score this season"
            if out["image"]["ndvi"] >= 0.5:
                headline = f"Still green (NDVI {out['image']['ndvi']:.2f}) after harvest in your area: cover crop, hay, or late harvest? Season {final}."
            else:
                headline = f"Season over: {final}."
            priority = 0
        elif not fresh:
            headline, priority = f"No clear image in the last {RECENT_DAYS} days (clouds). Last one: {out['image']['label']}.", 1
        elif risk_mod:
            headline, priority = f"{top_d['name']} risk is moderate. Worth a look on your next pass.", 2
        elif out["change"]:
            c = out["change"]
            headline = {"greener": f"Getting greener: crop health up {c['delta']:.2f} since {c['since']}.",
                        "less green": f"Crop health down {abs(c['delta']):.2f} since {c['since']}. Check the Scout tab.",
                        "steady": f"Steady since {c['since']}. Nothing new to act on."}[c["word"]]
            priority = 2 if c["word"] == "less green" else 0
        else:
            headline, priority = f"New image {ago(age or 0)}. Nothing new to act on.", 0
        if not crop_known and priority < 2 and not season_over:
            headline += " Tip: set the crop (Manage → Edit name & crop) for disease risk."
        out["season_over"] = season_over
        out["headline"], out["priority"] = headline, priority
        return out

    @r.get("/api/brief")
    def brief(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        fields = []
        for fid, role in accounts.fields_for(user["id"]).items():
            try:
                f = store.get_field(fid)
            except KeyError:
                continue
            if not f.get("archived"):
                fields.append(field_brief(f, role, today))
        fields.sort(key=lambda b: (-b["priority"], b["name"]))
        priorities = [{"field_id": b["id"], "field": b["name"], "text": b["headline"]} for b in fields if b["priority"] >= 2][:3]
        local = [i for i in feed(section="local", user=user) if i["type"] in ("roundup", "report", "storm")][:4]
        return {"date": today.isoformat(), "date_label": f"{today:%A}, {today:%B} {today.day}", "name": (accounts.profile(user["id"]) or {}).get("display_name"),
                "priorities": priorities, "fields": fields, "local": local, "prices": prices.board(), "recent_days": RECENT_DAYS}

    return r
