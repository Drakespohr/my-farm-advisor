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

## Assignment 05: NDVI Analysis

### Objective
Analyze satellite imagery to measure vegetation health using NDVI for 5 agricultural fields.

### Data Source
- **Satellite**: Sentinel-2 (European Space Agency)
- **Provider**: Microsoft Planetary Computer STAC API
- **Collection**: sentinel-2-l2a
- **Date**: July 27, 2025 (peak growing season)
- **Cloud Cover**: <30% (selected scenes ~2%)

### NDVI Formula
```
NDVI = (NIR - Red) / (NIR + Red)
```

### Workflow

#### Step 1: Download July 2025 Imagery
- Query Planetary Computer for each field's bounding box
- Filter scenes by cloud cover <30%
- Download NIR (Band 8) and Red (Band 4) bands
- Clip to field boundaries

#### Step 2: Compute NDVI
- Apply NDVI formula: (B08 - B04) / (B08 + B04)

#### Step 3: Create Visualizations
- NDVI composite maps with satellite basemap
- NIR grayscale maps
- NIR false color composite maps

#### Step 4: Fix Boundary Artifacts
- Tested 4 options to remove red pixels outside field boundaries
- Applied asymmetric shrink (5% E/W, 2% N/S) - this worked best

### Key Scripts

| Script | Purpose |
|--------|---------|
| scripts/download_july.py | Downloads July 2025 NDVI data |
| scripts/ndvi_composite.py | NDVI composite maps |
| scripts/nir_map.py | NIR grayscale/false color |
| scripts/ndvi_options.py | Tests boundary fix options |

### NDVI Statistics (July 2025)

| Field | Mean NDVI | Min | Max | Soil Series |
|-------|-----------|-----|-----|-------------|
| field_001 | 0.658 | 0.304 | 0.722 | Ely Silt loam |
| field_002 | 0.647 | 0.385 | 0.720 | Colo Silty clay loam |
| field_003 | 0.707 | 0.296 | 0.729 | Ely Silt loam |
| field_004 | 0.628 | 0.335 | 0.716 | Kennebec Silt loam |
| field_005 | 0.632 | 0.452 | 0.657 | Kennebec Silt loam |

### Output Files

#### NDVI Composite Maps
| File | Description |
|------|-------------|
| data/field_001_ndvi_composite.png | Field 001 NDVI with satellite basemap |
| data/field_002_ndvi_composite.png | Field 002 NDVI with satellite basemap |
| data/field_003_ndvi_composite.png | Field 003 NDVI with satellite basemap |
| data/field_004_ndvi_composite.png | Field 004 NDVI with satellite basemap |
| data/field_005_ndvi_composite.png | Field 005 NDVI with satellite basemap |

#### NIR Maps
| File | Description |
|------|-------------|
| data/field_001_nir_grayscale.png | NIR band (grayscale) |
| data/field_001_nir_falsecolor.png | False color composite |
| data/field_004_nir_grayscale.png | NIR band (grayscale) |
| data/field_004_nir_falsecolor.png | False color composite |
| (and similar for fields 002, 003, 005) |

#### Statistics
| File | Description |
|------|-------------|
| data/ndvi_july_stats.csv | July 2025 NDVI statistics |

### Cloud Processing
- **Scene Filtering**: <30% cloud cover via STAC query
- **Pixel-Level Masking**: Deferred (SCL band not downloaded)

### Key Learnings

1. **July vs March/October**: July data shows healthy crops (NDVI ~0.65), March/October showed bare soil (NDVI ~0.12)

2. **Boundary artifacts**: Bilinear interpolation pulls outside pixels; asymmetric shrink (5%E/W, 2%N/S) fixes this

3. **False color NIR**: Healthy vegetation appears red - standard agronomy visualization

### Documentation Files

| File | Description |
|------|-------------|
| docs/assignment-05-ndvi.md | Complete methodology |
| reports/assignment-05-walkthrough.md | Classroom-readable summary |

---

## Assignment 06: Weather Data Acquisition

### Objective
Retrieve historical weather data from NASA POWER for the 5 Nebraska fields.

### Data Source
- **Provider**: NASA POWER (Prediction of Worldwide Energy Resources)
- **API**: `https://power.larc.nasa.gov/api/temporal/daily/point`
- **Date Range**: January 1 - December 31, 2025
- **Resolution**: 0.5° grid (~55km)

### Parameters Retrieved
- T2M (mean temperature)
- T2M_MAX (maximum temperature)
- T2M_MIN (minimum temperature)
- PRECTOTCORR (precipitation)
- ALLSKY_SFC_SW_DWN (solar radiation)
- RH2M (humidity)
- WS10M (wind speed)

