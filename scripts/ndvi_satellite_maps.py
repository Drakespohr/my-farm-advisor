#!/usr/bin/env python3
"""Generate satellite + NDVI overlay maps from existing NDVI data."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from pathlib import Path
import contextily as ctx


def create_satellite_ndvi_map(ndvi_path: Path, field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create a map with satellite basemap and NDVI overlay."""
    
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)
        transform = src.transform
        crs = src.crs
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    # Calculate extent in the raster's CRS
    x_min = transform[0]
    x_max = transform[0] + transform[1] * ndvi.shape[1]
    y_max = transform[3]
    y_min = transform[3] + transform[5] * ndvi.shape[0]
    
    # Set extent first (critical for basemap)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    
    # Add satellite basemap
    try:
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16, attribution=False)
    except Exception as e:
        print(f"Warning: Could not add satellite basemap: {e}")
    
    # Overlay NDVI with transparency
    im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, 
                   extent=[x_min, x_max, y_min, y_max],
                   origin='upper', alpha=0.6)
    
    # Add field boundary from the original boundary file
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    if boundary_file.exists():
        field_gdf = gpd.read_file(boundary_file)
        field_gdf_proj = field_gdf.to_crs(crs)
        field_gdf_proj.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
        
        centroid = field_gdf_proj.iloc[0].geometry.centroid
    else:
        centroid = None
    
    # Create label
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
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, label='NDVI', fraction=0.04, pad=0.02)
    cbar.set_label('NDVI', fontsize=12)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 with Esri World Imagery', 
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    fields_dir = data_dir / "fields"
    
    fields = ["field_001", "field_002", "field_003", "field_004", "field_005"]
    
    # Field information
    field_info_map = {
        "field_001": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 322.5},
        "field_002": {"soil_series": "Colo", "surface_texture": "Silty clay loam", "area_acres": 146.7},
        "field_003": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 102.7},
        "field_004": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 290.6},
        "field_005": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 70.7},
    }
    
    # Stats from previous run
    stats_map = {
        "field_001": {"mean_ndvi": 0.121},
        "field_002": {"mean_ndvi": 0.108},
        "field_003": {"mean_ndvi": 0.106},
        "field_004": {"mean_ndvi": 0.160},
        "field_005": {"mean_ndvi": 0.135},
    }
    
    for field_id in fields:
        print(f"\nProcessing {field_id}...")
        
        # Try different paths for NDVI file
        ndvi_path = ndvi_dir / field_id / "ndvi.tif"
        if not ndvi_path.exists():
            ndvi_path = ndvi_dir / f"{field_id}_ndvi.tif"
        
        if not ndvi_path.exists():
            print(f"No NDVI file found for {field_id}")
            continue
        
        output_path = data_dir / f"{field_id}_ndvi_satellite_map.png"
        
        field_info = field_info_map.get(field_id, {})
        stats = stats_map.get(field_id, {})
        
        create_satellite_ndvi_map(ndvi_path, field_id, output_path, field_info, stats)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
