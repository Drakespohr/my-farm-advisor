#!/usr/bin/env python3
"""Generate NDVI field fill with satellite background - NDVI inside boundary, satellite outside."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
import contextily as ctx
from rasterio.mask import mask
from rasterio.warp import transform_bounds
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')


def create_composite_map(field_id: str, mean_ndvi: float, soil: str, texture: str, acres: float):
    """Create map with NDVI inside field, satellite outside."""
    
    ndvi_path = Path(f'data/ndvi/{field_id}/ndvi.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not ndvi_path.exists():
        print(f'  No NDVI file: {field_id}')
        return
    
    print(f'Processing {field_id}...')
    
    # Read data
    with rasterio.open(ndvi_path) as src:
        field_gdf = gpd.read_file(boundary_file)
        field_gdf_3857 = field_gdf.to_crs(epsg=3857)
        field_gdf_proj = field_gdf.to_crs(src.crs)
        
        # Clip NDVI to field boundary
        clipped, clip_transform = mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        clipped_masked = np.ma.masked_invalid(clipped[0])
        
        # Get NDVI extent in 3857
        ndvi_bounds_3857 = transform_bounds(src.crs, 'EPSG:3857',
                                            src.bounds.left, src.bounds.bottom,
                                            src.bounds.right, src.bounds.top)
        
        # Get stats
        valid = clipped_masked[~clipped_masked.mask]
        if len(valid) > 0:
            ndvi_min = float(np.min(valid))
            ndvi_max = float(np.max(valid))
            ndvi_mean = float(np.mean(valid))
        else:
            ndvi_min = ndvi_max = ndvi_mean = 0
    
    # Get field bounds in 3857
    field_bounds_3857 = field_gdf_3857.total_bounds
    
    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(16, 14))
    
    # Set extent to field bounds + margin (for satellite context)
    margin = 500  # Larger margin to show surrounding area
    ax.set_xlim(field_bounds_3857[0] - margin, field_bounds_3857[2] + margin)
    ax.set_ylim(field_bounds_3857[1] - margin, field_bounds_3857[3] + margin)
    
    # Add satellite basemap (this becomes the background - outside field)
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
    
    # Overlay NDVI ONLY inside the field boundary (higher opacity for vivid colors)
    # Use the NDVI bounds in 3857 for the overlay
    im = ax.imshow(clipped_masked, cmap='RdYlGn', 
                   vmin=-0.1, vmax=0.8,
                   extent=ndvi_bounds_3857,
                   origin='upper',
                   alpha=0.85,
                   interpolation='nearest')
    
    # Add field boundary outline (thick yellow)
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=4, facecolor='none')
    
    # Add white outer outline for contrast
    field_gdf_3857.plot(ax=ax, edgecolor='white', linewidth=6, facecolor='none')
    
    # Add comprehensive label
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
    
    # Add vivid colorbar
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
