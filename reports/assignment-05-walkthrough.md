# Assignment 05: NDVI Analysis of Agricultural Fields

## Overview
This assignment analyzes satellite imagery to measure vegetation health using NDVI (Normalized Difference Vegetation Index) for 5 agricultural fields in Nebraska.

## Data Source
- **Satellite**: Sentinel-2 (European Space Agency)
- **Date**: July 27, 2025 (peak growing season)
- **Bands Used**: NIR (Band 8) and Red (Band 4)

## What We Did
1. Downloaded satellite imagery from Planetary Computer STAC API
2. Computed NDVI: (NIR - Red) / (NIR + Red)
3. Created composite maps with satellite basemap overlay

## Cloud Processing Note
- **Scene Filtering**: Selected scenes with <30% cloud cover via STAC query. The July 2025 scenes used had ~2% cloud cover.
- **Pixel-Level Masking**: Deferred. Full SCL (Scene Classification Layer) cloud masking would require downloading an additional band. Scene-level filtering is sufficient for clean imagery.

## Results

### Field 001 (Ely Silt Loam, 322.5 acres)
**Mean NDVI: 0.658**

![NDVI Composite](../data/field_001_ndvi_composite.png)

![False Color NIR](../data/field_001_nir_falsecolor.png)

### Field 004 (Kennebec Silt Loam, 290.6 acres)
**Mean NDVI: 0.628**

![NDVI Composite](../data/field_004_ndvi_composite.png)

![False Color NIR](../data/field_004_nir_falsecolor.png)

## What the Images Show

**NDVI Composite**: Uses a color scale where green = healthy vegetation, yellow = moderate, red = stressed or bare soil. Higher NDVI values (0.6-0.7) indicate vigorous crop growth.

**False Color NIR**: Maps the near-infrared band to red. Healthy vegetation reflects strongly in NIR and appears bright red. This is the standard false color composite used by agronomists to assess crop health.
