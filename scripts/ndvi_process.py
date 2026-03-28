#!/usr/bin/env python3
"""Process NDVI for fields using existing downloaded data."""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask

SENTINEL_CLOUD_CLASSES = {3, 8, 9, 10, 11}


def compute_ndvi(red_path: Path, nir_path: Path, scl_path: Path | None, output_path: Path) -> Path:
    with rasterio.open(red_path) as red_src:
        red = red_src.read(1).astype("float32")
        red_profile = red_src.profile.copy()

    with rasterio.open(nir_path) as nir_src:
        nir = nir_src.read(1).astype("float32")

    cloud_mask = np.zeros_like(red, dtype=bool)

    if scl_path is not None and scl_path.exists():
        with rasterio.open(scl_path) as scl_src:
            scl = scl_src.read(1)
        cloud_mask = (np.isin(scl, list(SENTINEL_CLOUD_CLASSES))).astype(bool)

    denominator = nir + red
    valid = np.isfinite(red) & np.isfinite(nir) & (denominator != 0) & ~cloud_mask
    ndvi = np.full(red.shape, np.nan, dtype="float32")
    ndvi[valid] = (nir[valid] - red[valid]) / denominator[valid]
    ndvi = np.clip(ndvi, -1.0, 1.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    red_profile.update(dtype='float32', count=1, nodata=np.nan, compress='lzw')
    with rasterio.open(output_path, "w", **red_profile) as dst:
        dst.write(ndvi, 1)
    return output_path


def extract_field_stats(ndvi_path: Path, field_geometry, field_id: str) -> dict:
    with rasterio.open(ndvi_path) as src:
        data = src.read(1)
        valid = data[~np.isnan(data)]

    if valid.size == 0:
        return {
            "field_id": field_id,
            "mean_ndvi": np.nan,
            "min_ndvi": np.nan,
            "max_ndvi": np.nan,
            "std_ndvi": np.nan,
            "pixel_count": 0,
        }

    return {
        "field_id": field_id,
        "mean_ndvi": float(np.mean(valid)),
        "min_ndvi": float(np.min(valid)),
        "max_ndvi": float(np.max(valid)),
        "std_ndvi": float(np.std(valid)),
        "pixel_count": int(valid.size),
    }


def create_ndvi_map(ndvi_path: Path, field_geometry, field_id: str, output_path: Path, field_info: dict, stats: dict):
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))

    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0)

    field_gdf = gpd.GeoSeries([field_geometry], crs="EPSG:4326")
    field_gdf.plot(ax=ax, edgecolor='black', linewidth=2, facecolor='none')

    centroid = field_gdf.iloc[0].centroid
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)

    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    ax.annotate(label, (0.5, 0.95), ha='center', va='top', transform=ax.transAxes,
                fontsize=10, fontweight='bold', color='black',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    plt.colorbar(im, ax=ax, label='NDVI', fraction=0.046, pad=0.04)
    ax.set_title(f'NDVI Analysis - {field_id}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Pixel Column')
    ax.set_ylabel('Pixel Row')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def process_field(field_id: str, field_gdf: gpd.GeoDataFrame, output_dir: Path):
    print(f"\n{'='*60}")
    print(f"Processing {field_id}...")
    print(f"{'='*60}")

    field_geom = field_gdf.iloc[0].geometry
    field_info = field_gdf.iloc[0]

    field_dir = output_dir / field_id
    
    if not field_dir.exists():
        print(f"No downloaded data for {field_id}, skipping...")
        return None

    red_path = field_dir / "B04.tif"
    nir_path = field_dir / "B08.tif"
    scl_path = field_dir / "SCL.tif"

    if not red_path.exists() or not nir_path.exists():
        print(f"Missing band files for {field_id}, skipping...")
        return None

    ndvi_path = field_dir / "ndvi.tif"
    if not ndvi_path.exists():
        print("Computing NDVI...")
        compute_ndvi(red_path, nir_path, scl_path, ndvi_path)

    print("Extracting field statistics...")
    stats = extract_field_stats(ndvi_path, field_geom, field_id)
    stats["scene_id"] = "existing_data"
    stats["cloud_cover"] = 0
    stats["date"] = "2025-03-17"

    print(f"Mean NDVI: {stats['mean_ndvi']:.4f}")
    print(f"Min NDVI: {stats['min_ndvi']:.4f}")
    print(f"Max NDVI: {stats['max_ndvi']:.4f}")
    print(f"Pixel count: {stats['pixel_count']}")

    map_path = output_dir.parent / f"{field_id}_ndvi_map.png"
    field_info_dict = {
        'soil_series': str(field_info.get('soil_series', 'Unknown')),
        'surface_texture': str(field_info.get('surface_texture', 'Unknown')),
        'area_acres': float(field_info.get('area_acres', 0)),
    }
    print("Generating NDVI map...")
    create_ndvi_map(ndvi_path, field_geom, field_id, map_path, field_info_dict, stats)

    return stats


def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    ndvi_dir.mkdir(exist_ok=True)

    fields_dir = data_dir / "fields"
    fields = ["field_001", "field_002", "field_003", "field_004", "field_005"]

    all_stats = []

    for field_id in fields:
        boundary_file = fields_dir / f"{field_id}_boundary.geojson"
        if not boundary_file.exists():
            print(f"Boundary file not found: {boundary_file}")
            continue

        gdf = gpd.read_file(boundary_file)
        gdf = gdf.to_crs("EPSG:4326")

        stats = process_field(field_id, gdf, ndvi_dir)
        if stats:
            all_stats.append(stats)

    if all_stats:
        stats_df = pd.DataFrame(all_stats)
        stats_csv = ndvi_dir.parent / "ndvi_stats.csv"
        stats_df.to_csv(stats_csv, index=False)
        print(f"\nSaved statistics to: {stats_csv}")
        print(stats_df.to_string())

    print("\n" + "="*60)
    print("NDVI processing complete!")
    print("="*60)


if __name__ == "__main__":
    main()
