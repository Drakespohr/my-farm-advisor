/* Farm and field log: list entries, add new ones, correct or void mistakes.
   Forms are generated from the server's record schemas (/api/log/kinds), so every activity type
   gets the same checks as the API. User-provided text is always set with textContent. */
(() => {
  "use strict";
  const FM = window.FM;
  const { el, api } = FM;
  let kindsInfo = null;
  const WRITE_ROLES = ["owner", "operator", "applicator", "agronomist"];

  // Most specific first: "_bu_per_ac" before "_per_ac".
  const UNITS = [[/_k_per_ac$/, "k/ac"], [/_lb_per_ac$/, "lb/ac"], [/_bu_per_ac$/, "bu/ac"], [/_gal_per_ac$/, "gal/ac"], [/_per_ac$/, "/ac"],
    [/_pct$/, "%"], [/_ppm$/, "ppm"], [/_in$/, "in"], [/_ac$/, "ac"], [/_f$/, "°F"], [/_mph$/, "mph"], [/_cb$/, "cb"]];
  function prettyLabel(name) {
    let unit = "";
    let base = name;
    for (const [re, u] of UNITS) if (re.test(name)) { unit = u; base = name.replace(re, ""); break; }
    const words = base.replace(/_/g, " ").replace(/\b(epa|n|p2o5|k2o|ph|vwc|rup)\b/gi, (m) => m.toUpperCase());
    return `${words.charAt(0).toUpperCase()}${words.slice(1)}${unit ? ` (${unit})` : ""}`;
  }
  function propType(prop) {
    const opts = prop.anyOf ? prop.anyOf.filter((x) => x.type !== "null") : [prop];
    const p = opts[0] || {};
    if (p.enum) return { type: "select", options: p.enum };
    if (p.const !== undefined) return { type: "const", value: p.const };
    if (p.type === "boolean") return { type: "check" };
    if (p.type === "number" || p.type === "integer") return { type: "number", min: p.minimum ?? p.exclusiveMinimum, max: p.maximum ?? p.exclusiveMaximum };
    if (p.format === "date") return { type: "date" };
    return { type: "text", maxLength: p.maxLength };
  }

  function localNow() {
    const d = new Date(); d.setSeconds(0, 0);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function localInput(iso) { // server times are UTC without offset
    const d = new Date(`${iso}Z`);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  const niceWhen = (iso) => new Date(`${iso}Z`).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });

  function summary(e) {
    const d = e.details || {};
    const n = (v, digits = 1) => (v == null ? null : Number(v).toFixed(digits).replace(/\.0+$/, ""));
    switch (e.kind) {
      case "fertilizer": return [d.product, `${n(d.rate_lb_per_ac)} lb/ac`, d.n_pct != null ? `${n(d.rate_lb_per_ac * d.n_pct / 100)} lb N/ac` : null, d.method && d.method.replace(/_/g, " ")];
      case "foliar": return [d.product, `${n(d.rate, 2)} ${d.rate_unit || ""}`, d.purpose, d.epa_reg_number ? `EPA ${d.epa_reg_number}` : null, d.restricted_use ? "restricted use" : null];
      case "scouting": return [d.target, `${n(d.incidence_pct, 0)}% of plants`, d.severity_pct != null ? `${n(d.severity_pct, 0)}% leaf area` : null, d.pattern && d.pattern.replace(/_/g, " ")];
      case "irrigation": case "rainfall": return [`${n(d.amount_in, 2)} in`, d.method || d.source];
      case "planting": return [`${n(d.population_k_per_ac)}k seeds/ac`, d.row_spacing_in ? `${n(d.row_spacing_in)}" rows` : null];
      case "harvest": return [`${n(d.yield_bu_per_ac)} bu/ac`, d.moisture_pct != null ? `${n(d.moisture_pct)}% moisture` : null];
      case "soil_test": return [`sampled ${d.sampled_on}`, `${n(d.depth_in)}" deep`, d.nitrate_n_ppm != null ? `nitrate ${n(d.nitrate_n_ppm)} ppm` : null, d.ph != null ? `pH ${n(d.ph)}` : null];
      case "note": return [d.text];
      default: return Object.entries(d).filter(([, v]) => v != null && v !== "").slice(0, 4).map(([k, v]) => `${prettyLabel(k)}: ${v}`);
    }
  }

  // Build an entry form for `kind`; `prefill` fills values (for corrections).
  function entryForm({ farmId, fields, fieldId, kind, prefill, supersedes, onSaved, onCancel }) {
    const schema = kindsInfo.schemas[kind];
    const form = el("form", { class: "log-form" });
    const msg = el("p", { class: "small", role: "status" });
    const when = el("input", { type: "datetime-local", required: "", value: prefill ? localInput(prefill.occurred_at) : localNow() });
    const farmLevel = kindsInfo.farm_level.includes(kind);
    let fieldSel = null;
    const grid = el("div", { class: "grid2" }, el("label", { class: "small" }, "When", when));
    if (!fieldId && !supersedes) {
      fieldSel = el("select", { required: farmLevel ? null : "" },
        farmLevel ? el("option", { value: "", text: "Whole farm" }) : el("option", { value: "", text: "Choose a field", disabled: "", selected: "" }),
        fields.map((f) => el("option", { value: f.id, text: f.name })));
      grid.append(el("label", { class: "small" }, "Where", fieldSel));
    }
    const inputs = {};
    const required = new Set(schema.required || []);
    for (const [name, prop] of Object.entries(schema.properties || {})) {
      if (name === "kind") continue;
      const t = propType(prop);
      const val = prefill ? prefill.details[name] : prop.default;
      const label = `${prettyLabel(name)}${required.has(name) ? " *" : ""}`;
      let inp;
      if (t.type === "select") {
        inp = el("select", {}, required.has(name) ? null : el("option", { value: "", text: "—" }),
          t.options.map((o) => el("option", { value: o, text: String(o).replace(/_/g, " "), ...(o === val ? { selected: "" } : {}) })));
      } else if (t.type === "check") {
        inp = el("input", { type: "checkbox", ...(val ? { checked: "" } : {}) });
      } else if (t.type === "number") {
        inp = el("input", { type: "number", step: "any", inputmode: "decimal", value: val ?? "", ...(t.min != null ? { min: t.min } : {}), ...(t.max != null ? { max: t.max } : {}) });
      } else if (t.type === "date") {
        inp = el("input", { type: "date", value: val ?? "" });
      } else if (name === "text" || name === "notes") {
        inp = el("textarea", { rows: "3", maxlength: t.maxLength || "4000", text: val ?? "" });
      } else {
        inp = el("input", { type: "text", value: val ?? "", ...(t.maxLength ? { maxlength: t.maxLength } : {}) });
      }
      if (required.has(name) && t.type !== "check") inp.required = true;
      inputs[name] = [inp, t];
      grid.append(el("label", { class: `small${t.type === "check" ? " check" : ""}${name === "text" ? " wide" : ""}` },
        t.type === "check" ? [inp, " ", label] : [label, inp]));
    }
    form.append(grid, msg, el("div", { class: "row gap" },
      el("button", { class: "btn primary", type: "submit", text: supersedes ? "Save correction" : "Save entry" }),
      el("button", { class: "btn ghost", type: "button", text: "Cancel", onclick: onCancel })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const details = {};
      for (const [name, [inp, t]] of Object.entries(inputs)) {
        if (t.type === "check") details[name] = inp.checked;
        else if (inp.value === "") continue;
        else details[name] = t.type === "number" ? Number(inp.value) : inp.value;
      }
      const body = { kind, details, occurred_at: new Date(when.value).toISOString(), field_id: fieldId || (fieldSel && fieldSel.value) || null };
      if (supersedes) body.supersedes_id = supersedes;
      try { onSaved(await api(`/api/farms/${encodeURIComponent(farmId)}/log`, { method: "POST", body: JSON.stringify(body) })); }
      catch (err) { msg.textContent = err.message; }
    });
    return form;
  }

  /* Render a log into `box`. opts: { farmId, fieldId (optional), fields: [{id,name}], role } */
  FM.renderLog = async (box, opts) => {
    kindsInfo = kindsInfo || await api("/api/log/kinds");
    const canWrite = WRITE_ROLES.includes(opts.role);
    const names = Object.fromEntries((opts.fields || []).map((f) => [f.id, f.name]));
    const path = opts.fieldId ? `/api/fields/${encodeURIComponent(opts.fieldId)}/log` : `/api/farms/${encodeURIComponent(opts.farmId)}/log`;
    let filterKind = "";
    const list = el("div", { class: "log-list" });
    const formBox = el("div", {});

    async function refresh() {
      const entries = await api(`${path}${filterKind ? `?kind=${filterKind}` : ""}`);
      list.replaceChildren();
      if (!entries.length) list.append(el("p", { class: "muted small", text: filterKind ? "Nothing of this kind yet." : "Nothing logged yet." }));
      for (const e of entries) {
        const parts = summary(e).filter(Boolean);
        const where = opts.fieldId ? null : (e.field_id ? (names[e.field_id] || "field") : "Whole farm");
        const due = e.record_due_by ? el("span", { class: `chip ${e.recorded_late ? "risk-high" : "risk-low"}`,
          text: e.recorded_late ? "record was late" : "record on time" }) : null;
        const row = el("div", { class: "log-row" },
          el("div", { class: "log-main" },
            el("div", {}, el("b", { text: e.label }), where ? el("span", { class: "muted", text: ` · ${where}` }) : null, " ", due),
            el("div", { class: "small", text: parts.join(" · ") }),
            el("div", { class: "muted small", text: `${niceWhen(e.occurred_at)} · ${e.recorded_by_name || "someone"}${e.supersedes_id ? " · corrected" : ""}${e.source !== "manual" ? ` · via ${e.source}` : ""}` })));
        if (canWrite) {
          row.append(el("div", { class: "log-actions" },
            el("button", { type: "button", class: "linkbtn small", text: "Correct", onclick: () => openForm(e.kind, e) }),
            el("button", { type: "button", class: "linkbtn small", text: "Void", onclick: async () => {
              if (!confirm(`Withdraw this ${e.label.toLowerCase()} entry? It stays in the history.`)) return;
              await api(`/api/farms/${encodeURIComponent(e.farm_id)}/log`, { method: "POST", body: JSON.stringify({ kind: e.kind, supersedes_id: e.id, void: true, occurred_at: new Date().toISOString() }) });
              refresh();
            } })));
        }
        list.append(row);
      }
    }

    function openForm(kind, prefill = null) {
      formBox.replaceChildren(entryForm({
        farmId: opts.farmId, fields: opts.fields || [], fieldId: opts.fieldId || (prefill && prefill.field_id), kind, prefill,
        supersedes: prefill ? prefill.id : null,
        onSaved: () => { formBox.replaceChildren(); refresh(); },
        onCancel: () => formBox.replaceChildren(),
      }));
      formBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    const head = el("div", { class: "row space wrap gap" });
    const filter = el("select", { "aria-label": "Show" }, el("option", { value: "", text: "All activity" }),
      kindsInfo.kinds.map((k) => el("option", { value: k, text: kindsInfo.schemas[k].label })));
    filter.addEventListener("change", () => { filterKind = filter.value; refresh(); });
    head.append(filter);
    if (canWrite) {
      const add = el("select", { "aria-label": "Add to log" }, el("option", { value: "", text: "＋ Add to log…" }),
        kindsInfo.kinds.map((k) => el("option", { value: k, text: kindsInfo.schemas[k].label })));
      add.addEventListener("change", () => { if (add.value) openForm(add.value); add.value = ""; });
      head.append(add);
    }
    box.replaceChildren(head, formBox, list);
    await refresh();
  };
})();
