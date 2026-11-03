# Field Monitor

Satellite field monitoring for farms. Outline a field on a satellite map the way you plan a
drone mapping mission, or import a KML/KMZ/GeoJSON boundary, and the field keeps itself up to date.
Every new Sentinel-2 pass is pulled automatically and turned into per-field vegetation,
chlorophyll, moisture, water, and residue maps, plus a season trend, scouting hotspots with GPS
points, management zones, and before/after change maps.

- **Imagery:** Copernicus Sentinel-2 L2A surface reflectance, 10 m, a new pass every ~2–5 days.
  It's free and needs no account. Scenes come from the
  [Earth Search STAC API](https://earth-search.aws.element84.com/v1) (Element 84). If that is
  unreachable, the app falls back to listing the public `sentinel-cogs` S3 bucket directly.
- **Runs anywhere:** a Python backend (FastAPI + rasterio) and a single-page UI with Leaflet
  vendored in. Only the basemap tiles come from the internet.
- **Stewardship:** stable field IDs, immutable boundaries, write-once observations, and an
  append-only `lineage.jsonl`. Every result records its scene, asset URLs, processing
  version, and git commit.

## Accounts, sharing, and the marketplace

The monitoring is the backbone of a small ag-business network:

- **Accounts:** email + password (scrypt-hashed), or **Continue with Google** when
  `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are set (redirect URI:
  `<FIELD_MONITOR_PUBLIC_URL>/api/auth/google/callback`). Members pick roles: farmer/grower,
  agronomist/consultant, service provider, input & equipment dealer, crop buyer.
- **Private fields:** every field belongs to the account that created it and is invisible to
  everyone else. Owners can **Share** a field (view-only) with another account by email, e.g.
  their agronomist, and can opt in to show a field's **score** (never the map) on their profile.
  The first account created claims fields that existed before accounts.
- **Marketplace:** post services offered, products or crops for sale, or "wanted" requests;
  browse and filter by type, category, or keyword; message the poster in-app. No payments in
  this version.
- **Profiles:** business name, roles, location, bio, website/phone, active listings, and
  showcased field scores. Listings and profiles can be reported.

Security: HttpOnly SameSite=Lax session cookie (hashed at rest, 30 days), a required
`X-Requested-With` header on every write as CSRF protection, login rate limiting, and access
checks on every field endpoint (unknown and forbidden fields both return 404). In production,
run behind HTTPS (set `FIELD_MONITOR_COOKIE_SECURE=1` if TLS ends at a proxy) and back up
`$FIELD_MONITOR_DATA/app.db` along with the field data.

## Field score

A 0–100 score in the field header (and on the field list) answers "how is this field doing
compared with the farmland around it?":

| Part | Weight | Measure |
|---|---|---|
| Vigor vs. area | 60% | The field's NDVI as a percentile of NDVI across **cropland within 2 km** (ESA WorldCover 2021 cropland class), latest 3 good in-season images |
| Evenness | 25% | Within-field variability (CV ≤ 0.05 → 100, ≥ 0.25 → 0) |
| Keeping pace | 15% | Field NDVI change minus the area's change over the last 2–6 weeks |

Labels: 80+ Excellent, 65+ Good, 50+ Fair, below 50 Needs attention. Only QA-"ok" images count.
The season ends when nearby cropland falls below 75% of its own peak, after which the score
freezes ("Season over") so harvest doesn't read as failure. On the 2025 test field it ran 31
(mid-June, behind its neighbors) → 68 (mid-July) → 85 (mid-August) → 78 at season end.

## Quick start

```bash
cd apps/field-monitor
uv venv && uv pip install -e '.[dev]'      # or: python -m venv .venv && pip install -e '.[dev]'

# Optional: import boundaries (GeoJSON, WGS84 lon/lat)
.venv/bin/fieldmonitor import ../../skills/my-farm-advisor/field-management/field-boundaries/examples/real_10_fields_iowa.geojson

.venv/bin/fieldmonitor serve            # http://127.0.0.1:8765
```

### Mapping a field (drone-mission style)

1. Press **＋ Map a new field**. It opens automatically when a farm has no fields yet.
2. Find the field. Search a town, road, or address, type coordinates as `lat, lon`, or press
   **◎ My location** in the field.
3. Tap each corner of the field edge on the satellite image. The bottom sheet shows live
   acreage, hectares, and edge length.
4. Adjust: **drag** a corner to move it, tap a **＋** midpoint to add a corner on that edge,
   tap a corner to remove it. **Undo** (Ctrl/⌘-Z) steps back. Edges that cross, or outlines
   over ~12,000 ac, are flagged and can't be saved.
5. Name it, optionally set the crop, and press **Save field**.

Saving starts an automatic backfill of the last 12 months of imagery (about 1–2 minutes;
progress shows on the field). After that, the server checks every field for new passes every
12 hours. **Check for new imagery** forces a check now. Older seasons can be pulled from
**Fetch a specific date range**.

**Edit boundary** opens the same editor on an existing field. Saving creates a new version
(`<id>-v2`) with its own imagery and archives the old one. The old boundary keeps its
history, so past numbers are never silently re-measured on a different shape.

**Import** accepts `.kml` and `.kmz` (DJI Pilot 2 / DJI Terra mapping-area missions, Google
Earth, most farm software) and `.geojson`. Every polygon becomes a field. Imports of more
than 5 fields are backfilled one field at a time by the scheduler.

Only the field's own pixels are read from the cloud-optimized GeoTIFFs, not whole 110 km tiles.

Data is written to `./data` by default. Set `FIELD_MONITOR_DATA=/path` to change it.
Set `FIELD_MONITOR_REFRESH_HOURS` to change how often fields are checked for new passes
(default `12`; `0` turns automatic updates off). Other settings: `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `FIELD_MONITOR_PUBLIC_URL` (for the Google redirect), and
`FIELD_MONITOR_COOKIE_SECURE=1`.
In Docker deployments, point it at persistent storage (for example `/data/field-monitor`).

### Command line (for scheduled jobs and the Data Pipeline agent)

```bash
fieldmonitor fields                                         # list fields
fieldmonitor import boundaries.geojson --owner you@example.com --id-property FIELD_ID --name-property NAME
fieldmonitor process all --start 2026-04-01 --end 2026-11-01   # idempotent; safe to re-run daily
```

`process` skips scenes already processed at the current processing version, so a daily
cron job only does new work.

## What it measures

| Layer | Formula (S2 bands) | Use it for | Watch out for |
|---|---|---|---|
| **NDVI** | (B08−B04)/(B08+B04) | Emergence → canopy closure; stand problems, drown-outs, hail, weed escapes | Saturates ~0.8+ in closed canopy |
| **NDRE** | (B8A−B05)/(B8A+B05) | Mid/late-season chlorophyll and N status after NDVI saturates | 20 m. S2's 705 nm band reads higher than drone red-edge sensors |
| **CIre** | B07/B05 − 1 | Chlorophyll content; N-sufficiency vs. a reference strip | Unbounded; compare ratios, not absolutes |
| **GNDVI** | (B08−B03)/(B08+B03) | Chlorophyll variation at 10 m | Mixes biomass and chlorophyll |
| **EVI** | 2.5(B08−B04)/(B08+6B04−7.5B02+1) | Dense canopy and hazy dates | Sensitive to residual cloud |
| **SAVI** | 1.5(B08−B04)/(B08+B04+0.5) | Early season (<40% cover) | Fixed soil factor |
| **MSAVI2** | self-adjusting SAVI | Emergence and cover-crop establishment | Little value after canopy closure |
| **NDMI** | (B8A−B11)/(B8A+B11) | Canopy water: drought/heat stress, irrigation uniformity, dry-down | Plant water, not soil moisture |
| **NDWI** | (B03−B08)/(B03+B08) | Standing water, ponding, failed tile drainage | >0 ≈ open water |
| **NDTI** | (B11−B12)/(B11+B12) | Residue cover; verifying tillage and conservation practices | Only meaningful with no green canopy |
| True color | B04/B03/B02 | Sanity check: clouds, haze, tillage patterns | — |

The UI shows each layer's interpretation guide and caveats next to the map. The guides are
orientation aids, not agronomic thresholds. Confirm anything that matters by scouting.

## How it works

1. **Search.** Find Sentinel-2 L2A scenes that intersect the field, then deduplicate to one per
   date. Prefer the reprocessed (`_1_`) copy and the tile that contains the field centroid.
   The scene-level cloud filter is deliberately loose (60%) because…
2. **Mask per field.** The Scene Classification Layer is read first. Pixels classed as cloud,
   cloud shadow, cirrus, snow, or saturated are masked and dilated by 2 px (40 m) to catch
   fuzzy cloud edges. If less than 50% of the field is clear, only true color is stored
   (status `cloudy`), so the UI can show *why* a date was skipped.
3. **Read only what's needed.** Windowed HTTP range reads from the COGs, snapped to the 20 m
   grid so 10 m and 20 m bands align exactly. The 20 m bands are bilinearly resampled to 10 m.
4. **Scale correctly.** Reflectance = DN × scale + offset from each asset's `raster:bands`,
   *except* when Earth Search marks `earthsearch:boa_offset_applied: true`. Then the −0.1
   offset is already baked into the pixels, even though the metadata still lists it.
   Applying it twice drives red reflectance negative and NDVI above 1. See the validation notes.
5. **Shrink the field 10 m** before computing statistics, so mixed edge pixels (roads,
   headlands, tree lines) don't bias field numbers.
6. **Compute every index**, then store per-field stats (mean, median, p10/p90, std, CV), the
   rasters, and Web Mercator PNG overlays.

### Clouds, haze, and anomalies: filtered vs. flagged

The goal is to throw out what the *atmosphere* did and flag what the *crop* did. Layers, in order:

| Layer | What it removes or flags | How |
|---|---|---|
| Scene | Hopeless tiles | Skip if >60% cloud across the 110 km tile (kept loose on purpose) |
| Pixel | Cloud, cirrus, cloud shadow, snow, terrain shadow, bad data | Sen2Cor scene classification, cloud/shadow grown 40 m |
| Field | Mostly-cloudy dates | <50% of the field clear → `cloudy`: no stats or alerts, photo kept |
| Date: haze | Thin cloud/smoke/haze the mask missed | Aerosol optical thickness > 0.40 or blue reflectance over canopy > 0.075 → **suspect**; > 0.25 / > 0.060 → caution |
| Date: transient | Missed cloud/shadow on one pass | Median jumps ≥ 0.08 away from both the previous and next clear pass, which agree within 0.04 → **suspect** |
| Pair | False change from different clear areas | Dates are compared only on pixels clear on **both**; pairs with <50% common clear ground are skipped |

**Suspect** dates stay in storage, the data table, and the CSV (with reasons), but are left out of
the trend line, alerts, the field-list headline value, and multi-date zones. They show as hollow
gray dots on the chart.

A drop on good dates becomes an alert, then:

- **Pending** until a later clear pass exists (typically 2–5 days). **Confirmed** if that pass is
  still low; **recovered** (info) if not.
- **Field-specific vs. regional**: the same index is tracked in a ring 30–150 m *outside* the field.
  If the neighbors fell by ≥60% as much, the change is **regional** (weather, haze, area-wide
  maturity). If they held steady, it's **this field only** (management, disease, pests, drainage,
  a hail strip), with a pointer to the hotspot list.
