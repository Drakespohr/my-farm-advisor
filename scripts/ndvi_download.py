#!/usr/bin/env python3
"""Download NDVI data for fields using Microsoft Planetary Computer STAC API."""

import time
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pystac_client
import planetary_computer
import rasterio
from rasterio.mask import mask

SENTINEL_COLLECTION = "sentinel-2-l2a"
SENTINEL_CLOUD_CLASSES = {3, 8, 9, 10, 11}


def growing_season_range(year: int) -> str:
    return f"{year}-03-01T00:00:00Z/{year}-10-31T23:59:59Z"


def get_bbox(geometry) -> tuple:
    bounds = geometry.bounds
    return (bounds[0], bounds[1], bounds[2], bounds[3])


def search_sentinel(bbox: tuple, datetime_range: str, cloud_lt: float = 20):
    catalog = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    search = catalog.search(
        collections=[SENTINEL_COLLECTION],
        bbox=bbox,
        datetime=datetime_range,
        query={"eo:cloud_cover": {"lt": cloud_lt}},
        limit=20,
    )
    items = list(search.items())
    items = sorted(items, key=lambda x: (x.properties.get("eo:cloud_cover", 100), x.datetime), reverse=False)
    return items


def download_band(asset, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    href = asset.href
    with rasterio.open(href) as src:
        data = src.read(1)
        profile = src.profile.copy()
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(data, 1)
    return output_path


def compute_ndvi(red_path: Path, nir_path: Path, scl_path: Path | None, output_path: Path) -> Path:
    from rasterio.warp import Resampling

    with rasterio.open(red_path) as red_src:
        red = red_src.read(1).astype("float32")
        red_profile = red_src.profile.copy()
        red_shape = (red_src.height, red_src.width)

    with rasterio.open(nir_path) as nir_src:
        nir = nir_src.read(1).astype("float32")

    cloud_mask = np.zeros(red_shape, dtype=bool)

    if scl_path is not None and scl_path.exists():
        try:
            with rasterio.open(scl_path) as scl_src:
                if scl_src.shape[0] != red_shape[0] or scl_src.shape[1] != red_shape[1]:
                    data = np.zeros(red_shape, dtype=np.uint8)
                    rasterio.warp.reproject(
                        source=rasterio.band(scl_src, 1),
                        destination=data,
                        src_transform=scl_src.transform,
                        src_crs=scl_src.crs,
                        dst_transform=red_profile['transform'],
                        dst_crs=red_profile['crs'],
                        resampling=Resampling.nearest
                    )
                    scl = data
                else:
                    scl = scl_src.read(1)
            cloud_mask = (np.isin(scl, list(SENTINEL_CLOUD_CLASSES))).astype(bool)
        except Exception as e:
            print(f"Warning: Could not process SCL band: {e}")

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
        field = gpd.GeoSeries([field_geometry], crs="EPSG:4326").to_crs(src.crs)
        try:
            out_image, _ = mask(src, [field.iloc[0].__geo_interface__], crop=True, nodata=np.nan)
            data = out_image[0]
            valid = data[~np.isnan(data)]
        except Exception:
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


def create_ndvi_map(ndvi_path: Path, field_geometry, field_id: str, output_path: Path, field_info: dict):
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)
        transform = src.transform
        crs = src.crs

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))

    x_min = transform[0]
    x_max = transform[0] + transform[1] * ndvi.shape[1]
    y_min = transform[3] + transform[4] * ndvi.shape[0]
    y_max = transform[3]

    extent = [x_min, x_max, y_min, y_max]

    if abs(x_max - x_min) < 1 or abs(y_max - y_min) < 1:
        extent = None

    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, extent=extent)

    field_gdf = gpd.GeoSeries([field_geometry], crs="EPSG:4326").to_crs(crs)
    field_gdf.plot(ax=ax, edgecolor='black', linewidth=2, facecolor='none')

    centroid = field_gdf.iloc[0].centroid
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)

    stats = extract_field_stats(ndvi_path, field_gdf.iloc[0], field_id)

    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    ax.annotate(label, (centroid.x, centroid.y), ha='center', va='center',
                fontsize=10, fontweight='bold', color='black',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    plt.colorbar(im, ax=ax, label='NDVI', fraction=0.046, pad=0.04)

    ax.set_title(f'NDVI Analysis - {field_id}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return output_path


def process_field(field_id: str, field_gdf: gpd.GeoDataFrame, output_dir: Path, year: int = 2025):
    print(f"\n{'='*60}")
    print(f"Processing {field_id}...")
    print(f"{'='*60}")

    field_geom = field_gdf.iloc[0].geometry
    field_info = field_gdf.iloc[0]
    bbox = get_bbox(field_geom)
    datetime_range = growing_season_range(year)

    print(f"Bounding box: {bbox}")
    print(f"Date range: {datetime_range}")

    items = search_sentinel(bbox, datetime_range, cloud_lt=20)

    if not items:
        print(f"No Sentinel-2 scenes found for {field_id}")
        return None

    best_item = items[0]
    signed_item = planetary_computer.sign(best_item)

    feature_id = signed_item.id
    cloud_cover = signed_item.properties.get("eo:cloud_cover", 0)
    date = signed_item.datetime.isoformat() if signed_item.datetime else "unknown"

    print(f"Selected scene: {feature_id}")
    print(f"Cloud cover: {cloud_cover}%")
    print(f"Date: {date}")

    assets = signed_item.assets

    red_asset = assets.get("B04")
    nir_asset = assets.get("B08")
    scl_asset = assets.get("SCL")

    if not red_asset or not nir_asset:
        print(f"Missing required bands for {field_id}")
        print(f"Available assets: {list(assets.keys())}")
        return None

    field_dir = output_dir / field_id
    red_path = field_dir / "B04.tif"
    nir_path = field_dir / "B08.tif"
    scl_path = field_dir / "SCL.tif" if scl_asset else None

    if not red_path.exists():
        print("Downloading red band (B04)...")
        download_band(red_asset, red_path)

    if not nir_path.exists():
        print("Downloading NIR band (B08)...")
        download_band(nir_asset, nir_path)

    if scl_asset and scl_path and not scl_path.exists():
        print("Downloading SCL band...")
        download_band(scl_asset, scl_path)

    ndvi_path = field_dir / "ndvi.tif"
    if not ndvi_path.exists():
        print("Computing NDVI...")
        compute_ndvi(red_path, nir_path, scl_path, ndvi_path)

    print("Extracting field statistics...")
    stats = extract_field_stats(ndvi_path, field_geom, field_id)
    stats["scene_id"] = feature_id
    stats["cloud_cover"] = cloud_cover
    stats["date"] = date

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
    create_ndvi_map(ndvi_path, field_geom, field_id, map_path, field_info_dict)

    return stats


def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    ndvi_dir.mkdir(exist_ok=True)

    fields_dir = data_dir / "fields"
    fields = [f"field_{i:03d}" for i in range(1, 21)]

    all_stats = []

    for field_id in fields:
        boundary_file = fields_dir / f"{field_id}_boundary.geojson"
        if not boundary_file.exists():
            print(f"Boundary file not found: {boundary_file}")
            continue

        gdf = gpd.read_file(boundary_file)
        gdf = gdf.to_crs("EPSG:4326")

        stats = process_field(field_id, gdf, ndvi_dir, year=2025)
        if stats:
            all_stats.append(stats)

    if all_stats:
        stats_df = pd.DataFrame(all_stats)
        stats_csv = ndvi_dir.parent / "ndvi_stats.csv"
        stats_df.to_csv(stats_csv, index=False)
        print(f"\nSaved statistics to: {stats_csv}")
        print(stats_df.to_string())

    print("\n" + "="*60)
    print("NDVI download and processing complete!")
    print("="*60)


if __name__ == "__main__":
    main()
