#!/usr/bin/env python3
"""Generate NDVI map PNGs for fields 012-020 from existing TIFF files with satellite basemap."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from pathlib import Path
import geopandas as gpd
from rasterio.warp import calculate_default_transform, reproject, Resampling
import time


def create_ndvi_map(ndvi_path: Path, field_geojson_path: Path, field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create NDVI map with satellite basemap."""
    import contextily as ctx
    
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        src_crs = src.crs
        src_transform = src.transform
        src_width = src.width
        src_height = src.height

    ndvi_masked = np.ma.masked_invalid(ndvi)
    
    field_gdf = gpd.read_file(field_geojson_path)
    field_gdf = field_gdf.to_crs("EPSG:4326")
    field_geometry = field_gdf.iloc[0].geometry
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    # Try to add satellite basemap with retry logic
    basemap_success = False
    for attempt in range(2):
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
            
            ax.imshow(ndvi_3857, cmap='RdYlGn', vmin=0.0, vmax=0.6, 
                      extent=[transform[0], transform[0] + transform[1] * width,
                              transform[3] + transform[4] * height, transform[3]],
                      origin='upper', alpha=0.85)
            
            basemap_success = True
            break
            
        except Exception as e:
            print(f"  Basemap attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                print("  Retrying in 3 seconds...")
                time.sleep(3)
            continue
    
    if not basemap_success:
        print("  Warning: Could not add satellite basemap, using NDVI only")
        ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=0.0, vmax=0.6)
    
    field_gdf_3857 = field_gdf.to_crs("EPSG:3857")
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')

    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)

    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    ax.annotate(label, (0.5, 0.95), ha='center', va='top', transform=ax.transAxes,
                fontsize=11, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))

    sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=0.0, vmax=0.6))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, label='NDVI', fraction=0.03, pad=0.02)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 Satellite Imagery with NDVI Overlay', 
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    fields_dir = data_dir / "fields"
    output_dir = data_dir
    
    fields_csv = pd.read_csv(data_dir / "farm_20fields.csv")
    fields_df = fields_csv.set_index("field_id")
    
    fields = [f"field_{i:03d}" for i in range(12, 21)]
    
    for i, field_id in enumerate(fields):
        print(f"\nProcessing {field_id}...")
        
        # Delay between fields to avoid rate limits
        if i > 0:
            print("  Waiting 2 seconds before next field...")
            time.sleep(2)
        
        ndvi_path = ndvi_dir / field_id / "ndvi.tif"
        if not ndvi_path.exists():
            print(f"  NDVI file not found: {ndvi_path}")
            continue
            
        boundary_path = fields_dir / f"{field_id}_boundary.geojson"
        if not boundary_path.exists():
            print(f"  Boundary file not found: {boundary_path}")
            continue
        
        output_path = output_dir / f"{field_id}_ndvi_july.png"
        
        row = fields_df.loc[field_id]
        field_info = {
            'soil_series': str(row['soil_series']),
            'surface_texture': str(row['surface_texture']),
            'area_acres': float(row['area_acres']),
        }
        
        with rasterio.open(ndvi_path) as src:
            data = src.read(1)
            valid = data[~np.isnan(data)]
        
        if valid.size == 0:
            print(f"  No valid NDVI pixels")
            continue
            
        stats = {
            'mean_ndvi': float(np.mean(valid)),
        }
        
        create_ndvi_map(ndvi_path, boundary_path, field_id, output_path, field_info, stats)
    
    print("\nDone!")


if __name__ == "__main__":
    main()