- **Normal maturity** (info) requires all three: after the seasonal peak, confirmed, and regional.
  A field-only drop after the peak stays a decline, noting it may be early dry-down.

Thresholds were calibrated on the 2025 test season (see validation notes) and live in
`pipeline.py` (`HAZE_*`) and `qa.py`.

### Analyses

- **Hotspot explanations:** tap a numbered spot for a pop-up that shows the spot's own NDVI,
  NDRE, and crop water (NDMI) against the **field average** ("NDVI here is 0.26 below the field
  average"), plus what's visible (along the edge, a straight strip, low all season, new since
  a date, open water) and up to three ranked possible causes, each with what to check on the
  ground. Rules live in `fieldmonitor/diagnose.py`; they're hedged on purpose (the satellite
  shows where, not why). Surface water is only called when NDWI > 0: NDWI also rises wherever
  the crop thins, which on the test field had falsely flagged every weak spot as "water".
- **Scout (hotspots):** contiguous patches more than max(1.5×std, 0.05) below the field median,
  at least 0.2 ha. Ranked by area × deficit, each with a GPS point guaranteed to lie inside the
  patch and a Google Maps directions link.
- **Zones:** equal-area quantile zones with a 3×3 majority filter, exportable as GeoJSON.
  The default *multi-date* mode averages standardized index maps across every clear date with
  established canopy (median NDVI ≥ 0.5). Single-date zones warn when the field is too uniform
  for zones to mean anything.
