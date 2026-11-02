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
      maxZoom: 20, maxNativeZoom: 18, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    }),
    Streets: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap contributors",
    }),
  };
  const labels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 20, maxNativeZoom: 18, attribution: "Labels © Esri",
  });
  basemaps.Satellite.addTo(map);
  labels.addTo(map);
  L.control.layers(basemaps, { "Roads & places": labels }, { position: "bottomright" }).addTo(map);
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

  // ---------- mapping mode (draw / edit a boundary) -------------------------
  // Works like a drone mapping mission: tap the satellite map to drop corners, drag corners
  // to adjust, tap a "+" midpoint to add a corner on an edge, tap a corner to remove it.
  const mapper = { mode: null, editField: null, pts: [], history: [], layer: L.layerGroup().addTo(map), poly: null, fix: null };
  const R_EARTH = 6378137;
  const rad = (d) => (d * Math.PI) / 180;

  function geodesicAreaM2(pts) {
    if (pts.length < 3) return 0;
    let a = 0;
    for (let i = 0; i < pts.length; i++) {
      const [lat1, lon1] = pts[i];
      const [lat2, lon2] = pts[(i + 1) % pts.length];
      a += rad(lon2 - lon1) * (2 + Math.sin(rad(lat1)) + Math.sin(rad(lat2)));
    }
    return Math.abs((a * R_EARTH * R_EARTH) / 2);
  }
  function perimeterM(pts) {
    let d = 0;
    const n = pts.length >= 3 ? pts.length : pts.length - 1;
    for (let i = 0; i < n; i++) d += L.latLng(pts[i]).distanceTo(L.latLng(pts[(i + 1) % pts.length]));
    return Math.max(0, d);
  }
  function segmentsCross(a, b, c, d) {
    const o = (p, q, r) => Math.sign((q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1]));
    return o(a, b, c) !== o(a, b, d) && o(c, d, a) !== o(c, d, b) && o(a, b, c) !== 0 && o(c, d, a) !== 0;
  }
  function selfIntersects(pts) {
    const n = pts.length;
    if (n < 4) return false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (Math.abs(i - j) <= 1 || (i === 0 && j === n - 1)) continue; // adjacent edges share a corner
        if (segmentsCross(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return true;
      }
    }
    return false;
  }

  function snapshot() {
    mapper.history.push(mapper.pts.map((p) => p.slice()));
    if (mapper.history.length > 200) mapper.history.shift();
  }
  const vertexIcon = (first) => L.divIcon({ className: "", html: `<div class="vertex-icon${first ? " first" : ""}"></div>`, iconSize: [22, 22], iconAnchor: [11, 11] });
  const midIcon = L.divIcon({ className: "", html: '<div class="mid-icon">+</div>', iconSize: [14, 14], iconAnchor: [7, 7] });

  function mapperRender() {
    mapper.layer.clearLayers();
    const pts = mapper.pts;
    mapper.poly = pts.length >= 3
      ? L.polygon(pts, { color: "#22c55e", weight: 3, fillColor: "#22c55e", fillOpacity: 0.18, interactive: false })
      : L.polyline(pts, { color: "#22c55e", weight: 3, dashArray: "6 6", interactive: false });
    mapper.layer.addLayer(mapper.poly);

    const edges = pts.length >= 3 ? pts.length : pts.length - 1;
    for (let i = 0; i < edges; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      const mid = L.marker([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], { icon: midIcon, draggable: true, title: "Drag or tap to add a corner here", keyboard: false });
      const insertAt = i + 1;
      mid.on("click", (e) => { L.DomEvent.stopPropagation(e); snapshot(); pts.splice(insertAt, 0, [mid.getLatLng().lat, mid.getLatLng().lng]); mapperRender(); });
      mid.on("dragstart", () => snapshot());
      mid.on("drag", (e) => { const tmp = pts.slice(); tmp.splice(insertAt, 0, [e.latlng.lat, e.latlng.lng]); mapper.poly.setLatLngs(tmp); });
      mid.on("dragend", () => { const ll = mid.getLatLng(); pts.splice(insertAt, 0, [ll.lat, ll.lng]); mapperRender(); });
      mapper.layer.addLayer(mid);
    }
    pts.forEach((p, i) => {
      const v = L.marker(p, { icon: vertexIcon(i === 0), draggable: true, title: `Corner ${i + 1}: drag to move, tap to remove`, autoPan: true });
      v.on("click", (e) => { L.DomEvent.stopPropagation(e); snapshot(); pts.splice(i, 1); mapperRender(); });
      v.on("dragstart", () => snapshot());
      v.on("drag", (e) => { pts[i] = [e.latlng.lat, e.latlng.lng]; mapper.poly.setLatLngs(pts); mapperStats(); });
      v.on("dragend", () => mapperRender());
      mapper.layer.addLayer(v);
    });
    mapperStats();
  }

  function mapperStats() {
    const pts = mapper.pts;
    const ha = geodesicAreaM2(pts) / 1e4;
    const crosses = selfIntersects(pts);
    const tooBig = ha > 5000;
    $("mapper-stats").textContent = pts.length
      ? `${pts.length} corner${pts.length === 1 ? "" : "s"}${pts.length >= 3 ? ` · ${fmt(ha * 2.471054, 1)} ac (${fmt(ha, 1)} ha)` : ""} · ${fmt(perimeterM(pts) / 1609.344, 2)} mi edge`
      : "";
    $("mapper-hint").textContent = pts.length === 0
      ? "Zoom in on the field, then tap each corner of the field edge in order."
      : pts.length < 3
        ? "Keep tapping corners around the field. You need at least 3."
        : "Drag white corners to adjust · tap + to add a corner on an edge · tap a corner to remove it.";
    const warn = $("mapper-warn");
    warn.hidden = !(crosses || tooBig);
    warn.textContent = crosses ? "Edges cross each other. Drag corners so the outline doesn't overlap itself."
      : tooBig ? `That's ${fmt(ha * 2.471054, 0)} acres; fields are limited to about 12,000 ac. Zoom in and outline a single field.` : "";
    $("mapper-save").disabled = pts.length < 3 || crosses || tooBig;
    $("mapper-undo").disabled = !mapper.history.length;
  }

  function mapperOpen(mode, field = null) {
    mapper.mode = mode;
    mapper.editField = field;
    mapper.history = [];
    mapper.pts = [];
    if (field) {
      const ring = (field.geometry.type === "Polygon" ? field.geometry.coordinates : field.geometry.coordinates[0])[0];
      mapper.pts = ring.slice(0, -1).map(([lon, lat]) => [lat, lon]);
    }
    document.body.classList.add("mapping");
    $("sidebar").classList.remove("open");
    $("mapper-top").hidden = false;
    $("mapper-sheet").hidden = false;
    $("mapper-meta").hidden = mode === "edit";
    $("mapper-title").textContent = mode === "edit" ? `Edit boundary: ${field.name}` : "Map a new field";
    $("mapper-save").textContent = mode === "edit" ? "Save new boundary" : "Save field";
    setOverlay(null);
    markersLayer.clearLayers();
    if (map.hasLayer(basemaps.Streets)) { map.removeLayer(basemaps.Streets); basemaps.Satellite.addTo(map); }
    map.getContainer().classList.add("mapping-cursor");
    map.invalidateSize();
    if (field) map.fitBounds(L.geoJSON(field.geometry).getBounds(), { padding: [60, 60], animate: false });
    mapperRender();
    if (mode === "new") $("search-input").focus();
  }

  function mapperClose() {
    mapper.mode = null;
    mapper.layer.clearLayers();
    if (mapper.fix) { map.removeLayer(mapper.fix); mapper.fix = null; }
    document.body.classList.remove("mapping");
    $("mapper-top").hidden = true;
    $("mapper-sheet").hidden = true;
    $("search-results").hidden = true;
    map.getContainer().classList.remove("mapping-cursor");
    map.invalidateSize();
    if (state.field) { showSelected(); map.fitBounds(L.geoJSON(state.field.geometry).getBounds(), { padding: [30, 30], maxZoom: 16, animate: false }); }
  }

  map.on("click", (e) => {
    if (!mapper.mode) return;
    snapshot();
    mapper.pts.push([e.latlng.lat, e.latlng.lng]);
    mapperRender();
  });
  $("new-field-btn").addEventListener("click", () => mapperOpen("new"));
  $("edit-boundary-btn").addEventListener("click", () => state.field && mapperOpen("edit", state.field));
  $("mapper-undo").addEventListener("click", () => { if (mapper.history.length) { mapper.pts = mapper.history.pop(); mapperRender(); } });
  $("mapper-clear").addEventListener("click", () => { if (mapper.pts.length) { snapshot(); mapper.pts = []; mapperRender(); } });
  $("mapper-cancel").addEventListener("click", () => {
    if (mapper.pts.length >= 3 && mapper.mode === "new" && !confirm("Discard this outline?")) return;
    mapperClose();
  });
  document.addEventListener("keydown", (e) => {
    if (!mapper.mode || e.target.matches("input, textarea")) return;
    if (e.key === "Escape") $("mapper-cancel").click();
    else if ((e.key === "z" && (e.ctrlKey || e.metaKey)) || e.key === "Backspace") { e.preventDefault(); $("mapper-undo").click(); }
    else if (e.key === "Enter" && !$("mapper-save").disabled) $("mapper-save").click();
  });

  $("mapper-save").addEventListener("click", async () => {
    const ring = mapper.pts.map(([lat, lng]) => [lng, lat]);
    ring.push(ring[0]);
    const geojson = { type: "Polygon", coordinates: [ring] };
    const btn = $("mapper-save");
    btn.disabled = true;
    try {
      let id;
      if (mapper.mode === "edit") {
        const res = await api(`/api/fields/${mapper.editField.id}/redraw`, { method: "POST", body: JSON.stringify({ geojson }) });
        id = res.field.id;
      } else {
        const name = $("mapper-name").value.trim() || `Field ${state.fields.length + 1}`;
        const res = await api("/api/fields", { method: "POST", body: JSON.stringify({ name, crop: $("mapper-crop").value.trim() || null, geojson }) });
        id = res.created[0] || res.existing[0];
        $("mapper-name").value = "";
        $("mapper-crop").value = "";
      }
      mapperClose();
      await loadFields();
      if (id) await selectField(id);
      refresh();
    } catch (err) {
      const warn = $("mapper-warn");
      warn.hidden = false;
      warn.textContent = `Could not save: ${err.message}`;
      btn.disabled = false;
    }
  });

  // Place search and GPS
  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("search-input").value.trim();
    const list = $("search-results");
    if (q.length < 2) return;
    list.hidden = false;
    list.replaceChildren(el("li", { class: "small muted", text: "Searching…", style: "padding:8px" }));
    try {
      const hits = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      list.replaceChildren();
      if (!hits.length) list.append(el("li", { class: "small muted", text: "No matches. Try a town, road, or 'lat, lon'.", style: "padding:8px" }));
      for (const h of hits) {
        list.append(el("li", {}, el("button", { type: "button", text: h.name, onclick: () => {
          list.hidden = true;
          if (h.bbox) map.fitBounds(h.bbox, { maxZoom: 17 });
          else map.setView([h.lat, h.lon], 17);
        } })));
      }
    } catch (err) {
      list.replaceChildren(el("li", { class: "small", text: err.message, style: "padding:8px" }));
    }
  });
  $("locate-btn").addEventListener("click", () => {
    $("locate-btn").textContent = "◎ Locating…";
    map.locate({ setView: true, maxZoom: 17, enableHighAccuracy: true, timeout: 15000 });
  });
  map.on("locationfound", (e) => {
    $("locate-btn").textContent = "◎ My location";
    if (mapper.fix) map.removeLayer(mapper.fix);
    mapper.fix = L.layerGroup([
      L.circle(e.latlng, { radius: e.accuracy, color: "#3b82f6", weight: 1, fillOpacity: 0.1, interactive: false }),
      L.circleMarker(e.latlng, { radius: 7, color: "#fff", weight: 2, fillColor: "#3b82f6", fillOpacity: 1, interactive: false }),
    ]).addTo(map);
  });
  map.on("locationerror", (e) => {
    $("locate-btn").textContent = "◎ My location";
    alert(`Couldn't get your location: ${e.message}`);
  });

  // File import: KML/KMZ (DJI Pilot, Google Earth) or GeoJSON
  $("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const msg = $("import-msg");
    msg.textContent = `Importing ${file.name}…`;
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      const res = await api("/api/fields", { method: "POST", body: JSON.stringify({ file_name: file.name, file_b64: btoa(bin) }) });
      msg.textContent = `${res.created.length} field${res.created.length === 1 ? "" : "s"} added, ${res.existing.length} already present` +
        (res.errors.length ? `, ${res.errors.length} skipped (${res.errors[0].error})` : "") +
        (res.created.length ? ". Pulling imagery now." : "");
      await loadFields();
      if (res.created.length === 1) await selectField(res.created[0]);
      else fitAllFields();
      refresh();
    } catch (err) {
      msg.textContent = `Import failed: ${err.message}`;
    }
  });

  // ---------- fields -------------------------------------------------------
  async function loadFields() {
    state.fields = await api(`/api/fields?include_archived=${$("show-archived").checked}`);
    if (state.field) {
      const fresh = state.fields.find((x) => x.id === state.field.id);
      if (fresh) { state.field = fresh; renderSync(); }
    }
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
        el("span", { class: "sub", text: `${fmt(f.area_ac, 1)} ac${f.crop ? " · " + f.crop : ""} · ${syncShort(f)}` }),
      );
      if (state.field && state.field.id === f.id) btn.classList.add("active");
      list.append(el("li", {}, btn));
      const layer = L.geoJSON(f.geometry, { style: { color: "#fde047", weight: 2, fillOpacity: 0 } });
      layer.on("click", (e) => { if (!mapper.mode) { L.DomEvent.stopPropagation(e); selectField(f.id); } });
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
    state.obsPinned = false;
    document.querySelectorAll(".field-list button").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
    $("detail").hidden = false;
    $("f-name").textContent = f.name;
    $("f-meta").textContent = `${f.id} · ${fmt(f.area_ac, 1)} ac (${fmt(f.area_ha, 1)} ha)${f.crop ? " · " + f.crop : ""}`;
    $("archive-btn").textContent = f.archived ? "Unarchive" : "Archive";
    $("edit-boundary-btn").hidden = Boolean(f.replaced_by);
    renderSync();
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

  // ---------- automatic updates --------------------------------------------
  function relTime(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} days ago`;
  }
  function jobRunning(f) { return f && f.sync && f.sync.job && ["searching", "processing"].includes(f.sync.job.state); }
  function syncShort(f) {
    if (jobRunning(f)) {
      const j = f.sync.job;
      return j.state === "searching" ? "finding imagery…" : `updating ${j.done}/${j.total}…`;
    }
    const latest = f.latest ? `latest clear ${f.latest.date}` : "no clear imagery yet";
    return f.sync && f.sync.last_synced ? `${latest} · checked ${relTime(f.sync.last_synced)}` : latest;
  }

  function renderSync() {
    const f = state.field;
    if (!f) return;
    const st = f.sync || {};
    const job = st.job;
    const running = jobRunning(f);
    if (f.replaced_by) {
      $("sync-line").textContent = `Replaced by a newer boundary (${f.replaced_by}). History kept here.`;
      $("sync-sub").textContent = "";
    } else if (running) {
      $("sync-line").textContent = job.reason === "new-field" ? "Pulling the last 12 months of satellite imagery…" : "Checking for new satellite passes…";
      $("sync-sub").textContent = `${job.start} → ${job.end}`;
    } else {
      $("sync-line").textContent = st.auto_refresh_hours
        ? `Auto-updates every ${st.auto_refresh_hours} h · last checked ${relTime(st.last_synced)}`
        : `Last checked ${relTime(st.last_synced)} (automatic updates are off)`;
      const r = st.last_result;
      $("sync-sub").textContent = r
        ? `Last check: ${r.new} new pass${r.new === 1 ? "" : "es"} (${r.clear} clear over this field in the window). Sentinel-2 passes every 2–5 days; cloudy passes are skipped.`
        : "Sentinel-2 passes every 2–5 days; cloudy passes are skipped automatically.";
      if (st.last_error) $("sync-sub").textContent += ` Some scenes failed: ${st.last_error}`;
    }
    $("sync-btn").disabled = running || Boolean(f.replaced_by);
    $("process-btn").disabled = running;
    $("job").hidden = !job;
    if (job) {
      $("job-bar").style.width = job.total ? `${(100 * job.done) / job.total}%` : "0";
      $("job-text").textContent = running
        ? (job.state === "searching" ? "Searching for satellite passes…" : `Processed ${job.done} of ${job.total} passes · ${job.clear} clear over this field`)
        : job.state === "done"
          ? `Done: ${job.total} passes checked, ${job.new} new, ${job.clear} clear.` + (job.errors.length ? ` ${job.errors.length} failed.` : "")
          : `Failed: ${job.errors.join("; ")}`;
    }
  }

  async function startJob(url, body) {
    try {
      await api(url, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      await refresh();
    } catch (err) {
      $("job").hidden = false;
      $("job-text").textContent = `Failed: ${err.message}`;
    }
  }
  $("sync-btn").addEventListener("click", () => state.field && startJob(`/api/fields/${state.field.id}/sync`));
  $("process-btn").addEventListener("click", () => state.field && startJob(`/api/fields/${state.field.id}/process`, {
    start: $("p-start").value, end: $("p-end").value, max_cloud: Number($("p-cloud").value),
  }));

  function initDates() {
    const today = new Date();
    $("p-start").value = `${today.getFullYear() - 1}-04-01`;
    $("p-end").value = `${today.getFullYear() - 1}-11-01`;
  }

  // Poll for updates: quickly while a job runs, slowly otherwise. Reload the open
  // field's imagery whenever its observation count changes.
  let pollTimer = null;
  async function refresh() {
    clearTimeout(pollTimer);
    try {
      const before = state.field ? state.field.observations : null;
      if (!mapper.mode) await loadFields();
      if (state.field && state.field.observations !== before) await loadObservations(true);
    } catch (_) { /* transient; try again next tick */ }
    const busy = state.fields.some(jobRunning);
    pollTimer = setTimeout(refresh, busy ? 3000 : 120000);
  }

  // ---------- observations -------------------------------------------------
  async function loadObservations(keepSelection = false) {
    if (!state.field) return;
    state.obs = await api(`/api/fields/${state.field.id}/observations`);
    const clear = state.obs.filter((o) => o.status === "clear");
    // Follow the newest image unless the user picked a date themselves.
    if (!keepSelection || !state.obsPinned || !state.obs.some((o) => o.id === state.obsId)) {
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
  $("obs-select").addEventListener("change", (e) => { state.obsId = e.target.value; state.obsPinned = true; showSelected(); renderChart(); });
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
      state.obsPinned = true;
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
      t.append(el("tr", { class: "clickable", onclick: () => { state.obsId = o.id; state.obsPinned = true; renderObsSelect(); showSelected(); renderChart(); } },
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
    if (!state.fields.length) mapperOpen("new");
    refresh();
  }
  window.addEventListener("hashchange", () => { const id = decodeURIComponent(location.hash.slice(1)); if (id) selectField(id); });
  boot().catch((err) => { document.body.prepend(el("div", { class: "alert high", text: `Failed to start: ${err.message}` })); });
})();
