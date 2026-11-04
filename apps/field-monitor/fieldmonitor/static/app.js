/* Overacre web UI (package: fieldmonitor). Plain JS + Leaflet; all names from the API are inserted with textContent. */
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
    // The custom header is our CSRF guard: the server rejects writes without it.
    const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Requested-With": "fieldmonitor", ...(opts.headers || {}) } });
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      state.me = null;
      if (!location.hash.startsWith("#/login")) location.hash = "#/login";
      throw new Error("Please log in.");
    }
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
  const qaText = (o) => (o.qa_flags || []).map((f) => f.detail).join("; ");
  const qaShort = (o) => {
    const codes = (o.qa_flags || []).map((f) => f.code);
    if (o.qa_status === "suspect") return codes.includes("haze") ? "suspect: haze" : "suspect: one-pass artifact";
    if (codes.includes("haze-caution")) return "slightly hazy";
    if (codes.includes("partial")) return "partly cloudy";
    return "";
  };
  const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
  const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  const isoToday = () => new Date().toISOString().slice(0, 10);
  // The real scale of each layer's numbers (the color ramp may stop short of it).
  const SCALE = { ndmi: "−1 to 1 scale", ndwi: "−1 to 1 scale", cire: "ratio, no fixed scale" };
  const scaleOf = (key) => SCALE[key] || "0–1 scale";
  // "2025-08-13" -> "Aug 13, 2025" (year only when it isn't this year). Parsed as a local date, never shifted by time zone.
  const niceDate = (iso) => {
    if (!iso) return "";
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(y !== new Date().getFullYear() ? { year: "numeric" } : {}) });
  };
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
  // The legend sits outside Leaflet's panes, so hide it while a popup is open.
  map.on("popupopen", () => { $("map-legend").style.visibility = "hidden"; });
  map.on("popupclose", () => { $("map-legend").style.visibility = ""; });
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

  // Search: your fields, an address, a place, or coordinates (always on the map)
  const searchPin = L.layerGroup().addTo(map);
  let suggestSeq = 0;
  let suggestTimer = null;
  function closeSearch() { $("search-results").hidden = true; $("search-input").setAttribute("aria-expanded", "false"); }
  function searchNote(text) { return el("li", { class: "small muted search-note", text }); }
  function goToPlace(p) {
    closeSearch();
    searchPin.clearLayers();
    if (p.bbox && p.kind === "place") map.fitBounds(p.bbox, { maxZoom: 17 });
    else map.setView([p.lat, p.lon], Math.max(map.getZoom(), 16));
    L.marker([p.lat, p.lon], { title: p.name, icon: L.divIcon({ className: "", html: '<div class="search-pin"></div>', iconSize: [22, 22], iconAnchor: [11, 22], popupAnchor: [0, -20] }) }).bindPopup(el("div", { class: "small" },
      el("strong", { text: p.name }), el("br"), `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`)).addTo(searchPin).openPopup();
  }
  function goToField(f) {
    closeSearch();
    searchPin.clearLayers();
    if (mapper.mode || !state.fields.some((x) => x.id === f.id)) map.fitBounds(f.bbox, { padding: [30, 30], maxZoom: 16 });
    else selectField(f.id);
  }
  function fieldWhy(f) {
    if (f.match === "inside") return "the point is in this field";
    if (f.match === "near") return `${fmt(f.distance_mi, 1)} mi away`;
    return "name matches";
  }
  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("search-input").value.trim();
    const list = $("search-results");
    if (q.length < 2) return;
    clearTimeout(suggestTimer);
    suggestSeq++;
    list.hidden = false;
    list.replaceChildren(searchNote("Searching…"));
    try {
      const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
      list.replaceChildren();
      if (res.fields.length) list.append(el("li", { class: "search-head", text: "Your fields" }));
      for (const f of res.fields) {
        list.append(el("li", {}, el("button", { type: "button", class: "search-field", onclick: () => goToField(f) },
          el("strong", { text: f.name }), f.crop ? ` · ${f.crop}` : "", f.archived ? " · archived" : "",
          el("span", { class: "muted", text: ` — ${fieldWhy(f)}` }))));
      }
      if (res.places.length) list.append(el("li", { class: "search-head", text: res.coordinates ? "Coordinates" : "Places" }));
      for (const p of res.places) {
        const near = p.fields_near.length ? ` · ${p.fields_near.length} of your fields nearby` : "";
        list.append(el("li", {}, el("button", { type: "button", onclick: () => goToPlace(p) },
          p.name, el("span", { class: "muted", text: near + (p.source ? ` · ${p.source}` : "") }))));
      }
      if (res.error) list.append(searchNote(res.error));
      if (!res.fields.length && !res.places.length && !res.error)
        list.append(searchNote("No matches. Try a field name, an address (1234 Road 12, Aurora NE), a town, or 'lat, lon'."));
    } catch (err) {
      list.replaceChildren(searchNote(err.message));
    }
  });
  map.on("click", closeSearch);

  // Type-ahead: suggestions while typing (your fields, coordinates, places near the map view).
  // Arrow keys move through them, Enter picks the highlighted one, or runs the full search.
  function highlight(text, q) {
    const i = text.toLowerCase().indexOf(q.toLowerCase());
    if (!q || i < 0) return [text];
    return [text.slice(0, i), el("mark", { text: text.slice(i, i + q.length) }), text.slice(i + q.length)];
  }
  function suggestItems() { return [...$("search-results").querySelectorAll("button[data-suggest]")]; }
  function setActive(idx) {
    const items = suggestItems();
    items.forEach((b, i) => { b.classList.toggle("active", i === idx); b.setAttribute("aria-selected", String(i === idx)); });
    if (items[idx]) items[idx].scrollIntoView({ block: "nearest" });
    $("search-input").setAttribute("aria-activedescendant", items[idx] ? items[idx].id : "");
  }
  function renderSuggestions(res, q) {
    const list = $("search-results");
    list.replaceChildren();
    let n = 0;
    const item = (onclick, ...kids) => el("li", { role: "option" }, el("button", { type: "button", "data-suggest": "1", id: `sugg-${n++}`, onclick }, ...kids));
    if (res.fields.length) list.append(el("li", { class: "search-head", text: "Your fields" }));
    for (const f of res.fields) {
      list.append(item(() => goToField(f), el("strong", {}, ...highlight(f.name, q)), f.crop ? ` · ${f.crop}` : "",
        f.match !== "name" ? el("span", { class: "muted", text: ` — ${fieldWhy(f)}` }) : null));
    }
    if (res.places.length) list.append(el("li", { class: "search-head", text: res.places[0].kind === "coordinates" ? "Coordinates" : "Suggestions" }));
    for (const p of res.places) list.append(item(() => goToPlace(p), ...highlight(p.name, q)));
    list.append(item(() => $("search-form").requestSubmit(), el("span", { class: "muted", text: `Search all results for “${q}” ↵` })));
    if (res.error) list.append(searchNote(res.error));
    list.hidden = false;
    $("search-input").setAttribute("aria-expanded", "true");
  }
  async function fetchSuggestions(q) {
    const seq = ++suggestSeq;
    const c = map.getCenter();
    try {
      const res = await api(`/api/search/suggest?q=${encodeURIComponent(q)}&lat=${c.lat.toFixed(3)}&lon=${c.lng.toFixed(3)}`);
      if (seq !== suggestSeq || $("search-input").value.trim() !== q) return; // a newer keystroke won
      renderSuggestions(res, q);
    } catch (_) { /* typing shouldn't show errors; Enter still runs the full search */ }
  }
  $("search-input").addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const q = $("search-input").value.trim();
    if (q.length < 2) { suggestSeq++; closeSearch(); return; }
    suggestTimer = setTimeout(() => fetchSuggestions(q), 250);
  });
  $("search-input").addEventListener("keydown", (e) => {
    const items = suggestItems();
    const cur = items.findIndex((b) => b.classList.contains("active"));
    if (e.key === "Escape") { closeSearch(); return; }
    if ($("search-results").hidden || !items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(cur + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(cur - 1, -1)); }
    else if (e.key === "Enter" && cur >= 0) { e.preventDefault(); clearTimeout(suggestTimer); items[cur].click(); }
  });
  $("search-input").addEventListener("focus", () => {
    if ($("search-results").children.length && $("search-input").value.trim().length >= 2) $("search-results").hidden = false;
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
        f.score && f.score.score != null
          ? el("span", { class: `val score-pill ${scoreClass(f.score.score)}`, title: f.score.label, text: String(f.score.score) })
          : el("span", { class: "val" }, sw, latest ? `NDVI ${fmt(latest.ndvi)}` : ""),
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
    $("f-meta").textContent = `${fmt(f.area_ac, 1)} ac${f.crop ? " · " + f.crop : ""}${f.my_role === "viewer" ? " · shared with you" : ""}`;
    $("archive-btn").textContent = f.archived ? "Unarchive" : "Archive";
    $("edit-boundary-btn").hidden = Boolean(f.replaced_by);
    $("owner-actions").hidden = f.my_role !== "owner";
    $("sync-btn").hidden = f.my_role !== "owner";
    $("share-panel").hidden = true;
    renderScore(f.score);
    loadWatch(f);
    loadYield(f);
    renderSync();
    $("csv-link").href = `/api/fields/${encodeURIComponent(id)}/timeseries.csv`;
    map.fitBounds(L.geoJSON(f.geometry).getBounds(), { padding: [30, 30], maxZoom: 16, animate: false });
    const want = `#/fields/${encodeURIComponent(id)}`;
    if (location.hash !== want) history.replaceState(null, "", want);
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
  function syncPending(f) { return jobRunning(f) || Boolean(f && f.sync && f.sync.queued); }
  function syncShort(f) {
    if (jobRunning(f)) {
      const j = f.sync.job;
      return j.state === "searching" ? "finding imagery…" : `updating ${j.done}/${j.total}…`;
    }
    if (f.sync && f.sync.queued) return `queued for imagery (#${f.sync.queue_position})`;
    const latest = f.latest ? `last clear image ${niceDate(f.latest.date)}` : "no clear image yet";
    return f.sync && f.sync.last_synced ? `${latest} · checked ${relTime(f.sync.last_synced)}` : latest;
  }

  function renderSync() {
    const f = state.field;
    if (!f) return;
    const st = f.sync || {};
    const job = st.job;
    const running = jobRunning(f);
    let line;
    if (f.replaced_by) line = "Old boundary (replaced). History kept.";
    else if (running) line = job.reason === "new-field" ? "Getting the newest images first, then the last 12 months…" : "Checking for new images…";
    else if (st.queued) line = `Queued for imagery (#${st.queue_position}); it starts as soon as the fields ahead finish.`;
    else line = `Updated ${relTime(st.last_synced)}` + (st.auto_refresh_hours ? ` · checks every ${st.auto_refresh_hours} h` : "");
    $("sync-line").textContent = line;
    $("sync-line").title = st.last_error ? `Some images failed: ${st.last_error}` : "";
    $("sync-btn").disabled = running || Boolean(f.replaced_by);
    $("process-btn").disabled = running;
    $("job").hidden = !(job && running);
    if (job && running) {
      $("job-bar").style.width = job.total ? `${(100 * job.done) / job.total}%` : "0";
      $("job-text").textContent = job.state === "searching" ? "Finding images…" : `${job.done} of ${job.total} images · ${job.clear} clear`;
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
    const busy = state.fields.some(syncPending);
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
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    for (const o of visibleObs().slice().reverse()) {
      const label = o.status !== "clear" ? `${niceDate(o.date)} · too cloudy`
        : `${niceDate(o.date)} · ${state.indexByKey[key].name} ${fmt(o.stats[key].median)}${o.qa_status === "suspect" ? " · ⚠ hazy" : ""}`;
      sel.append(el("option", { value: o.id, text: label }));
    }
    if (state.obsId) sel.value = state.obsId;
  }
  $("obs-select").addEventListener("change", (e) => {
    state.obsId = e.target.value;
    state.obsPinned = true;
    showSelected();
    renderChart();
    // Looking at an older image: show the disease pressure and weather from that time.
    const o = currentObs();
    const latest = state.obs.filter((x) => x.status === "clear").slice(-1)[0];
    loadWatch(state.field, o && latest && o.id !== latest.id ? o.date : null);
  });
  $("show-cloudy").addEventListener("change", () => { renderObsSelect(); renderChart(); });

  function renderIndexSelect() {
    const sel = $("index-select");
    sel.replaceChildren();
    for (const i of state.indices) sel.append(el("option", { value: i.key, text: `${i.plain} (${i.name}): ${i.shows.toLowerCase()}` }));
    sel.append(el("option", { value: "truecolor", text: "Photo: what the satellite saw" }));
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
    const li = (k, v) => el("li", {}, el("b", { text: `${k}: ` }), v);
    if (state.indexKey === "truecolor") {
      box.append(li("Shows", "What the satellite saw"), li("Use it to", "Spot clouds, haze, water, tillage"));
      return;
    }
    const i = state.indexByKey[state.indexKey];
    box.append(li("Best for", i.use), li("Watch out", i.watch));
  }

  function currentObs() { return state.obs.find((o) => o.id === state.obsId) || null; }

  function showSelected() {
    const o = currentObs();
    markersLayer.clearLayers();
    if (!o || !state.field) { setOverlay(null); $("hotspots").replaceChildren(); return; }
    const base = `/api/fields/${state.field.id}/observations/${o.id}`;
    const showTrue = state.indexKey === "truecolor" || o.status !== "clear";
    const warn = o.qa_status === "suspect" ? "⚠ Hazy or bad image: trust less" : "";
    if (showTrue) {
      setOverlay(`${base}/truecolor.png`, o.bounds, {
        title: `Photo · ${niceDate(o.date)}`, items: o.status === "clear" ? (warn ? [{ color: "#ccc", label: warn }] : []) : [{ color: "#ccc", label: "Too cloudy to measure" }],
      });
    } else {
      const i = state.indexByKey[state.indexKey];
      const st = o.stats[i.key];
      setOverlay(`${base}/${i.key}.png`, o.bounds, {
        title: `${i.plain} (${i.name}) · ${niceDate(o.date)}`, cmap: i.colormap, lo: `${i.display_range[0]} low`, hi: `high ${i.display_range[1]}`,
        note: warn || `Field average ${i.name} ${fmt(st.mean)} (${scaleOf(i.key)})`,
      });
    }
    renderHealth(o);
    loadHotspots(o);
  }

  // Crop-health tile: the selected image's NDVI on a colored 0–1 bar with a plain label.
  function renderHealth(o) {
    const box = $("health");
    box.replaceChildren();
    const nd = state.indexByKey.ndvi;
    if (!o || o.status !== "clear" || !nd) {
      box.append(el("div", { class: "big muted", text: "—" }), el("div", { class: "sub", text: o ? `Too cloudy on ${niceDate(o.date)}` : "No images yet" }));
      return;
    }
    const v = o.stats.ndvi.median;
    const label = nd.guide.reduce((acc, [cut, text]) => (v >= cut ? text : acc), "bare soil");
    const pos = Math.max(0, Math.min(1, v / 0.9)) * 100;
    box.append(
      el("div", {}, el("span", { class: "big", text: fmt(v) }), el("span", { class: "unit", text: " NDVI (0–1 scale)" })),
      el("div", { class: "bar", style: `background:${gradient(nd.colormap)}`, role: "img", "aria-label": `NDVI ${fmt(v)} of 1` }, el("i", { style: `left:${pos}%` })),
      el("div", { class: "ends" }, el("span", { text: "bare" }), el("span", { text: "dense" })),
      el("div", { class: "sub", text: `${label[0].toUpperCase()}${label.slice(1)} · image ${niceDate(o.date)}` }));
    const ageDays = Math.floor((Date.now() - new Date(`${o.date}T12:00:00`)) / 864e5);
    if (ageDays > 14) box.append(el("div", { class: "sub stale", text: `⚠ This image is ${ageDays} days old` }));
  }

  const signed = (v, d = 2) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
  const shortCause = (c) => c.split(":")[0];

  // Popup explaining one hotspot. Built with DOM nodes (no HTML strings).
  function spotPopup(h, keyName) {
    const ex = h.explain || {};
    const box = el("div", { class: "spot-pop" });
    box.append(el("h4", { text: `Spot ${h.rank} · ${fmt(h.area_ac, 1)} ac` }));
    if (ex.summary) box.append(el("p", { class: "sum", text: ex.summary }));
    // Surface water only matters when there is some (NDWI > 0); otherwise it just mirrors thin crop.
    const vals = (ex.values || []).filter((v) => v.key !== "ndwi" || v.spot > 0);
    if (vals.length) {
      box.append(el("div", { class: "lbl", text: "Here vs. field average (0–1 scales)" }));
      box.append(el("ul", {}, vals.map((v) => el("li", {},
        `${v.label}: ${fmt(v.spot)} here vs ${fmt(v.field)} avg `,
        el("span", { class: v.key === "ndwi" ? "" : v.diff < 0 ? "neg" : "pos", text: `(${signed(v.diff)})` })))));
    }
    if (ex.pattern && ex.pattern.length) {
      box.append(el("div", { class: "lbl", text: "What we see" }), el("ul", {}, ex.pattern.map((p) => el("li", { text: p }))));
    }
    if (ex.causes && ex.causes.length) {
      box.append(el("div", { class: "lbl", text: "Possible causes" }),
        el("ul", {}, ex.causes.map((c, i) => {
          const [head, ...rest] = c.cause.split(": ");
          return el("li", {}, el("b", { text: head }), rest.length ? `: ${rest.join(": ")}` : "",
            i === 0 ? el("div", { class: "hint", text: `Check: ${c.check}` }) : null);
        })));
    }
    box.append(el("div", { class: "hint", text: "Satellite shows where, not why. Confirm on the ground." }));
    box.append(el("a", { href: `https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lon}`, target: "_blank", rel: "noopener", text: `Directions (${h.lat.toFixed(5)}, ${h.lon.toFixed(5)})` }));
    return box;
  }

  async function loadHotspots(o) {
    const box = $("hotspots");
    box.replaceChildren();
    if (o.status !== "clear") { box.append(el("p", { class: "small muted", text: "Too cloudy on this date. Pick another." })); return; }
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    let res;
    try { res = await api(`/api/fields/${state.field.id}/observations/${o.id}/hotspots?index=${key}`); }
    catch (err) { box.append(el("p", { class: "small", text: err.message })); return; }
    if (o.id !== state.obsId) return;
    const name = state.indexByKey[key].name;
    if (!res.hotspots.length) {
      box.append(el("p", { class: "small", text: `✓ No problem spots on ${niceDate(res.date)}. The field looks even.` }));
      return;
    }
    const list = el("div", { class: "spot-list" });
    for (const h of res.hotspots) {
      const outline = L.geoJSON(h.geometry, { style: { color: "#ff3b30", weight: 2, fillOpacity: 0.08 } });
      const marker = L.marker([h.lat, h.lon], {
        title: `Spot ${h.rank}`,
        icon: L.divIcon({ className: "", html: `<div class="spot-marker">${h.rank}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] }),
      }).bindPopup(() => spotPopup(h, name), {
        maxWidth: 290, maxHeight: Math.max(200, Math.min(380, Math.round(map.getSize().y * 0.6))), autoPanPadding: [12, 12],
      });
      markersLayer.addLayer(outline);
      markersLayer.addLayer(marker);
      const ndvi = (h.explain && h.explain.values || []).find((v) => v.key === "ndvi");
      const top = h.explain && h.explain.causes && h.explain.causes[0];
      list.append(el("button", { type: "button", class: "spot-row", onclick: () => {
        map.setView([h.lat, h.lon], Math.max(map.getZoom(), 16), { animate: false });
        marker.openPopup();
        document.querySelectorAll(".spot-row").forEach((r) => r.classList.remove("active"));
        list.children[h.rank - 1].classList.add("active");
      } },
        el("span", { class: "num", text: String(h.rank) }),
        el("span", { class: "t", text: `${fmt(h.area_ac, 1)} ac` }),
        el("span", { class: "d", text: ndvi ? `NDVI ${fmt(Math.abs(ndvi.diff))} ${ndvi.diff < 0 ? "below" : "above"} field avg` : `${name} ${fmt(h.deficit)} below avg` }),
        el("span", { class: "s", text: top ? `Maybe: ${shortCause(top.cause)}` : "Tap for details" }),
      ));
    }
    box.append(list);
  }

  function renderAlerts() {
    const box = $("alerts");
    box.replaceChildren();
    if (!state.field || state.indexKey === "truecolor") return;
    api(`/api/fields/${state.field.id}/alerts?index=${state.indexKey}`).then((alerts) => {
      const label = { high: "▲ Alert", medium: "● Watch", pending: "◷ Pending", info: "○ Info" };
      // Only the last two weeks: older alerts stay in history, not on the main view.
      const cutoff = new Date(Date.now() - 14 * 864e5).toISOString().slice(0, 10);
      for (const a of alerts.filter((x) => x.date >= cutoff).slice(-2).reverse()) {
        box.append(el("div", { class: `alert ${a.severity}`, role: "status" },
          el("div", { class: "alert-title" }, el("span", { class: "icon", text: label[a.severity] || "● Watch" }), a.title || a.message),
          a.points ? el("ul", {}, a.points.map((p) => el("li", { text: p }))) : null));
      }
    }).catch(() => {});
  }

  // ---------- chart --------------------------------------------------------
  function renderChart() {
    const box = $("chart");
    box.replaceChildren();
    const key = state.indexKey === "truecolor" ? "ndvi" : state.indexKey;
    const idx = state.indexByKey[key];
    $("chart-title").textContent = `${idx.plain} this season`;
    const allPts = state.obs.filter((o) => o.status === "clear").map((o) => ({ o, t: Date.parse(o.datetime), s: o.stats[key] }));
    const pts = allPts.filter((p) => p.o.qa_status !== "suspect");
    const suspect = allPts.filter((p) => p.o.qa_status === "suspect");
    const cloudy = $("show-cloudy").checked ? state.obs.filter((o) => o.status !== "clear") : [];
    $("chart-empty").hidden = pts.length > 0;
    if (!pts.length) return;
    box.setAttribute("aria-label", `${idx.name} median over time, ${pts.length} clear dates`);

    const W = box.clientWidth || 600, H = box.clientHeight || 220;
    const m = { l: 40, r: 12, t: 10, b: 24 };
    const allT = allPts.map((p) => p.t).concat(cloudy.map((o) => Date.parse(o.datetime)));
    let t0 = Math.min(...allT), t1 = Math.max(...allT);
    if (t1 - t0 < 864e5 * 10) { t0 -= 864e5 * 5; t1 += 864e5 * 5; }
    let y0 = Math.min(idx.display_range[0], ...allPts.map((p) => p.s.p10));
    let y1 = Math.max(idx.display_range[1], ...allPts.map((p) => p.s.p90));
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
    const unit = svg("text", { x: m.l, y: m.t - 2, class: "unit-label" });
    unit.textContent = `${idx.name} (${scaleOf(idx.key)})`;
    axis.append(unit);
    s.append(axis);

    const band = pts.map((p) => `${x(p.t)},${y(p.s.p90)}`).concat(pts.slice().reverse().map((p) => `${x(p.t)},${y(p.s.p10)}`));
    s.append(svg("polygon", { class: "band", points: band.join(" ") }));
    s.append(svg("polyline", { class: "line", points: pts.map((p) => `${x(p.t)},${y(p.s.median)}`).join(" ") }));
    for (const o of cloudy) {
      const cx = x(Date.parse(o.datetime));
      s.append(svg("line", { class: "cloudy", x1: cx, x2: cx, y1: H - m.b, y2: H - m.b - 6 }));
    }
    for (const p of suspect) {
      s.append(svg("circle", { class: "dot suspect" + (p.o.id === state.obsId ? " selected" : ""), cx: x(p.t), cy: y(p.s.median), r: p.o.id === state.obsId ? 6 : 4.5 }));
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
      return allPts.reduce((a, b) => (Math.abs(x(b.t) - px) < Math.abs(x(a.t) - px) ? b : a));
    };
    hit.addEventListener("pointermove", (evt) => {
      const p = nearest(evt);
      cross.setAttribute("x1", x(p.t)); cross.setAttribute("x2", x(p.t)); cross.setAttribute("visibility", "visible");
      tip.replaceChildren(
        el("div", { class: "v", text: `${idx.name} ${fmt(p.s.median)}` }),
        el("div", { text: niceDate(p.o.date) }),
        el("div", { class: "muted", text: `10–90%: ${fmt(p.s.p10)}–${fmt(p.s.p90)} · CV ${fmt(p.s.cv)}` }),
        el("div", { class: "muted", text: `${pct(p.o.field_clear_fraction)} of field clear · click to view` }),
        p.o.qa_status === "suspect" ? el("div", { class: "warn-text", text: `⚠ Excluded from trend and alerts: ${qaText(p.o)}` })
          : qaShort(p.o) ? el("div", { class: "muted", text: `Note: ${qaText(p.o)}` }) : null,
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
        title: `${state.indexByKey[key].plain} zones`,
        items: z.zones.map((zz, i) => ({ color: colors[i], label: `Zone ${zz.zone}${i === 0 ? " (weakest)" : i === z.zones.length - 1 ? " (strongest)" : ""}` })),
      });
      out.replaceChildren();
      if (z.warning) out.append(el("div", { class: "warning", text: "This date is too even for useful zones. Try \"Whole season\" or the Nitrogen layer." }));
      const ul = el("ul", { class: "bullets small" });
      z.zones.forEach((zz, i) => ul.append(el("li", {},
        el("span", { class: "swatch", style: `background:${colors[i]}` }),
        el("b", { text: `Zone ${zz.zone}${i === 0 ? " (weakest)" : i === z.zones.length - 1 ? " (strongest)" : ""}: ` }),
        `${fmt(zz.area_ac, 1)} ac (${pct(zz.share)})`)));
      ul.append(el("li", { text: z.mode === "multi-date" ? `Based on ${z.dates.length} images, ${niceDate(z.dates[0])} → ${niceDate(z.dates[z.dates.length - 1])}` : `Based on ${niceDate(z.dates[0])} only` }));
      ul.append(el("li", { text: "Use for sampling or variable-rate plans; check against yield maps" }));
      out.append(ul);
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
      sel.replaceChildren(...clear.map((o) => el("option", { value: o.id, text: niceDate(o.date) })));
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
        title: `${state.indexByKey[key].plain}: ${niceDate(c.before)} → ${niceDate(c.after)}`,
        cmap: [[0, [178, 24, 43]], [0.25, [239, 138, 98]], [0.5, [247, 247, 247]], [0.75, [103, 169, 207]], [1, [33, 102, 172]]],
        lo: "worse", hi: "better", note: "Red = got worse · blue = got better",
      });
      for (const h of c.decline_hotspots) {
        markersLayer.addLayer(L.geoJSON(h.geometry, { style: { color: "#ff3b30", weight: 2, fillOpacity: 0 } }));
        markersLayer.addLayer(L.marker([h.lat, h.lon]).bindPopup(`<b>Decline ${h.rank}</b><br>${h.lat.toFixed(5)}, ${h.lon.toFixed(5)}<br>${h.area_ac} ac`));
      }
      const ul = el("ul", { class: "bullets small" });
      const li = (k, v) => ul.append(el("li", {}, el("b", { text: `${k}: ` }), v));
      li("Typical change", `${signed(c.stats.median)} ${name} (${scaleOf(key)})`);
      li("Got worse", `${fmt(c.declined_ha * 2.471, 1)} ac (${pct(c.declined_share)})`);
      li("Got better", `${fmt(c.improved_ha * 2.471, 1)} ac (${pct(c.improved_share)})`);
      if (c.ring_change != null && c.field_change < -0.05) {
        const regional = c.ring_change <= 0.6 * c.field_change;
        li("Neighbors", regional ? `also dropped (${signed(c.ring_change)}): likely weather, haze, or crop stage` : `held steady (${signed(c.ring_change)}): problem is in this field`);
      } else if (c.declined_share > 0.8 && !c.decline_hotspots.length) {
        li("Pattern", "whole field changed together (maturity, harvest, or a field-wide event)");
      }
      if (c.decline_hotspots.length) li("Problem patches", `${c.decline_hotspots.length} marked on the map`);
      if (c.overlap < 0.95) li("Note", `compared on the ${pct(c.overlap)} of the field clear on both dates`);
      out.replaceChildren(ul);
      for (const n of c.notes || []) if (!/clear on both/.test(n)) out.append(el("div", { class: "warning small", text: n }));
    } catch (err) {
      out.replaceChildren(el("p", { class: "small", text: err.message }));
    }
  });

  // ---------- data table ---------------------------------------------------
  function renderTable() {
    const t = $("obs-table");
    t.replaceChildren();
    const keys = state.indices.map((i) => i.key);
    t.append(el("tr", {}, ["Date", "Scene", "Clear", "Quality", ...state.indices.map((i) => i.name)].map((h) => el("th", { text: h }))));
    for (const o of state.obs.slice().reverse()) {
      t.append(el("tr", { class: "clickable", onclick: () => { state.obsId = o.id; state.obsPinned = true; renderObsSelect(); showSelected(); renderChart(); } },
        el("td", { text: niceDate(o.date) }),
        el("td", { text: o.scene_id, class: "muted" }),
        el("td", { text: pct(o.field_clear_fraction) }),
        el("td", { text: o.status !== "clear" ? "cloudy" : (qaShort(o) || "ok"), title: qaText(o) }),
        ...keys.map((k) => el("td", { text: o.status === "clear" ? fmt(o.stats[k].median) : "–" }))));
    }
  }

  // ---------- boot ---------------------------------------------------------
  $("toggle-sidebar").addEventListener("click", () => $("sidebar").classList.toggle("open"));

  // ---------- score ------------------------------------------------------------
  function scoreClass(v) { return v >= 80 ? "s-excellent" : v >= 65 ? "s-good" : v >= 50 ? "s-fair" : "s-poor"; }
  function renderScore(sc) {
    const box = $("score");
    const pts = $("score-points");
    box.replaceChildren();
    pts.replaceChildren();
    if (!sc || sc.score == null) {
      box.append(el("div", { class: "sub", text: "Appears after the first in-season images" }));
      return;
    }
    const r = 30, c = 2 * Math.PI * r;
    const ring = svg("svg", { viewBox: "0 0 72 72", class: `ring ${scoreClass(sc.score)}`, role: "img", "aria-label": `Field score ${sc.score} out of 100, ${sc.label}` });
    ring.append(svg("circle", { cx: 36, cy: 36, r, class: "track" }));
    ring.append(svg("circle", { cx: 36, cy: 36, r, class: "fill", "stroke-dasharray": `${(c * sc.score) / 100} ${c}`, transform: "rotate(-90 36 36)" }));
    const t = svg("text", { x: 36, y: 38, "text-anchor": "middle", class: "num" });
    t.textContent = String(sc.score);
    const of = svg("text", { x: 36, y: 50, "text-anchor": "middle", class: "of" });
    of.textContent = "/ 100";
    ring.append(t, of);
    const status = sc.status !== "paused" ? "vs. nearby farms" : sc.points[0].startsWith("Season over") ? "season over" : "off-season";
    box.append(ring, el("div", { class: "score-label" }, el("b", { text: sc.label }), el("span", { class: "muted", text: status })));
    box.title = `Score out of 100 as of ${niceDate(sc.as_of)}. ${sc.method}.`;
    pts.append(el("li", { class: "cap-li", text: "Why this score:" }));
    for (const p of sc.points) pts.append(el("li", { text: p }));
  }

  // ---------- yield estimate --------------------------------------------------------
  const METHOD_NAMES = {
    harvest: "Harvested", count: "Field count", satellite: "Satellite vs. nearby cropland",
    satellite_calibrated: "Satellite, calibrated on your farm", relative: "Satellite, relative only",
  };
  // Field-count inputs per crop. Lists accept several spots, e.g. "31, 29, 30".
  const COUNT_FORMS = {
    corn: [
      ["row_in", "Row spacing (in)", 30], ["row_ft", "Row length counted (ft)", 17.42],
      ["ears", "Ears in that length (each spot)", "", true], ["kernel_rows", "Kernel rows per ear", "", true],
      ["kernels_per_row", "Kernels per row", "", true],
      ["kernels_per_bu", "Kernels per bushel", 90000, false, [[80000, "80,000 · good grain fill"], [90000, "90,000 · normal"], [100000, "100,000 · stress"]]],
    ],
    soybean: [
      ["row_in", "Row spacing (in)", 30], ["row_ft", "Row length counted (ft)", 17.42],
      ["plants", "Plants in that length (each spot)", "", true], ["pods_per_plant", "Pods per plant", "", true],
      ["seeds_per_pod", "Seeds per pod", 2.5], ["seeds_per_lb", "Seeds per pound", 3000, false, [[3000, "3,000 · normal"], [3500, "3,500 · small seed / late stress"]]],
    ],
    wheat: [
      ["row_in", "Row spacing (in)", 7.5], ["row_ft", "Row length counted (ft)", 3],
      ["heads", "Heads in that length (each spot)", "", true], ["kernels_per_head", "Kernels per head", 22, true],
      ["kernels_per_lb", "Kernels per pound", 15000],
    ],
  };
  const nums = (v) => String(v).split(/[\s,;]+/).filter(Boolean).map(Number);

  function yieldTile(y) {
    const box = $("yield-tile");
    box.replaceChildren();
    if (y.bu_ac != null) {
      box.append(el("div", { class: "big" }, `${Math.round(y.bu_ac)} `, el("small", { text: "bu/ac" })),
        el("div", { class: "sub", text: y.method === "harvest" ? `Harvested ${y.year}` : `${y.low}–${y.high} · ${METHOD_NAMES[y.method] || ""}` }));
    } else {
      box.append(el("div", { class: "big muted", text: "—" }), el("div", { class: "sub", text: y.label }));
    }
  }

  function yieldWhy(y) {
    const li = [];
    const s = y.signal || {};
    if (s.rel) {
      const pct = Math.round((s.rel - 1) * 100);
      li.push(`Peak canopy ${niceDate(s.window[0])}${s.window[1] !== s.window[0] ? `–${niceDate(s.window[1])}` : ""} (${s.images} image${s.images > 1 ? "s" : ""}): NDVI ${fmt(s.field_ndvi)} vs ${fmt(s.area_ndvi)} for cropland within 2 km, ${pct === 0 ? "on par" : `${Math.abs(pct)}% ${pct > 0 ? "greener" : "less green"}`}.`);
      if (s.stage === "early") li.push("Nearby crops haven't peaked yet, so this can still move a lot.");
    } else if (s.stage === "none") li.push(`No in-season images for ${y.year} yet.`);
    if (y.expected) li.push(`Expected yield ${y.expected.bu_ac} bu/ac${y.expected.year !== y.year ? ` (entered for ${y.expected.year})` : ""}.`);
    if (y.calibration) li.push(`Calibrated on ${y.calibration.n} of your field-years: each 1% greener than neighbors ≈ ${fmt(y.calibration.k, 1)}% more yield${y.calibration.loo_rmse_bu != null ? `; typical miss ±${Math.round(y.calibration.loo_rmse_bu)} bu/ac` : ""}.`);
    else if (y.method === "satellite") li.push("Rough until calibrated: it assumes yield moves one-for-one with greenness, which understates differences. Record harvests to calibrate (3+ field-years).");
    if (y.method === "count" && y.satellite) li.push(`Satellite says about ${y.satellite.bu_ac} bu/ac (${y.satellite.low}–${y.satellite.high}).`);
    if (y.count && y.method !== "count") li.push(`Last field count: ${y.count.bu_ac} bu/ac on ${niceDate(y.count.date)}.`);
    return li;
  }

  function yieldForm(f, y, kind) {
    const form = el("form", {});
    const msg = el("p", { class: "small", role: "status" });
    const save = async (body) => {
      try {
        renderYield(await api(`/api/fields/${encodeURIComponent(f.id)}/yield/records`, { method: "POST", body: JSON.stringify({ year: y.year, ...body }) }), f);
      } catch (err) { msg.textContent = err.message; }
    };
    if (kind === "expected" || kind === "harvest") {
      const inp = el("input", { type: "number", min: "1", max: "400", step: "0.1", required: "", value: kind === "expected" && y.expected ? y.expected.bu_ac : "" });
      form.append(el("label", {}, kind === "expected" ? `Expected yield for ${y.year} (bu/ac), e.g. your APH` : `Harvested yield ${y.year} (bu/ac, field average)`, inp),
        el("button", { class: "btn primary", type: "submit", text: "Save" }), msg);
      form.addEventListener("submit", (e) => { e.preventDefault(); save({ kind, bu_ac: Number(inp.value) }); });
      return form;
    }
    const spec = COUNT_FORMS[y.crop];
    const grid = el("div", { class: "grid2" });
    const inputs = {};
    for (const [key, label, def, list, options] of spec) {
      const inp = options
        ? el("select", {}, options.map(([v, t]) => el("option", { value: String(v), text: t, ...(v === def ? { selected: "" } : {}) })))
        : el("input", { type: list ? "text" : "number", step: "any", value: String(def), inputmode: "decimal", placeholder: list ? "e.g. 31, 29, 30" : "" });
      inputs[key] = [inp, list];
      grid.append(el("label", { class: "small" }, label, inp));
    }
    const preview = el("div", { class: "preview", "aria-live": "polite", text: "Enter your counts to see bu/ac" });
    let t = null;
    const body = () => Object.fromEntries(Object.entries(inputs).filter(([, [inp]]) => inp.value !== "")
      .map(([k, [inp, list]]) => [k, list ? nums(inp.value) : Number(inp.value)]));
    form.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        try {
          const r = await api("/api/yield/count", { method: "POST", body: JSON.stringify({ crop: y.crop, inputs: body() }) });
          preview.textContent = `${r.bu_ac} bu/ac (${r.low}–${r.high})`;
        } catch (err) { preview.textContent = err.message; }
      }, 300);
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); save({ kind: "count", inputs: body() }); });
    form.append(el("p", { class: "small muted", text: "Count several spots across the field (not the edges). The default length is 1/1000 acre for that row spacing." }),
      grid, preview, el("button", { class: "btn primary", type: "submit", text: "Save count" }), msg);
    for (const [k, [inp]] of Object.entries(inputs)) {
      if (k === "row_in") inp.addEventListener("change", () => { if (y.crop !== "wheat") inputs.row_ft[0].value = (522.72 / Number(inp.value)).toFixed(2); });
    }
    return form;
  }

  function renderYield(y, f) {
    yieldTile(y);
    const p = $("yield-panel");
    p.replaceChildren();
    p.hidden = false;
    const chips = el("div", { class: "chips" });
    if (y.method) chips.append(el("span", { class: `ychip ${y.method === "harvest" ? "measured" : y.confidence === "field count" ? "count" : y.confidence}`, text: METHOD_NAMES[y.method] }));
    if (y.confidence && y.method !== "harvest") chips.append(el("span", { class: "ychip", text: y.confidence === "rough" ? "rough (±20%)" : y.confidence }));
    p.append(el("h3", { text: `Yield estimate · ${y.year}${y.crop ? ` · ${y.crop}` : ""}` }), el("div", { class: "big", text: y.label }), chips);
    const why = yieldWhy(y);
    if (why.length) p.append(el("ul", { class: "bullets small" }, why.map((t) => el("li", { text: t }))));
    if (f.my_role === "owner" && y.crop) {
      const steps = y.next_steps || [];
      const sec = (title, kind, open) => el("details", open ? { open: "" } : {}, el("summary", { text: title }), yieldForm(f, y, kind));
      if (!y.harvest) {
        p.append(sec(y.expected ? `Expected yield: ${y.expected.bu_ac} bu/ac (change)` : "Set expected yield (APH) to see bushels", "expected", steps.includes("set_expected")));
        p.append(sec("Count in the field (most accurate in season)", "count", false));
      }
      p.append(sec(y.harvest ? `Harvested: ${y.harvest.bu_ac} bu/ac (change)` : "Record harvest yield (calibrates future estimates)", "harvest", false));
    } else if (!y.crop && f.my_role === "owner") {
      p.append(el("p", { class: "small muted", text: "Set the crop (Manage → Edit name & crop) to corn, soybeans, or wheat." }));
    }
    p.append(el("details", {}, el("summary", { text: "How this is calculated" }),
      el("ul", { class: "bullets small" },
        el("li", { text: "Field count: ears × kernels ÷ kernels per bushel (corn); plants × pods × seeds ÷ seeds per lb ÷ 60 (soybeans); heads × kernels ÷ kernels per lb ÷ 60 (wheat)." }),
        el("li", { text: "Satellite: expected yield × how green the field was at peak canopy vs. cropland within 2 km on the same images. Calibrated with your harvests once you have 3+ field-years." }),
        ...(state.yieldSources || []).map((s) => el("li", {}, el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.label }))))));
  }

  async function loadYield(f) {
    $("yield-panel").hidden = true;
    $("yield-tile").replaceChildren(el("div", { class: "sub", text: "Loading…" }));
    try {
      if (!state.yieldSources) state.yieldSources = (await api("/api/yield/methods")).sources;
      const y = await api(`/api/fields/${encodeURIComponent(f.id)}/yield`);
      if (state.field && state.field.id === f.id) renderYield(y, f);
    } catch (err) {
      $("yield-tile").replaceChildren(el("div", { class: "sub", text: "Estimate unavailable" }));
    }
  }

  // ---------- watch list & disease reports --------------------------------------
  const LEVELS = ["Low", "Moderate", "High"];
  function renderRisk(p) {
    const box = $("pressure-badge");
    box.replaceChildren();
    if (p.score == null) { box.append(el("div", { class: "big muted", text: "—" }), el("div", { class: "sub", text: "Set the crop to see disease risk" })); return; }
    const lvl = p.level.toLowerCase();
    box.append(
      el("div", {}, el("span", { class: `big ${lvl}`, text: p.level }), el("span", { class: "unit", text: ` · ${p.score}/100` })),
      el("div", { class: "meter", role: "img", "aria-label": `Disease risk ${p.level}` }, LEVELS.map((L) => el("span", { class: `${L.toLowerCase()}${L === p.level ? " on" : ""}` }))),
      el("div", { class: "scale" }, LEVELS.map((L) => el("span", { text: L }))),
      el("div", { class: "sub", text: `Highest: ${p.top}` }));
    box.title = `${p.method} As of ${niceDate(p.as_of)}.`;
  }

  async function loadWatch(f, asOf = null) {
    const box = $("watch");
    box.replaceChildren(el("p", { class: "small muted", text: "Checking disease risk and weather…" }));
    $("pressure-badge").replaceChildren(el("div", { class: "sub", text: "Checking…" }));
    let w;
    try { w = await api(`/api/fields/${f.id}/watch${asOf ? `?as_of=${asOf}` : ""}`); }
    catch (err) { box.replaceChildren(el("p", { class: "small muted", text: `Watch list unavailable: ${err.message}` })); return; }
    if (!state.field || state.field.id !== f.id) return;
    renderRisk(w.pressure);
    box.replaceChildren(el("h4", { text: asOf ? `Things to watch · as of ${niceDate(asOf)}` : "Things to watch" }));
    if (!w.pressure.crop_kind) box.append(el("p", { class: "small muted", text: "Tip: set the crop (Manage → Edit name & crop) to get corn- or soybean-specific disease risk." }));

    // Disease risk: one bar per disease (0–100), details for the top one only.
    const top = w.diseases.slice(0, 3);
    if (top.length) {
      const sec = el("div", { class: "sec" }, el("div", { class: "cap", text: "Disease risk (0–100, from recent weather, nearby reports, and crop stage)" }));
      for (const d of top) {
        const lvl = d.level.toLowerCase();
        sec.append(el("div", { class: "dbar" }, el("span", { text: d.name }),
          el("div", { class: "track", role: "img", "aria-label": `${d.name} ${d.score} out of 100, ${d.level}` }, el("div", { class: `fill ${lvl}`, style: `width:${Math.max(3, d.score)}%` })),
          el("span", { class: "val", text: `${d.score}/100` })));
      }
      const d = top[0];
      sec.append(el("ul", { class: "bullets small" },
        d.points.slice(0, 2).map((x) => el("li", { text: x })),
        d.level !== "Low" ? el("li", {}, el("b", { text: `Look for (${d.name.toLowerCase()}): ` }), d.watch) : null));
      box.append(sec);
    }

    // Weather: three small tiles for the last 14 days.
    const wx = w.weather || {};
    if (wx.hours) {
      const src = w.weather_source || {};
      box.append(el("div", { class: "sec" },
        el("div", { class: "cap", text: `Weather, last ${wx.days} days${src.station ? ` · ${src.station}` : ""}` }),
        el("div", { class: "wtiles" },
          el("div", { class: "wtile" }, el("b", { text: wx.rain_mm == null ? "n/a" : `${(wx.rain_mm / 25.4).toFixed(1)} in` }), wx.rain_mm == null ? "rain not reported" : "☔ rain"),
          el("div", { class: "wtile" }, el("b", { text: `${wx.wet_hours} h` }), "💧 humid or wet"),
          el("div", { class: "wtile" }, el("b", { text: `${wx.hot_days} days` }), "🌡 at 95°F+"))));
    } else if (w.weather_note) {
      box.append(el("p", { class: "small muted", text: `🌦 ${w.weather_note}` }));
    }

    // Satellite alerts, as short titled bullets.
    for (const it of w.items.filter((i) => i.kind === "satellite")) {
      box.append(el("div", { class: "witem" },
        el("span", { class: "ic", "aria-hidden": "true", text: "🛰" }),
        el("span", { class: `t l-${String(it.level).toLowerCase()}`, text: it.title }),
        el("ul", {}, (it.points || []).slice(0, 2).map((x) => el("li", { text: x })))));
    }
  }

  let diseaseList = null;
  $("report-btn").addEventListener("click", async () => {
    const form = $("report-form");
    form.hidden = !form.hidden;
    if (form.hidden) return;
    diseaseList = diseaseList || (await api("/api/diseases"));
    const crop = (state.field.crop || "").toLowerCase().includes("soy") ? "soybean" : (state.field.crop || "").toLowerCase().includes("corn") ? "corn" : null;
    const sel = $("rep-disease");
    sel.replaceChildren(...diseaseList.filter((d) => !crop || !d.crop || d.crop === crop)
      .map((d) => el("option", { value: d.key, text: d.crop ? `${d.name} (${d.crop})` : d.name })));
    $("rep-date").value = isoToday();
    $("rep-date").max = isoToday();
    $("rep-msg").textContent = "";
  });
  $("rep-cancel").addEventListener("click", () => { $("report-form").hidden = true; });
  $("report-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/crop-reports", { method: "POST", body: JSON.stringify({
        disease: $("rep-disease").value, severity: $("rep-severity").value, observed: $("rep-date").value,
        notes: $("rep-notes").value || null, field_id: state.field.id, share: $("rep-share").checked }) });
      $("rep-notes").value = "";
      $("rep-msg").textContent = "Thanks! Nearby fields' disease pressure now includes your report.";
      loadWatch(state.field);
    } catch (err) { $("rep-msg").textContent = err.message; }
  });

  document.querySelectorAll("#owner-actions .menu button").forEach((b) => b.addEventListener("click", () => { $("owner-actions").open = false; }));
  $("details-btn").addEventListener("click", async () => {
    const f = state.field;
    const name = prompt("Field name", f.name);
    if (name === null) return;
    const crop = prompt("Crop this season (e.g. corn, soybean, wheat)", f.crop || "");
    if (crop === null) return;
    await api(`/api/fields/${f.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() || f.name, crop: crop.trim() || null }) });
    await loadFields();
    selectField(f.id);
  });

  // ---------- sharing ----------------------------------------------------------
  async function renderShare() {
    const f = state.field;
    const list = $("share-list");
    list.replaceChildren();
    const members = await api(`/api/fields/${f.id}/members`);
    for (const m of members) {
      list.append(el("li", {}, `${m.display_name} (${m.email}) · ${m.role === "owner" ? "owner" : "can view"} `,
        m.role === "viewer" ? el("button", { type: "button", class: "linkbtn", text: "remove", onclick: async () => {
          await api(`/api/fields/${f.id}/members/${m.user_id}`, { method: "DELETE" });
          renderShare();
        } }) : null));
    }
    $("showcase-toggle").checked = Boolean(f.showcase);
  }
  $("share-btn").addEventListener("click", () => {
    const p = $("share-panel");
    p.hidden = !p.hidden;
    if (!p.hidden) renderShare();
  });
  $("share-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/fields/${state.field.id}/members`, { method: "POST", body: JSON.stringify({ email: $("share-email").value.trim() }) });
      $("share-email").value = "";
      $("share-msg").textContent = "Shared.";
      renderShare();
    } catch (err) { $("share-msg").textContent = err.message; }
  });
  $("showcase-toggle").addEventListener("change", async (e) => {
    const rec = await api(`/api/fields/${state.field.id}`, { method: "PATCH", body: JSON.stringify({ showcase: e.target.checked }) });
    state.field.showcase = rec.showcase;
    $("share-msg").textContent = rec.showcase ? "Score is now on your profile." : "Removed from your profile.";
  });

  // ---------- routing & boot -----------------------------------------------------
  const VIEWS = ["auth", "today", "feed", "fields", "market", "messages", "profile"];
  function showView(name) {
    for (const v of VIEWS) $(`view-${v}`).hidden = v !== name;
    $("topbar").hidden = name === "auth";
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
    $("toggle-sidebar").hidden = name !== "fields";
    if (name === "fields") setTimeout(() => map.invalidateSize(), 0);
  }

  let fieldsReady = false;
  async function startFields() {
    if (fieldsReady) return;
    fieldsReady = true;
    await loadFields();
    if (!state.fields.length) mapperOpen("new");
    else if (state.fields.length === 1 && !state.field) selectField(state.fields[0].id);
    else if (!state.field) fitAllFields();
    refresh();
  }

  async function route() {
    let h = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
    if (!state.me && !h.startsWith("login")) { location.hash = "#/login"; return; }
    const [view, ...rest] = h.split("/");
    const arg = rest.join("/");
    if (view === "login") { showView("auth"); FM.views.auth(); return; }
    if (view === "market") {
      if (!(FM.features || {}).marketplace) { location.hash = "#/today"; return; }
      showView("market"); FM.views.market(arg); return;
    }
    if (view === "news" || view === "feed") { showView("feed"); FM.views.news(arg); return; }
    if (view === "today" || view === "") { showView("today"); FM.views.today(); return; }
    if (view === "messages") { showView("messages"); FM.views.messages(arg); return; }
    if (view === "u" || view === "me") { showView("profile"); FM.views.profile(view === "me" ? state.me.user.id : arg, view === "me"); return; }
    showView("fields");
    await startFields();
    const fid = view === "fields" ? arg : view; // old links were "#<field id>"
    if (fid && (!state.field || state.field.id !== fid)) selectField(fid);
  }
  window.addEventListener("hashchange", () => route().catch((e) => console.error(e)));

  async function boot() {
    initDates();
    const [health, indices] = await Promise.all([api("/api/health"), api("/api/indices")]);
    FM.appName = health.app_name || "Overacre";
    FM.tagline = health.tagline || "";
    FM.features = health.features || {};
    // Marketplace is paused unless the server turns it on (FIELD_MONITOR_MARKETPLACE=1).
    document.querySelectorAll('[data-nav="market"]').forEach((a) => { a.hidden = !FM.features.marketplace; });
    document.title = FM.appName;
    $("brand").textContent = FM.appName;
    $("version").textContent = `v${health.app_version} · processing v${health.processing_version}${health.git_commit ? " · " + health.git_commit.slice(0, 7) : ""}`;
    state.indices = indices;
    state.indexByKey = Object.fromEntries(indices.map((i) => [i.key, i]));
    renderIndexSelect();
    renderIndexInfo();
    try { state.me = await api("/api/me"); } catch (_) { state.me = null; }
    FM.setMe(state.me);
    await route();
  }

  window.FM = Object.assign(window.FM || {}, { el, api, fmt, pct, relTime, state, views: {}, route, scoreClass, svg, setMe: () => {} });
  $("logout-btn").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    location.hash = "#/login";
    location.reload();
  });
  // market.js registers its views on window.FM before boot runs.
  window.addEventListener("DOMContentLoaded", () => {
    boot().catch((err) => { document.body.prepend(el("div", { class: "alert high", text: `Failed to start: ${err.message}` })); });
  });
})();
