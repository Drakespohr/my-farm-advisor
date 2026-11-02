"""HTTP API and static web UI for field monitoring."""

from __future__ import annotations

import base64
import csv
import io
import logging
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import PROCESSING_VERSION, __version__, analysis
from .catalog import search_scenes
from .indices import DIVERGING, INDICES, ZONES, describe
from .pipeline import process_observation
from .render import classes_rgba, colorize, to_web_png
from .store import Store, git_commit

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class FieldIn(BaseModel):
    name: str | None = None
    field_id: str | None = None
    crop: str | None = None
    notes: str | None = None
    geojson: dict[str, Any] = Field(description="Polygon, Feature, or FeatureCollection in WGS84 lon/lat")


class FieldPatch(BaseModel):
    name: str | None = None
    crop: str | None = None
    notes: str | None = None
    archived: bool | None = None


class ProcessIn(BaseModel):
    start: date
    end: date
    max_cloud: float = Field(60.0, ge=0, le=100, description="Scene-level cloud % filter; field-level masking happens after.")


class Job:
    def __init__(self, field_id: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.field_id = field_id
        self.state: Literal["searching", "processing", "done", "error"] = "searching"
        self.total = 0
        self.done = 0
        self.clear = 0
        self.errors: list[str] = []
        self.source: str | None = None
        self.lock = threading.Lock()

    def view(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("id", "field_id", "state", "total", "done", "clear", "errors", "source")}


def _png_data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _parse_features(geojson: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    t = geojson.get("type")
    if t == "FeatureCollection":
        return [(f["geometry"], f.get("properties") or {}) for f in geojson.get("features", []) if f.get("geometry")]
    if t == "Feature":
        return [(geojson["geometry"], geojson.get("properties") or {})]
    if t in ("Polygon", "MultiPolygon"):
        return [(geojson, {})]
    raise ValueError("Expected a GeoJSON Polygon, MultiPolygon, Feature, or FeatureCollection.")


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store.from_env()
    app = FastAPI(title="Field Monitor", version=__version__)
    jobs: dict[str, Job] = {}
    jobs_lock = threading.Lock()
    scene_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scene")

    def field_or_404(field_id: str) -> dict[str, Any]:
        try:
            return store.get_field(field_id)
        except KeyError:
            raise HTTPException(404, f"Unknown field {field_id!r}") from None

    def clear_obs(field_id: str) -> list[dict[str, Any]]:
        return [o for o in store.list_observations(field_id) if o["status"] == "clear"]

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
            "app_version": __version__,
            "processing_version": PROCESSING_VERSION,
            "git_commit": git_commit(),
            "data_dir": str(store.root),
        }

    @app.get("/api/indices")
    def indices() -> list[dict[str, object]]:
        return [describe(i) for i in INDICES.values()]

    # ---- fields --------------------------------------------------------------
    @app.get("/api/fields")
    def list_fields(include_archived: bool = False) -> list[dict[str, Any]]:
        out = []
        for f in store.list_fields(include_archived):
            obs = store.list_observations(f["id"])
            clear = [o for o in obs if o["status"] == "clear"]
            latest = clear[-1] if clear else None
            out.append(
                {
                    **f,
                    "observations": len(obs),
                    "clear_observations": len(clear),
                    "latest": {"date": latest["date"], "ndvi": latest["stats"]["ndvi"]["median"], "ndre": latest["stats"]["ndre"]["median"]}
                    if latest
                    else None,
                }
            )
        return out

    @app.post("/api/fields", status_code=201)
    def create_fields(body: FieldIn) -> dict[str, Any]:
        try:
            feats = _parse_features(body.geojson)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from None
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
                    source="api",
                )
                (created if is_new else existing).append(rec["id"])
            except (ValueError, FileExistsError) as exc:
                errors.append({"feature": n, "name": name, "error": str(exc)})
        if errors and not (created or existing):
            raise HTTPException(400, {"errors": errors})
        return {"created": created, "existing": existing, "errors": errors}

    @app.get("/api/fields/{field_id}")
    def get_field(field_id: str) -> dict[str, Any]:
        return field_or_404(field_id)

    @app.patch("/api/fields/{field_id}")
    def patch_field(field_id: str, body: FieldPatch) -> dict[str, Any]:
        field_or_404(field_id)
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(400, "Nothing to change.")
        return store.update_field_meta(field_id, **changes)

    # ---- scenes & processing -------------------------------------------------
    @app.get("/api/fields/{field_id}/scenes")
    def scenes(field_id: str, start: date, end: date, max_cloud: float = 60.0) -> dict[str, Any]:
        f = field_or_404(field_id)
        found, source = search_scenes(f["geometry"], start, end, max_cloud)
        done = {o["scene_id"]: o for o in store.list_observations(field_id)}
        return {
            "source": source,
            "scenes": [
                {**s.summary(), "processed": s.id in done, "status": done[s.id]["status"] if s.id in done else None}
                for s in found
            ],
        }

    def run_job(job: Job, field: dict[str, Any], body: ProcessIn) -> None:
        try:
            found, job.source = search_scenes(field["geometry"], body.start, body.end, body.max_cloud)
            job.total = len(found)
            job.state = "processing"

            def one(scene: Any) -> None:
                try:
                    meta = process_observation(store, field, scene)
                    if meta["status"] == "clear":
                        with job.lock:
                            job.clear += 1
                except Exception as exc:  # keep going; record per-scene failures
                    log.exception("scene %s failed", scene.id)
                    with job.lock:
                        job.errors.append(f"{scene.id}: {exc}")
                finally:
                    with job.lock:
                        job.done += 1

            list(scene_pool.map(one, found))
            job.state = "done"
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.errors.append(str(exc))
            job.state = "error"

    @app.post("/api/fields/{field_id}/process", status_code=202)
    def process(field_id: str, body: ProcessIn) -> dict[str, Any]:
        f = field_or_404(field_id)
        if body.end < body.start:
            raise HTTPException(400, "end must be on or after start")
        if body.end - body.start > timedelta(days=400):
            raise HTTPException(400, "Process at most ~one season (400 days) per request.")
        job = Job(field_id)
        with jobs_lock:
            jobs[job.id] = job
        threading.Thread(target=run_job, args=(job, f, body), daemon=True).start()
        return job.view()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return job.view()

    # ---- observations --------------------------------------------------------
    @app.get("/api/fields/{field_id}/observations")
    def observations(field_id: str) -> list[dict[str, Any]]:
        field_or_404(field_id)
        return store.list_observations(field_id)

    @app.get("/api/fields/{field_id}/observations/{obs_id}/{name}.png")
    def obs_png(field_id: str, obs_id: str, name: str) -> FileResponse:
        try:
            path = store.png_path(field_id, obs_id, name)
        except KeyError:
            raise HTTPException(404) from None
        if not path.exists():
            raise HTTPException(404)
        # Observations are immutable, so browsers may cache them indefinitely.
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/fields/{field_id}/observations/{obs_id}/hotspots")
    def obs_hotspots(field_id: str, obs_id: str, index: str = "ndvi", min_area_ha: float = Query(0.2, ge=0.01)) -> dict[str, Any]:
        index_or_400(index)
        arrays, transform, crs, meta = arrays_or_404(field_id, obs_id)
        spots = analysis.hotspots(arrays[index], arrays["valid"], transform, crs, min_area_ha=min_area_ha)
        return {
            "obs_id": obs_id,
            "date": meta["date"],
            "index": index,
            "field_median": meta["stats"][index]["median"],
            "hotspots": spots,
            "method": "Contiguous patches more than max(1.5 x std, 0.05) below the field median, at least min_area_ha in size.",
        }

    # ---- zones ---------------------------------------------------------------
    def zone_input(field_id: str, index: str, obs_id: str | None) -> tuple[np.ndarray, np.ndarray, Any, str, list[str]]:
        if obs_id:
            arrays, transform, crs, meta = arrays_or_404(field_id, obs_id)
            return arrays[index], arrays["valid"], transform, crs, [meta["date"]]
        clear = clear_obs(field_id)
        if not clear:
            raise HTTPException(409, "No clear observations yet. Process some imagery first.")
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
    def zones(field_id: str, index: str = "ndvi", n: int = Query(3, ge=2, le=7), obs_id: str | None = None) -> dict[str, Any]:
        field_or_404(field_id)
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
    def change(field_id: str, before: str, after: str, index: str = "ndvi", threshold: float = Query(0.1, gt=0)) -> dict[str, Any]:
        field_or_404(field_id)
        index_or_400(index)
        a, ta, crs_a, ma = arrays_or_404(field_id, before)
        b, tb, crs_b, mb = arrays_or_404(field_id, after)
        if ma["grid"] != mb["grid"]:
            raise HTTPException(409, "These dates come from different satellite tiles/grids; pick two dates from the same tile.")
        c = analysis.change(a[index], b[index], a["valid"] & b["valid"], threshold)
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
            "decline_hotspots": declines,
        }

    # ---- time series & export ------------------------------------------------
    @app.get("/api/fields/{field_id}/alerts")
    def alerts(field_id: str, index: str = "ndvi") -> list[dict[str, Any]]:
        field_or_404(field_id)
        index_or_400(index)
        return analysis.timeseries_alerts(clear_obs(field_id), index)

    @app.get("/api/fields/{field_id}/timeseries.csv")
    def timeseries_csv(field_id: str) -> Response:
        f = field_or_404(field_id)
        buf = io.StringIO()
        w = csv.writer(buf)
        metrics = ("median", "mean", "p10", "p90", "cv")
        w.writerow(
            ["field_id", "date", "scene_id", "status", "field_clear_fraction", "processing_version"]
            + [f"{i}_{m}" for i in INDICES for m in metrics]
        )
        for o in store.list_observations(field_id):
            row = [f["id"], o["date"], o["scene_id"], o["status"], o["field_clear_fraction"], o["processing_version"]]
            for i in INDICES:
                st = o["stats"].get(i) or {}
                row += [st.get(m, "") if st.get(m) is not None else "" for m in metrics]
            w.writerow(row)
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{f["id"]}_timeseries.csv"'},
        )

    @app.exception_handler(FileExistsError)
    def conflict(_: Any, exc: FileExistsError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
