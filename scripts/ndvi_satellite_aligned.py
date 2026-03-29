#!/usr/bin/env python3
"""Generate properly aligned satellite + NDVI overlay maps."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
import contextily as ctx
from rasterio.warp import calculate_default_transform, reproject, Resampling
from pathlib import Path


def create_satellite_ndvi_map_aligned(ndvi_path: Path, field_id: str, output_path: Path, 
                                       field_info: dict, stats: dict):
    """Create properly aligned satellite + NDVI overlay map."""
    
    # Read the field boundary in its original CRS
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    field_gdf = gpd.read_file(boundary_file)
    field_crs = field_gdf.crs
    
    # Read NDVI raster
    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        ndvi_masked = np.ma.masked_invalid(ndvi)
        
        # Get source CRS and transform
        src_crs = src.crs
        src_transform = src.transform
        src_bounds = src.bounds
    
    # Reproject field boundary to match NDVI CRS
    if str(field_crs) != str(src_crs):
        field_gdf_proj = field_gdf.to_crs(src_crs)
    else:
        field_gdf_proj = field_gdf.copy()
    
    # Get field bounds in the NDVI CRS
    field_bounds = field_gdf_proj.total_bounds  # [minx, miny, maxx, maxy]
    
    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    
    # Add margin
    margin = 0.01  # degrees for geographic, or adjust for projected
    
    # Check if we're in projected or geographic CRS
    if src_crs.is_geographic:
        ax.set_xlim(field_bounds[0] - margin, field_bounds[2] + margin)
        ax.set_ylim(field_bounds[1] - margin, field_bounds[3] + margin)
        
        # Add satellite basemap
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=16, crs='EPSG:4326')
        
        # For NDVI overlay, get extent in lat/lon
        with rasterio.open(ndvi_path) as src:
            ndvi_bounds = src.bounds
            extent = [ndvi_bounds.left, ndvi_bounds.right, ndvi_bounds.bottom, ndvi_bounds.top]
        
    else:
        # Projected CRS - use meters
        field_gdf_3857 = field_gdf.to_crs(epsg=3857)
        bounds_3857 = field_gdf_3857.total_bounds
        
        ax.set_xlim(bounds_3857[0] - 300, bounds_3857[2] + 300)
        ax.set_ylim(bounds_3857[1] - 300, bounds_3857[3] + 300)
        
        # Add satellite basemap
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
        
        # Reproject NDVI to EPSG:3857
        with rasterio.open(ndvi_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, 'EPSG:3857', src.width, src.height, *src.bounds
            )
            
            ndvi_3857 = np.zeros((height, width), dtype=np.float32)
            reproject(
                source=ndvi_masked.filled(np.nan),
                destination=ndvi_3857,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs='EPSG:3857',
                resampling=Resampling.bilinear
            )
        
        ndvi_3857 = np.ma.masked_invalid(ndvi_3857)
        
        # Calculate extent in 3857
        extent = [
            transform[0], transform[0] + transform[1] * width,
            transform[3] + transform[4] * height, transform[3]
        ]
        
        # Overlay NDVI
        ax.imshow(ndvi_3857, cmap='RdYlGn', vmin=-0.2, vmax=1.0, alpha=0.5, extent=extent)
        
        # Overlay field boundary in 3857
        field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
        
        # Add label
        soil = field_info.get('soil_series', 'Unknown')
        texture = field_info.get('surface_texture', 'Unknown')
        acres = field_info.get('area_acres', 0)
        
        mean_ndvi = stats.get('mean_ndvi', 0)
        label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {mean_ndvi:.3f}"
        ax.annotate(label, (0.02, 0.98), ha='left', va='top',
                   transform=ax.transAxes, fontsize=10, color='white', fontweight='bold',
                   bbox=dict(facecolor='black', alpha=0.7))
        
        # Add colorbar
        sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=-0.2, vmax=1.0))
        sm.set_array([])
        plt.colorbar(sm, ax=ax, label='NDVI', fraction=0.04, pad=0.02)
        
        ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 with Satellite Basemap')
        ax.set_xlabel('Easting (m)')
        ax.set_ylabel('Northing (m)')
        
        plt.tight_layout()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"Saved: {output_path}")
        return
    
    # Geographic CRS path (fallback)
    ax.imshow(ndvi_masked, cmap='RdYlGn', vmin=-0.2, vmax=1.0, alpha=0.5, extent=extent)
    field_gdf.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
    
    soil = field_info.get('soil_series', 'Unknown')
    texture = field_info.get('surface_texture', 'Unknown')
    acres = field_info.get('area_acres', 0)
    
    mean_ndvi = stats.get('mean_ndvi', 0)
    label = f"{field_id}\n{soil} {texture}\n{acres:.1f} ac\nMean NDVI: {mean_ndvi:.3f}"
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=10, color='white', fontweight='bold',
               bbox=dict(facecolor='black', alpha=0.7))
    
    sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=-0.2, vmax=1.0))
    sm.set_array([])
    plt.colorbar(sm, ax=ax, label='NDVI', fraction=0.04, pad=0.02)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 with Satellite Basemap')
    
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"Saved: {output_path}")


def main():
    fields = ['field_001', 'field_002', 'field_003', 'field_004', 'field_005']
    
    field_info_map = {
        "field_001": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 322.5},
        "field_002": {"soil_series": "Colo", "surface_texture": "Silty clay loam", "area_acres": 146.7},
        "field_003": {"soil_series": "Ely", "surface_texture": "Silt loam", "area_acres": 102.7},
        "field_004": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 290.6},
        "field_005": {"soil_series": "Kennebec", "surface_texture": "Silt loam", "area_acres": 70.7},
    }
    
    stats_map = {}
    
    for field_id in fields:
        print(f"\nProcessing {field_id}...")
        
        # Find NDVI file - prefer July data
        ndvi_path = Path(f'data/ndvi/{field_id}/ndvi_july.tif')
        if not ndvi_path.exists():
            ndvi_path = Path(f'data/ndvi/{field_id}/ndvi.tif')
        if not ndvi_path.exists():
            ndvi_path = Path(f'data/ndvi/{field_id}_ndvi.tif')
        
        if not ndvi_path.exists():
            print(f'No NDVI file for {field_id}')
            continue
        
        # Compute stats from the actual NDVI file
        with rasterio.open(ndvi_path) as src:
            data = src.read(1)
            valid = ~np.isnan(data)
            stats = {
                "mean_ndvi": float(np.mean(data[valid])) if valid.any() else 0,
                "min_ndvi": float(np.min(data[valid])) if valid.any() else 0,
                "max_ndvi": float(np.max(data[valid])) if valid.any() else 0,
            }
        
        output_path = Path(f'data/{field_id}_ndvi_july.png')
        
        field_info = field_info_map.get(field_id, {})
        stats = stats_map.get(field_id, {})
        
        create_satellite_ndvi_map_aligned(ndvi_path, field_id, output_path, field_info, stats)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
