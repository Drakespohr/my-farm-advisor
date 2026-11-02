/* Field Monitor web UI. Plain JS + Leaflet; all names from the API are inserted with textContent. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  const state = {
    indices: [],
    indexByKey: {},
    fields: [],
    field: null,
    obs: [],
    indexKey: "ndvi",
    obsId: null,
    zonesGeojson: null,
  };

  // ---------- helpers ------------------------------------------------------
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return node;
  }
  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }
  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch (_) { /* keep status text */ }
      throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }
  const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
  const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  const isoToday = () => new Date().toISOString().slice(0, 10);
  const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  const gradient = (cmap) => `linear-gradient(to right, ${cmap.map(([p, c]) => `${rgb(c)} ${p * 100}%`).join(", ")})`;
  function colorAt(cmap, t) {
    t = Math.max(0, Math.min(1, t));
    for (let i = 1; i < cmap.length; i++) {
      const [p1, c1] = cmap[i];
      const [p0, c0] = cmap[i - 1];
      if (t <= p1) {
        const f = (t - p0) / (p1 - p0 || 1);
        return [0, 1, 2].map((k) => Math.round(c0[k] + f * (c1[k] - c0[k])));
      }
    }
    return cmap[cmap.length - 1][1];
  }

  // ---------- map ----------------------------------------------------------
  const map = L.map("map", { zoomControl: true, doubleClickZoom: false }).setView([41.9, -93.6], 7);
  const basemaps = {
    Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    }),
    Streets: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap contributors",
    }),
  };
  basemaps.Satellite.addTo(map);
  L.control.layers(basemaps, {}, { position: "bottomright" }).addTo(map);
  L.control.scale({ imperial: true, metric: true }).addTo(map);

  const fieldsLayer = L.featureGroup().addTo(map);
  const overlayGroup = L.layerGroup().addTo(map);
  const markersLayer = L.layerGroup().addTo(map);
  let overlay = null;

  function setOverlay(url, bounds, legend) {
    overlayGroup.clearLayers();
    overlay = null;
    if (url) {
      overlay = L.imageOverlay(url, bounds, { opacity: Number($("opacity").value), className: "pixelated", interactive: false });
      overlayGroup.addLayer(overlay);
    }
    renderLegend(legend);
  }
  $("opacity").addEventListener("input", (e) => overlay && overlay.setOpacity(Number(e.target.value)));

  function renderLegend(legend) {
    const box = $("map-legend");
    box.replaceChildren();
    if (!legend) { box.hidden = true; return; }
    box.hidden = false;
    box.append(el("div", { text: legend.title, style: "font-weight:600" }));
    if (legend.items) {
      for (const it of legend.items) {
        box.append(el("div", {}, el("span", { class: "swatch", style: `background:${it.color}` }), it.label));
      }
    } else {
      box.append(el("div", { class: "ramp", style: `background:${gradient(legend.cmap)}` }));
      box.append(el("div", { class: "ticks" }, el("span", { text: legend.lo }), el("span", { text: legend.hi })));
      if (legend.note) box.append(el("div", { class: "muted", text: legend.note }));
    }
  }

  // ---------- drawing ------------------------------------------------------
  const draw = { active: false, pts: [], line: null, markers: L.layerGroup().addTo(map) };
  function drawRedraw() {
    draw.markers.clearLayers();
    if (draw.line) map.removeLayer(draw.line);
    draw.pts.forEach((p) => draw.markers.addLayer(L.circleMarker(p, { radius: 4, color: "#fff", weight: 2, fillColor: "#1a7f45", fillOpacity: 1 })));
    draw.line = draw.pts.length > 1 ? L.polygon(draw.pts, { color: "#fde047", weight: 2, dashArray: "4 4", fillOpacity: 0.1 }).addTo(map) : null;
    $("draw-finish").disabled = draw.pts.length < 3;
  }
  function drawStop() {
    draw.active = false;
    draw.pts = [];
    drawRedraw();
    $("draw-form").hidden = true;
    map.getContainer().style.cursor = "";
  }
  $("draw-btn").addEventListener("click", () => {
    draw.active = true;
    draw.pts = [];
    drawRedraw();
    $("draw-form").hidden = false;
    map.getContainer().style.cursor = "crosshair";
    $("draw-name").focus();
  });
  map.on("click", (e) => {
    if (!draw.active) return;
    draw.pts.push([e.latlng.lat, e.latlng.lng]);
    drawRedraw();
  });
  map.on("dblclick", () => { if (draw.active && draw.pts.length >= 3) saveDrawn(); });
  $("draw-undo").addEventListener("click", () => { draw.pts.pop(); drawRedraw(); });
  $("draw-cancel").addEventListener("click", drawStop);
  $("draw-finish").addEventListener("click", saveDrawn);
  async function saveDrawn() {
    const ring = draw.pts.map(([lat, lng]) => [lng, lat]);
    ring.push(ring[0]);
    const name = $("draw-name").value.trim() || "New field";
    try {
      const res = await api("/api/fields", {
        method: "POST",
        body: JSON.stringify({ name, crop: $("draw-crop").value.trim() || null, geojson: { type: "Polygon", coordinates: [ring] } }),
      });
      drawStop();
      $("draw-name").value = "";
      $("draw-crop").value = "";
      await loadFields();
      const id = res.created[0] || res.existing[0];
      if (id) selectField(id);
    } catch (err) {
      $("import-msg").textContent = `Could not save: ${err.message}`;
    }
  }

  $("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const msg = $("import-msg");
    msg.textContent = "Importing…";
    try {
      const geojson = JSON.parse(await file.text());
      const res = await api("/api/fields", { method: "POST", body: JSON.stringify({ geojson }) });
      msg.textContent = `${res.created.length} created, ${res.existing.length} already present` +
        (res.errors.length ? `, ${res.errors.length} skipped (${res.errors[0].error})` : "");
      await loadFields();
      fitAllFields();
    } catch (err) {
      msg.textContent = `Import failed: ${err.message}`;
    }
  });

  // ---------- fields -------------------------------------------------------
  async function loadFields() {
    state.fields = await api(`/api/fields?include_archived=${$("show-archived").checked}`);
    const list = $("field-list");
    list.replaceChildren();
    fieldsLayer.clearLayers();
    $("no-fields").hidden = state.fields.length > 0;
    const ndvi = state.indexByKey.ndvi;
    for (const f of state.fields) {
      const latest = f.latest;
      const sw = latest && ndvi
        ? el("span", { class: "swatch", style: `background:${rgb(colorAt(ndvi.colormap, (latest.ndvi - ndvi.display_range[0]) / (ndvi.display_range[1] - ndvi.display_range[0])))}` })
        : null;
      const btn = el("button", { "data-id": f.id, onclick: () => selectField(f.id) },
        el("span", { class: "name", text: f.name + (f.archived ? " (archived)" : "") }),
        el("span", { class: "val" }, sw, latest ? `NDVI ${fmt(latest.ndvi)}` : ""),
        el("span", { class: "sub", text: `${fmt(f.area_ac, 1)} ac${f.crop ? " · " + f.crop : ""} · ${latest ? "latest clear " + latest.date : "no imagery yet"}` }),
      );
      if (state.field && state.field.id === f.id) btn.classList.add("active");
      list.append(el("li", {}, btn));
      const layer = L.geoJSON(f.geometry, { style: { color: "#fde047", weight: 2, fillOpacity: 0 } });
      layer.on("click", (e) => { if (!draw.active) { L.DomEvent.stopPropagation(e); selectField(f.id); } });
      layer.bindTooltip(f.name, { sticky: true });
      fieldsLayer.addLayer(layer);
    }
  }
  function fitAllFields() {
    if (fieldsLayer.getLayers().length) map.fitBounds(fieldsLayer.getBounds(), { padding: [20, 20] });
  }
  $("show-archived").addEventListener("change", loadFields);

  async function selectField(id) {
    const f = state.fields.find((x) => x.id === id) || (await api(`/api/fields/${id}`));
    state.field = f;
    document.querySelectorAll(".field-list button").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
    $("detail").hidden = false;
    $("f-name").textContent = f.name;
    $("f-meta").textContent = `${f.id} · ${fmt(f.area_ac, 1)} ac (${fmt(f.area_ha, 1)} ha)${f.crop ? " · " + f.crop : ""}`;
    $("archive-btn").textContent = f.archived ? "Unarchive" : "Archive";
    $("csv-link").href = `/api/fields/${encodeURIComponent(id)}/timeseries.csv`;
    map.fitBounds(L.geoJSON(f.geometry).getBounds(), { padding: [30, 30], maxZoom: 16, animate: false });
    if (location.hash.slice(1) !== id) history.replaceState(null, "", `#${encodeURIComponent(id)}`);
    $("sidebar").classList.remove("open");
    markersLayer.clearLayers();
    $("zones-out").replaceChildren();
    $("change-out").replaceChildren();
    await loadObservations();
  }

  $("archive-btn").addEventListener("click", async () => {
    if (!state.field) return;
    const archived = !state.field.archived;
    await api(`/api/fields/${state.field.id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
    await loadFields();
    if (!archived || $("show-archived").checked) selectField(state.field.id);
    else { $("detail").hidden = true; state.field = null; setOverlay(null); }
  });

  // ---------- processing ---------------------------------------------------
  function initDates() {
    const today = new Date();
    const start = new Date(today.getFullYear(), 3, 1); // Apr 1 this season
    if (start > today) start.setFullYear(today.getFullYear() - 1);
    $("p-start").value = start.toISOString().slice(0, 10);
    $("p-end").value = isoToday();
  }

  $("process-btn").addEventListener("click", async () => {
    if (!state.field) return;
    const btn = $("process-btn");
    btn.disabled = true;
    $("job").hidden = false;
    $("job-text").textContent = "Searching for scenes…";
    $("job-bar").style.width = "0";
    try {
      let job = await api(`/api/fields/${state.field.id}/process`, {
        method: "POST",
        body: JSON.stringify({ start: $("p-start").value, end: $("p-end").value, max_cloud: Number($("p-cloud").value) }),
      });
      let lastDone = -1;
      while (job.state === "searching" || job.state === "processing") {
        await new Promise((r) => setTimeout(r, 1500));
        job = await api(`/api/jobs/${job.id}`);
        if (job.total) $("job-bar").style.width = `${(100 * job.done) / job.total}%`;
        $("job-text").textContent = job.state === "searching"
          ? "Searching for scenes…"
          : `Processed ${job.done} of ${job.total} scenes · ${job.clear} clear over this field`;
        if (job.done !== lastDone && job.done % 4 === 0) { lastDone = job.done; await loadObservations(true); }
      }
      $("job-text").textContent = job.state === "done"
        ? `Done: ${job.total} scenes (${job.clear} clear over the field) via ${job.source}.` + (job.errors.length ? ` ${job.errors.length} failed: ${job.errors[0]}` : "")
        : `Failed: ${job.errors.join("; ")}`;
      $("job-bar").style.width = "100%";
      await loadObservations();
      await loadFields();
    } catch (err) {
      $("job-text").textContent = `Failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- observations -------------------------------------------------
  async function loadObservations(keepSelection = false) {
    if (!state.field) return;
    state.obs = await api(`/api/fields/${state.field.id}/observations`);
    const clear = state.obs.filter((o) => o.status === "clear");
    if (!keepSelection || !state.obs.some((o) => o.id === state.obsId)) {
      state.obsId = clear.length ? clear[clear.length - 1].id : (state.obs.length ? state.obs[state.obs.length - 1].id : null);
    }
    renderObsSelect();
    renderChangeSelects();
    renderTable();
    renderChart();
    renderAlerts();
    showSelected();
  }

  function visibleObs() {
    return $("show-cloudy").checked ? state.obs : state.obs.filter((o) => o.status === "clear" || o.id === state.obsId);
  }

  function renderObsSelect() {
    const sel = $("obs-select");
    sel.replaceChildren();
    for (const o of visibleObs().slice().reverse()) {
      const label = o.status === "clear"
        ? `${o.date} · ${state.indexByKey[state.indexKey].name} ${fmt(o.stats[state.indexKey].median)}`
        : `${o.date} · cloudy (${pct(o.field_clear_fraction)} clear)`;
      sel.append(el("option", { value: o.id, text: label }));
    }
    if (state.obsId) sel.value = state.obsId;
  }
  $("obs-select").addEventListener("change", (e) => { state.obsId = e.target.value; showSelected(); renderChart(); });
  $("show-cloudy").addEventListener("change", () => { renderObsSelect(); renderChart(); });

  function renderIndexSelect() {
    const sel = $("index-select");
    sel.replaceChildren();
    for (const i of state.indices) sel.append(el("option", { value: i.key, text: `${i.name} — ${i.measures.split(";")[0].replace(/\.$/, "")}` }));
    sel.append(el("option", { value: "truecolor", text: "True color (what the satellite saw)" }));
    sel.value = state.indexKey;
  }
  $("index-select").addEventListener("change", (e) => {
    state.indexKey = e.target.value;
    renderObsSelect();
    renderIndexInfo();
    renderChart();
    renderAlerts();
    showSelected();
  });

  function renderIndexInfo() {
    const box = $("index-info");
    box.replaceChildren();
    if (state.indexKey === "truecolor") {
      box.append(el("div", {}, el("b", { text: "True color. " }), "Natural-color composite (B04, B03, B02). Use it to sanity-check index maps: clouds, haze, tillage patterns, standing water."));
      return;
    }
    const i = state.indexByKey[state.indexKey];
    box.append(
      el("div", {}, el("b", { text: `${i.name}. ` }), i.measures, " ", el("span", { class: "formula", text: i.formula })),
      el("div", {}, el("b", { text: "Best for: " }), i.best_for),
      el("div", {}, el("b", { text: "Caveats: " }), i.caveats),
      el("div", { class: "muted" }, "Rough guide: ", i.guide.map(([v, l]) => `≈${v} ${l}`).join(" · "), `. Higher = ${i.higher_is}. Confirm with scouting.`),
    );
  }

  function currentObs() { return state.obs.find((o) => o.id === state.obsId) || null; }

  function showSelected() {
    const o = currentObs();
    markersLayer.clearLayers();
    if (!o || !state.field) { setOverlay(null); $("hotspots").replaceChildren(); return; }
    const base = `/api/fields/${state.field.id}/observations/${o.id}`;
    const showTrue = state.indexKey === "truecolor" || o.status !== "clear";
    if (showTrue) {
      setOverlay(`${base}/truecolor.png`, o.bounds, {
        title: `True color · ${o.date}`, items: o.status === "clear" ? [] : [{ color: "#ccc", label: `Cloudy: ${pct(o.field_clear_fraction)} of field clear` }],
      });
    } else {
      const i = state.indexByKey[state.indexKey];
      const st = o.stats[i.key];
      setOverlay(`${base}/${i.key}.png`, o.bounds, {
        title: `${i.name} · ${o.date}`, cmap: i.colormap, lo: i.display_range[0], hi: i.display_range[1],
        note: `Field median ${fmt(st.median)} · CV ${fmt(st.cv)} · ${pct(o.field_clear_fraction)} clear`,
      });
    }
    loadHotspots(o);
  }

  async function loadHotspots(o) {
    const box = $("hotspots");
    box.replaceChildren();
    if (o.status !== "clear") { box.append(el("p", { class: "small muted", text: "This date is cloudy over the field; pick a clear date." })); return; }
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    let res;
    try { res = await api(`/api/fields/${state.field.id}/observations/${o.id}/hotspots?index=${key}`); }
    catch (err) { box.append(el("p", { class: "small", text: err.message })); return; }
    if (o.id !== state.obsId) return;
    const name = state.indexByKey[key].name;
    if (!res.hotspots.length) {
      box.append(el("p", { class: "small", text: `No significant low ${name} patches on ${res.date}. The field looks uniform at 10 m.` }));
      return;
    }
    const table = el("table", { class: "table small" });
    table.append(el("tr", {}, ["#", "Location (lat, lon)", "Area", `${name}`, "Below median", ""].map((h) => el("th", { text: h }))));
    for (const h of res.hotspots) {
      const coords = `${h.lat.toFixed(5)}, ${h.lon.toFixed(5)}`;
      const outline = L.geoJSON(h.geometry, { style: { color: "#ff3b30", weight: 2, fillOpacity: 0.05 } });
      const marker = L.marker([h.lat, h.lon], { title: `Hotspot ${h.rank}` })
        .bindPopup(`<b>Hotspot ${h.rank}</b><br>${coords}<br>${h.area_ac} ac · ${name} ${fmt(h.mean)} (−${fmt(h.deficit)})`);
      markersLayer.addLayer(outline);
      markersLayer.addLayer(marker);
      table.append(el("tr", { class: "clickable", onclick: () => { map.setView([h.lat, h.lon], 17); marker.openPopup(); } },
        el("td", { text: String(h.rank) }),
        el("td", { text: coords }),
        el("td", { text: `${fmt(h.area_ac, 1)} ac` }),
        el("td", { text: fmt(h.mean) }),
        el("td", { text: `−${fmt(h.deficit)}` }),
        el("td", {}, el("a", { href: `https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lon}`, target: "_blank", rel: "noopener", text: "Directions", onclick: (e) => e.stopPropagation() })),
      ));
    }
    box.append(el("p", { class: "small", text: `Field median ${name} ${fmt(res.field_median)} on ${res.date}.` }), el("div", { class: "table-wrap" }, table));
  }

  function renderAlerts() {
    const box = $("alerts");
    box.replaceChildren();
    if (!state.field || state.indexKey === "truecolor") return;
    api(`/api/fields/${state.field.id}/alerts?index=${state.indexKey}`).then((alerts) => {
      for (const a of alerts.slice(-3).reverse()) {
        box.append(el("div", { class: `alert ${a.severity}`, role: "status" },
          el("span", { class: "icon", text: a.severity === "high" ? "▲ Alert" : a.severity === "info" ? "○ Info" : "● Watch" }),
          el("span", {}, el("b", { text: `${a.date}: ` }), a.message)));
      }
    }).catch(() => {});
  }

  // ---------- chart --------------------------------------------------------
  function renderChart() {
    const box = $("chart");
    box.replaceChildren();
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    const idx = state.indexByKey[key];
    $("chart-title").textContent = `${idx.name} through the season`;
    const pts = state.obs.filter((o) => o.status === "clear").map((o) => ({ o, t: Date.parse(o.datetime), s: o.stats[key] }));
    const cloudy = $("show-cloudy").checked ? state.obs.filter((o) => o.status !== "clear") : [];
    $("chart-empty").hidden = pts.length > 0;
    if (!pts.length) return;
    box.setAttribute("aria-label", `${idx.name} median over time, ${pts.length} clear dates`);

    const W = box.clientWidth || 600, H = box.clientHeight || 220;
    const m = { l: 40, r: 12, t: 10, b: 24 };
    const allT = pts.map((p) => p.t).concat(cloudy.map((o) => Date.parse(o.datetime)));
    let t0 = Math.min(...allT), t1 = Math.max(...allT);
    if (t1 - t0 < 864e5 * 10) { t0 -= 864e5 * 5; t1 += 864e5 * 5; }
    let y0 = Math.min(idx.display_range[0], ...pts.map((p) => p.s.p10));
    let y1 = Math.max(idx.display_range[1], ...pts.map((p) => p.s.p90));
    const x = (t) => m.l + ((t - t0) / (t1 - t0)) * (W - m.l - m.r);
    const y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);

    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
    const axis = svg("g", { class: "axis" });
    const nTicks = 4;
    for (let i = 0; i <= nTicks; i++) {
      const v = y0 + ((y1 - y0) * i) / nTicks;
      axis.append(svg("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      const tx = svg("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" });
      tx.textContent = v.toFixed(Math.abs(y1 - y0) < 2 ? 2 : 1);
      axis.append(tx);
    }
    const d0 = new Date(t0);
    for (let d = new Date(d0.getFullYear(), d0.getMonth() + 1, 1); d.getTime() <= t1; d.setMonth(d.getMonth() + 1)) {
      const tx = svg("text", { x: x(d.getTime()), y: H - 6, "text-anchor": "middle" });
      tx.textContent = d.toLocaleString(undefined, { month: "short" }) + (d.getMonth() === 0 ? ` ${d.getFullYear()}` : "");
      axis.append(tx);
    }
    s.append(axis);

    const band = pts.map((p) => `${x(p.t)},${y(p.s.p90)}`).concat(pts.slice().reverse().map((p) => `${x(p.t)},${y(p.s.p10)}`));
    s.append(svg("polygon", { class: "band", points: band.join(" ") }));
    s.append(svg("polyline", { class: "line", points: pts.map((p) => `${x(p.t)},${y(p.s.median)}`).join(" ") }));
    for (const o of cloudy) {
      const cx = x(Date.parse(o.datetime));
      s.append(svg("line", { class: "cloudy", x1: cx, x2: cx, y1: H - m.b, y2: H - m.b - 6 }));
    }
    for (const p of pts) {
      const dot = svg("circle", { class: "dot" + (p.o.id === state.obsId ? " selected" : ""), cx: x(p.t), cy: y(p.s.median), r: p.o.id === state.obsId ? 6 : 4.5 });
      s.append(dot);
    }
    const cross = svg("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
    s.append(cross);
    const hit = svg("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent", style: "cursor:pointer" });
    s.append(hit);
    box.append(s);

    const tip = $("tooltip");
    const nearest = (evt) => {
      const r = s.getBoundingClientRect();
      const px = ((evt.clientX - r.left) / r.width) * W;
      return pts.reduce((a, b) => (Math.abs(x(b.t) - px) < Math.abs(x(a.t) - px) ? b : a));
    };
    hit.addEventListener("pointermove", (evt) => {
      const p = nearest(evt);
      cross.setAttribute("x1", x(p.t)); cross.setAttribute("x2", x(p.t)); cross.setAttribute("visibility", "visible");
      tip.replaceChildren(
        el("div", { class: "v", text: `${idx.name} ${fmt(p.s.median)}` }),
        el("div", { text: p.o.date }),
        el("div", { class: "muted", text: `10–90%: ${fmt(p.s.p10)}–${fmt(p.s.p90)} · CV ${fmt(p.s.cv)}` }),
        el("div", { class: "muted", text: `${pct(p.o.field_clear_fraction)} of field clear · click to view` }),
      );
      tip.hidden = false;
      const tx = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = `${tx}px`;
      tip.style.top = `${evt.clientY - tip.offsetHeight - 10}px`;
    });
    hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
    hit.addEventListener("click", (evt) => {
      const p = nearest(evt);
      state.obsId = p.o.id;
      $("obs-select").value = p.o.id;
      showSelected();
      renderChart();
    });
  }
  window.addEventListener("resize", () => { if (state.field) renderChart(); });

  // ---------- tabs ---------------------------------------------------------
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => { t.classList.toggle("active", t === tab); t.setAttribute("aria-selected", String(t === tab)); });
    document.querySelectorAll(".tabpanel").forEach((p) => { p.hidden = p.id !== `tab-${tab.dataset.tab}`; });
    if (tab.dataset.tab === "scout") showSelected();
  }));

  // ---------- zones --------------------------------------------------------
  $("zones-btn").addEventListener("click", async () => {
    const out = $("zones-out");
    out.replaceChildren(el("p", { class: "small muted", text: "Building zones…" }));
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    const n = $("zone-n").value;
    const q = new URLSearchParams({ index: key, n });
    if ($("zone-mode").value === "single" && state.obsId) q.set("obs_id", state.obsId);
    try {
      const z = await api(`/api/fields/${state.field.id}/zones?${q}`);
      state.zonesGeojson = z.geojson;
      $("zones-dl").disabled = false;
      markersLayer.clearLayers();
      const colors = z.zones.map((_, i) => rgb(colorAt([[0, [215, 48, 39]], [0.33, [252, 141, 89]], [0.66, [145, 207, 96]], [1, [26, 152, 80]]], i / Math.max(1, z.n - 1))));
      setOverlay(z.png, z.bounds, {
        title: `${state.indexByKey[key].name} zones (${z.mode})`,
        items: z.zones.map((zz, i) => ({ color: colors[i], label: `Zone ${zz.zone}${i === 0 ? " (lowest)" : i === z.zones.length - 1 ? " (highest)" : ""}: ${zz.area_ac} ac` })),
      });
      out.replaceChildren();
      if (z.warning) out.append(el("div", { class: "warning", text: z.warning }));
      out.append(el("p", { class: "small muted", text: `${z.mode === "multi-date" ? `Built from ${z.dates.length} clear canopy dates (${z.dates[0]} → ${z.dates[z.dates.length - 1]}) as the mean standardized ${state.indexByKey[key].name}.` : `Built from ${z.dates[0]} only.`} Zones are relative to this field: a starting point for variable-rate or sampling plans, to be checked against yield maps and soil data.` }));
      const t = el("table", { class: "table small" });
      t.append(el("tr", {}, ["Zone", "Acres", "Hectares", "Share", z.mode === "multi-date" ? "Mean z-score" : "Mean value"].map((h) => el("th", { text: h }))));
      z.zones.forEach((zz, i) => t.append(el("tr", {},
        el("td", {}, el("span", { class: "swatch", style: `background:${colors[i]}` }), String(zz.zone)),
        el("td", { text: fmt(zz.area_ac, 1) }), el("td", { text: fmt(zz.area_ha, 1) }), el("td", { text: pct(zz.share) }), el("td", { text: fmt(zz.mean) }))));
      out.append(el("div", { class: "table-wrap" }, t));
    } catch (err) {
      out.replaceChildren(el("p", { class: "small", text: err.message }));
    }
  });
  $("zones-dl").addEventListener("click", () => {
    if (!state.zonesGeojson) return;
    const blob = new Blob([JSON.stringify(state.zonesGeojson)], { type: "application/geo+json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `${state.field.id}_zones.geojson` });
    document.body.append(a); a.click(); a.remove();
  });

  // ---------- change -------------------------------------------------------
  function renderChangeSelects() {
    const clear = state.obs.filter((o) => o.status === "clear");
    for (const [id, def] of [["chg-before", Math.max(0, clear.length - 2)], ["chg-after", clear.length - 1]]) {
      const sel = $(id);
      sel.replaceChildren(...clear.map((o) => el("option", { value: o.id, text: o.date })));
      if (clear.length) sel.value = clear[def].id;
    }
  }
  $("change-btn").addEventListener("click", async () => {
    const out = $("change-out");
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    const name = state.indexByKey[key].name;
    out.replaceChildren(el("p", { class: "small muted", text: "Comparing…" }));
    const q = new URLSearchParams({ index: key, before: $("chg-before").value, after: $("chg-after").value, threshold: $("chg-thr").value });
    try {
      const c = await api(`/api/fields/${state.field.id}/change?${q}`);
      markersLayer.clearLayers();
      setOverlay(c.png, c.bounds, {
        title: `${name} change ${c.before} → ${c.after}`,
        cmap: [[0, [178, 24, 43]], [0.25, [239, 138, 98]], [0.5, [247, 247, 247]], [0.75, [103, 169, 207]], [1, [33, 102, 172]]],
        lo: `−${c.color_limit}`, hi: `+${c.color_limit}`, note: "Red = declined · blue = improved",
      });
      for (const h of c.decline_hotspots) {
        markersLayer.addLayer(L.geoJSON(h.geometry, { style: { color: "#ff3b30", weight: 2, fillOpacity: 0 } }));
        markersLayer.addLayer(L.marker([h.lat, h.lon]).bindPopup(`<b>Decline ${h.rank}</b><br>${h.lat.toFixed(5)}, ${h.lon.toFixed(5)}<br>${h.area_ac} ac`));
      }
      const uniform = c.declined_share > 0.8 && !c.decline_hotspots.length;
      out.replaceChildren(el("div", { class: "kpis" },
        kpi("Median change", fmt(c.stats.median, 3)),
        kpi(`Declined ≥ ${c.threshold}`, `${pct(c.declined_share)} · ${fmt(c.declined_ha * 2.471, 1)} ac`),
        kpi(`Improved ≥ ${c.threshold}`, `${pct(c.improved_share)} · ${fmt(c.improved_ha * 2.471, 1)} ac`),
        kpi("Decline patches", String(c.decline_hotspots.length)),
      ));
      if (uniform) out.append(el("p", { class: "small muted", text: "The whole field changed together: typical of maturity, harvest, or a field-wide event (frost, hail), not a localized problem." }));
    } catch (err) {
      out.replaceChildren(el("p", { class: "small", text: err.message }));
    }
  });
  const kpi = (k, v) => el("div", { class: "kpi" }, el("div", { class: "k", text: k }), el("div", { class: "v", text: v }));

  // ---------- data table ---------------------------------------------------
  function renderTable() {
    const t = $("obs-table");
    t.replaceChildren();
    const keys = state.indices.map((i) => i.key);
    t.append(el("tr", {}, ["Date", "Scene", "Clear", ...state.indices.map((i) => i.name)].map((h) => el("th", { text: h }))));
    for (const o of state.obs.slice().reverse()) {
      t.append(el("tr", { class: "clickable", onclick: () => { state.obsId = o.id; renderObsSelect(); showSelected(); renderChart(); } },
        el("td", { text: o.date }),
        el("td", { text: o.scene_id, class: "muted" }),
        el("td", { text: pct(o.field_clear_fraction) }),
        ...keys.map((k) => el("td", { text: o.status === "clear" ? fmt(o.stats[k].median) : "–" }))));
    }
  }

  // ---------- boot ---------------------------------------------------------
  $("toggle-sidebar").addEventListener("click", () => $("sidebar").classList.toggle("open"));

  async function boot() {
    initDates();
    const [health, indices] = await Promise.all([api("/api/health"), api("/api/indices")]);
    $("version").textContent = `v${health.app_version} · processing v${health.processing_version}${health.git_commit ? " · " + health.git_commit.slice(0, 7) : ""}`;
    state.indices = indices;
    state.indexByKey = Object.fromEntries(indices.map((i) => [i.key, i]));
    renderIndexSelect();
    renderIndexInfo();
    await loadFields();
    const fromHash = decodeURIComponent(location.hash.slice(1));
    if (fromHash && state.fields.some((f) => f.id === fromHash)) selectField(fromHash);
    else if (state.fields.length === 1) selectField(state.fields[0].id);
    else fitAllFields();
  }
  window.addEventListener("hashchange", () => { const id = decodeURIComponent(location.hash.slice(1)); if (id) selectField(id); });
  boot().catch((err) => { document.body.prepend(el("div", { class: "alert high", text: `Failed to start: ${err.message}` })); });
})();
