#!/usr/bin/env python3
"""Generate satellite + NDVI overlay maps with visible NDVI values."""

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


def create_visible_ndvi_map(field_id: str, mean_ndvi: float, soil: str, texture: str, acres: float):
    """Create satellite + NDVI map with visible overlay."""
    
    ndvi_path = Path(f'data/ndvi/{field_id}/ndvi.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not ndvi_path.exists():
        print(f'  No NDVI file')
        return
    
    print(f'Processing {field_id}...')
    
    with rasterio.open(ndvi_path) as src:
        field_gdf = gpd.read_file(boundary_file)
        field_gdf_3857 = field_gdf.to_crs(epsg=3857)
        field_gdf_proj = field_gdf.to_crs(src.crs)
        
        clipped, transform = mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        clipped_masked = np.ma.masked_invalid(clipped[0])
        
        # Get NDVI bounds in 3857
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
    
    field_bounds_3857 = field_gdf_3857.total_bounds
    
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    margin = 300
    ax.set_xlim(field_bounds_3857[0] - margin, field_bounds_3857[2] + margin)
    ax.set_ylim(field_bounds_3857[1] - margin, field_bounds_3857[3] + margin)
    
    # Add satellite basemap
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=13)
    
    # Overlay NDVI with HIGHER opacity (80%) and adjusted scale
    # Use vmin=0, vmax=0.5 to stretch the low values
    im = ax.imshow(clipped_masked, cmap='RdYlGn', 
                   vmin=0, vmax=0.6,
                   extent=ndvi_bounds_3857, 
                   origin='upper', 
                   alpha=0.85)  # Higher opacity
    
    # Add boundary
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=3, facecolor='none')
    
    # Add label with full stats
    label = f'''{field_id}
Soil: {soil} {texture}
Area: {acres:.1f} acres

NDVI Statistics:
  Mean: {ndvi_mean:.3f}
  Min:  {ndvi_min:.3f}
  Max:  {ndvi_max:.3f}'''
    
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=11, color='white', fontweight='bold',
               bbox=dict(facecolor='black', alpha=0.85),
               linespacing=1.4)
    
    # Add colorbar with adjusted range
    cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02, shrink=0.8)
    cbar.set_label('NDVI Value', fontsize=12, fontweight='bold')
    cbar.ax.tick_params(labelsize=10)
    
    # Add category markers on colorbar
    cbar.ax.axhline(y=0.0, color='red', linewidth=1, linestyle='--')
    cbar.ax.axhline(y=0.2, color='orange', linewidth=1, linestyle='--')
    cbar.ax.axhline(y=0.5, color='yellow', linewidth=1, linestyle='--')
    cbar.ax.axhline(y=0.6, color='green', linewidth=1, linestyle='--')
    
    ax.set_title(f'NDVI Analysis - {field_id}\nSentinel-2 Satellite with NDVI Overlay', 
                 fontsize=14, fontweight='bold')
    ax.set_axis_off()
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_ndvi_satellite.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_ndvi_satellite.png (mean={ndvi_mean:.3f}, range={ndvi_min:.3f}-{ndvi_max:.3f})')


def main():
    fields = [
        ('field_001', 0.121, 'Ely', 'Silt loam', 322.5),
        ('field_002', 0.108, 'Colo', 'Silty clay loam', 146.7),
        ('field_003', 0.106, 'Ely', 'Silt loam', 102.7),
        ('field_004', 0.160, 'Kennebec', 'Silt loam', 290.6),
        ('field_005', 0.135, 'Kennebec', 'Silt loam', 70.7),
    ]
    
    for field_id, mean_ndvi, soil, texture, acres in fields:
        create_visible_ndvi_map(field_id, mean_ndvi, soil, texture, acres)
    
    print('\nDone!')


if __name__ == "__main__":
    main()