- **Change:** per-pixel difference between two dates on the same grid, with the area that
  declined or improved past a threshold and the decline patches.
- **Alerts:** see the section above: drops of ≥0.08 and rising within-field variability (CV)
  over an established canopy, each pending → confirmed and scoped field vs. regional.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/indices` | Index catalog with formulas, ranges, color ramps, guidance |
| GET/POST | `/api/fields` | List fields (with sync status) / create from GeoJSON or an uploaded KML/KMZ/GeoJSON file; new fields start syncing |
| GET/PATCH | `/api/fields/{id}` | Read / edit name, crop, notes, archived (boundaries are immutable) |
| POST | `/api/fields/{id}/redraw` | Save an edited boundary as a new field version; archives the old one |
| POST | `/api/fields/{id}/sync` | Check for new imagery now (otherwise automatic every 12 h) |
| GET | `/api/geocode?q=` | Place search for the mapper (`lat, lon` or OpenStreetMap Nominatim) |
| GET | `/api/fields/{id}/scenes?start&end&max_cloud` | Available scenes and whether each is processed |
| POST | `/api/fields/{id}/process` | Fetch a specific date range `{start, end, max_cloud}` → poll `/api/jobs/{job}` |
| GET | `/api/fields/{id}/observations` | All observations with stats and provenance |
| GET | `/api/fields/{id}/observations/{obs}/{layer}.png` | Map overlay (immutable, cacheable) |
| GET | `/api/fields/{id}/observations/{obs}/hotspots?index=` | Scouting targets |
| GET | `/api/fields/{id}/zones?index&n&obs_id` | Management zones (omit `obs_id` for multi-date) |
| GET | `/api/fields/{id}/change?before&after&index&threshold` | Change map and stats |
| GET | `/api/fields/{id}/alerts?index=` | Time-series alerts |
| GET | `/api/fields/{id}/timeseries.csv` | Every observation × index (median, mean, p10, p90, CV) |

Interactive API docs are served at `/docs`.

## Data layout and stewardship

```
$FIELD_MONITOR_DATA/
  lineage.jsonl                      append-only: every field/observation write and sync, with app version + git commit
  fields/<field_id>/field.json       boundary (immutable) + editable name/crop/notes/archived, replaces/replaced_by links
  fields/<field_id>/sync.json        last automatic/manual check: window, scenes found, new, clear, errors
  fields/<field_id>/obs/<date>_<scene>_v<processing_version>/
      meta.json                      stats, cloud fractions, grid, provenance (asset URLs, params)
      arrays.npz                     index rasters + masks on the native 10 m UTM grid
      truecolor.png, <index>.png     overlays
