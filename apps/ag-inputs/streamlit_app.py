"""Streamlit prototype: log field events, see who is drafting what, and track record deadlines.

Run the API first, then:
    AG_AUTH_MODE=dev AG_API_URL=http://localhost:8000 streamlit run streamlit_app.py

Talks to the API over HTTP (ui_client.py), so membership and role checks apply exactly as
they do for any other client. Times are entered in AG_UI_TZ (default America/Chicago; use
America/Denver for Mountain-time fields) and sent to the API with their offset.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import streamlit as st

import ui_client as api
from ag_inputs import EVENT_KINDS, record_due_by

TZ = ZoneInfo(os.getenv("AG_UI_TZ", "America/Chicago"))
REFRESH = os.getenv("AG_UI_REFRESH_SECONDS", "10") + "s"
OTHER = "Other (type it)"
BLANK = "—"

# Standard fertilizer grades (N-P2O5-K2O %). Defaults only; every value stays editable.
FERTILIZER_GRADES: dict[str, tuple[float, float, float]] = {
    "Anhydrous ammonia": (82, 0, 0),
    "UAN 28%": (28, 0, 0),
    "UAN 32%": (32, 0, 0),
    "Urea": (46, 0, 0),
    "Ammonium sulfate": (21, 0, 0),
    "MAP 11-52-0": (11, 52, 0),
    "DAP 18-46-0": (18, 46, 0),
    "Ammonium polyphosphate 10-34-0": (10, 34, 0),
    "Potash 0-0-60": (0, 0, 60),
    "Manure": (0, 0, 0),
}
STAGES = {
    "corn": ["VE"] + [f"V{i}" for i in range(1, 19)] + ["VT"] + [f"R{i}" for i in range(1, 7)],
    "soybeans": ["VE", "VC"] + [f"V{i}" for i in range(1, 7)] + [f"R{i}" for i in range(1, 9)],
}
FOLIAR_CARRY = ("purpose", "active_ingredient", "rate_unit", "epa_reg_number", "restricted_use", "applicator_type")


@dataclass
class F:
    """One form input. type: select | pick (select + free text) | number | text | date | time | check."""
    name: str
    type: str
    label: str = ""
    options: Any = None  # list, or callable(ctx) -> list
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    help: Optional[str] = None
    on_change: Optional[Callable[..., None]] = None
    required: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _prev(kind: str, key: str) -> Callable[[dict[str, Any]], list[str]]:
    """Values already logged this season for a payload key (newest first)."""
    def opts(ctx: dict[str, Any]) -> list[str]:
        seen: list[str] = []
        for e in reversed(ctx["events"]):
            v = e["payload"].get(key)
            if e["kind"] == kind and v and v not in seen:
                seen.append(v)
        return seen
    return opts


def _fert_options(ctx: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(_prev("fertilizer", "product")(ctx) + list(FERTILIZER_GRADES)))


def _on_fert_product() -> None:
    grade = FERTILIZER_GRADES.get(st.session_state.get("fertilizer.product"))
    if grade:
        for k, v in zip(("n_pct", "p2o5_pct", "k2o_pct"), grade):
            st.session_state[f"fertilizer.{k}"] = float(v)
        if st.session_state["fertilizer.product"] == "Manure":
            st.session_state["fertilizer.method"] = "manure"


def _on_foliar_product() -> None:
    """Copy label details from the last time this product was logged (never invented)."""
    name = st.session_state.get("foliar.product")
    for e in reversed(st.session_state.get("_events", [])):
        if e["kind"] == "foliar" and e["payload"].get("product") == name:
            for k in FOLIAR_CARRY:
                if e["payload"].get(k) is not None:
                    st.session_state[f"foliar.{k}"] = e["payload"][k]
            return


def _stage_options(ctx: dict[str, Any]) -> list[str]:
    return STAGES.get(ctx["season"]["crop"], [])


FORMS: dict[str, list[F]] = {
    "planting": [
        F("population_k_per_ac", "number", "Population (k seeds/ac)", required=True, default=34.0, min=0.1, max=100, step=0.5),
        F("row_spacing_in", "select", "Row spacing (in)", options=[30.0, 15.0, 20.0, 22.0, 36.0, 38.0], default=30.0),
        F("seed_depth_in", "number", "Seed depth (in)", default=2.0, min=0.1, max=6, step=0.25),
        F("notes", "text", "Notes"),
    ],
    "stand_count": [
        F("plants_per_ac", "number", "Plants/ac", required=True, default=32000.0, min=0, max=500000, step=500),
        F("notes", "text", "Notes"),
    ],
    "growth_stage": [F("stage", "pick", "Stage", required=True, options=_stage_options)],
    "irrigation": [
        F("amount_in", "number", "Amount (in)", required=True, default=1.0, min=0.01, max=10, step=0.05),
        F("method", "select", "Method", options=["pivot", "drip", "furrow", "flood", "sprinkler", "other"], default="pivot"),
        F("source", "pick", "Source (well, canal)", options=_prev("irrigation", "source")),
    ],
    "rainfall": [
        F("amount_in", "number", "Amount (in)", required=True, default=0.5, min=0.0, max=30, step=0.05),
        F("source", "select", "Source", options=["on_farm_gauge", "weather_feed", "estimate"], default="on_farm_gauge"),
    ],
    "soil_moisture": [
        F("depth_in", "select", "Depth (in)", options=[6.0, 12.0, 18.0, 24.0, 36.0, 48.0], default=12.0),
        F("vwc_pct", "number", "Volumetric water (%)", min=0, max=100, step=0.5),
        F("tension_cb", "number", "Tension (cb)", min=0, max=200, step=1),
    ],
    "fertilizer": [
        F("product", "pick", "Product", required=True, options=_fert_options, on_change=_on_fert_product),
        F("method", "select", "Method", options=["pre_plant", "starter", "broadcast", "banded", "side_dress", "fertigation", "manure"], default="side_dress"),
        F("rate_lb_per_ac", "number", "Rate (lb product/ac)", required=True, default=100.0, min=0.1, max=1000, step=1),
        F("n_pct", "number", "N %", min=0, max=100, step=0.5),
        F("p2o5_pct", "number", "P2O5 %", min=0, max=100, step=0.5),
        F("k2o_pct", "number", "K2O %", min=0, max=100, step=0.5),
    ],
    "foliar": [
        F("product", "pick", "Product", required=True, options=_prev("foliar", "product"), on_change=_on_foliar_product,
          help="Products logged this season carry over their EPA number and RUP flag."),
        F("purpose", "select", "Purpose", options=["fungicide", "insecticide", "herbicide", "micronutrient", "biostimulant", "growth_regulator", "other"], default="fungicide"),
        F("active_ingredient", "text", "Active ingredient"),
        F("rate", "number", "Rate", required=True, default=None, min=0.001, step=0.1),
        F("rate_unit", "select", "Rate unit", options=["fl_oz/ac", "oz/ac", "pt/ac", "qt/ac", "gal/ac", "lb/ac"], default="fl_oz/ac"),
        F("epa_reg_number", "text", "EPA Reg. No.", help="From the label, e.g. 100-1070"),
        F("restricted_use", "check", "Restricted-use product", default=False),
        F("applicator_type", "select", "Applicator type", options=["private", "commercial", "noncommercial"], default="private"),
        F("applicator", "text", "Applicator name"),
        F("start_time", "time", "Start time"),
        F("location", "text", "Location", help="Field/area where applied"),
        F("area_treated_ac", "number", "Area treated (ac)", min=0.01, step=1, extra={"default_from": "acres"}),
        F("water_gal_per_ac", "number", "Carrier (gal/ac)", min=0.1, max=100, step=0.5),
        F("adjuvant", "text", "Adjuvant"),
        F("target", "pick", "Target pest/disease/weed", options=_prev("scouting", "target")),
        F("growth_stage", "pick", "Growth stage", options=_stage_options),
        F("temp_f", "number", "Temp (°F)", min=-40, max=130, step=1, help="Required for commercial/noncommercial"),
        F("wind_mph", "number", "Wind (mph)", min=0, max=100, step=1),
        F("humidity_pct", "number", "Humidity (%)", min=0, max=100, step=1),
    ],
    "scouting": [
        F("target", "pick", "Target", required=True, options=_prev("scouting", "target")),
        F("category", "select", "Category", options=["disease", "insect", "weed", "nutrient_deficiency", "abiotic", "other"], default="disease"),
        F("incidence_pct", "number", "Incidence (% plants)", required=True, default=0.0, min=0, max=100, step=1),
        F("severity_pct", "number", "Severity (% leaf area)", min=0, max=100, step=1),
        F("pattern", "select", "Pattern", options=["field_wide", "patchy", "edge", "point"], default="patchy"),
        F("growth_stage", "pick", "Growth stage", options=_stage_options),
        F("scout", "text", "Scout"),
    ],
    "soil_test": [
        F("sampled_on", "date", "Sampled on (field date)", required=True),
        F("depth_in", "select", "Depth (in)", options=[6.0, 8.0, 12.0, 24.0, 36.0, 48.0], default=6.0),
        F("lab", "pick", "Lab", options=_prev("soil_test", "lab")),
        F("nitrate_n_ppm", "number", "Nitrate-N (ppm)", min=0, step=0.5),
        F("ph", "number", "pH", min=0, max=14, step=0.1),
        F("organic_matter_pct", "number", "OM (%)", min=0, max=100, step=0.1),
        F("phosphorus_ppm", "number", "P (ppm)", min=0, step=1),
        F("potassium_ppm", "number", "K (ppm)", min=0, step=1),
        F("sulfur_ppm", "number", "S (ppm)", min=0, step=0.5),
        F("zinc_ppm", "number", "Zn (ppm)", min=0, step=0.1),
    ],
    "irrigation_water_sample": [
        F("nitrate_n_ppm", "number", "Nitrate-N (ppm)", required=True, min=0, max=500, step=0.1),
        F("source", "pick", "Well or source", options=_prev("irrigation", "source")),
        F("well_registration", "text", "Well registration no."),
        F("lab", "text", "Lab"),
        F("notes", "text", "Notes"),
    ],
    "manure_sample": [
        F("animal", "select", "Animal", options=["swine", "beef", "dairy", "poultry", "other"], default="swine"),
        F("form", "select", "Form", options=["liquid", "solid"], default="liquid",
          help="Liquid results are lb per 1,000 gal; solid are lb per ton"),
        F("total_n", "number", "Total N", required=True, min=0, max=1000, step=0.1),
        F("ammonium_n", "number", "Ammonium N", min=0, max=1000, step=0.1),
        F("p2o5", "number", "P2O5", min=0, max=1000, step=0.1),
        F("k2o", "number", "K2O", min=0, max=1000, step=0.1),
        F("dry_matter_pct", "number", "Dry matter (%)", min=0, max=100, step=0.5),
        F("source", "text", "Source (barn, lagoon, pile)"),
        F("lab", "text", "Lab"),
    ],
    "harvest": [
        F("yield_bu_per_ac", "number", "Yield (bu/ac)", required=True, default=None, min=0, max=1000, step=1),
        F("moisture_pct", "number", "Moisture (%)", min=0, max=100, step=0.1),
    ],
}
assert set(FORMS) == set(EVENT_KINDS), "every event kind needs a form"


# ---- helpers ---------------------------------------------------------------------------

def who() -> Optional[str]:
    return st.session_state.get("dev_user") or None


def call(method: str, path: str, **kw: Any) -> Any:
    r = api.call(method, path, who(), **kw)
    if r.status_code >= 400:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def countdown(due: dt.datetime, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    secs = (due - now).total_seconds()
    d, rem = divmod(abs(int(secs)), 86400)
    h, m = rem // 3600, rem % 3600 // 60
    span = f"{d}d {h}h" if d else f"{h}h {m}m"
    return f"{span} left" if secs >= 0 else f"overdue by {span}"


def local(ts: str) -> str:
    t = dt.datetime.fromisoformat(ts)
    t = t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t
    return t.astimezone(TZ).strftime("%b %d %H:%M")


def ago(ts: str) -> str:
    t = dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc)
    mins = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds() // 60)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago"


def due_for(kind: str, content: dict[str, Any]) -> Optional[dt.datetime]:
    """Deadline for a form's current values, using the API's own rule."""
    when = content.get("__date"), content.get("__time")
    if kind != "foliar" or not all(when):
        return None
    occurred = dt.datetime.combine(dt.date.fromisoformat(when[0]), dt.time.fromisoformat(when[1]), TZ)
    due = record_due_by(kind, content, occurred.astimezone(dt.timezone.utc).replace(tzinfo=None))
    return due.replace(tzinfo=dt.timezone.utc) if due else None


# ---- form state <-> drafts -------------------------------------------------------------

def _key(kind: str, name: str) -> str:
    return f"{kind}.{name}"


def form_content(kind: str) -> dict[str, Any]:
    """Current widget values for a form, JSON-ready (what a draft stores)."""
    out: dict[str, Any] = {}
    for f in FORMS[kind] + [F("__date", "date"), F("__time", "time")]:
        v = st.session_state.get(_key(kind, f.name))
        if f.type == "pick" and v == OTHER:
            v = st.session_state.get(_key(kind, f.name) + ".other") or None
        elif v == BLANK:
            v = None
        if isinstance(v, (dt.date, dt.time)):
            v = v.isoformat(timespec="minutes") if isinstance(v, dt.time) else v.isoformat()
        out[f.name] = v
    return out


def load_into_widgets(kind: str, content: dict[str, Any], ctx: dict[str, Any]) -> None:
    for f in FORMS[kind] + [F("__date", "date"), F("__time", "time")]:
        v = content.get(f.name)
        k = _key(kind, f.name)
        if v is None:
            st.session_state.pop(k, None)
        elif f.type == "number":
            st.session_state[k] = float(v)
        elif f.type == "date":
            st.session_state[k] = dt.date.fromisoformat(v)
        elif f.type == "time":
            st.session_state[k] = dt.time.fromisoformat(v)
        elif f.type == "pick" and v not in _options(f, ctx):
            st.session_state[k] = OTHER
            st.session_state[k + ".other"] = v
        else:
            st.session_state[k] = v


def clear_form(kind: str) -> None:
    for k in [k for k in st.session_state if str(k).startswith(kind + ".")]:
        del st.session_state[k]


def _options(f: F, ctx: dict[str, Any]) -> list[Any]:
    return list(f.options(ctx) if callable(f.options) else f.options or [])


def payload_from(kind: str, content: dict[str, Any]) -> dict[str, Any]:
    p = {k: v for k, v in content.items() if not k.startswith("__") and v not in (None, "")}
    if kind == "manure_sample":
        p["unit"] = "lb_per_ton" if p.get("form") == "solid" else "lb_per_1000_gal"
    return {"kind": kind, **p}


# ---- widgets ---------------------------------------------------------------------------

def render_field(kind: str, f: F, ctx: dict[str, Any]) -> None:
    k = _key(kind, f.name)
    label = (f.label or f.name) + (" *" if f.required else "")
    if k not in st.session_state:
        default = ctx["field"].get(f.extra["default_from"]) if "default_from" in f.extra else f.default
        if default is not None:
            st.session_state[k] = float(default) if f.type == "number" else default
    num = {n: None if v is None else float(v) for n, v in (("min_value", f.min), ("max_value", f.max), ("step", f.step))}
    if f.type == "select":
        opts = _options(f, ctx)
        st.selectbox(label, opts, key=k, help=f.help, on_change=f.on_change)
    elif f.type == "pick":
        opts = [BLANK] + _options(f, ctx) + [OTHER]
        if st.session_state.get(k) not in opts:
            st.session_state[k] = opts[0]
        st.selectbox(label, opts, key=k, help=f.help, on_change=f.on_change)
        if st.session_state[k] == OTHER:
            st.text_input(f"{label} (type)", key=k + ".other")
    elif f.type == "number":
        # Pass `value` only for a fresh widget; Streamlit rejects value + session-state together.
        st.number_input(label, **num, key=k, help=f.help,
                        placeholder="required" if f.required else "optional", **({} if k in st.session_state else {"value": None}))
    elif f.type == "text":
        st.text_input(label, key=k, help=f.help)
    elif f.type == "date":
        st.date_input(label, key=k, help=f.help,
                      **({} if k in st.session_state else {"value": dt.datetime.now(TZ).date()}))
    elif f.type == "time":
        st.time_input(label, key=k, step=dt.timedelta(minutes=5), help=f.help,
                      **({} if k in st.session_state else {"value": None}))
    elif f.type == "check":
        st.checkbox(label, key=k, help=f.help)


def event_form(ctx: dict[str, Any], can_write: bool) -> None:
    sid = ctx["season"]["id"]
    kind = st.selectbox("Record", EVENT_KINDS, format_func=lambda k: k.replace("_", " ").capitalize(), key="form_kind")

    # First time this form opens in the session: pick up my saved draft, if any.
    loaded = st.session_state.setdefault("_drafts_loaded", set())
    if (sid, kind) not in loaded:
        loaded.add((sid, kind))
        r = api.call("GET", f"/api/seasons/{sid}/drafts/{kind}", who())
        if r.status_code == 200:
            load_into_widgets(kind, r.json()["content"], ctx)
            st.toast("Loaded your draft")

    now = dt.datetime.now(TZ)
    c1, c2 = st.columns(2)
    st.session_state.setdefault(_key(kind, "__date"), now.date())
    st.session_state.setdefault(_key(kind, "__time"), now.time().replace(second=0, microsecond=0))
    with c1:
        st.date_input("Date", key=_key(kind, "__date"))
    with c2:
        st.time_input("Time", key=_key(kind, "__time"), step=dt.timedelta(minutes=5))
    cols = st.columns(2)
    for i, f in enumerate(FORMS[kind]):
        with cols[i % 2]:
            render_field(kind, f, ctx)

    content = form_content(kind)
    due = due_for(kind, content)
    if due:
        st.warning(f"⏱ Record due by {due.astimezone(TZ):%b %d %H:%M %Z} — {countdown(due)}")

    # Autosave my draft when the form changes, so others see I'm working on it.
    last = st.session_state.get(f"_saved.{sid}.{kind}")
    if can_write and last is not None and content != last:
        try:
            call("PUT", f"/api/seasons/{sid}/drafts/{kind}", json={"content": content})
        except RuntimeError as e:
            st.caption(f"Draft not saved: {e}")
    st.session_state[f"_saved.{sid}.{kind}"] = content

    b1, b2 = st.columns(2)
    if b1.button("Save record", type="primary", disabled=not can_write, use_container_width=True):
        occurred = dt.datetime.combine(dt.date.fromisoformat(content["__date"]), dt.time.fromisoformat(content["__time"]), TZ)
        try:
            out = call("POST", f"/api/seasons/{sid}/events",
                       json={"occurred_at": occurred.isoformat(), "payload": payload_from(kind, content)})
        except RuntimeError as e:
            st.error(str(e))
        else:
            api.call("DELETE", f"/api/seasons/{sid}/drafts/{kind}", who())
            clear_form(kind)
            st.session_state.pop(f"_saved.{sid}.{kind}", None)
            msg = f"Saved {kind.replace('_', ' ')} #{out['id']}"
            if out.get("record_due_by"):
                msg += f" · record due by {local(out['record_due_by'])}" + (" (recorded late)" if out["recorded_late"] else "")
            st.session_state["_flash"] = msg
            st.rerun()
    if b2.button("Discard draft", disabled=not can_write, use_container_width=True):
        api.call("DELETE", f"/api/seasons/{sid}/drafts/{kind}", who())
        clear_form(kind)
        st.session_state.pop(f"_saved.{sid}.{kind}", None)
        st.rerun()
    if not can_write:
        st.caption("Viewers can read records but not add them.")


@st.fragment(run_every=REFRESH)
def live_panel(sid: int) -> None:
    """Refreshes on a timer: who is drafting what, and record deadlines."""
    st.subheader("Open drafts")
    try:
        drafts = call("GET", f"/api/seasons/{sid}/drafts")
        events = call("GET", f"/api/seasons/{sid}/events", params={"kind": "foliar", "include_superseded": False})
    except RuntimeError as e:
        st.error(str(e))
        return
    if not drafts:
        st.caption("Nobody has a draft open.")
    for d in drafts:
        line = f"**{d['user_name']}** · {d['form_type'].replace('_', ' ')} · {ago(d['updated_at'])}"
        due = due_for(d["form_type"], d["content"]) if "content" in d else None
        if due:
            line += f" · ⏱ {countdown(due)}"
        st.markdown(line)

    st.subheader("Restricted-use & applicator records")
    tracked = [e for e in events if e.get("record_due_by")]
    if not tracked:
        st.caption("No applications with a record deadline this season.")
    for e in reversed(tracked):
        p = e["payload"]
        due = dt.datetime.fromisoformat(e["record_due_by"]).replace(tzinfo=dt.timezone.utc)
        status = "🔴 recorded late" if e["recorded_late"] else "🟢 on time"
        tag = "RUP" if p.get("restricted_use") else p.get("applicator_type", "")
        st.markdown(f"**{p['product']}** ({tag}, EPA {p.get('epa_reg_number') or '—'}) · applied {local(e['occurred_at'])}  \n"
                    f"due {local(e['record_due_by'])} · {status} · window {countdown(due)}")
    st.caption(f"Updated {dt.datetime.now(TZ):%H:%M:%S} · refreshes every {REFRESH}")


# ---- page ------------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Field records", page_icon="🌽", layout="wide")
    with st.sidebar:
        st.header("Field records")
        if os.getenv("AG_AUTH_MODE", "").lower() == "dev" and not os.getenv("AG_API_TOKEN"):
            st.text_input("Signed in as (dev)", key="dev_user", value=os.getenv("AG_UI_DEV_USER", ""),
                          help="Dev mode only: any id works. Cognito replaces this.")
        try:
            farms = call("GET", "/api/farms")
        except RuntimeError as e:
            st.error(f"Can't reach the API: {e}")
            return
        if not farms:
            st.info("You're not on any farm yet.")
            with st.form("new_farm"):
                name = st.text_input("Create a farm")
                if st.form_submit_button("Create") and name:
                    call("POST", "/api/farms", json={"name": name})
                    st.rerun()
            return
        farm = st.selectbox("Farm", farms, format_func=lambda f: f"{f['name']} ({f['role']})")
        fields = call("GET", f"/api/farms/{farm['id']}/fields")
        if not fields:
            st.info("No fields on this farm yet (add them through the API).")
            return
        fld = st.selectbox("Field", fields, format_func=lambda f: f["name"])
        seasons = call("GET", f"/api/fields/{fld['id']}/seasons")
        if not seasons:
            st.info("No seasons for this field yet.")
            return
        season = st.selectbox("Season", sorted(seasons, key=lambda s: -s["year"]), format_func=lambda s: f"{s['year']} {s['crop']}")

    sid = season["id"]
    events = call("GET", f"/api/seasons/{sid}/events", params={"include_superseded": False})
    st.session_state["_events"] = events
    ctx = {"season": season, "field": fld, "events": events}

    if msg := st.session_state.pop("_flash", None):
        st.success(msg)
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader(f"{fld['name']} · {season['year']} {season['crop']}")
        event_form(ctx, can_write=farm["role"] != "viewer")
    with right:
        live_panel(sid)
        st.subheader("Reports")
        r = api.call("GET", f"/api/seasons/{sid}/export.csv", who())
        if r.status_code == 200:
            st.download_button("Download season CSV", r.content, file_name=f"{fld['name']}_{season['year']}.csv", mime="text/csv")
        try:
            n = call("GET", f"/api/seasons/{sid}/n-summary")
            st.metric("Fertilizer N (lb/ac)", n["fertilizer_n_lb_per_ac"],
                      help=n["comparison"]["detail"])
        except RuntimeError:
            pass


main()
