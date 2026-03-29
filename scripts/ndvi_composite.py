#!/usr/bin/env python3
"""Generate NDVI field fill - REVERTED: full coverage inside boundary (may have small corner artifacts)."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
import contextily as ctx
from rasterio.mask import mask as raster_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')
import rasterio.transform


def create_composite_map(field_id: str, mean_ndvi: float, soil: str, texture: str, acres: float):
    """Create map with full NDVI coverage inside field."""
    
    ndvi_path = Path(f'data/ndvi/{field_id}/ndvi_july.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not ndvi_path.exists():
        print(f'  No NDVI file: {field_id}')
        return
    
    print(f'Processing {field_id}...')
    
    # Read boundary
    field_gdf = gpd.read_file(boundary_file)
    field_gdf_3857 = field_gdf.to_crs(epsg=3857)
    bounds_3857 = field_gdf_3857.total_bounds
    
    # Get source CRS and project boundary
    with rasterio.open(ndvi_path) as src:
        src_crs = src.crs
        field_gdf_proj = field_gdf.to_crs(src_crs)
        
        # Clip NDVI to boundary
        clipped, clip_transform = raster_mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        clipped_masked = np.ma.masked_invalid(clipped[0])
        
        # Get stats from valid data
        valid = clipped_masked[~clipped_masked.mask]
        if len(valid) > 0:
            ndvi_min = float(np.min(valid))
            ndvi_max = float(np.max(valid))
            ndvi_mean = float(np.mean(valid))
        else:
            ndvi_min = ndvi_max = ndvi_mean = 0
        
        # Use exact field bounds (no shrink)
        target_width = int((bounds_3857[2] - bounds_3857[0]) / 10)
        target_height = int((bounds_3857[3] - bounds_3857[1]) / 10)
        
        target_transform = rasterio.transform.from_bounds(
            bounds_3857[0], bounds_3857[1], bounds_3857[2], bounds_3857[3],
            target_width, target_height
        )
        
        # Create a pixel-level mask from the field boundary
        from rasterio.features import geometry_mask
        field_mask = geometry_mask(
            [field_gdf_proj.iloc[0].geometry],
            out_shape=(target_height, target_width),
            transform=target_transform,
            all_touched=False
        )
        
        # Reproject to exact field bounds
        reprojected = np.full((target_height, target_width), np.nan, dtype=np.float32)
        
        reproject(
            source=clipped_masked.filled(np.nan),
            destination=reprojected,
            src_transform=clip_transform,
            src_crs=src_crs,
            dst_transform=target_transform,
            dst_crs='EPSG:3857',
            resampling=Resampling.bilinear
        )
        
        # Apply field boundary mask to remove outside pixels
        reprojected[~field_mask] = np.nan
        reprojected_masked = np.ma.masked_invalid(reprojected)
        
        # Exact extent matches field bounds
        ndvi_extent = [bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]]
    
    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(16, 14))
    
    # Set extent with margin
    margin = 500
    ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
    ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
    
    # Add satellite basemap
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
    
    # Overlay NDVI
    im = ax.imshow(reprojected_masked, cmap='RdYlGn', 
                   vmin=-0.1, vmax=0.8,
                   extent=ndvi_extent,
                   origin='upper',
                   alpha=1.0,
                   interpolation='nearest')
    
    # Add field boundary outlines
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=4, facecolor='none')
    field_gdf_3857.plot(ax=ax, edgecolor='white', linewidth=6, facecolor='none')
    
    # Add label
    label = f'''{field_id}
{soil} {texture}
{acres:.1f} acres

NDVI Stats:
  Mean: {ndvi_mean:.3f}
  Min:  {ndvi_min:.3f}
  Max:  {ndvi_max:.3f}'''
    
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=12, fontweight='bold',
               bbox=dict(facecolor='white', alpha=0.9, edgecolor='black', linewidth=2),
               linespacing=1.5)
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02, shrink=0.8)
    cbar.set_label('NDVI Value', fontsize=14, fontweight='bold')
    cbar.ax.tick_params(labelsize=11)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nNDVI (field) + Satellite (background)', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_axis_off()
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_ndvi_composite.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_ndvi_composite.png')


def main():
    fields = [
        ('field_001', 0.121, 'Ely', 'Silt loam', 322.5),
        ('field_002', 0.108, 'Colo', 'Silty clay loam', 146.7),
        ('field_003', 0.106, 'Ely', 'Silt loam', 102.7),
        ('field_004', 0.160, 'Kennebec', 'Silt loam', 290.6),
        ('field_005', 0.135, 'Kennebec', 'Silt loam', 70.7),
    ]
    
    for field_id, mean_ndvi, soil, texture, acres in fields:
        create_composite_map(field_id, mean_ndvi, soil, texture, acres)
    
    print('\nDone!')


if __name__ == "__main__":
    main()
