"""Separate atmospheric artifacts from real crop change.

Each clear observation gets a QA status:

- ``suspect``: excluded from trends, alerts, and multi-date zones (but never deleted):
  * ``haze``: high aerosol optical thickness or a bright blue band over canopy
    (thin cloud/smoke that the scene classification mask missed);
  * ``transient``: the field median jumps away from both the previous and the next clear
    pass, which agree with each other. Crops don't drop and fully recover within a
    few days, while missed clouds, haze, and shadows do.
- ``ok``: possibly with informational flags (``haze-caution``, ``partial``).

Alerts compare consecutive ``ok`` dates on the pixels that were clear on **both** dates,
so partly cloudy days don't masquerade as change. Each decline is then:

- ``pending`` until a later clear pass exists, ``confirmed`` if that pass is still low,
  ``recovered`` if it isn't;
- scoped ``field`` vs ``regional`` by comparing the same change in a ring 30–150 m outside
  the field. A drop that stops at the field edge points at management, disease,
  drainage, or pests; one that also hits the neighbors points at weather or atmosphere.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .store import Store

MAX_GAP_DAYS = 21
TRANSIENT_DELTA = 0.08  # departure from both neighbors to call a date transient
RECOVER_TOL = 0.04  # neighbors agree / value back within this
DECLINE_DELTA = 0.08
CANOPY_MIN = 0.4  # declines and patchiness only matter over an established canopy
MIN_OVERLAP = 0.5  # fraction of field pixels clear on both dates
REGIONAL_SHARE = 0.6  # ring drop >= 60% of field drop -> regional
PARTIAL_BELOW = 0.8


def _days(a: str, b: str) -> int:
    return int((np.datetime64(b) - np.datetime64(a)).astype(int))


def _median(o: dict[str, Any], index: str) -> float | None:
    return ((o.get("stats") or {}).get(index) or {}).get("median")


def classify(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate observations in place with `qa_status` and `qa_flags`. Returns the same list."""
    clear = [o for o in observations if o.get("status") == "clear"]
    for o in observations:
        o["qa_flags"] = []
        o["qa_status"] = None if o.get("status") != "clear" else "ok"
    for o in clear:
        qa = o.get("qa") or {}
        if qa.get("haze") == "suspect":
            o["qa_flags"].append({"code": "haze", "suspect": True, "detail": "; ".join(qa.get("haze_reasons", []))})
        elif qa.get("haze") == "caution":
            o["qa_flags"].append({"code": "haze-caution", "suspect": False, "detail": "; ".join(qa.get("haze_reasons", []))})
        if o.get("field_clear_fraction", 1) < PARTIAL_BELOW:
            o["qa_flags"].append(
                {"code": "partial", "suspect": False, "detail": f"only {o['field_clear_fraction']:.0%} of the field was clear"}
            )

    # Transient outliers judged on NDVI against non-hazy neighbors.
    usable = [o for o in clear if not any(f["suspect"] for f in o["qa_flags"]) and _median(o, "ndvi") is not None]
    for i in range(1, len(usable) - 1):
        prev, cur, nxt = usable[i - 1], usable[i], usable[i + 1]
        if _days(prev["date"], cur["date"]) > MAX_GAP_DAYS or _days(cur["date"], nxt["date"]) > MAX_GAP_DAYS:
            continue
        p, c, n = _median(prev, "ndvi"), _median(cur, "ndvi"), _median(nxt, "ndvi")
        if abs(n - p) <= RECOVER_TOL and abs(c - p) >= TRANSIENT_DELTA and abs(c - n) >= TRANSIENT_DELTA:
            direction = "dip" if c < p else "spike"
            cur["qa_flags"].append(
                {
                    "code": "transient",
                    "suspect": True,
                    "detail": f"one-pass NDVI {direction} ({p:.2f} → {c:.2f} → {n:.2f}) that reverted by {nxt['date']}",
                }
            )
    for o in clear:
        if any(f["suspect"] for f in o["qa_flags"]):
            o["qa_status"] = "suspect"
    return observations


def _paired_medians(store: Store, field_id: str, a: dict[str, Any], b: dict[str, Any], index: str) -> tuple[float, float, float | None]:
    """Medians of `index` on pixels clear on both dates, plus the overlap fraction of the field."""
    if a.get("grid") != b.get("grid"):
        return _median(a, index), _median(b, index), None
    try:
        arr_a, _, _ = store.load_arrays(field_id, a["id"])
        arr_b, _, _ = store.load_arrays(field_id, b["id"])
    except (KeyError, FileNotFoundError):
        return _median(a, index), _median(b, index), None
    both = arr_a["valid"] & arr_b["valid"]
    n_field = int(arr_a["field"].sum()) or 1
    overlap = float(both.sum()) / n_field
    if both.sum() < 20:
        return _median(a, index), _median(b, index), overlap
    return float(np.median(arr_a[index][both])), float(np.median(arr_b[index][both])), overlap


