/* Crossroads lookup that runs in the browser ("250th and Waverly Road").
   Used when the server can't reach OpenStreetMap's Overpass API (some networks block the server
   but not the browser). Same matching rules as fieldmonitor/intersections.py: each side matches
   road names/route numbers by word, ignoring a trailing suffix (Road, St, ...) and a leading
   direction (N, North, ...); "250" matches "250th" but not "2500th". */
(() => {
  "use strict";
  const SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"];
  const SUFFIXES = new Set(["road", "rd", "street", "st", "avenue", "ave", "av", "highway", "hwy", "lane", "ln", "drive", "dr",
    "boulevard", "blvd", "trail", "trl", "parkway", "pkwy", "way", "court", "ct", "place", "pl", "circle", "cir"]);
  const DIRS = new Set(["n", "s", "e", "w", "north", "south", "east", "west", "ne", "nw", "se", "sw"]);
  const GAP_M = 30;

  function parse(q) {
    const parts = q.trim().split(/\s+(?:and|at)\s+|\s*[&/+]\s*/i);
    if (parts.length < 2) return null;
    const a = parts[0].trim(), b = parts.slice(1).join(" ").trim();
    return a.length >= 2 && b.length >= 2 ? [a, b] : null;
  }
  function nameRegex(part) {
    let words = part.toLowerCase().match(/[a-z0-9]+/g) || [];
    if (words.length > 1 && DIRS.has(words[0])) words = words.slice(1);
    if (words.length > 1 && SUFFIXES.has(words[words.length - 1])) words = words.slice(0, -1);
    if (!words.length) return null;
    const body = words.map((w) => { const m = w.match(/^(\d+)(st|nd|rd|th)?$/); return m ? `${m[1]}(st|nd|rd|th)?` : w; }).join(" ");
    return `(^| )${body}( |$)`;
  }

  // Planar math in meters around the search point (fine over a few dozen miles).
  function projector(lat0) {
    const kx = 111320 * Math.cos(lat0 * Math.PI / 180), ky = 110540;
    return { to: ([lat, lon]) => [lon * kx, lat * ky], from: ([x, y]) => [y / ky, x / kx] };
  }
  function segX(p1, p2, p3, p4) {
    const d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
    if (Math.abs(d) < 1e-9) return null;
    const t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
    const u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
    return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])] : null;
  }
  function nearestOnSeg(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
    const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
    return [a[0] + t * dx, a[1] + t * dy];
  }
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  function crossings(ways, ra, rb, center) {
    const P = projector(center[0]);
    const reA = new RegExp(ra, "i"), reB = new RegExp(rb, "i");
    const label = (t) => t.name || t.ref || "road";
    const lines = (re) => ways.filter((w) => re.test(w.tags.name || "") || re.test(w.tags.ref || ""))
      .map((w) => ({ name: label(w.tags), pts: w.geometry.map((g) => P.to([g.lat, g.lon])) }));
    const A = lines(reA), B = lines(reB), c = P.to(center), found = [];
    for (const a of A) for (const b of B) {
      if (a.name === b.name && a.pts.length === b.pts.length) continue;
      let hit = null;
      for (let i = 1; i < a.pts.length && !hit; i++)
        for (let j = 1; j < b.pts.length && !hit; j++) hit = segX(a.pts[i - 1], a.pts[i], b.pts[j - 1], b.pts[j]);
      if (!hit) {  // T-junction drawn a few meters short
        for (const [line, other] of [[a.pts, b.pts], [b.pts, a.pts]]) {
          for (const end of [line[0], line[line.length - 1]]) {
            for (let j = 1; j < other.length; j++) {
              const q = nearestOnSeg(end, other[j - 1], other[j]);
              if (dist(end, q) <= GAP_M) { hit = [(end[0] + q[0]) / 2, (end[1] + q[1]) / 2]; break; }
            }
            if (hit) break;
          }
          if (hit) break;
        }
      }
      if (hit) found.push({ xy: hit, a: a.name, b: b.name });
    }
    found.sort((p, q) => dist(p.xy, c) - dist(q.xy, c));
    const uniq = [];
    for (const f of found) if (uniq.every((u) => dist(u.xy, f.xy) > 300)) uniq.push(f);
    return uniq.slice(0, 3).map((u) => {
      const [lat, lon] = P.from(u.xy), miles = Math.round(dist(u.xy, c) / 160.9) / 10;
      return { type: "place", kind: "intersection", main: `${u.a} & ${u.b}`, name: `${u.a} & ${u.b}`, lat, lon, bbox: null,
               secondary: `Intersection · ${miles} mi away`, miles, source: "OpenStreetMap" };
    });
  }

  async function query(ql) {
    let last;
    for (const url of SERVERS) {
      try {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 20000);
        const r = await fetch(url, { method: "POST", body: new URLSearchParams({ data: ql }), signal: ctl.signal });
        clearTimeout(timer);
        if (!r.ok) throw new Error(`${url.split("/")[2]} answered ${r.status}`);
        return await r.json();
      } catch (err) { last = err; }
    }
    throw last || new Error("no Overpass server");
  }

  /* Crossroads for "A and B" near center [lat, lon]; nearest first. Throws if Overpass can't be reached. */
  async function find(q, center) {
    const sides = parse(q);
    if (!sides) return [];
    const ra = nameRegex(sides[0]), rb = nameRegex(sides[1]);
    if (!ra || !rb) return [];
    for (const half of [0.25, 0.6]) {  // nearby first, then wider
      const dlon = half / Math.max(0.2, Math.cos(center[0] * Math.PI / 180));
      const bbox = [center[0] - half, center[1] - dlon, center[0] + half, center[1] + dlon].map((v) => v.toFixed(4)).join(",");
      const sel = (rx) => `way["highway"]["name"~"${rx}",i](${bbox});way["highway"]["ref"~"${rx}",i](${bbox});`;
      const data = await query(`[out:json][timeout:20];(${sel(ra)}${sel(rb)});out tags geom;`);
      const ways = (data.elements || []).filter((e) => e.type === "way" && (e.geometry || []).length > 1);
      const out = crossings(ways, ra, rb, center);
      if (out.length) return out;
    }
    return [];
  }

  window.OveracreCrossroads = { find, parse, nameRegex, crossings };
})();
