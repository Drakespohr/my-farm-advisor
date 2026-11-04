"""File-backed storage for fields and observations.

Layout (all under FIELD_MONITOR_DATA, default ./data):

    lineage.jsonl                          append-only log of every write
    fields/<field_id>/field.json           boundary + metadata (geometry is immutable)
    fields/<field_id>/obs/<obs_id>/        one folder per scene x processing version
        meta.json                          provenance, per-index stats, overlay bounds
        arrays.npz                         index rasters (UTM 10 m grid) + masks
        truecolor.png, <index>.png         Web Mercator overlays

Nothing is deleted or overwritten. Fields are archived, not removed. A change to the
processing code bumps PROCESSING_VERSION, which writes new observation folders.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from affine import Affine
from pyproj import Geod
from shapely.geometry import mapping, shape
from shapely.validation import make_valid

from . import PROCESSING_VERSION, __version__

_GEOD = Geod(ellps="WGS84")
MAX_FIELD_HA = 5000.0  # ~12,000 ac; larger shapes are almost always a drawing mistake
_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:48] or "field"


def geometry_area_ha(geometry: dict[str, Any]) -> float:
    area, _ = _GEOD.geometry_area_perimeter(shape(geometry))
    return abs(area) / 10_000.0


def normalize_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    g = shape(geometry)
    if g.has_z:
        from shapely import force_2d

        g = force_2d(g)
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        polys = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            raise ValueError("Field boundary must be a Polygon or MultiPolygon.")
        from shapely.ops import unary_union

        g = unary_union(polys)
    minx, miny, maxx, maxy = g.bounds
    if not (-180 <= minx <= 180 and -90 <= miny <= 90 and -180 <= maxx <= 180 and -90 <= maxy <= 90):
        raise ValueError("Coordinates must be WGS84 longitude/latitude (EPSG:4326).")
    return mapping(g)


def geometry_hash(geometry: dict[str, Any]) -> str:
    return hashlib.sha256(shape(geometry).wkb).hexdigest()[:12]


@dataclass
class Store:
    root: Path
    _array_cache: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> "Store":
        return cls(Path(os.environ.get("FIELD_MONITOR_DATA", "data")).resolve())

    # ---- lineage -------------------------------------------------------------
    def log(self, event: str, **data: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now_iso(), "event": event, "app_version": __version__, "git_commit": git_commit(), **data}
        with _LOCK, open(self.root / "lineage.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    # ---- fields --------------------------------------------------------------
    def _field_dir(self, field_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", field_id):
            raise KeyError(field_id)
        return self.root / "fields" / field_id

    def list_fields(self, include_archived: bool = False) -> list[dict[str, Any]]:
        base = self.root / "fields"
        if not base.exists():
            return []
        out = []
        for p in sorted(base.iterdir()):
            f = p / "field.json"
            if f.exists():
                rec = json.loads(f.read_text())
                if include_archived or not rec.get("archived"):
                    out.append(rec)
        return out

    def get_field(self, field_id: str) -> dict[str, Any]:
        f = self._field_dir(field_id) / "field.json"
        if not f.exists():
            raise KeyError(field_id)
        return json.loads(f.read_text())

    def create_field(
        self,
        geometry: dict[str, Any],
        name: str,
        *,
        field_id: str | None = None,
        crop: str | None = None,
        notes: str | None = None,
        source: str = "manual",
        replaces: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create a field. Returns (record, created). Re-importing the same boundary is a no-op."""
        geometry = normalize_geometry(geometry)
        ghash = geometry_hash(geometry)
        fid = slugify(field_id) if field_id else f"{slugify(name)}-{ghash[:6]}"
        d = self._field_dir(fid)
        with _LOCK:
            if (d / "field.json").exists():
                existing = json.loads((d / "field.json").read_text())
                if existing["geometry_hash"] != ghash:
                    raise FileExistsError(
                        f"Field {fid!r} already exists with a different boundary. "
                        "Boundaries are immutable; create a new field (e.g. '{fid}-2026') instead."
                    )
                return existing, False
            area = geometry_area_ha(geometry)
            if area > MAX_FIELD_HA:
                raise ValueError(
                    f"Boundary covers {area:,.0f} ha ({area * 2.471:,.0f} ac); fields are limited to {MAX_FIELD_HA:,.0f} ha. "
                    "Zoom in and draw a single field, or split the area."
                )
            d.mkdir(parents=True, exist_ok=True)
            rec = {
                "id": fid,
                "name": name,
                "crop": crop,
                "notes": notes,
                "geometry": geometry,
                "geometry_hash": ghash,
                "area_ha": round(area, 2),
                "area_ac": round(area * 2.471053814671653, 2),
                "source": source,
                "created": now_iso(),
                "archived": False,
                "replaces": replaces,
                "replaced_by": None,
            }
            (d / "field.json").write_text(json.dumps(rec, indent=2))
        self.log("field.created", field_id=fid, geometry_hash=ghash, source=source, replaces=replaces)
        return rec, True

    def update_field_meta(self, field_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"name", "crop", "notes", "archived", "showcase"}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"Cannot change {sorted(bad)}; only {sorted(allowed)} are editable.")
        with _LOCK:
            rec = self.get_field(field_id)
            before = {k: rec.get(k) for k in changes}
            rec.update(changes)
            (self._field_dir(field_id) / "field.json").write_text(json.dumps(rec, indent=2))
        self.log("field.updated", field_id=field_id, before=before, after=changes)
        return rec

    # ---- yield records (append-only) ----------------------------------------------
    YIELD_KINDS = ("expected", "harvest", "count")

    def add_yield_record(self, field_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Append an expected yield, harvest yield, or field count. Later entries for the same
        year win when reading; earlier ones are kept for the record."""
        self.get_field(field_id)  # KeyError if unknown
        if record.get("kind") not in self.YIELD_KINDS:
            raise ValueError(f"kind must be one of {', '.join(self.YIELD_KINDS)}")
        rec = {**record, "recorded": now_iso()}
        with _LOCK:
            with (self._field_dir(field_id) / "yields.jsonl").open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        self.log("yield.recorded", field_id=field_id, kind=rec["kind"], year=rec.get("year"))
        return rec

    def list_yield_records(self, field_id: str) -> list[dict[str, Any]]:
        p = self._field_dir(field_id) / "yields.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def redraw_field(self, field_id: str, geometry: dict[str, Any]) -> dict[str, Any]:
        """Save an edited boundary as a new field version and archive the old one.

        Boundaries are immutable so every observation stays tied to the shape it was
        measured on. The old version keeps its history and links forward via `replaced_by`.
        """
        old = self.get_field(field_id)
        if old.get("replaced_by"):
            raise FileExistsError(f"{field_id} was already replaced by {old['replaced_by']}; edit that version instead.")
        base = re.sub(r"-v\d+$", "", field_id)
        n = 2
        while (self.root / "fields" / f"{base}-v{n}").exists():
            n += 1
        new, _ = self.create_field(
            geometry, old["name"], field_id=f"{base}-v{n}", crop=old.get("crop"), notes=old.get("notes"),
            source="redraw", replaces=field_id,
        )
        with _LOCK:
            rec = self.get_field(field_id)
            rec.update(archived=True, replaced_by=new["id"])
            (self._field_dir(field_id) / "field.json").write_text(json.dumps(rec, indent=2))
        self.log("field.replaced", field_id=field_id, replaced_by=new["id"])
        return new

    # ---- observations --------------------------------------------------------
    @staticmethod
    def obs_id(scene_id: str, date: str) -> str:
        return f"{date}_{scene_id}_v{PROCESSING_VERSION}"

    def obs_dir(self, field_id: str, obs_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", obs_id):
            raise KeyError(obs_id)
        return self._field_dir(field_id) / "obs" / obs_id

    def list_observations(self, field_id: str, all_versions: bool = False) -> list[dict[str, Any]]:
        base = self._field_dir(field_id) / "obs"
        if not base.exists():
            return []
        out = []
        for p in sorted(base.iterdir()):
            m = p / "meta.json"
            if m.exists():
                rec = json.loads(m.read_text())
                if all_versions or rec.get("processing_version") == PROCESSING_VERSION:
                    out.append(rec)
        return sorted(out, key=lambda r: r["datetime"])

    def get_observation(self, field_id: str, obs_id: str) -> dict[str, Any]:
        m = self.obs_dir(field_id, obs_id) / "meta.json"
        if not m.exists():
            raise KeyError(obs_id)
        return json.loads(m.read_text())

    def write_observation(
        self, field_id: str, obs_id: str, meta: dict[str, Any], arrays: dict[str, np.ndarray], pngs: dict[str, bytes]
    ) -> None:
        d = self.obs_dir(field_id, obs_id)
        if (d / "meta.json").exists():
            raise FileExistsError(f"Observation {obs_id} already exists; refusing to overwrite.")
        tmp = d.with_name(f".{d.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        if arrays:
            np.savez_compressed(tmp / "arrays.npz", **arrays)
        for name, data in pngs.items():
            (tmp / f"{name}.png").write_bytes(data)
        (tmp / "meta.json").write_text(json.dumps(meta, indent=2))
        try:
            tmp.rename(d)  # atomic publish: readers never see a half-written observation
        except OSError:
            # Another worker published the same observation first; discard our scratch copy.
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            raise FileExistsError(f"Observation {obs_id} already exists; refusing to overwrite.")
        self.log("observation.created", field_id=field_id, obs_id=obs_id, scene_id=meta.get("scene_id"), status=meta.get("status"))

    def load_arrays(self, field_id: str, obs_id: str) -> tuple[dict[str, np.ndarray], Affine, str]:
        # Observations are immutable, so a small in-memory cache is always safe.
        key = (field_id, obs_id)
        hit = self._array_cache.get(key)
        if hit is not None:
            return hit
        out = self._load_arrays(field_id, obs_id)
        if len(self._array_cache) >= 256:
            self._array_cache.pop(next(iter(self._array_cache)))
        self._array_cache[key] = out
        return out

    def _load_arrays(self, field_id: str, obs_id: str) -> tuple[dict[str, np.ndarray], Affine, str]:
        meta = self.get_observation(field_id, obs_id)
        path = self.obs_dir(field_id, obs_id) / "arrays.npz"
        if not path.exists():
            raise FileNotFoundError(f"Observation {obs_id} has no rasters (status={meta.get('status')}).")
        with np.load(path) as npz:
            arrays = {k: npz[k] for k in npz.files}
        g = meta["grid"]
        return arrays, Affine(*g["transform"]), g["crs"]

    def png_path(self, field_id: str, obs_id: str, name: str) -> Path:
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise KeyError(name)
        return self.obs_dir(field_id, obs_id) / f"{name}.png"