```

- Fields are archived, never deleted. A changed boundary is a new field, so history stays
  attached to the geometry it was measured on.
- Observations are published atomically and never overwritten. Any change to index math,
  masking, resampling, or rendering must bump `PROCESSING_VERSION` in
  `fieldmonitor/__init__.py`. New results then land beside the old ones instead of replacing them.
  The next sync of each field automatically re-processes every scene it had under the old
  version, so history reappears without a manual backfill.

## Validation notes

Run on 2026-11-02 against real data: field `OSM_998713335` (230 ac, southern Iowa, tile
15TVF) from `real_10_fields_iowa.geojson`, for 2025-04-15 → 2025-10-31.

- 48 scenes found (via the S3 fallback; the sandbox blocked the STAC API), 21 clear over the
  field, processed in ~65 s.
- The NDVI trajectory matches a corn season: ~0.20 bare soil in early May, 0.64 on Jun 19,
  0.92 at peak (late Jul–Aug), then 0.86 on Sep 7 and 0.44 on Sep 14 (dry-down), and ~0.25
  after harvest.
- **Bug caught:** the first run produced NDVI 1.32. Raw red DN over dense canopy was 177–354,
  which proved Earth Search had already removed the BOA offset (`boa_offset_applied: true`)
  while `raster:bands` still listed `offset: -0.1`. Fixed in `raster.py`; the flag is recorded
  in each observation's provenance.
- Haze QA (processing v2), same season: clear days had AOT 0.06–0.15 and blue-over-canopy
  0.026–0.044. Two dates were flagged suspect: **May 30** (AOT 0.53) and **Sep 14** (blue
  0.086, 3× normal, with 81% clear, so thin cloud the mask missed). Before QA, Sep 14 produced
  a "patchiness" watch and an exaggerated 0.42 one-week drop. After QA, the only NDVI alert is
  Sep 7 → 22: confirmed, regional (neighbors 0.84 → 0.37), normal maturity. Re-running the season
  at v2 took ~70 s and left the v1 results on disk untouched.
- NDRE at peak was ~0.78, higher than drone-derived NDRE guides suggest (B05 is at 705 nm),
  so the NDRE display range was set to 0.1–0.8 from this data.
- Single-date NDVI zones at peak canopy had breaks 0.919/0.926 (pure noise). This is why
  multi-date zones are the default, and why single-date zones warn on low spread.

Mapping and auto-update were verified end-to-end in a browser (desktop + mobile touch): a field
drawn by tapping 4 corners, a dragged corner, an inserted and undone midpoint, then saved.
The automatic backfill found 73 passes, 30 clear over the field, newest 2026-10-22. Edit
boundary produced `-v2`. Map and server acreage agree (16.5 ac).

Tests: `pytest` runs 77 unit/API tests (index formulas against hand calculations, a planted
1 ha hotspot recovered at the right GPS location, zone balance, change detection, alert
logic, store immutability, path-traversal guards, sync windows and per-field job dedupe,
boundary versioning, DJI-style KML/KMZ parsing, haze and transient suspects, paired-pixel
comparison, pending/confirmed/recovered, field vs. regional scope, stale-version reprocessing,
hotspot cause rules for water, strips, nitrogen, dry spots, persistent vs. new, edges, stand,
accounts/login/CSRF, private fields and view-only sharing, listings, messages, unread counts,
profile showcase, and the field score incl. season end). No network is needed.

## Limitations and next steps

- Sentinel-2 only. Landsat 8/9 (30 m, thermal) or HLS would add revisit frequency.
  Sentinel-1 radar would see through clouds.
- Sen2Cor cloud masking misses some thin cloud and haze. The haze and transient checks catch
  most of what it misses, but the latest date can't be judged transient until the next pass;
  that's why new declines start as *pending*. A learned cloud mask (s2cloudless/OmniCloudMask)
  would be the next upgrade.
- The neighbor ring sees whatever surrounds the field (roads, farmsteads, a different crop).
  It compares *change*, not level, which tolerates that, but a field surrounded by a crop on a
  different schedule can make maturity look field-specific.
- Hotspots and zones are relative to each field. They tell you *where* to look, not *why*.
  Ground-truth before applying inputs.
- Automatic updates run inside the server process. If the server is down, fields catch up on
  the next start (each sync overlaps the previous one by 14 days). For server-less setups,
  cron `fieldmonitor process all` instead.
- No push/email notifications yet. Alerts show in the app.
- Zipped shapefiles aren't imported yet. Convert to GeoJSON or KML first.