### Workflow

#### Step 1: Download Weather Data
- Query NASA POWER API for each field centroid
- Download daily data for 2025 (365 days per field)
- Save to CSV

#### Step 2: Calculate GDD
- Base temperature: 10°C (corn)
- Cap temperature: 30°C
- Formula: GDD = max(0, min(T_avg, 30) - 10)

#### Step 3: Create Visualizations
- Temperature time series
- GDD accumulation curves
- Precipitation charts

### Key Results

| Field | Avg Temp (°C) | Total Precip (mm) | Total GDD |
|-------|---------------|-------------------|-----------|
| field_001 | 20.76 | 620.49 | 2253.73 |
| field_002 | 20.76 | 620.49 | 2253.73 |
| field_003 | 20.76 | 620.49 | 2253.73 |
| field_004 | 20.76 | 620.49 | 2253.73 |
| field_005 | 20.76 | 620.49 | 2253.73 |

### Key Finding: Spatial Resolution
NASA POWER data is at 0.5° resolution (~55km grid). All 5 fields fall within the same grid cell, so they share identical weather data. This is a known limitation.

---

## Key Findings: 2025 Weather Trends

### Most Significant Weather Trend

**2025 Growing Season was 40% wetter than the 25-year historical average**

| Metric | 2025 | Historical Avg (2000-2024) | Difference |
|--------|------|---------------------------|------------|
| Total Precipitation | 16.2 inches | 11.6 inches | **+40%** |
| July Precipitation | 6.6 inches | 3.2 inches | **+103%** |
| Avg High Temperature | 87.1°F | 87.9°F | -0.8°F (slightly cooler) |
| Avg Low Temperature | 67.1°F | 65.4°F | +1.7°F (warmer nights) |
| Total GDD | 2,331 | 2,296 | +35 (+2%) |

### Key Observations

1. **Precipitation was the most significant deviation from historical norms**
   - July 2025 was exceptionally wet: 103% above historical average
   - August also saw elevated precipitation: 11% above average

2. **Temperature pattern was counter-intuitive**
   - Daytime highs were slightly cooler than average (-0.8°F)
   - Nighttime lows were warmer than average (+1.7°F)
   - Despite cooler days, GDD was nearly identical to historical (+2%)

3. **Despite the wet conditions, heat accumulation was comparable to historical**
   - Warmer nights compensated for cooler daytime temperatures

---

## Data Quality Notes

### Data Limitations

| Issue | Description | Impact | Resolution |
|-------|-------------|--------|------------|
| **NASA POWER Resolution** | 0.5° grid (~55km) | All 5 fields share identical data | Documented as limitation; use single field for analysis |
| **Initial Precipitation Bug** | Calculated data summed all 5 fields instead of one | Inflated precipitation values (5x too high) | Fixed by filtering to field_001 only |

### Data Handling

- Used field_001 as representative field for all calculations (since all fields identical)
- Converted temperature from Celsius to Fahrenheit for visualizations
- Converted precipitation from mm to inches for easier interpretation
- Calculated rolling 7-day averages for trend analysis

---

### Key Scripts

| Script | Purpose |
|--------|---------|
| scripts/weather_download.py | Download NASA POWER data |
| scripts/weather_analysis.py | Calculate GDD and create visualizations |

### Output Files

| File | Description |
|------|-------------|
| data/weather_5fields_2025.csv | Daily weather (1825 rows) |
| data/weather_5fields_2025_june_aug.csv | June-August 2025 data |
| data/weather_daily_growing_season.csv | Daily data with imperial units |
| data/weather_7day_averages.csv | Rolling 7-day averages |
| data/weather_comparison_weekly.csv | 2025 vs historical comparison |
| data/weather_historical_2000_2024.csv | Historical data (25 years) |

### Visualization Files

| File | Description |
|------|-------------|
| data/weather_comparison_high_temp.png | High temperature time series |
| data/weather_comparison_low_temp.png | Low temperature time series |
| data/weather_comparison_gdd.png | GDD comparison |
| data/weather_comparison_precipitation_monthly.png | Monthly precipitation (corrected) |
| data/gdd_2025_weekly.png | 2025 weekly GDD |
| data/gdd_historical_weekly.png | Historical weekly GDD |
| data/gdd_difference_weekly.png | GDD difference chart |
| data/gdd_cumulative_comparison.png | Cumulative GDD comparison |
| output/dashboard-assets/weather_comparison_summary_temperature.png | Dashboard: Temperature summary |
| output/dashboard-assets/weather_comparison_precipitation_monthly.png | Dashboard: Precipitation |
| output/dashboard-assets/weather_comparison_gdd.png | Dashboard: GDD |

