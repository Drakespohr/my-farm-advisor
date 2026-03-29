#!/usr/bin/env python3
"""Fast NDVI download - clips data to field boundary during download."""

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
from rasterio.warp import Resampling, reproject

SENTINEL_COLLECTION = "sentinel-2-l2a"
SENTINEL_CLOUD_CLASSES = {3, 8, 9, 10, 11}


def growing_season_range(year: int) -> str:
    return f"{year}-07-01T00:00:00Z/{year}-07-31T23:59:59Z"


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


def download_and_clip_band(asset, field_gdf, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    href = asset.get_absolute_href()
    
    with rasterio.open(href) as src:
        field_proj = field_gdf.to_crs(src.crs)
        try:
            out_image, out_transform = mask(src, [field_proj.iloc[0].geometry.__geo_interface__], crop=True, nodata=0)
        except Exception:
            return None
        
        profile = src.profile.copy()
        profile.update(
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
            compress='lzw'
        )
        
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(out_image[0], 1)
    
    return output_path


def compute_ndvi(red_path: Path, nir_path: Path, output_path: Path) -> Path:
    with rasterio.open(red_path) as red_src:
        red = red_src.read(1).astype("float32")
        red_profile = red_src.profile.copy()

    with rasterio.open(nir_path) as nir_src:
        nir = nir_src.read(1).astype("float32")

    denominator = nir + red
    valid = np.isfinite(red) & np.isfinite(nir) & (denominator != 0)
    ndvi = np.full(red.shape, np.nan, dtype="float32")
    ndvi[valid] = (nir[valid] - red[valid]) / denominator[valid]
    ndvi = np.clip(ndvi, -1.0, 1.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    red_profile.update(dtype='float32', count=1, nodata=np.nan, compress='lzw')
    with rasterio.open(output_path, "w", **red_profile) as dst:
        dst.write(ndvi, 1)
    return output_path


def create_ndvi_map(ndvi_path: Path, field_geometry, field_id: str, output_path: Path, field_info: dict, stats: dict):
    import contextily as ctx
    from rasterio.warp import calculate_default_transform, reproject

    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        src_crs = src.crs
        src_transform = src.transform
        src_width = src.width
        src_height = src.height

    ndvi_masked = np.ma.masked_invalid(ndvi)

    field_gdf_4326 = gpd.GeoSeries([field_geometry], crs="EPSG:4326")
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    try:
        transform, width, height = calculate_default_transform(
            src_crs, "EPSG:3857", src_width, src_height, *src.bounds
        )
        
        ndvi_3857 = np.zeros((height, width), dtype=np.float32)
        reproject(
            source=ndvi_masked.filled(np.nan),
            destination=ndvi_3857,
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=transform,
            dst_crs="EPSG:3857",
            resampling=Resampling.bilinear
        )
        
        ndvi_3857 = np.ma.masked_invalid(ndvi_3857)
        
        bounds_3857 = transform * (width, height)
        
        ax.set_xlim(bounds_3857[0], transform[0])
        ax.set_ylim(transform[3], bounds_3857[1])
        
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16)
        
        ax.imshow(ndvi_3857, cmap='RdYlGn', vmin=-0.2, vmax=1.0, 
                  extent=[transform[0], transform[0] + transform[1] * width,
                          transform[3] + transform[4] * height, transform[3]],
                  origin='upper', alpha=0.5)
        
    except Exception as e:
        print(f"Warning: Could not add satellite basemap: {e}")
        ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0)
    
    field_gdf_3857 = field_gdf_4326.to_crs("EPSG:3857")
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')

    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)

    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    ax.annotate(label, (0.5, 0.95), ha='center', va='top', transform=ax.transAxes,
                fontsize=11, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))

    sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=-0.2, vmax=1.0))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, label='NDVI', fraction=0.03, pad=0.02)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 Satellite Imagery with NDVI Overlay', 
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def process_field(field_id: str, field_gdf: gpd.GeoDataFrame, output_dir: Path, year: int = 2025):
    print(f"\n{'='*60}")
    print(f"Processing {field_id}...")
    print(f"{'='*60}")

    field_geom = field_gdf.iloc[0].geometry
    field_info = field_gdf.iloc[0]
    bbox = get_bbox(field_geom)
    datetime_range = growing_season_range(year)

    print(f"Bounding box: {bbox}")

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

    if not red_asset or not nir_asset:
        print(f"Missing required bands for {field_id}")
        return None

    field_dir = output_dir / field_id
    red_path = field_dir / "B04_clip.tif"
    nir_path = field_dir / "B08_clip.tif"

    if not red_path.exists():
        print("Downloading and clipping red band (B04)...")
        result = download_and_clip_band(red_asset, field_gdf, red_path)
        if result is None:
            print("Failed to clip red band")
            return None

    if not nir_path.exists():
        print("Downloading and clipping NIR band (B08)...")
        result = download_and_clip_band(nir_asset, field_gdf, nir_path)
        if result is None:
            print("Failed to clip NIR band")
            return None

    ndvi_path = field_dir / "ndvi.tif"
    if not ndvi_path.exists():
        print("Computing NDVI...")
        compute_ndvi(red_path, nir_path, ndvi_path)

    print("Extracting field statistics...")
    with rasterio.open(ndvi_path) as src:
        data = src.read(1)
        valid = data[~np.isnan(data)]

    if valid.size == 0:
        print("No valid NDVI pixels")
        return None

    stats = {
        "field_id": field_id,
        "mean_ndvi": float(np.mean(valid)),
        "min_ndvi": float(np.min(valid)),
        "max_ndvi": float(np.max(valid)),
        "std_ndvi": float(np.std(valid)),
        "pixel_count": int(valid.size),
        "scene_id": feature_id,
        "cloud_cover": cloud_cover,
        "date": date,
    }

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