def _ring_change(a: dict[str, Any], b: dict[str, Any], index: str) -> float | None:
    ra, rb = (a.get("qa") or {}).get("ring") or {}, (b.get("qa") or {}).get("ring") or {}
    if ra.get(index) is None or rb.get(index) is None:
        return None
    return float(rb[index] - ra[index])


def alerts(store: Store, field_id: str, observations: list[dict[str, Any]], index: str = "ndvi") -> list[dict[str, Any]]:
    ok = [o for o in observations if o.get("qa_status") == "ok" and _median(o, index) is not None]
    if len(ok) < 2:
        return []
    peak = int(np.argmax([_median(o, index) for o in ok]))
    name = index.upper()
    out: list[dict[str, Any]] = []
    for i in range(1, len(ok)):
        prev, cur = ok[i - 1], ok[i]
        gap = _days(prev["date"], cur["date"])
        if gap > MAX_GAP_DAYS:
            continue
        m_prev, m_cur, overlap = _paired_medians(store, field_id, prev, cur, index)
        if overlap is not None and overlap < MIN_OVERLAP:
            continue  # too little common clear ground to compare fairly
        drop = m_prev - m_cur
        later = ok[i + 1 :]
        base = {
            "date": cur["date"],
            "compared_to": prev["date"],
            "index": index,
            "overlap": round(overlap, 3) if overlap is not None else None,
        }

        if drop >= DECLINE_DELTA and m_prev >= CANOPY_MIN:
            ring = _ring_change(prev, cur, index)
            if ring is None:
                scope = "unknown"
            elif -ring >= REGIONAL_SHARE * drop:
                scope = "regional"
            else:
                scope = "field"
            later_vals = [_median(o, index) for o in later]
            recovered_any = any(v >= m_prev - RECOVER_TOL for v in later_vals)
            if not later:
                status = "pending"
            else:
                _, m_next, _ = _paired_medians(store, field_id, prev, later[0], index)
                status = "confirmed" if m_next <= m_prev - drop / 2 else "recovered"

            where = {
                "field": "Neighbors outside the field held steady, so this is specific to this field (management, disease, pests, drainage, hail strip).",
                "regional": f"Neighbors outside the field changed too ({ring:+.2f}), so this is likely weather, haze, or area-wide crop stage.",
                "unknown": "Not enough clear ground outside the field to tell field-specific from regional.",
            }[scope]
            change = f"Median {name} {m_prev:.2f} → {m_cur:.2f} ({-drop:+.2f}) over {gap} days"
            if overlap is not None:
                change += f", measured on the {overlap:.0%} of the field clear on both dates"

            post_peak = i > peak and not recovered_any
            if post_peak and status == "confirmed" and scope == "regional":
                out.append({**base, "kind": "senescence", "severity": "info", "status": status, "scope": scope, "change": round(-drop, 4),
                            "ring_change": ring, "message": f"{change}. After the seasonal peak with no recovery: consistent with maturity, dry-down, or harvest. {where}"})
                continue
            if status == "pending":
                severity, lead = "pending", "Possible decline, waiting for the next clear pass to confirm."
            elif status == "recovered":
                severity, lead = "info", f"Decline did not hold: {name} recovered on {later[0]['date']}."
            elif scope == "regional":
                severity, lead = "medium", "Confirmed decline, shared with neighboring land."
            else:
                severity, lead = ("high" if drop >= 0.15 else "medium"), "Confirmed decline on the next clear pass."
            action = " Scout the hotspot list for this date." if status == "confirmed" and scope != "regional" else ""
            if post_peak and status != "pending" and scope != "regional":
                action += " This came after the seasonal peak; if this field matures earlier than its neighbors, it may be normal dry-down."
            elif status == "pending" and i > peak:
                action += " If the crop is near maturity, this may turn out to be normal dry-down."
            out.append({**base, "kind": "decline", "severity": severity, "status": status, "scope": scope, "change": round(-drop, 4),
                        "ring_change": ring, "message": f"{lead} {change}. {where}{action}"})

        pst, st = prev["stats"][index], cur["stats"][index]
        if (
            st.get("cv") is not None
            and pst.get("cv") is not None
            and st["cv"] - pst["cv"] >= 0.05
            and st["cv"] >= 0.12
            and m_cur >= CANOPY_MIN
        ):
            nxt_cv = later[0]["stats"][index].get("cv") if later else None
            status = "pending" if nxt_cv is None else ("confirmed" if nxt_cv >= st["cv"] - 0.03 else "recovered")
            if status == "recovered":
                continue
            out.append({**base, "kind": "patchiness", "severity": "medium" if status == "confirmed" else "pending", "status": status,
                        "scope": "field", "message": f"Within-field variability rose (CV {pst['cv']:.2f} → {st['cv']:.2f})"
                        + (" and held on the next pass." if status == "confirmed" else "; waiting for the next clear pass.")
                        + " Check the hotspot list for where to scout."})
    return out


def assess(store: Store, field_id: str, index: str = "ndvi") -> dict[str, Any]:
    obs = classify(store.list_observations(field_id))
    return {"observations": obs, "alerts": alerts(store, field_id, obs, index)}
