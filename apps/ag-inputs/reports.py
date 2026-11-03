"""Per-field season reports: CSV export and nitrogen summary (inputs for NUE and NRD reports).

Pure functions over season/field/event objects; ag_inputs.py wires them to endpoints.
Pass only effective events (corrections applied) unless you want history.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Callable, Iterable, Optional

import nrd_rules

# Sections in export order. Each maps an event kind to the columns it fills.
EXPORT_KINDS = ("planting", "irrigation", "rainfall", "fertilizer", "foliar", "scouting", "soil_test",
                "irrigation_water_sample", "manure_sample", "harvest")

COLUMNS = [
    "field_id", "field_name", "farm_id", "acres", "nrd", "season_id", "year", "crop", "variety", "planting_date",
    "section", "event_id", "occurred_at", "recorded_by", "supersedes_id",
    "product", "method_or_type", "amount", "unit", "n_lb_per_ac",
    "epa_reg_number", "restricted_use", "applicator_type", "record_due_by", "recorded_late",
    "details",
]

# Lb of N per acre from 1 inch of water at 1 ppm nitrate-N:
# 1 acre-inch = 27,154 gal x 8.34 lb/gal = 226,464 lb water; 1 ppm of that = 0.2265 lb.
LB_N_PER_ACRE_INCH_PPM = 0.2265


def fertilizer_n_lb_per_ac(payload: dict[str, Any]) -> float:
    """Same formula the diagnosis context uses: product rate x N%."""
    return payload["rate_lb_per_ac"] * (payload.get("n_pct") or 0) / 100


def _main_columns(kind: str, p: dict[str, Any]) -> dict[str, Any]:
    if kind == "planting":
        return {"amount": p.get("population_k_per_ac"), "unit": "k_seeds/ac"}
    if kind in ("irrigation", "rainfall"):
        return {"method_or_type": p.get("method") or p.get("source"), "amount": p.get("amount_in"), "unit": "in"}
    if kind == "fertilizer":
        return {"product": p.get("product"), "method_or_type": p.get("method"), "amount": p.get("rate_lb_per_ac"),
                "unit": "lb/ac", "n_lb_per_ac": round(fertilizer_n_lb_per_ac(p), 2)}
    if kind == "foliar":
        return {"product": p.get("product"), "method_or_type": p.get("purpose"), "amount": p.get("rate"),
                "unit": p.get("rate_unit"), "epa_reg_number": p.get("epa_reg_number"),
                "restricted_use": p.get("restricted_use", False), "applicator_type": p.get("applicator_type", "private")}
    if kind == "scouting":
        return {"product": p.get("target"), "method_or_type": p.get("category"), "amount": p.get("incidence_pct"), "unit": "% incidence"}
    if kind == "soil_test":
        return {"method_or_type": "soil_test", "amount": p.get("nitrate_n_ppm"), "unit": "ppm nitrate-N"}
    if kind == "irrigation_water_sample":
        return {"product": p.get("source"), "amount": p.get("nitrate_n_ppm"), "unit": "ppm nitrate-N"}
    if kind == "manure_sample":
        return {"product": p.get("animal"), "method_or_type": p.get("form"), "amount": p.get("total_n"),
                "unit": f"lb total N {'per ton' if p.get('unit') == 'lb_per_ton' else 'per 1000 gal'}"}
    if kind == "harvest":
        return {"amount": p.get("yield_bu_per_ac"), "unit": "bu/ac"}
    return {}


def _safe(value: Any) -> Any:
    """Keep spreadsheet apps from running user text as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_rows(field: Any, season: Any, events: Iterable[Any],
                deadline: Callable[[Any], dict[str, Any]]) -> list[dict[str, Any]]:
    base = {
        "field_id": field.id, "field_name": field.name, "farm_id": field.farm_id, "acres": field.acres,
        "nrd": field.nrd, "season_id": season.id, "year": season.year, "crop": season.crop,
        "variety": season.variety, "planting_date": season.planting_date.isoformat() if season.planting_date else None,
    }
    order = {k: i for i, k in enumerate(EXPORT_KINDS)}
    rows = []
    for e in sorted((e for e in events if e.kind in order), key=lambda e: (order[e.kind], e.occurred_at, e.id)):
        main = _main_columns(e.kind, e.payload)
        used = {"kind", "product", "method", "purpose", "rate", "rate_unit", "rate_lb_per_ac", "amount_in",
                "epa_reg_number", "restricted_use", "applicator_type"}
        rows.append({
            **base, "section": e.kind, "event_id": e.id, "occurred_at": e.occurred_at.isoformat(),
            "recorded_by": e.recorded_by, "supersedes_id": e.supersedes_id, **main, **deadline(e),
            "details": json.dumps({k: v for k, v in e.payload.items() if k not in used and v is not None},
                                  sort_keys=True, default=str),
        })
    return rows


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _safe(v) for k, v in r.items()})
    return buf.getvalue()


