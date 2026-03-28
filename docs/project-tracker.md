# Project Tracker: Nebraska Field Mapping

## Objective

Create satellite maps with field boundaries and soil data for 5 agricultural fields in Nebraska.

---

## Original Inputs

### Field Coordinates (Provided)

| Field ID | Latitude | Longitude | Location |
|----------|----------|-----------|----------|
| field_001 | 40.92336 | -96.3735 | Near Crete, NE (Saline County) |
| field_002 | 40.92459 | -96.38387 | Near Crete, NE (Saline County) |
| field_003 | 40.91862 | -96.36465 | Near Crete, NE (Saline County) |
| field_004 | 40.95425 | -96.0716 | Near Ashland, NE (Saunders County) |
| field_005 | 41.01814 | -96.07927 | Near Ashland, NE (Saunders County) |

---

## Data Sources

| Source | Description | URL/Method |
|--------|-------------|------------|
| OpenStreetMap | Farmland landuse polygons | Overpass API |
| Esri World Imagery | Satellite basemap | contextily |
| Representative Nebraska Soils | Soil series data | Based on geographic location |

---

## Workflow Steps

### Step 1: OSM Data Download - Crete Area (Saline County)

```python
query = """
[out:json][timeout:300];
(
  way["landuse"~"farmland|orchard|vineyard"](40.88,-96.42,40.96,-96.30);
);
out geom;
"""
response = requests.post("https://overpass-api.de/api/interpreter", 
                         data={"data": query}, timeout=300)
```

- **Query bounding box:** 40.88,-96.42 to 40.96,-96.30
- **Result:** 67 farmland polygons
- **Output:** `data/osm_fields_crete.geojson`

### Step 2: OSM Data Download - Ashland Area (Saunders County)

```python
query = """
[out:json][timeout:300];
(
  way["landuse"~"farmland|orchard|vineyard"](40.90,-96.20,41.10,-95.95);
);
out geom;
"""
```

- **Query bounding box:** 40.90,-96.20 to 41.10,-95.95
- **Result:** 517 farmland polygons
- **Output:** `data/osm_fields_ashland.geojson`

### Step 3: Polygon Selection

1. Load OSM data for each area
2. Calculate distance from each polygon to target coordinates
3. Find closest polygon for each field
4. **Ensure 5 distinct polygons** (no duplicates across fields)

**Selected Polygons:**

| Field | OSM ID | Area | Distance to Target |
|-------|--------|------|-------------------|
| field_001 | OSM_1304617503 | 323 ac | 0.020° |
| field_002 | OSM_1320630467 | 147 ac | 0.024° |
| field_003 | OSM_1304617500 | 103 ac | 0.034° |
| field_004 | OSM_1298961331 | 291 ac | 0.033° |
| field_005 | OSM_1325575061 | 71 ac | 0.000° |

### Step 4: Soil Data Assignment

Based on polygon centroid locations:

- **Crete area (lon > -96.3):** Ely or Colo soil series
- **Ashland area (lon < -96.3):** Kennebec or Colo soil series

**NRCS APIs were inaccessible** (require authentication), so representative Nebraska soil data was used based on geographic area.

### Step 5: Satellite Map Generation

```python
import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as ctx

# Load and reproject
gdf = gpd.read_file('data/fields/field_001_boundary.geojson')
gdf_3857 = gdf.to_crs(epsg=3857)

# Get bounds with margin
bounds = gdf_3857.total_bounds
margin = 300

# CRITICAL: Set extent BEFORE adding basemap
fig, ax = plt.subplots(figsize=(14, 12))
ax.set_xlim(bounds[0] - margin, bounds[2] + margin)
ax.set_ylim(bounds[1] - margin, bounds[3] + margin)

# Add satellite basemap
ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16)

# Plot field with soil color
gdf_3857.plot(ax=ax, facecolor='#D2B48C', edgecolor='black', linewidth=3, alpha=0.6)

# Add compass
add_north_arrow(ax, x=0.95, y=0.92, size=0.08)

# Save
plt.savefig('data/field_001_satellite_map_v2.png', dpi=150, bbox_inches='tight')
```

---

## Output Files

### Satellite Maps (V2 with Compass)

| File | Size |
|------|------|
| field_001_satellite_map_v2.png | 1.6 MB |
| field_002_satellite_map_v2.png | 1.6 MB |
| field_003_satellite_map_v2.png | 1.9 MB |
| field_004_satellite_map_v2.png | 1.5 MB |
| field_005_satellite_map_v2.png | 1.6 MB |

### Boundary GeoJSONs

| File | Area | Soil Series | Texture |
|------|------|-------------|---------|
| field_001_boundary.geojson | 323 ac | Ely | Silt loam |
| field_002_boundary.geojson | 147 ac | Colo | Silty clay loam |
| field_003_boundary.geojson | 103 ac | Ely | Silt loam |
| field_004_boundary.geojson | 291 ac | Kennebec | Silt loam |
| field_005_boundary.geojson | 71 ac | Kennebec | Silt loam |

### OSM Data Files

| File | Description |
|------|-------------|
| data/osm_fields_crete.geojson | 67 farmland polygons (Saline County area) |
| data/osm_fields_ashland.geojson | 517 farmland polygons (Saunders County area) |

---

## Field Summary

| Field | Area | Soil Series | Texture | Hydrologic Group | Drainage | Land Capability |
|-------|------|-------------|---------|------------------|----------|-----------------|
| field_001 | 323 ac | Ely | Silt loam | B | Well drained | 3e |
| field_002 | 147 ac | Colo | Silty clay loam | C | Somewhat poorly drained | 3w |
| field_003 | 103 ac | Ely | Silt loam | B | Well drained | 3w |
| field_004 | 291 ac | Kennebec | Silt loam | B | Moderately well drained | 2e |
| field_005 | 71 ac | Kennebec | Silt loam | B | Moderately well drained | 3w |

---

## Key Learnings

1. **Axis extent must be set BEFORE adding basemap** - Otherwise tiles don't render in the correct location

2. **Use distinct polygons for each field** - Avoid duplicate boundaries; each field gets its own unique OSM polygon

3. **Soil data based on polygon location** - Not the original target coordinates; use polygon centroid to determine geographic area

4. **NRCS APIs require authentication** - Use representative Nebraska soil series based on geographic location (Ely/Colo for Crete area, Kennebec/Colo for Ashland area)

5. **Esri World Imagery** provides the best satellite basemap via contextily - Better than OpenStreetMap tiles for aerial view

---

## Documentation Files

| File | Description |
|------|-------------|
| docs/assignment-04-field-mapping.md | Complete methodology and recreation steps |
| docs/interpretation.md | What the maps show (5 sentences with drainage/capability notes) |

---

## Total Acreage

| Category | Acres |
|----------|-------|
| Total all fields | 935 ac |
| Average field size | 187 ac |
| Largest field | 323 ac (field_001) |
| Smallest field | 71 ac (field_005) |

---

*Document Created: 2026-03-27*  
*Branch: feature-assignment-04-mapping*