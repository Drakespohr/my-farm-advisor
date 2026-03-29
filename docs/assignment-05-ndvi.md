# Assignment 05: NDVI Analysis

## Overview

This assignment involves retrieving Sentinel-2 satellite imagery and computing NDVI (Normalized Difference Vegetation Index) for the 5 agricultural fields from Assignment 04. NDVI is a measure of vegetation health based on how plants reflect light.

---

## Methodology

### Data Source

**Microsoft Planetary Computer STAC API**
- Collection: `sentinel-2-l2a` (Sentinel-2 Level-2A)
- Date range: 2025 growing season (March 1 - October 31)
- Cloud cover filter: < 20%
- Resolution: 10 meters

### NDVI Calculation

NDVI is calculated using the formula:

```
NDVI = (NIR - Red) / (NIR + Red)
```

Where:
- **NIR**: Near-infrared band (B08)
- **Red**: Red band (B04)

NDVI values range from -1 to +1:
- **< 0**: Water/non-vegetation
- **0 - 0.2**: Bare soil/rock
- **0.2 - 0.5**: Sparse vegetation
- **> 0.5**: Dense/healthy vegetation

---

## Results

### Field NDVI Statistics

| Field ID | Mean NDVI | Min NDVI | Max NDVI | Std Dev | Pixel Count | Scene Date |
|----------|-----------|----------|----------|---------|-------------|------------|
| field_001 | 0.121 | -0.283 | 0.644 | 0.049 | 22,968,784 | 2025-03-17 |
| field_002 | 0.108 | 0.084 | 0.192 | 0.011 | 5,939 | 2025-03-17 |
| field_003 | 0.106 | 0.091 | 0.152 | 0.006 | 4,164 | 2025-03-17 |
| field_004 | 0.160 | 0.100 | 0.533 | 0.046 | 11,763 | 2025-10-10 |
| field_005 | 0.135 | 0.088 | 0.549 | 0.051 | 2,875 | 2025-10-10 |

### Interpretation

| Field ID | Mean NDVI | Interpretation |
|----------|-----------|----------------|
| field_001 | 0.121 | Low - early season (March), bare soil/early growth |
| field_002 | 0.108 | Low - early season (March), bare soil/early growth |
| field_003 | 0.106 | Low - early season (March), bare soil/early growth |
| field_004 | 0.160 | Moderate - October (post-harvest or cover crop) |
| field_005 | 0.135 | Moderate - October (post-harvest or cover crop) |

### Scene Information

| Field ID | Scene ID | Cloud Cover | Date |
|----------|----------|------------|------|
| field_001 | S2B_MSIL2A_20250317T171939_R012_T15TTF | 0.001% | 2025-03-17 |
| field_002 | S2B_MSIL2A_20250317T171939_R012_T15TTF | 0.001% | 2025-03-17 |
| field_003 | S2B_MSIL2A_20250317T171939_R012_T15TTF | 0.001% | 2025-03-17 |
| field_004 | S2B_MSIL2A_20251010T171139_R112_T14TQL | 0.003% | 2025-10-10 |
| field_005 | S2B_MSIL2A_20251010T171139_R112_T14TQL | 0.003% | 2025-10-10 |

**Note:** Fields 001-003 were imaged on March 17 (early season, before crops emerge). Fields 004-005 were imaged on October 10 (late season, after harvest). This explains the low NDVI values.

---

## Output Files

### Location: `data/ndvi/`

| File | Description |
|------|-------------|
| `field_001/ndvi.tif` | NDVI raster for field_001 |
| `field_001/B04_clip.tif` | Clipped red band |
| `field_001/B08_clip.tif` | Clipped NIR band |
| `field_002/ndvi.tif` | NDVI raster for field_002 |
| `field_003/ndvi.tif` | NDVI raster for field_003 |
| `field_004/ndvi.tif` | NDVI raster for field_004 |
| `field_005/ndvi.tif` | NDVI raster for field_005 |

### Location: `data/`

| File | Description |
|------|-------------|
| `ndvi_stats.csv` | Statistics for all 5 fields |
| `field_001_ndvi_map.png` | NDVI visualization for field_001 |
| `field_002_ndvi_map.png` | NDVI visualization for field_002 |
| `field_003_ndvi_map.png` | NDVI visualization for field_003 |
| `field_004_ndvi_map.png` | NDVI visualization for field_004 |
| `field_005_ndvi_map.png` | NDVI visualization for field_005 |
| `field_001_ndvi_satellite.png` | NDVI overlay on satellite for field_001 |
| `field_002_ndvi_satellite.png` | NDVI overlay on satellite for field_002 |
| `field_003_ndvi_satellite.png` | NDVI overlay on satellite for field_003 |
| `field_004_ndvi_satellite.png` | NDVI overlay on satellite for field_004 |
| `field_005_ndvi_satellite.png` | NDVI overlay on satellite for field_005 |

---

## Visualization

### NDVI Color Scale

| Color | NDVI Range | Interpretation |
|-------|------------|----------------|
| Red | < 0 | Water/non-vegetation |
| Orange | 0 - 0.2 | Bare soil |
| Yellow | 0.2 - 0.4 | Sparse vegetation |
| Light Green | 0.4 - 0.6 | Moderate vegetation |
| Dark Green | > 0.6 | Dense/healthy vegetation |

---

## Scripts

### Fast Download Script: `scripts/ndvi_download_fast.py`

This optimized script:
1. Searches Planetary Computer STAC API for each field
2. Downloads and clips bands directly to field boundary (reduces file size)
3. Computes NDVI
4. Generates statistics and visualizations

```bash
python scripts/ndvi_download_fast.py
```

### Original Download Script: `scripts/ndvi_download.py`

Full scene download (larger files, longer download time).

### Processing Script: `scripts/ndvi_process.py`

Process existing downloaded data.

---

## Recreation Steps

### 1. Install Required Packages

```bash
pip install pystac-client planetary-computer rasterio geopandas numpy pandas matplotlib
```

### 2. Run the Fast Download Script

```bash
python scripts/ndvi_download_fast.py
```

This will:
- Query Planetary Computer for each field's bounding box
- Download clipped bands (much faster than full scene)
- Compute NDVI
- Generate statistics and maps

### 3. View Results

- Statistics: `data/ndvi_stats.csv`
- Maps: `data/field_XXX_ndvi_map.png`

---

## Future Work

- [ ] Acquire mid-season imagery for accurate crop health assessment
- [ ] Calculate zonal statistics by soil type
- [ ] Compare NDVI with yield data
- [ ] Add multi-temporal NDVI analysis (seasonal trends)
- [ ] Generate cloud-free composites

---

## References

- [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
- [Sentinel-2 Level-2A Product](https://sentinels.copernicus.eu/web/sentinel/level-2a)
- [NDVI Wikipedia](https://en.wikipedia.org/wiki/Normalized_difference_vegetation_index)

---

*Document Version: 1.1*  
*Created: 2026-03-28*  
*Branch: feature-assignment-05-ndvi*
