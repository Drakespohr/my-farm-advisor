# Field Monitor

Satellite field monitoring for farms. Draw or import field boundaries, pull every
Sentinel-2 pass over them, and get per-field vegetation, chlorophyll, moisture,
water, and residue maps, plus a season trend, scouting hotspots with GPS points,
management zones, and before/after change maps.

- **Imagery:** Copernicus Sentinel-2 L2A surface reflectance, 10 m, a new pass every ~2–5 days.
  It's free and needs no account. Scenes come from the
  [Earth Search STAC API](https://earth-search.aws.element84.com/v1) (Element 84). If that is
  unreachable, the app falls back to listing the public `sentinel-cogs` S3 bucket directly.
- **Runs anywhere:** a Python backend (FastAPI + rasterio) and a single-page UI with Leaflet
  vendored in. Only the basemap tiles come from the internet.
- **Stewardship:** stable field IDs, immutable boundaries, write-once observations, and an
  append-only `lineage.jsonl`. Every result records its scene, asset URLs, processing
  version, and git commit.

## Quick start

```bash
cd apps/field-monitor
uv venv && uv pip install -e '.[dev]'      # or: python -m venv .venv && pip install -e '.[dev]'

# Optional: import boundaries (GeoJSON, WGS84 lon/lat)
.venv/bin/fieldmonitor import ../../skills/my-farm-advisor/field-management/field-boundaries/examples/real_10_fields_iowa.geojson

.venv/bin/fieldmonitor serve            # http://127.0.0.1:8765
```

In the UI: pick a field (or **Draw boundary**), set a date range, and press **Fetch imagery**.
A full season (~45 scenes) takes about a minute. Only the field's own pixels are read from
the cloud-optimized GeoTIFFs, not whole 110 km tiles.

Data is written to `./data` by default. Set `FIELD_MONITOR_DATA=/path` to change it.
In Docker deployments, point it at persistent storage (for example `/data/field-monitor`).

### Command line (for scheduled jobs and the Data Pipeline agent)

```bash
fieldmonitor fields                                         # list fields
fieldmonitor import boundaries.geojson --id-property FIELD_ID --name-property NAME
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

### Analyses

- **Scout (hotspots):** contiguous patches more than max(1.5×std, 0.05) below the field median,
  at least 0.2 ha. Ranked by area × deficit, each with a GPS point guaranteed to lie inside the
  patch and a Google Maps directions link.
- **Zones:** equal-area quantile zones with a 3×3 majority filter, exportable as GeoJSON.
  The default *multi-date* mode averages standardized index maps across every clear date with
  established canopy (median NDVI ≥ 0.5). Single-date zones warn when the field is too uniform
  for zones to mean anything.
- **Change:** per-pixel difference between two dates on the same grid, with the area that
  declined or improved past a threshold and the decline patches.
- **Alerts:** field-wide median drops of ≥0.08 within 21 days, and rising within-field
  variability (CV) over a canopy. A drop after the seasonal peak that never recovers is
  reported as *info* (maturity/harvest), not an alert.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/indices` | Index catalog with formulas, ranges, color ramps, guidance |
| GET/POST | `/api/fields` | List fields / create from Polygon, Feature, or FeatureCollection |
| GET/PATCH | `/api/fields/{id}` | Read / edit name, crop, notes, archived (boundaries are immutable) |
| GET | `/api/fields/{id}/scenes?start&end&max_cloud` | Available scenes and whether each is processed |
| POST | `/api/fields/{id}/process` | Start a background job `{start, end, max_cloud}` → poll `/api/jobs/{job}` |
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
  lineage.jsonl                      append-only: every field/observation write, with app version + git commit
  fields/<field_id>/field.json       boundary (immutable) + editable name/crop/notes/archived
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
- NDRE at peak was ~0.78, higher than drone-derived NDRE guides suggest (B05 is at 705 nm),
  so the NDRE display range was set to 0.1–0.8 from this data.
- Single-date NDVI zones at peak canopy had breaks 0.919/0.926 (pure noise). This is why
  multi-date zones are the default, and why single-date zones warn on low spread.

Tests: `pytest` runs 38 unit/API tests (index formulas against hand calculations, a planted
1 ha hotspot recovered at the right GPS location, zone balance, change detection, alert
logic, store immutability, path-traversal guards). No network is needed.

## Limitations and next steps

- Sentinel-2 only. Landsat 8/9 (30 m, thermal) or HLS would add revisit frequency.
  Sentinel-1 radar would see through clouds.
- Sen2Cor cloud masking misses some thin cloud and haze. Check true color before acting on a
  surprising date. A better mask (e.g. s2cloudless/OmniCloudMask) would be the next upgrade.
- Hotspots and zones are relative to each field. They tell you *where* to look, not *why*.
  Ground-truth before applying inputs.
- Scheduled processing and notifications are left to cron/the Data Pipeline agent via
  `fieldmonitor process all`.
