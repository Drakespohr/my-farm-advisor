"""Keep saved fields up to date with new satellite passes.

- A new field is backfilled with the last `INITIAL_DAYS` of imagery.
- After that, each sync looks back `OVERLAP_DAYS` before the previous sync (scenes can be
  published a few days after acquisition) through today. Already-processed scenes are
  skipped without any download, so overlapping windows cost almost nothing.
- A background scheduler syncs every active field whose last sync is older than
  `refresh_hours`. Only one job runs per field at a time.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from . import PROCESSING_VERSION
from .catalog import load_scene, search_scenes
from .pipeline import process_observation
from .store import Store, now_iso

log = logging.getLogger(__name__)

INITIAL_DAYS = 365
OVERLAP_DAYS = 14
MAX_WINDOW_DAYS = 400


class Job:
    def __init__(self, field_id: str, start: date, end: date, reason: str, max_cloud: float = 60.0) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.field_id = field_id
        self.start = start
        self.end = end
        self.reason = reason  # "new-field" | "scheduled" | "manual" | "range"
        self.max_cloud = max_cloud
        self.state: Literal["searching", "processing", "done", "error"] = "searching"
        self.total = 0
        self.done = 0
        self.clear = 0
        self.new = 0
        self.errors: list[str] = []
        self.source: str | None = None
        self.started_at = now_iso()
        self.finished_at: str | None = None
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.state in ("searching", "processing")

    def view(self) -> dict[str, Any]:
        keys = ("id", "field_id", "reason", "state", "total", "done", "clear", "new", "errors", "source", "started_at", "finished_at")
        out = {k: getattr(self, k) for k in keys}
        out.update(start=self.start.isoformat(), end=self.end.isoformat())
        return out


class Syncer:
    def __init__(self, store: Store, refresh_hours: float = 12.0, max_cloud: float = 60.0) -> None:
        self.store = store
        self.refresh_hours = refresh_hours
        self.max_cloud = max_cloud
        self.jobs: dict[str, Job] = {}
        self._by_field: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scene")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- status --------------------------------------------------------------
    def _status_path(self, field_id: str):
        return self.store.root / "fields" / field_id / "sync.json"

    def status(self, field_id: str) -> dict[str, Any]:
        p = self._status_path(field_id)
        st: dict[str, Any] = json.loads(p.read_text()) if p.exists() else {}
        job = self._by_field.get(field_id)
        st["job"] = job.view() if job else None
        st["auto_refresh_hours"] = self.refresh_hours or None
        if self.refresh_hours and st.get("last_synced"):
            nxt = datetime.fromisoformat(st["last_synced"]) + timedelta(hours=self.refresh_hours)
            st["next_check"] = nxt.replace(microsecond=0).isoformat()
        return st

    def _write_status(self, field_id: str, job: Job) -> None:
        rec = {
            "last_synced": job.finished_at,
            "last_window": [job.start.isoformat(), job.end.isoformat()],
            "last_reason": job.reason,
            "last_result": {"scenes": job.total, "clear": job.clear, "new": job.new, "source": job.source},
            "last_error": "; ".join(job.errors[:3]) or None,
        }
        p = self._status_path(field_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2))
        tmp.replace(p)

    # ---- jobs ----------------------------------------------------------------
    def window_for(self, field_id: str, today: date | None = None) -> tuple[date, date]:
        today = today or date.today()
        p = self._status_path(field_id)
        last = json.loads(p.read_text()).get("last_synced") if p.exists() else None
        if not last:
            return today - timedelta(days=INITIAL_DAYS), today
        start = datetime.fromisoformat(last).date() - timedelta(days=OVERLAP_DAYS)
        return max(start, today - timedelta(days=MAX_WINDOW_DAYS)), today

    def start(
        self, field_id: str, reason: str, start: date | None = None, end: date | None = None, max_cloud: float | None = None
    ) -> Job:
        """Start a sync for the field, or return the one already running."""
        with self._lock:
            cur = self._by_field.get(field_id)
            if cur and cur.running:
                return cur
            field = self.store.get_field(field_id)
            if start is None or end is None:
                start, end = self.window_for(field_id)
            job = Job(field_id, start, end, reason, self.max_cloud if max_cloud is None else max_cloud)
            self.jobs[job.id] = job
            self._by_field[field_id] = job
        threading.Thread(target=self._run, args=(job, field), daemon=True, name=f"sync-{field_id}").start()
        return job

    def _run(self, job: Job, field: dict[str, Any]) -> None:
        try:
            before = {o["scene_id"] for o in self.store.list_observations(field["id"])}
            found, job.source = search_scenes(field["geometry"], job.start, job.end, job.max_cloud)
            # After a processing-version bump, re-run every scene this field already had so
            # its history reappears under the new version (old results stay on disk).
            have = before | {s.id for s in found}
            stale = self.stale_scene_hrefs(field["id"], exclude=have)
            if stale:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    found += [s for s in pool.map(self._try_load, stale) if s is not None]
            job.total = len(found)
            job.state = "processing"

            def one(scene: Any) -> None:
                try:
                    meta = process_observation(self.store, field, scene)
                    with job.lock:
                        if meta["status"] == "clear":
                            job.clear += 1
                        if scene.id not in before:
                            job.new += 1
                except Exception as exc:  # keep going; record per-scene failures
                    log.exception("scene %s failed", scene.id)
                    with job.lock:
                        job.errors.append(f"{scene.id}: {exc}")
                finally:
                    with job.lock:
                        job.done += 1

            list(self._pool.map(one, found))
            job.finished_at = now_iso()
            # Persist status before flipping state so pollers never see "done" without it.
            self._write_status(job.field_id, job)
            job.state = "done"
        except Exception as exc:
            log.exception("sync %s failed", job.id)
            job.errors.append(str(exc))
            job.finished_at = job.finished_at or now_iso()
            job.state = "error"
        finally:
            self.store.log("sync.finished", field_id=job.field_id, **{k: v for k, v in job.view().items() if k != "field_id"})

    def stale_scene_hrefs(self, field_id: str, exclude: set[str] | None = None) -> list[str]:
        exclude = exclude or set()
        current = {o["scene_id"] for o in self.store.list_observations(field_id)}
        hrefs = {}
        for o in self.store.list_observations(field_id, all_versions=True):
            sid = o.get("scene_id")
            href = (o.get("provenance") or {}).get("scene_href")
            if o.get("processing_version") != PROCESSING_VERSION and sid not in current and sid not in exclude and href:
                hrefs[sid] = href
        return list(hrefs.values())

    @staticmethod
    def _try_load(href: str) -> Any:
        try:
            return load_scene(href)
        except Exception as exc:  # scene metadata moved or unreachable; skip it
            log.warning("could not reload %s: %s", href, exc)
            return None

    def wait(self, job: Job, timeout: float = 600.0) -> None:
        t0 = time.monotonic()
        while job.running and time.monotonic() - t0 < timeout:
            time.sleep(0.2)

    # ---- scheduler -----------------------------------------------------------
    def due_fields(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        due = []
        for f in self.store.list_fields():
            last = self.status(f["id"]).get("last_synced")
            if not last or now - datetime.fromisoformat(last) >= timedelta(hours=self.refresh_hours):
                due.append(f["id"])
            elif self.stale_scene_hrefs(f["id"]):
                due.append(f["id"])  # processing upgraded: rebuild history now, not in 12 h
        return due

    def _loop(self, poll_seconds: float, startup_delay: float) -> None:
        if self._stop.wait(startup_delay):
            return
        while not self._stop.is_set():
            try:
                for fid in self.due_fields():
                    if self._stop.is_set():
                        break
                    # One field at a time keeps load on the public imagery service polite.
                    self.wait(self.start(fid, "scheduled"))
            except Exception:
                log.exception("scheduler pass failed")
            self._stop.wait(poll_seconds)

    def start_scheduler(self, poll_seconds: float = 300.0, startup_delay: float = 20.0) -> None:
        if not self.refresh_hours or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, args=(poll_seconds, startup_delay), daemon=True, name="field-sync")
        self._thread.start()
        log.info("auto-refresh every %s h", self.refresh_hours)

    def stop(self) -> None:
        self._stop.set()
