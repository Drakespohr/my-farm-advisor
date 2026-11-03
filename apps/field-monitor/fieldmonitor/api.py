"""HTTP API and static web UI for field monitoring."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import logging
import os
import re
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, AsyncIterator
from xml.etree.ElementTree import ParseError

import httpx
from shapely.geometry import shape

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_NAME, APP_TAGLINE, PROCESSING_VERSION, __version__, analysis, diagnose, qa, score as scoring
from .accounts import SESSION_COOKIE, Accounts
from .feed import Watcher, make_router as make_feed_router
from .news import News
from .usda_ams import STATE_FULL
from .prices import Prices
from .social import make_router
from .weather import WeatherStore
from .boundaries import parse_file, parse_geojson
from .catalog import search_scenes
from .indices import DIVERGING, INDICES, ZONES, describe
from .raster import to_crs
from .render import classes_rgba, colorize, to_web_png
from .store import Store, git_commit
from .sync import Syncer

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class FieldIn(BaseModel):
    name: str | None = None
    field_id: str | None = None
    crop: str | None = None
    notes: str | None = None
    geojson: dict[str, Any] | None = Field(None, description="Polygon, Feature, or FeatureCollection in WGS84 lon/lat")
    file_name: str | None = Field(None, description="Uploaded boundary file name (.kml, .kmz, .geojson, .json)")
    file_b64: str | None = Field(None, description="Base64 file contents, used with file_name")
    auto_sync: bool = Field(True, description="Start pulling imagery for new fields immediately")


class RedrawIn(BaseModel):
    geojson: dict[str, Any]


class FieldPatch(BaseModel):
    name: str | None = None
    crop: str | None = None
    notes: str | None = None
    archived: bool | None = None
    showcase: bool | None = None


class ProcessIn(BaseModel):
    start: date
    end: date
    max_cloud: float = Field(60.0, ge=0, le=100, description="Scene-level cloud % filter; field-level masking happens after.")


def _png_data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
BULK_SYNC_LIMIT = 5  # larger imports are left to the scheduler, which syncs one field at a time
_COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


def create_app(store: Store | None = None, refresh_hours: float | None = None) -> FastAPI:
    store = store or Store.from_env()
    if refresh_hours is None:
        refresh_hours = float(os.environ.get("FIELD_MONITOR_REFRESH_HOURS", "12"))
    syncer = Syncer(store, refresh_hours=refresh_hours)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        syncer.start_scheduler()
        if refresh_hours:
            watcher.start()  # keep disease pressure current even when nobody has the app open
            news.start()
            prices.start(lambda: field_states())
        yield
        syncer.stop()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.syncer = syncer
    accounts = Accounts(store.root / "app.db")
    app.state.accounts = accounts
    watcher = Watcher(store, accounts, WeatherStore(store.root / "weather.db"))
    app.state.watcher = watcher
    news = News(accounts, store.root)
    prices = Prices(accounts)
    app.state.news, app.state.prices = news, prices

    def field_states() -> list[str]:
        """Full state names for all active fields (used to pick USDA grain bid reports)."""
        out: list[str] = []
        for f in store.list_fields():
            c = shape(f["geometry"]).centroid
            county = news.counties.lookup(c.y, c.x)
            name = STATE_FULL.get(county["state"]) if county else None
            if name and name not in out:
                out.append(name)
        return out

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next: Any) -> Any:
        # State-changing API calls must come from our own page (custom header => same-origin only).
        if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("x-requested-with") != "fieldmonitor":
                return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
        return await call_next(request)

    def require_user(request: Request) -> dict[str, Any]:
        user = accounts.session_user(request.cookies.get(SESSION_COOKIE))
        if not user:
            raise HTTPException(401, "Please log in.")
        return user

    def field_score_for(field_id: str, as_of: str | None = None) -> dict[str, Any]:
        return scoring.field_score(qa.classify(store.list_observations(field_id)), as_of)
    geocode_lock = threading.Lock()
    geocode_last = [0.0]

    def field_or_404(field_id: str, user: dict[str, Any], need: str = "viewer") -> dict[str, Any]:
        """Return the field if `user` may see it (404 otherwise, so ids don't leak)."""
        role = accounts.access(field_id, user["id"])
        try:
            f = store.get_field(field_id)
        except KeyError:
            role = None
        if role is None:
            raise HTTPException(404, f"Unknown field {field_id!r}")
        if need == "owner" and role != "owner":
            raise HTTPException(403, "Only the field's owner can do that.")
        return {**f, "my_role": role}

    def ok_obs(field_id: str) -> list[dict[str, Any]]:
        """Clear observations that passed QA (no haze or one-pass artifacts)."""
        return [o for o in qa.classify(store.list_observations(field_id)) if o["qa_status"] == "ok"]

    def arrays_or_404(field_id: str, obs_id: str) -> tuple[dict[str, np.ndarray], Any, str, dict[str, Any]]:
        try:
            meta = store.get_observation(field_id, obs_id)
            arrays, transform, crs = store.load_arrays(field_id, obs_id)
        except KeyError:
            raise HTTPException(404, f"Unknown observation {obs_id!r}") from None
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from None
        return arrays, transform, crs, meta

    def index_or_400(index: str) -> None:
        if index not in INDICES:
            raise HTTPException(400, f"Unknown index {index!r}. Choose from {', '.join(INDICES)}.")

    # ---- meta ----------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "app_name": APP_NAME,
            "tagline": APP_TAGLINE,
            "app_version": __version__,
            "processing_version": PROCESSING_VERSION,
            "git_commit": git_commit(),
            "auto_refresh_hours": syncer.refresh_hours or None,
        }

    @app.get("/api/indices")
    def indices() -> list[dict[str, object]]:
        return [describe(i) for i in INDICES.values()]

    # ---- fields --------------------------------------------------------------
    @app.get("/api/fields")
    def list_fields(include_archived: bool = False, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        out = []
        mine = accounts.fields_for(user["id"])
        for f in store.list_fields(include_archived):
            if f["id"] not in mine:
                continue
            obs = qa.classify(store.list_observations(f["id"]))
            clear = [o for o in obs if o["status"] == "clear"]
            good = [o for o in clear if o["qa_status"] == "ok"]
            latest = good[-1] if good else None
            out.append(
                {
                    **f,
                    "observations": len(obs),
                    "clear_observations": len(clear),
                    "latest": {"date": latest["date"], "ndvi": latest["stats"]["ndvi"]["median"], "ndre": latest["stats"]["ndre"]["median"]}
                    if latest
                    else None,
                    "sync": syncer.status(f["id"]),
                    "my_role": mine[f["id"]],
                    "score": scoring.field_score(obs),
                }
            )
        return out

    @app.post("/api/fields", status_code=201)
    def create_fields(body: FieldIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        try:
            if body.file_b64 and body.file_name:
                raw = base64.b64decode(body.file_b64, validate=True)
                if len(raw) > MAX_UPLOAD_BYTES:
                    raise ValueError("File is larger than 20 MB.")
                feats = parse_file(body.file_name, raw)
                source = f"upload:{Path(body.file_name).name}"
            elif body.geojson is not None:
                feats = parse_geojson(body.geojson)
                source = "api"
            else:
                raise ValueError("Send either geojson or file_name + file_b64.")
        except (ValueError, KeyError, ParseError, binascii.Error) as exc:
            raise HTTPException(400, f"Could not read boundaries: {exc}") from None
        if not feats:
            raise HTTPException(400, "No features with geometry found.")
        created, existing, errors = [], [], []
        for n, (geom, props) in enumerate(feats, start=1):
            name = (
                (body.name if len(feats) == 1 else None)
                or props.get("name")
                or props.get("field_name")
                or props.get("field_id")
                or f"Field {n}"
            )
            fid = (body.field_id if len(feats) == 1 else None) or props.get("field_id")
            try:
                rec, is_new = store.create_field(
                    geom,
                    str(name),
                    field_id=str(fid) if fid else None,
                    crop=body.crop or props.get("crop") or props.get("crop_name"),
                    notes=body.notes,
                    source=source,
                )
                owner = accounts.owner_of(rec["id"])
                if owner and owner != user["id"]:
                    errors.append({"feature": n, "name": name, "error": "Someone else already monitors this exact boundary."})
                    continue
                accounts.grant(rec["id"], user["id"], "owner")
                (created if is_new else existing).append(rec["id"])
            except (ValueError, FileExistsError) as exc:
                errors.append({"feature": n, "name": name, "error": str(exc)})
        if errors and not (created or existing):
            raise HTTPException(400, {"errors": errors})
        syncing = []
        if body.auto_sync and len(created) <= BULK_SYNC_LIMIT:
            syncing = [syncer.start(fid, "new-field").id for fid in created]
        return {"created": created, "existing": existing, "errors": errors, "sync_jobs": syncing}

    @app.post("/api/fields/{field_id}/redraw", status_code=201)
    def redraw(field_id: str, body: RedrawIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user, "owner")
        try:
            feats = parse_geojson(body.geojson)
            if len(feats) != 1:
                raise ValueError("Send exactly one boundary.")
            new = store.redraw_field(field_id, feats[0][0])
            accounts.grant(new["id"], user["id"], "owner")
            for m in accounts.field_members(field_id):
                if m["role"] == "viewer":
                    accounts.grant(new["id"], m["user_id"], "viewer")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        job = syncer.start(new["id"], "new-field")
        return {"field": new, "replaced": field_id, "sync_job": job.view()}

    @app.post("/api/fields/{field_id}/sync", status_code=202)
    def sync_now(field_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user, "owner")
        return syncer.start(field_id, "manual").view()

    @app.get("/api/geocode")
    def geocode(q: str = Query(..., min_length=2, max_length=200), user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        """Place search for the mapping screen. Accepts 'lat, lon' or a place name (OpenStreetMap Nominatim)."""
        m = _COORD.match(q)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return [{"name": f"{lat:.6f}, {lon:.6f}", "lat": lat, "lon": lon, "bbox": None}]
        # Nominatim's usage policy: identify the app and stay under 1 request/second.
        with geocode_lock:
            wait = 1.0 - (time.monotonic() - geocode_last[0])
            if wait > 0:
                time.sleep(wait)
            geocode_last[0] = time.monotonic()
        try:
            r = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "jsonv2", "limit": 6},
                headers={"User-Agent": f"my-farm-advisor-field-monitor/{__version__}"},
                timeout=10.0,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Place search unavailable ({exc.__class__.__name__}). Type coordinates as 'lat, lon' instead.") from None
        out = []
        for hit in r.json():
            bb = hit.get("boundingbox")
            out.append(
                {
                    "name": hit.get("display_name", ""),
                    "lat": float(hit["lat"]),
                    "lon": float(hit["lon"]),
                    "bbox": [[float(bb[0]), float(bb[2])], [float(bb[1]), float(bb[3])]] if bb else None,
                }
            )
        return out

    @app.get("/api/fields/{field_id}")
    def get_field(field_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        return field_or_404(field_id, user)

    @app.patch("/api/fields/{field_id}")
    def patch_field(field_id: str, body: FieldPatch, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user, "owner")
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(400, "Nothing to change.")
        return store.update_field_meta(field_id, **changes)

    # ---- scenes & processing -------------------------------------------------
    @app.get("/api/fields/{field_id}/scenes")
    def scenes(field_id: str, start: date, end: date, max_cloud: float = 60.0, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        f = field_or_404(field_id, user)
        found, source = search_scenes(f["geometry"], start, end, max_cloud)
        done = {o["scene_id"]: o for o in store.list_observations(field_id)}
        return {
            "source": source,
            "scenes": [
                {**s.summary(), "processed": s.id in done, "status": done[s.id]["status"] if s.id in done else None}
                for s in found
            ],
        }

    @app.post("/api/fields/{field_id}/process", status_code=202)
    def process(field_id: str, body: ProcessIn, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        f = field_or_404(field_id, user, "owner")
        if body.end < body.start:
            raise HTTPException(400, "end must be on or after start")
        if body.end - body.start > timedelta(days=400):
            raise HTTPException(400, "Process at most ~one season (400 days) per request.")
        return syncer.start(f["id"], "range", body.start, body.end, max_cloud=body.max_cloud).view()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        job = syncer.jobs.get(job_id)
        if not job or not accounts.access(job.field_id, user["id"]):
            raise HTTPException(404, "Unknown job")
        return job.view()

    # ---- observations --------------------------------------------------------
    @app.get("/api/fields/{field_id}/observations")
    def observations(field_id: str, user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        field_or_404(field_id, user)
        return qa.classify(store.list_observations(field_id))

    @app.get("/api/fields/{field_id}/observations/{obs_id}/{name}.png")
    def obs_png(field_id: str, obs_id: str, name: str, user: dict[str, Any] = Depends(require_user)) -> FileResponse:
        field_or_404(field_id, user)
        try:
            path = store.png_path(field_id, obs_id, name)
        except KeyError:
            raise HTTPException(404) from None
        if not path.exists():
            raise HTTPException(404)
        # Observations are immutable, so browsers may cache them indefinitely.
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/fields/{field_id}/observations/{obs_id}/hotspots")
    def obs_hotspots(field_id: str, obs_id: str, index: str = "ndvi", min_area_ha: float = Query(0.2, ge=0.01), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user)
        index_or_400(index)
        arrays, transform, crs, meta = arrays_or_404(field_id, obs_id)
        spots = analysis.hotspots(arrays[index], arrays["valid"], transform, crs, min_area_ha=min_area_ha)
        if spots:
            field = store.get_field(field_id)
            field_utm = to_crs(shape(field["geometry"]), "EPSG:4326", crs)
            # Earlier good dates on the same grid tell "always low here" from "new".
            ok = ok_obs(field_id)
            earlier = [o for o in ok if o["date"] < meta["date"] and o.get("grid") == meta.get("grid")][-8:]
            history = [(o["date"], store.load_arrays(field_id, o["id"])[0]) for o in earlier]
            medians = [(o["date"], o["stats"]["ndvi"]["median"] or 0) for o in ok]
            peak_date = max(medians, key=lambda t: t[1])[0] if medians else None
            field_ndvi = meta["stats"]["ndvi"]["median"] or 0
            if peak_date and meta["date"] > peak_date and field_ndvi < 0.85:
                stage = "late"
            elif field_ndvi < 0.5:
                stage = "early"
            else:
                stage = "mid"
            for h in spots:
                h["explain"] = diagnose.explain(h, arrays, field_utm, history, stage)
        return {
            "obs_id": obs_id,
            "date": meta["date"],
            "index": index,
            "field_median": meta["stats"][index]["median"],
            "field_mean": meta["stats"][index]["mean"],
            "hotspots": analysis.public(spots),
            "method": "Patches more than max(1.5 x std, 0.05) below the field median, at least min_area_ha in size.",
        }

    # ---- zones ---------------------------------------------------------------
    def zone_input(field_id: str, index: str, obs_id: str | None) -> tuple[np.ndarray, np.ndarray, Any, str, list[str]]:
        if obs_id:
            arrays, transform, crs, meta = arrays_or_404(field_id, obs_id)
            return arrays[index], arrays["valid"], transform, crs, [meta["date"]]
        clear = ok_obs(field_id)
        if not clear:
            raise HTTPException(409, "No clear, good-quality observations yet. Process some imagery first.")
        # Use dates with established canopy, otherwise zones mostly map soil color.
        canopy = [o for o in clear if (o["stats"]["ndvi"]["median"] or 0) >= 0.5]
        chosen = canopy if len(canopy) >= 2 else clear
        grid_key = Counter(str(o["grid"]) for o in chosen).most_common(1)[0][0]
        chosen = [o for o in chosen if str(o["grid"]) == grid_key]
        arrs, valids, dates = [], [], []
        transform = crs = None
        for o in chosen:
            a, transform, crs = store.load_arrays(field_id, o["id"])
            arrs.append(a[index])
            valids.append(a["valid"])
            dates.append(o["date"])
        try:
            mean, valid = analysis.standardized_mean(arrs, valids)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return mean, valid, transform, crs, dates

    @app.get("/api/fields/{field_id}/zones")
    def zones(field_id: str, index: str = "ndvi", n: int = Query(3, ge=2, le=7), obs_id: str | None = None, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user)
        index_or_400(index)
        arr, valid, transform, crs, dates = zone_input(field_id, index, obs_id)
        try:
            z = analysis.management_zones(arr, valid, transform, crs, n)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        png, bounds = to_web_png(classes_rgba(z["classes"], n, ZONES), transform, crs)
        lo, hi = INDICES[index].display_range
        warning = None
        if obs_id and z["spread_p10_p90"] < 0.05 * (hi - lo):
            warning = (
                f"{INDICES[index].name} varies very little across this field on {dates[0]} "
                f"(p10–p90 spread {z['spread_p10_p90']:.3f}). Zones will mostly reflect noise; "
                "try multi-date zones or a red-edge index (NDRE/CIre) for closed canopies."
            )
        return {
            "index": index,
            "mode": "single-date" if obs_id else "multi-date",
            "dates": dates,
            "n": n,
            "breaks": z["breaks"],
            "units": "index value" if obs_id else "mean z-score across dates",
            "zones": z["zones"],
            "spread_p10_p90": z["spread_p10_p90"],
            "warning": warning,
            "png": _png_data_url(png),
            "bounds": bounds,
            "geojson": z["geojson"],
        }

    # ---- change --------------------------------------------------------------
    @app.get("/api/fields/{field_id}/change")
    def change(field_id: str, before: str, after: str, index: str = "ndvi", threshold: float = Query(0.1, gt=0), user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user)
        index_or_400(index)
        a, ta, crs_a, ma = arrays_or_404(field_id, before)
        b, tb, crs_b, mb = arrays_or_404(field_id, after)
        if ma["grid"] != mb["grid"]:
            raise HTTPException(409, "These dates come from different satellite tiles/grids; pick two dates from the same tile.")
        c = analysis.change(a[index], b[index], a["valid"] & b["valid"], threshold)
        overlap = round(float(c["valid"].sum()) / max(1, int(a["field"].sum())), 3)
        ring_a = ((ma.get("qa") or {}).get("ring") or {}).get(index)
        ring_b = ((mb.get("qa") or {}).get("ring") or {}).get(index)
        ring_change = round(ring_b - ring_a, 4) if ring_a is not None and ring_b is not None else None
        notes = []
        if overlap < 0.7:
            notes.append(f"Only {overlap:.0%} of the field was clear on both dates; the comparison covers that part only.")
        for m in (ma, mb):
            flags = [f for f in qa.classify([dict(m)])[0]["qa_flags"] if f["code"].startswith("haze")]
            if flags:
                notes.append(f"⚠ {m['date']} was hazy, so its values may read low.")
        lim = max(threshold * 2, 0.2)
        png, bounds = to_web_png(colorize(c["diff"], c["valid"], DIVERGING, -lim, lim), ta, crs_a)
        declines = analysis.hotspots(c["diff"], c["valid"], ta, crs_a, z=1.0, min_delta=threshold)
        return {
            "index": index,
            "before": ma["date"],
            "after": mb["date"],
            "stats": c["stats"],
            "declined_share": c["declined_share"],
            "improved_share": c["improved_share"],
            "declined_ha": c["declined_ha"],
            "improved_ha": c["improved_ha"],
            "threshold": threshold,
            "color_limit": lim,
            "png": _png_data_url(png),
            "bounds": bounds,
            "decline_hotspots": analysis.public(declines),
            "overlap": overlap,
            "ring_change": ring_change,
            "field_change": c["stats"]["median"],
            "notes": notes,
        }

    # ---- time series & export ------------------------------------------------
    @app.get("/api/fields/{field_id}/alerts")
    def alerts(field_id: str, index: str = "ndvi", user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
        field_or_404(field_id, user)
        index_or_400(index)
        return qa.assess(store, field_id, index)["alerts"]

    @app.get("/api/fields/{field_id}/timeseries.csv")
    def timeseries_csv(field_id: str, user: dict[str, Any] = Depends(require_user)) -> Response:
        f = field_or_404(field_id, user)
        buf = io.StringIO()
        w = csv.writer(buf)
        metrics = ("median", "mean", "p10", "p90", "cv")
        w.writerow(
            ["field_id", "date", "scene_id", "status", "qa_status", "qa_flags", "aot", "blue_canopy",
             "field_clear_fraction", "ring_ndvi_median", "processing_version"]
            + [f"{i}_{m}" for i in INDICES for m in metrics]
        )
        for o in qa.classify(store.list_observations(field_id)):
            oqa = o.get("qa") or {}
            row = [f["id"], o["date"], o["scene_id"], o["status"], o["qa_status"] or "",
                   ";".join(fl["code"] for fl in o["qa_flags"]), oqa.get("aot", ""), oqa.get("blue_canopy", ""),
                   o["field_clear_fraction"], (oqa.get("ring") or {}).get("ndvi", ""), o["processing_version"]]
            for i in INDICES:
                st = o["stats"].get(i) or {}
                row += [st.get(m, "") if st.get(m) is not None else "" for m in metrics]
            w.writerow(row)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{f["id"]}_timeseries.csv"'},
        )

    @app.get("/api/fields/{field_id}/score")
    def field_score(field_id: str, as_of: str | None = None, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        field_or_404(field_id, user)
        return field_score_for(field_id, as_of)

    app.include_router(make_router(accounts, store, require_user, lambda fid: field_score_for(fid)))
    app.include_router(make_feed_router(accounts, store, watcher, require_user, lambda fid, user: field_or_404(fid, user), news, prices))

    @app.exception_handler(FileExistsError)
    def conflict(_: Any, exc: FileExistsError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
