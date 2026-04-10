#!/usr/bin/env python3
"""Generate satellite + NDVI overlay maps using EPSG:4326."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from pathlib import Path


def create_satellite_ndvi_map_simple(ndvi_path: Path, field_id: str, output_path: Path, field_info: dict, stats: dict):
    """Create a map with satellite basemap and NDVI overlay using lat/lon coordinates."""
    
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)
        
        # Get bounds in lat/lon
        bounds = src.bounds
        left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    # Set extent to field bounds
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    
    # Add satellite basemap using contextily with Web Mercator
    try:
        import contextily as ctx
        
        # Convert to Web Mercator for basemap
        field_gdf = gpd.read_file(f'data/fields/{field_id}_boundary.geojson')
        field_gdf_3857 = field_gdf.to_crs(epsg=3857)
        
        bounds_3857 = field_gdf_3857.total_bounds
        margin = 500  # meters
        
        ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
        ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
        
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16, attribution=False)
        
        # Overlay NDVI in lat/lon
        extent_lon = [left, right, bottom, top]
        im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=0.0, vmax=0.6, 
                      extent=extent_lon, origin='upper', alpha=0.85)
        
        # Plot field boundary in lat/lon
        field_gdf.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
        
    except Exception as e:
        print(f"Warning: {e}")
        # Fallback: just show NDVI without basemap
        im = ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=0.0, vmax=0.6, 
                      extent=[left, right, bottom, top], origin='upper')
        
        field_gdf = gpd.read_file(f'data/fields/{field_id}_boundary.geojson')
        field_gdf.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
    
    # Add label
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)
    
    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {stats['mean_ndvi']:.3f}"
    ax.annotate(label, (0.02, 0.98), ha='left', va='top', transform=ax.transAxes,
                fontsize=11, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label('NDVI', fontsize=12)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nEsri Satellite with NDVI Overlay', 
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def main():
    data_dir = Path("data")
    ndvi_dir = data_dir / "ndvi"
    
    fields = ["field_001", "field_002", "field_003", "field_004", "field_005"]
    
    field_info_map = {
        "field_001": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 322.5},
        "field_002": {"soil_series": "Colo", "surface_texture": "Silty clay loam", "area_acres": 146.7},
        "field_003": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 102.7},
        "field_004": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 290.6},
        "field_005": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 70.7},
    }
    
    stats_map = {
        "field_001": {"mean_ndvi": 0.121},
        "field_002": {"mean_ndvi": 0.108},
        "field_003": {"mean_ndvi": 0.106},
        "field_004": {"mean_ndvi": 0.160},
        "field_005": {"mean_ndvi": 0.135},
    }
    
    for field_id in fields:
        print(f"\nProcessing {field_id}...")
        
        ndvi_path = ndvi_dir / field_id / "ndvi.tif"
        if not ndvi_path.exists():
            ndvi_path = ndvi_dir / f"{field_id}_ndvi.tif"
        
        if not ndvi_path.exists():
            print(f"No NDVI file found for {field_id}")
            continue
        
        output_path = data_dir / f"{field_id}_ndvi_satellite.png"
        
        field_info = field_info_map.get(field_id, {})
        stats = stats_map.get(field_id, {})
        
        create_satellite_ndvi_map_simple(ndvi_path, field_id, output_path, field_info, stats)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
