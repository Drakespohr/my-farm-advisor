#!/usr/bin/env python3
"""Generate NDVI-only maps for fields 006-020 (without satellite background)."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from pathlib import Path
import pandas as pd

def create_ndvi_only_map(field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create a clean NDVI map without satellite basemap."""
    
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    boundary_file = data_dir / "fields" / f"{field_id}_boundary.geojson"
    
    # Find NDVI file
    ndvi_path = ndvi_dir / field_id / "ndvi.tif"
    if not ndvi_path.exists():
        ndvi_path = ndvi_dir / field_id / "ndvi_july.tif"
    if not ndvi_path.exists():
        print(f"  No NDVI file for {field_id}")
        return
    
    # Read field boundary
    if not boundary_file.exists():
        print(f"  No boundary file for {field_id}")
        return
    
    field_gdf = gpd.read_file(boundary_file)
    
    with rasterio.open(ndvi_path) as src:
        # Crop to field extent
        try:
            field_gdf_proj = field_gdf.to_crs(src.crs)
            out_image, out_transform = mask(src, field_gdf_proj.geometry, crop=True)
            ndvi = out_image[0]
        except Exception as e:
            print(f"  Could not mask: {e}")
            ndvi = src.read(1)
            out_transform = src.transform
        
        ndvi_masked = np.ma.masked_invalid(ndvi)
        
        if ndvi_masked.count() == 0:
            print(f"  No valid NDVI in field boundary, using full raster")
            ndvi = src.read(1)
            ndvi_masked = np.ma.masked_invalid(ndvi)
            out_transform = src.transform
        
        crs = src.crs
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    # Calculate extent
    height, width = ndvi_masked.shape
    x_min = out_transform[0]
    x_max = out_transform[0] + out_transform[1] * width
    y_max = out_transform[3]
    y_min = out_transform[3] + out_transform[5] * height
    
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    
    # Add light gray background for context
    ax.set_facecolor('#f0f0f0')
    
    # Overlay NDVI with colormap
    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, 
                   extent=[x_min, x_max, y_min, y_max],
                   origin='upper', alpha=0.8)
    
    # Add field boundary
    field_gdf_proj = field_gdf.to_crs(crs)
    field_gdf_proj.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
    
    try:
        centroid = field_gdf_proj.iloc[0].geometry.centroid
    except:
        centroid = None
    
    # Create label
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)
    mean_ndvi = stats.get('mean_ndvi', 0)
    
    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {mean_ndvi:.3f}"
    
    if centroid:
        ax.annotate(label, (centroid.x, centroid.y), ha='center', va='center',
                    fontsize=10, fontweight='bold', color='white',
                    bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    else:
        ax.annotate(label, (0.5, 0.95), ha='center', va='top', transform=ax.transAxes,
                    fontsize=10, fontweight='bold', color='white',
                    bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, label='NDVI', fraction=0.04, pad=0.02)
    cbar.set_label('NDVI', fontsize=12)
    
    ax.set_title(f'NDVI Analysis - {field_id}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def main():
    data_dir = Path("data")
    output_dir = data_dir
    
    # Load field data
    fields_df = pd.read_csv(data_dir / "farm_20fields.csv").set_index('field_id')
    ndvi_df = pd.read_csv(data_dir / "ndvi_july_stats.csv").set_index('field_id')
    
    # Process fields 006-020
    for i in range(6, 21):
        field_id = f"field_{i:03d}"
        print(f"\nProcessing {field_id}...")
        
        output_path = output_dir / f"{field_id}_ndvi_map.png"
        
        # Get field info
        if field_id in fields_df.index:
            row = fields_df.loc[field_id]
            field_info = {
                'soil_series': str(row.get('soil_series', 'Unknown')),
                'surface_texture': str(row.get('surface_texture', 'Unknown')),
                'area_acres': float(row.get('area_acres', 0)),
            }
        else:
            field_info = {'soil_series': 'Unknown', 'surface_texture': 'Unknown', 'area_acres': 0}
        
        # Get NDVI stats
        if field_id in ndvi_df.index:
            ndvi_row = ndvi_df.loc[field_id]
            stats = {
                'mean_ndvi': float(ndvi_row.get('mean_ndvi', 0)),
                'min_ndvi': float(ndvi_row.get('min_ndvi', 0)),
                'max_ndvi': float(ndvi_row.get('max_ndvi', 0)),
            }
        else:
            stats = {'mean_ndvi': 0, 'min_ndvi': 0, 'max_ndvi': 0}
        
        create_ndvi_only_map(field_id, output_path, field_info, stats)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
