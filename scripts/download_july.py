#!/usr/bin/env python3
"""Fast download July 2025 NDVI for one field."""

import time
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pystac_client
import planetary_computer
import rasterio
from rasterio.mask import mask

SENTINEL_COLLECTION = "sentinel-2-l2a"


def download_one_field(field_id: str):
    """Download NDVI for one field."""
    
    # Get field boundary
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    gdf = gpd.read_file(boundary_file)
    gdf = gdf.to_crs("EPSG:4326")
    
    field_geom = gdf.iloc[0].geometry
    bbox = (field_geom.bounds[0], field_geom.bounds[1], field_geom.bounds[2], field_geom.bounds[3])
    
    print(f"\n{'='*60}")
    print(f"Searching for {field_id}...")
    print(f"Bounding box: {bbox}")
    print(f"{'='*60}")
    
    # Search for July 2025 scenes
    catalog = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    search = catalog.search(
        collections=[SENTINEL_COLLECTION],
        bbox=bbox,
        datetime='2025-07-01/2025-07-31',
        query={"eo:cloud_cover": {"lt": 30}},
        limit=20,
    )
    items = list(search.items())
    
    if not items:
        print(f"No July 2025 scenes found!")
        return None
    
    # Sort by cloud cover
    items = sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
    
    best_item = items[0]
    signed_item = planetary_computer.sign(best_item)
    
    print(f"Best scene: {signed_item.id}")
    print(f"Cloud cover: {signed_item.properties.get('eo:cloud_cover')}%")
    print(f"Date: {signed_item.datetime}")
    
    # Download bands
    assets = signed_item.assets
    red_asset = assets.get("B04")
    nir_asset = assets.get("B08")
    
    if not red_asset or not nir_asset:
        print("Missing bands!")
        return None
    
    field_dir = Path(f'data/ndvi/{field_id}')
    field_dir.mkdir(parents=True, exist_ok=True)
    
    # Download red band
    red_path = field_dir / "B04_july.tif"
    if not red_path.exists():
        print("Downloading red band...")
        with rasterio.open(red_asset.get_absolute_href()) as src:
            data = src.read(1)
            profile = src.profile.copy()
        with rasterio.open(red_path, "w", **profile) as dst:
            dst.write(data, 1)
    
    # Download NIR band
    nir_path = field_dir / "B08_july.tif"
    if not nir_path.exists():
        print("Downloading NIR band...")
        with rasterio.open(nir_asset.get_absolute_href()) as src:
            data = src.read(1)
            profile = src.profile.copy()
        with rasterio.open(nir_path, "w", **profile) as dst:
            dst.write(data, 1)
    
    # Compute NDVI
    ndvi_path = field_dir / "ndvi_july.tif"
    print("Computing NDVI...")
    with rasterio.open(red_path) as red_src:
        red = red_src.read(1).astype("float32")
        profile = red_src.profile.copy()
    
    with rasterio.open(nir_path) as nir_src:
        nir = nir_src.read(1).astype("float32")
    
    denominator = nir + red
    valid = np.isfinite(red) & np.isfinite(nir) & (denominator != 0)
    ndvi = np.full(red.shape, np.nan, dtype="float32")
    ndvi[valid] = (nir[valid] - red[valid]) / denominator[valid]
    ndvi = np.clip(ndvi, -1.0, 1.0)
    
    profile.update(dtype='float32', count=1, nodata=np.nan, compress='lzw')
    with rasterio.open(ndvi_path, "w", **profile) as dst:
        dst.write(ndvi, 1)
    
    # Get stats
    with rasterio.open(ndvi_path) as src:
        data = src.read(1)
        valid_pixels = data[~np.isnan(data)]
    
    stats = {
        "field_id": field_id,
        "mean_ndvi": float(np.mean(valid_pixels)),
        "min_ndvi": float(np.min(valid_pixels)),
        "max_ndvi": float(np.max(valid_pixels)),
        "pixel_count": len(valid_pixels),
        "scene_id": signed_item.id,
        "cloud_cover": signed_item.properties.get("eo:cloud_cover"),
        "date": signed_item.datetime.isoformat(),
    }
    
    print(f"Mean NDVI: {stats['mean_ndvi']:.4f}")
    print(f"Saved to: {ndvi_path}")
    
    return stats


if __name__ == "__main__":
    fields = [f"field_{i:03d}" for i in range(7, 21)]
    
    all_stats = []
    for field_id in fields:
        try:
            stats = download_one_field(field_id)
            if stats:
                all_stats.append(stats)
        except Exception as e:
            print(f"Error for {field_id}: {e}")
    
    if all_stats:
        df = pd.DataFrame(all_stats)
        df.to_csv("data/ndvi_stats_july.csv", index=False)
        print("\nSaved to data/ndvi_stats_july.csv")
        print(df)