def n_summary(field: Any, season: Any, events: list[Any], nrd: Optional[str] = None) -> dict[str, Any]:
    """Season N applied per acre, with lb N per bushel compared to the NRD's NUE target if one is configured."""
    fert = [e for e in events if e.kind == "fertilizer"]
    applications = [{"event_id": e.id, "date": e.occurred_at.date().isoformat(), "product": e.payload["product"],
                     "method": e.payload["method"], "n_lb_per_ac": round(fertilizer_n_lb_per_ac(e.payload), 1)}
                    for e in fert]
    fert_n = sum(fertilizer_n_lb_per_ac(e.payload) for e in fert)
    missing_n_pct = [e.id for e in fert if e.payload.get("n_pct") is None]

    irrigation_in = sum(e.payload["amount_in"] for e in events if e.kind == "irrigation")
    water = [e.payload["nitrate_n_ppm"] for e in events if e.kind == "irrigation_water_sample"]
    water_ppm = sum(water) / len(water) if water else None
    water_n = irrigation_in * water_ppm * LB_N_PER_ACRE_INCH_PPM if water_ppm is not None else None

    yields = [e.payload["yield_bu_per_ac"] for e in events if e.kind == "harvest"]
    yield_bu = yields[-1] if yields else None
    per_bu = round(fert_n / yield_bu, 3) if yield_bu else None

    key = nrd or field.nrd
    cfg = nrd_rules.NRDS.get(key) if key else None
    target = cfg.nue_target_lb_n_per_bu if cfg else None
    if cfg is None:
        comparison = {"status": "no_nrd", "detail": "Field has no NRD set"}
    elif target is None:
        comparison = {"status": "no_target", "detail": f"{cfg.name} has no NUE target configured ({nrd_rules.TODO})"}
    elif per_bu is None:
        comparison = {"status": "no_yield", "detail": "Log harvest yield to compare"}
    else:
        comparison = {"status": "at_or_below_target" if per_bu <= target else "above_target",
                      "detail": f"{per_bu} lb N/bu vs target {target}"}

    return {
        "field_id": field.id, "season_id": season.id, "year": season.year, "crop": season.crop,
        "acres": field.acres,
        "fertilizer_n_lb_per_ac": round(fert_n, 1),
        "fertilizer_n_lb_total": round(fert_n * field.acres, 0) if field.acres else None,
        "applications": applications,
        "applications_missing_n_pct": missing_n_pct,
        "irrigation_in": round(irrigation_in, 2),
        "irrigation_water_nitrate_ppm": round(water_ppm, 2) if water_ppm is not None else None,
        # Estimate only; whether an NRD credits irrigation-water N is a TODO per NRD.
        "irrigation_water_n_lb_per_ac_estimate": round(water_n, 1) if water_n is not None else None,
        "yield_bu_per_ac": yield_bu,
        "fertilizer_n_lb_per_bu": per_bu,
        "nrd": cfg.key if cfg else key,
        "nue_target_lb_n_per_bu": target,
        "comparison": comparison,
    }
