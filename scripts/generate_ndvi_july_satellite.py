#!/usr/bin/env python3
"""Generate satellite-backed NDVI maps for fields 001-010."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from pathlib import Path
import pandas as pd
import contextily as ctx


def create_satellite_ndvi_map(field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create a satellite-backed NDVI map."""
    
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    boundary_file = data_dir / "fields" / f"{field_id}_boundary.geojson"
    
    # Find NDVI file - use ndvi_july.tif first (July data with proper values)
    ndvi_path = ndvi_dir / field_id / "ndvi_july.tif"
    if not ndvi_path.exists():
        ndvi_path = ndvi_dir / field_id / "ndvi.tif"
    
    if not ndvi_path.exists():
        print(f"  No NDVI file for {field_id}")
        return False
    
    # Read field boundary
    if not boundary_file.exists():
        print(f"  No boundary file for {field_id}")
        return False
    
    field_gdf = gpd.read_file(boundary_file)
    
    # Convert field boundary to Web Mercator for basemap
    field_gdf_3857 = field_gdf.to_crs(epsg=3857)
    bounds_3857 = field_gdf_3857.total_bounds
    
    # Read NDVI data and crop to field boundary
    with rasterio.open(ndvi_path) as src:
        src_crs = src.crs
        field_gdf_proj = field_gdf.to_crs(src.crs)
        try:
            out_image, out_transform = mask(src, field_gdf_proj.geometry, crop=True)
            ndvi = out_image[0]
        except Exception as e:
            print(f"  Could not crop to boundary: {e}")
            ndvi = src.read(1)
            out_transform = src.transform
        
        ndvi_masked = np.ma.masked_invalid(ndvi)
        
        if ndvi_masked.count() == 0:
            print(f"  No valid NDVI in field boundary")
            return False
        
        # Get dimensions
        height, width = ndvi_masked.shape
    
    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    # Set extent using field boundary bounds in EPSG:3857 with margin
    margin = 300
    ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
    ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
    
    # Add satellite basemap
    try:
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16, attribution=False)
        print(f"  Added Esri satellite basemap")
    except Exception as e:
        print(f"  Basemap failed: {e}")
        ax.set_facecolor('#e0e0e0')
    
    # Plot NDVI using field boundary extent in 3857 - full opacity
    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, 
                   extent=[bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]], 
                   origin='upper', alpha=1.0)
    
    # Plot field boundary in EPSG:3857
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
    
    # Add label
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)
    mean_ndvi = stats.get('mean_ndvi', 0)
    
    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {mean_ndvi:.3f}"
    ax.annotate(label, (0.02, 0.98), ha='left', va='top', transform=ax.transAxes,
                fontsize=11, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label('NDVI', fontsize=12)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nEsri Satellite with NDVI Overlay', 
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_path}")
    return True


def main():
    data_dir = Path("data")
    
    # Load field data from CSV files
    fields_df = pd.read_csv(data_dir / "farm_20fields.csv").set_index('field_id')
    ndvi_df = pd.read_csv(data_dir / "ndvi_july_stats.csv").set_index('field_id')
    
    print("Generating satellite-backed NDVI maps for fields 001-010...\n")
    
    success_count = 0
    fail_count = 0
    
    # Process fields 001-020
    for i in range(1, 21):
        field_id = f"field_{i:03d}"
        print(f"\nProcessing {field_id}...")
        
        output_path = data_dir / f"{field_id}_ndvi_july.png"
        
        # Get field info from CSV
        if field_id in fields_df.index:
            row = fields_df.loc[field_id]
            field_info = {
                'soil_series': str(row.get('soil_series', 'Unknown')),
                'surface_texture': str(row.get('surface_texture', 'Unknown')),
                'area_acres': float(row.get('area_acres', 0)),
            }
        else:
            field_info = {'soil_series': 'Unknown', 'surface_texture': 'Unknown', 'area_acres': 0}
        
        # Get NDVI stats from CSV
        if field_id in ndvi_df.index:
            ndvi_row = ndvi_df.loc[field_id]
            stats = {
                'mean_ndvi': float(ndvi_row.get('mean_ndvi', 0)),
                'min_ndvi': float(ndvi_row.get('min_ndvi', 0)),
                'max_ndvi': float(ndvi_row.get('max_ndvi', 0)),
            }
        else:
            stats = {'mean_ndvi': 0, 'min_ndvi': 0, 'max_ndvi': 0}
        
        if create_satellite_ndvi_map(field_id, output_path, field_info, stats):
            success_count += 1
        else:
            fail_count += 1
    
    print(f"\n{'='*50}")
    print(f"Completed: {success_count} maps generated, {fail_count} failed")


if __name__ == "__main__":
    main()
