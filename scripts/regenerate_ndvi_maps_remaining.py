#!/usr/bin/env python3
"""Generate all remaining NDVI maps (006-020)."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from pathlib import Path
import pandas as pd

def create_simple_ndvi_map(ndvi_path: Path, field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create a simple map without basemap for large-area files."""
    
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)
        transform = src.transform
        crs = src.crs
    
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    
    x_min = transform[0]
    x_max = transform[0] + transform[1] * ndvi.shape[1]
    y_max = transform[3]
    y_min = transform[3] + transform[5] * ndvi.shape[0]
    
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    
    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, 
                   extent=[x_min, x_max, y_min, y_max],
                   origin='upper', alpha=0.8)
    
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    if boundary_file.exists():
        field_gdf = gpd.read_file(boundary_file)
        field_gdf_proj = field_gdf.to_crs(crs)
        field_gdf_proj.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
        centroid = field_gdf_proj.iloc[0].geometry.centroid
    else:
        centroid = None
    
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)
    
    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    
    if centroid:
        ax.annotate(label, (centroid.x, centroid.y), ha='center', va='center',
                    fontsize=10, fontweight='bold', color='white',
                    bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    else:
        ax.annotate(label, (0.5, 0.95), ha='center', va='top', transform=ax.transAxes,
                    fontsize=10, fontweight='bold', color='white',
                    bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    
    cbar = plt.colorbar(im, ax=ax, label='NDVI', fraction=0.04, pad=0.02)
    ax.set_title(f'NDVI Analysis - {field_id}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")

def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    output_dir = Path("output/dashboard-assets")
    
    fields_df = pd.read_csv(data_dir / "farm_20fields.csv").set_index('field_id')
    ndvi_df = pd.read_csv(data_dir / "ndvi_july_stats.csv").set_index('field_id')
    
    # Process fields 006-020
    for i in range(6, 21):
        field_id = f"field_{i:03d}"
        print(f"\nProcessing {field_id}...")
        
        ndvi_path = ndvi_dir / field_id / "ndvi_july.tif"
        if not ndvi_path.exists():
            ndvi_path = ndvi_dir / field_id / "ndvi.tif"
        
        if not ndvi_path.exists():
            print(f"  No NDVI file found for {field_id}")
            continue
        
        output_path = output_dir / f"field_map_{field_id}.png"
        
        if field_id in fields_df.index:
            row = fields_df.loc[field_id]
            field_info = {
                'soil_series': str(row.get('soil_series', 'Unknown')),
                'surface_texture': str(row.get('surface_texture', 'Unknown')),
                'area_acres': float(row.get('area_acres', 0)),
            }
        else:
            field_info = {'soil_series': 'Unknown', 'surface_texture': 'Unknown', 'area_acres': 0}
        
        if field_id in ndvi_df.index:
            ndvi_row = ndvi_df.loc[field_id]
            stats = {
                'mean_ndvi': float(ndvi_row['mean_ndvi']),
                'min_ndvi': float(ndvi_row['min_ndvi']),
                'max_ndvi': float(ndvi_row['max_ndvi']),
            }
        else:
            stats = {'mean_ndvi': 0, 'min_ndvi': 0, 'max_ndvi': 0}
        
        create_simple_ndvi_map(ndvi_path, field_id, output_path, field_info, stats)
    
    print("\nDone with all fields!")

if __name__ == "__main__":
    main()
