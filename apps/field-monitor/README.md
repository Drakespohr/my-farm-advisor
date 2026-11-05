# Overacre

_Watch every acre. Trade with your neighbors._

> Working name. The product name lives in `APP_NAME` in `fieldmonitor/__init__.py`
> (plus the default `<title>`/brand text in `static/index.html`); the code package,
> folder, and `fieldmonitor` command keep their technical names.

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

## Today: the daily brief

After login the app opens on **Today**, a morning brief across all of your fields:

- **Greeting and date**, then **Today's priorities**: up to three one-line actions, e.g. "Creek
  Bottom: Crop health fell 23% · Oct 25 → Oct 30. Waiting for the next clear image to confirm;
  scout if you're nearby."
- **Prices** (when any exist).
- **Your fields**: one card per field with the score ring, disease-risk chip, crop health (NDVI,
  0–1) with its change since the previous image, a "season so far" trend line, one plain
  headline, and how old the latest image is.
- **Near you this week**: the local roundup, field reports, and storm reports.

**Two-week rule:** the brief, alerts, the watch list, and News show only the last 14 days
(`RECENT_DAYS` in `fieldmonitor/text.py`). Older images and history stay on each field's chart,
date picker, and Data tab. A field with no clear image for two weeks says so plainly ("No clear
image in the last 14 days (clouds). Last one: Sep 23.") instead of presenting old numbers as
current.

## Farms and team roles

A **farm** holds fields, and people get **one role on the whole farm**: owner, operator,
applicator, scout, or viewer (account menu → **Farm & team**). Your first field creates
"<your name>'s farm". Owners can rename the farm, add people by their account email, change roles,
remove people, create more farms (e.g. "Rented ground"), and move fields between farms they own.
A farm always keeps at least one owner.

For now, owners manage and every other role can see all the farm's fields; what operators,
applicators, and scouts may change comes next. **Share** on a single field still works for outside
people (an agronomist, a landlord): it opens only that field, never the rest of the farm.

Existing data moves over automatically on startup: each owner's fields go onto their farm, and
existing one-field shares stay one-field shares (nobody gains access to other fields).

## Accounts, sharing, and the marketplace

> The marketplace is **paused** by default: the tab, listing endpoints, and listing feed items are
> hidden. Listings already saved are kept. Set `FIELD_MONITOR_MARKETPLACE=1` to turn it back on.

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

## Disease pressure, watch list, and feed

**Field header:** three "at a glance" tiles: **Field score** (ring, out of 100, with "Why this
score" bullets), **Disease risk** (Low/Moderate/High meter, x/100, highest disease), and **Crop
health** (the selected image's NDVI on a colored 0–1 bar with a plain label like "Dense canopy").
Field actions sit under **Manage**. Every number carries a unit or scale (/100, 0–1 scale, in,
miles, °F) and dates read like "Aug 13, 2025".

**Things to watch** sits under the field score: the top disease risks, recent weather, and any
satellite alerts, a few bullets each. A badge beside the score shows the overall disease level.

**Disease pressure (0–100 per disease, Low / Moderate / High)** combines:

| Signal | How |
|---|---|
| Weather | "Favorable days": days with enough wet hours (RH ≥ 90% or rain) inside the disease's temperature range. Forecast days count half. |
| Nearby reports | Community scouting reports of the same disease, weighted by severity, distance, and age (3 weeks). Southern rust looks out ~155 mi, since spores blow in from the south. |
| Crop stage | From the field's latest canopy NDVI: discounted before canopy closure and after harvest. |

Corn: southern rust, tar spot, gray leaf spot, northern corn leaf blight. Soybean: white mold,
frogeye leaf spot. The field's crop (set with **Edit details**) picks the list. These are
simplified rules from extension guidance, **not** the validated tools (Tarspotter,
Sporecaster): use them to decide where and when to scout. Pressure is recomputed every 6 h in
the background, and on demand when you open a field. Picking an older image shows the pressure
*as of that date* from archived weather.

On the 2025 test field (Chariton, IA station), weather alone gave: mid-June low; July tar spot
and northern corn leaf blight high after a humid stretch; August gray leaf spot rising to
moderate-high; southern rust 60 (moderate) by August. Southern rust needs incoming spores, so
nearby reports, which 2025 had plenty of, are what push it to High.

**Weather sources** (`FIELD_MONITOR_WEATHER`):
- `nws` (default, US): National Weather Service observations + hourly forecast, public domain.
  Observations are stored as they arrive, so 30 days of history build up locally.
- `open-meteo`: 30 days of history + 7-day forecast immediately; its free API is for
  **non-commercial** use only.
- Past dates use NOAA Global Hourly (ISD) from AWS Open Data (currently ends in 2025).
Small airport stations often don't report rain; the app says "not reported" rather than 0.

**Field reports:** anyone can report a disease, insect, weed, or hail from a field they can see,
or from their location. Locations are rounded to ~1 km. Reports feed nearby fields' pressure
immediately and (optionally) post to the feed.

**News** (the old Feed): a price board for corn, soybeans, and wheat at the top, then sections:
All · Local · National · Community · Marketplace · My fields. **All** and the price board are
identical for every member (network-wide roundup, the latest storm reports, national news,
community posts, listings). **Local** (near my fields) and **My fields** (my alerts) are the
personal views.

- **Local:** a weekly roundup ("This week near your fields: Southern rust: 3 reports (Lucas,
  Wayne)"), community reports labeled by county ("Southern rust spotted in Lucas County, IA"),
  and NOAA storm reports within ~100 mi of your fields ("Hail (1.75 in) reported near 3 S
  Chariton, Lucas County, IA"). County names come from US Census boundaries.
- **National:** headlines from RSS/Atom sources (title, short summary, link to the publisher).
- **Sources** are server-side, refreshed every 2 h, and never fatal when they fail. Defaults are in
  `fieldmonitor/news.py` (`DEFAULT_SOURCES`); override with `$FIELD_MONITOR_DATA/news_sources.json`.
  The defaults (SPC hail/wind CSVs, farmdoc daily, USDA releases) were written without network
  access to verify them, so check `GET /api/news/sources` (admin) after the first refresh.

**Prices:** each card shows $/bu, change in cents with ▲/▼, a short trend line, and the type,
source, and time, so a manual cash bid is never mistaken for a live quote.
- Manual/admin: "Update prices" on the News page, `fieldmonitor set-price corn 4.32 --source
  "Chariton Co-op"`, or `fieldmonitor import-prices bids.csv --source ...`.
- Live futures: set `BARCHART_API_KEY` (Barchart OnDemand, licensed exchange data; delayed CBOT
  quotes refresh every 15 min). Other licensed feeds (DTN, CME) can be added in `prices.py`.
- **Free USDA cash bids** (public domain): see below.

### Free USDA cash bids (admin setup, once)

The server fetches USDA AMS Market News grain bids every 3 hours and shows the median bid per
crop (e.g. "Cash bid (14 locations) · USDA AMS · Iowa Daily Grain Bids") to **every** member.
Members never need a key.

1. Create a free account at https://mymarketnews.ams.usda.gov (register, then **My Profile** →
   API key). The account and key are yours; keep the key private and never commit it.
2. Give it to the server as an environment variable, e.g. on a Mac:
   `echo 'export USDA_AMS_API_KEY="paste-key-here"' >> ~/.zshrc && source ~/.zshrc`
3. Test it: `uv run fieldmonitor usda-check` (lists matching "<State> … Grain Bids" reports for
   your fields' states, and the prices parsed from the first one).
4. Restart `uv run fieldmonitor serve`. Prices refresh on their own.

Report selection is automatic by the states your fields are in. If members farm in several
states, pin the report(s) the shared board should use, e.g.
`export FIELD_MONITOR_AMS_REPORTS="2850"` (ids are printed by `usda-check`). The MARS response
format couldn't be tested from the build sandbox; if `usda-check` parses no prices, its output
shows the field names needed to adjust `usda_ams.py`.

**Feed items:** posts, field reports, new marketplace listings, and alerts on your own fields
(confirmed declines, disease pressure rising to Moderate/High). Filters: All, Near me (within
~100 mi of your fields), Reports, Posts, Marketplace, My fields. Posts can be tagged to a
field's area (rounded), marked Helpful, commented on, and reported.

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

## Yield estimate

Each field shows a yield estimate (field page tile and panel, and the Today card). It always says
which method it used and how sure it is:

| Method | When | Accuracy |
|---|---|---|
| **Harvested** | You recorded the harvest yield | Measured |
| **Field count** | A yield-component count in the last 45 days | Corn about ±20 bu/ac (Purdue); soybeans and wheat shown ±15% (our assumption; guides only say "rough") |
| **Satellite, calibrated on your farm** | 3+ of your field-years with harvest + expected yield | ± leave-one-out error on your own fields |
| **Satellite (rough)** | Expected yield (APH) set, in-season images | Shown ±20%; ±30% before nearby crops peak |
| **Relative only** | No expected yield yet | "Canopy X% greener than cropland nearby" |

Field-count formulas (extension yield-component methods):

- Corn: ears/ac × kernels/ear ÷ kernels per bushel (90,000 default; 80,000 good grain fill; 100,000 stress)
- Soybeans: plants/ac × pods/plant × seeds/pod ÷ seeds per lb ÷ 60 (2.5 seeds/pod, 3,000 seeds/lb)
- Wheat: heads/ft² × kernels/head × 43,560 ÷ kernels per lb ÷ 60 (22 kernels/head, 15,000 kernels/lb).
  With 18,150 kernels/lb this equals UNL NebGuide G1429's `(heads/ft × kernels/head ÷ row in) × 0.48`.

Satellite estimate: `expected × (1 + k × (rel − 1))`, where `rel` is the field's NDVI ÷ the median NDVI of
cropland within 2 km, averaged over the images where nearby cropland was within 90% of its season peak
(QA "ok" images only). `k = 1` until calibrated; calibration fits `k` on your harvests (NDVI saturates,
so `k` is usually above 1). Published Sentinel-2 field-scale models explain only about 25–60% of yield
variation without local calibration, which is why recording harvests matters.

Records (expected, harvest, count) are append-only in `fields/<id>/yields.jsonl`; the latest entry for a
year wins. Only owners can add them; shared viewers see the estimate but not the records.

## Quick start

```bash
cd apps/field-monitor
uv venv && uv pip install -e '.[dev]'      # or: python -m venv .venv && pip install -e '.[dev]'

# Optional: import boundaries (GeoJSON, WGS84 lon/lat)
.venv/bin/fieldmonitor import ../../skills/my-farm-advisor/field-management/field-boundaries/examples/real_10_fields_iowa.geojson

.venv/bin/fieldmonitor serve            # http://127.0.0.1:8765
```

### Finding a field on the map

The search bar at the top of the map is always there. Type any of:

- a field name (`north 80`): your matching fields;
- coordinates: `40.9245, -98.348`, `40.9245 N, 98.348 W`, `40°55'28"N 98°20'53"W`, or GIS order
  `-98.348, 40.9245`. If you drop the minus sign on a US longitude, it also offers the west point;
- a street address (`1234 Road 12, Aurora NE`): US Census Bureau geocoder;
- a town, road, or landmark: OpenStreetMap Nominatim (US only; set
  `FIELD_MONITOR_GEOCODE_COUNTRIES` to change, empty for worldwide).

As you type, a dropdown shows the **5 most likely matches** in one list, like a maps search bar:
typed coordinates, your fields whose name matches (at most 2 when addresses are also shown), then
addresses and places near the map view. Use ↑/↓ and Enter (Enter alone picks the first suggestion);
the **Search** button runs the full search.

Address suggestions come from **Google Places** when you set your own key, otherwise from the free
[Photon](https://photon.komoot.io) service (OpenStreetMap data, weaker on rural addresses):

1. In Google Cloud Console, create a project with billing, enable **Places API (New)**, and create an
   API key. Restrict the key to Places API (New) (and to your server's IP if you can).
2. Set it in the environment where you run the app (never in code):
   `export GOOGLE_MAPS_API_KEY=...` then `fieldmonitor serve`.
3. Optional: `GOOGLE_PLACES_DAILY_LIMIT` (default 1000) caps Google calls per day; past it,
   suggestions fall back to Photon. Also set a quota/budget alert in Cloud Console.

Each search uses one Google session token: the suggestions while you type plus one Place Details
call when you pick an address. The key stays on the server; the browser never sees it.

Results list **your fields first**: the field the point falls in, then fields within 3 miles (by
edge distance), then name matches. Tap a field to open it; tap a place to drop a pin there.
Only your own and shared fields are searched. Coordinates and field names work offline; if the
address services are down, the search says so and still shows field matches.

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

Saving starts an automatic backfill of the last 12 months of imagery, **newest image first**,
so the latest picture shows within a minute or two and the rest of the season fills in behind it
(progress shows on the field). After that, the server checks every field for new passes every
12 hours. **Check for new imagery** forces a check now. Older seasons can be pulled from
**Fetch a specific date range**.

**Edit boundary** opens the same editor on an existing field. Saving creates a new version
(`<id>-v2`) with its own imagery and archives the old one. The old boundary keeps its
history, so past numbers are never silently re-measured on a different shape.

**Import** accepts `.kml` and `.kmz` (DJI Pilot 2 / DJI Terra mapping-area missions, Google
Earth, most farm software) and `.geojson`. Every polygon becomes a field. The first 5 new fields
start downloading right away; the rest are queued and run one after another immediately (shown as
"queued for imagery"), even when the 12-hour auto-refresh is turned off.

Fields imported with `fieldmonitor import` download when `fieldmonitor serve` starts (fields with no
imagery go first), or right away with `fieldmonitor import ... --sync`.

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
| GET | `/api/search?q=` | Map search: your fields (inside / within 3 mi / by name) plus places (coordinates, US Census addresses, Nominatim) |
| GET | `/api/geocode?q=` | Places only (older clients) |
| GET | `/api/search/suggest?q=&lat=&lon=&session=` | Up to 5 suggestions: coordinates, your fields, Google (or Photon) addresses |
| GET | `/api/search/place?id=&session=` | Location of a picked Google suggestion (ends the session) |
| GET/POST | `/api/farms` | Your farms (with your role and field ids) / create a farm |
| PATCH | `/api/farms/{id}` | Owner: rename |
| GET/POST | `/api/farms/{id}/members` | Team list / owner: add someone or change their role `{email, role}` |
| DELETE | `/api/farms/{id}/members/{user_id}` | Owner: remove someone; anyone: leave |
| POST | `/api/farms/{id}/fields` | Owner: move fields you own onto this farm |
| GET | `/api/fields/{id}/yield?year=` | Yield estimate with method, range, canopy signal, calibration |
| POST | `/api/fields/{id}/yield/records` | Owner: add `expected`, `harvest`, or `count` (with inputs) |
| POST | `/api/yield/count` | Calculate a field count without saving |
| GET | `/api/yield/methods` | Count defaults, accuracy bands, and sources |
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

Tests: `pytest` runs 96 unit/API tests (index formulas against hand calculations, a planted
1 ha hotspot recovered at the right GPS location, zone balance, change detection, alert
logic, store immutability, path-traversal guards, sync windows and per-field job dedupe,
boundary versioning, DJI-style KML/KMZ parsing, haze and transient suspects, paired-pixel
comparison, pending/confirmed/recovered, field vs. regional scope, stale-version reprocessing,
hotspot cause rules for water, strips, nitrogen, dry spots, persistent vs. new, edges, stand,
accounts/login/CSRF, private fields and view-only sharing, listings, messages, unread counts,
profile showcase, the field score incl. season end, disease pressure rules, weather parsers
(NWS, Open-Meteo, NOAA ISD), report privacy rounding, the feed, RSS/Atom and SPC parsing,
local vs. national news, county labels, the roundup, and the price board). No network is needed.

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