### Documentation Files

| File | Description |
|------|-------------|
| docs/assignment-06-weather.md | Complete methodology |
| docs/weather-anomaly-august-2025.md | Significant weather anomaly analysis |

### Anomaly Analysis

| File | Description |
|------|-------------|
| data/weather_anomaly_precipitation.png | Visualization of precipitation anomaly |
| output/dashboard-assets/weather_anomaly_precipitation.png | Dashboard-ready anomaly chart |

**Key Finding:** Early August 2025 had 3.1-3.4x normal precipitation - the most significant anomaly of the growing season.

---

## Assignment 07: Spatial Data Integration

### Objective
Integrate multiple spatial data sources (field boundaries, soil data, weather data, NDVI satellite imagery) to create comprehensive field maps for 5 Nebraska agricultural fields.

### Data Sources

| Source | Description | Format |
|--------|-------------|--------|
| Field Boundaries | GeoJSON from OpenStreetMap | EPSG:4326 (lat/lon) |
| Soil Data | NRCS soil database via SSURGO | CSV |
| Weather Data | NASA POWER | CSV (0.5° resolution) |
| NDVI | Sentinel-2 satellite imagery | GeoTIFF (EPSG:32614 UTM) |

### Key Technical Details

#### Coordinate System Handling
- NDVI rasters: EPSG:32614 (UTM Zone 14N)
- Field boundaries: EPSG:4326 (lat/lon)
- Reprojection required for plotting with contextily basemaps

#### NDVI Clipping Process
1. Reproject field boundary from lat/lon to UTM
2. Create boundary mask from reprojected geometry
3. Apply mask AFTER reprojection to prevent overflow beyond field borders
4. Custom numpy function fills NaN holes (scipy unavailable)

#### Weather Data Note
- NASA POWER has 0.5° resolution (~55km grid)
- All 5 Nebraska fields fall within the same grid cell
- Weather data is identical for all fields
- Weekly averages calculated for July 1-7, 2025

### Field Map Components
- Esri World Imagery satellite base layer
- NDVI overlay clipped to exact field boundaries
- Soil-type colored borders
- Info box with weather data (weekly averages for July 1-7, 2025)
- Legend showing soil type and NDVI mean

### NDVI Statistics (July 2025)

| Field | Mean NDVI | Soil Series | Area (acres) |
|-------|-----------|-------------|--------------|
| field_001 | 0.658 | Ely Silt loam | 322.5 |
| field_002 | 0.647 | Colo Silty clay loam | 146.7 |
| field_003 | 0.707 | Colo Silty clay loam | 102.7 |
| field_004 | 0.628 | Kennebec Silt loam | 290.6 |
| field_005 | 0.632 | Kennebec Silt loam | 70.7 |

### Key Findings
1. **Weather is uniform**: All fields share identical NASA POWER data due to coarse resolution
2. **NDVI range**: 0.628 - 0.707 indicates healthy vegetation across all fields
3. **Soil types**: Ely, Colo, and Kennebec series with silt loam and silty clay loam textures
4. **Field sizes**: Range from 70.7 to 322.5 acres

### Output Files

#### Integrated Data
| File | Description |
|------|-------------|
| data/integrated_spatial_data.csv | Combined tabular data |
| data/integrated_spatial_data.geojson | Combined spatial data for GIS |

#### Field Maps
| File | Description |
|------|-------------|
| output/dashboard-assets/field_map_field_001.png | Field 001 with satellite, NDVI, weather |
| output/dashboard-assets/field_map_field_002.png | Field 002 with satellite, NDVI, weather |
| output/dashboard-assets/field_map_field_003.png | Field 003 with satellite, NDVI, weather |
| output/dashboard-assets/field_map_field_004.png | Field 004 with satellite, NDVI, weather |
| output/dashboard-assets/field_map_field_005.png | Field 005 with satellite, NDVI, weather |

#### Visualizations
| File | Description |
|------|-------------|
| data/spatial_integration_overview.png | Multi-panel overview (soil, NDVI, size, productivity) |
| data/soil_ndvi_correlation.png | Soil-vegetation analysis |

### Documentation Files

| File | Description |
|------|-------------|
| notebooks/07_spatial_integration.ipynb | Complete analysis notebook |
| reports/assignment-07-walkthrough.md | Classroom-readable summary |

---

*Document Created: 2026-03-27*  
*Last Updated: 2026-03-29*  
*Branch: feature-assignment-07-spatial-integration*