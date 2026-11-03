"""Nebraska Natural Resources District (NRD) reporting configuration and checks.

IMPORTANT: nothing here is verified regulatory text. Every requirement, due date, threshold,
and target is a TODO until confirmed with the NRD. The README lists the open items.
The only rule entered so far came from the product owner as an example
(Central Platte: no fall nitrogen on sandy soils in Phases 2 and 3); its details
(what counts as "fall" and "sandy") are TODO too.

Structure:
- DATA_ITEMS: things this app can check in a season's records (app facts, not regulations).
- NRDS: one NRDConfig per NRD. `required_items` names DATA_ITEMS keys the NRD requires.
- check_season(): what's missing for a season against an NRD's configured requirements.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

TODO = "TODO: confirm with the NRD"


# ---------------------------------------------------------------------------
# What the app can check (data in this app, not rules)
# ---------------------------------------------------------------------------

def _effective(events: list[Any]) -> list[Any]:
    replaced = {e.supersedes_id for e in events if getattr(e, "supersedes_id", None) is not None}
    return [e for e in events if e.id not in replaced]


def _has(kind: str, pred: Callable[[dict[str, Any]], bool] = lambda p: True) -> Callable[[Any, list[Any]], bool]:
    return lambda season, events: any(e.kind == kind and pred(e.payload) for e in events)


@dataclass(frozen=True)
class DataItem:
    key: str
    label: str
    present: Callable[[Any, list[Any]], bool]


DATA_ITEMS: dict[str, DataItem] = {d.key: d for d in [
    DataItem("crop", "Crop", lambda s, ev: bool(s.crop)),
    DataItem("planting_date", "Planting date", lambda s, ev: s.planting_date is not None or any(e.kind == "planting" for e in ev)),
    DataItem("fertilizer_n", "Fertilizer applications with N content", _has("fertilizer", lambda p: p.get("n_pct") is not None)),
    DataItem("manure_application", "Manure applications", _has("fertilizer", lambda p: p.get("method") == "manure")),
    DataItem("manure_analysis", "Manure nutrient analysis", _has("manure_sample")),
    DataItem("soil_nitrate_test", "Soil nitrate test", _has("soil_test", lambda p: p.get("nitrate_n_ppm") is not None)),
    DataItem("irrigation_amounts", "Irrigation amounts", _has("irrigation")),
    DataItem("irrigation_water_nitrate", "Irrigation water nitrate sample", _has("irrigation_water_sample")),
    DataItem("rainfall", "Rainfall records", _has("rainfall")),
    DataItem("yield", "Harvest yield", _has("harvest")),
]}


# ---------------------------------------------------------------------------
# NRD configs (all values TODO unless noted)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportDue:
    name: str
    due: Optional[str]  # e.g. "03-01" (month-day); None = TODO
    note: str = TODO


@dataclass(frozen=True)
class Restriction:
    key: str
    description: str
    source: str
    phases: tuple[int, ...] = ()        # management-area phases it applies to; () = all
    params: dict[str, Any] = field(default_factory=dict)
    verified: bool = False


@dataclass(frozen=True)
class NRDConfig:
    key: str
    name: str
    required_items: tuple[str, ...] = ()       # DATA_ITEMS keys; empty = TODO
    report_due: tuple[ReportDue, ...] = ()     # empty = TODO
    restrictions: tuple[Restriction, ...] = ()
    nue_target_lb_n_per_bu: Optional[float] = None  # None = no verified target (TODO)
    verified: bool = False
    todo: tuple[str, ...] = ()


_STANDARD_TODO = (
    "Which records are required in the annual report (fill required_items)",
    "Report due date(s) and where/how reports are submitted",
    "Restrictions by management-area phase (e.g. fall/winter N timing, soil sampling, water sampling)",
    "Any nitrogen-use or NUE target used for comparison",
)

NRDS: dict[str, NRDConfig] = {c.key: c for c in [
    NRDConfig("upper_big_blue", "Upper Big Blue NRD", todo=_STANDARD_TODO),
    NRDConfig(
        "central_platte", "Central Platte NRD",
        restrictions=(
            Restriction(
                key="no_fall_n_sandy",
                description="No fall nitrogen application on sandy soils (Phases 2 and 3)",
                source="Product owner, unverified",
                phases=(2, 3),
                params={
                    # TODO: confirm the date window the NRD treats as "fall".
                    "fall_window": None,          # e.g. ("09-01", "12-31") once confirmed
                    # TODO: confirm which soil textures count as "sandy".
                    "sandy_textures": None,       # e.g. ("sand", "loamy sand") once confirmed
                    # TODO: confirm whether any products/practices are exempt.
                    "exemptions": None,
                },
            ),
        ),
        todo=_STANDARD_TODO + ("Fall window, sandy-soil definition, and exemptions for the no-fall-N rule",),
    ),
    NRDConfig("lower_loup", "Lower Loup NRD", todo=_STANDARD_TODO),
    NRDConfig("lower_elkhorn", "Lower Elkhorn NRD", todo=_STANDARD_TODO),
    # The Republican River basin has three NRDs; confirm which ones you need.
    NRDConfig("upper_republican", "Upper Republican NRD", todo=_STANDARD_TODO),
    NRDConfig("middle_republican", "Middle Republican NRD", todo=_STANDARD_TODO),
    NRDConfig("lower_republican", "Lower Republican NRD", todo=_STANDARD_TODO),
]}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _in_window(day: dt.date, window: tuple[str, str]) -> bool:
    start, end = (dt.date(day.year, int(w[:2]), int(w[3:])) for w in window)
    return start <= day <= end if start <= end else (day >= start or day <= end)


def _check_restriction(r: Restriction, field_info: dict[str, Any], events: list[Any]) -> dict[str, Any]:
    phase = field_info.get("nrd_phase")
    if r.phases and phase is not None and phase not in r.phases:
        return {"key": r.key, "status": "not_applicable", "detail": f"Field is in phase {phase}"}
    if r.key == "no_fall_n_sandy":
        missing = [k for k in ("fall_window", "sandy_textures") if not r.params.get(k)]
        if missing:
            return {"key": r.key, "status": "cannot_evaluate", "detail": f"Rule parameters not configured: {', '.join(missing)} ({TODO})"}
        if phase is None or not field_info.get("soil_texture"):
            return {"key": r.key, "status": "missing_field_info", "detail": "Set the field's NRD phase and soil texture"}
        if field_info["soil_texture"] not in r.params["sandy_textures"]:
            return {"key": r.key, "status": "ok", "detail": "Soil is not in the sandy list"}
        hits = [e for e in events if e.kind == "fertilizer" and (e.payload.get("n_pct") or 0) > 0
                and _in_window(e.occurred_at.date(), r.params["fall_window"])]
        if hits:
            return {"key": r.key, "status": "violation", "detail": f"{len(hits)} N application(s) in the fall window",
                    "event_ids": [e.id for e in hits]}
        return {"key": r.key, "status": "ok", "detail": "No fall N applications"}
    return {"key": r.key, "status": "cannot_evaluate", "detail": "No checker for this restriction yet"}


def check_season(season: Any, events: list[Any], nrd: str, field_info: Optional[dict[str, Any]] = None,
                 config: Optional[NRDConfig] = None) -> dict[str, Any]:
    """What's missing from a season's records for an NRD's configured requirements.

    `season` needs `.crop` and `.planting_date`; `events` are EventRow-like objects
    (kind, payload, occurred_at, id, supersedes_id). Corrections replace originals.
    """
    cfg = config or NRDS.get(nrd)
    if cfg is None:
        raise KeyError(f"Unknown NRD {nrd!r}. Known: {', '.join(NRDS)}")
    ev = _effective(events)
    missing = []
    for key in cfg.required_items:
        item = DATA_ITEMS[key]
        if not item.present(season, ev):
            missing.append({"key": key, "label": item.label})
    return {
        "nrd": cfg.key,
        "nrd_name": cfg.name,
        "verified": cfg.verified,
        "requirements_configured": bool(cfg.required_items),
        "missing": missing,
        "restrictions": [_check_restriction(r, field_info or {}, ev) for r in cfg.restrictions],
        "report_due": [{"name": r.name, "due": r.due, "note": r.note} for r in cfg.report_due],
        "todo": list(cfg.todo),
    }
