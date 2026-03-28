#!/usr/bin/env python3
"""Generate vivid pure NDVI maps (no satellite background)."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from pathlib import Path


def create_pure_ndvi_map(field_id: str, mean_ndvi: float, soil: str, texture: str, acres: float):
    """Create vivid pure NDVI map without satellite background."""
    
    ndvi_path = Path(f'data/ndvi/{field_id}/ndvi.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not ndvi_path.exists():
        print(f'  No NDVI file: {field_id}')
        return
    
    print(f'Processing {field_id}...')
    
    with rasterio.open(ndvi_path) as src:
        field_gdf = gpd.read_file(boundary_file)
        field_gdf_proj = field_gdf.to_crs(src.crs)
        
        clipped, transform = mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        clipped_masked = np.ma.masked_invalid(clipped[0])
        
        # Get actual stats from clipped data
        valid = clipped_masked[~clipped_masked.mask]
        if len(valid) > 0:
            ndvi_min = float(np.min(valid))
            ndvi_max = float(np.max(valid))
            ndvi_mean = float(np.mean(valid))
        else:
            ndvi_min = ndvi_max = ndvi_mean = 0
    
    # Calculate extent
    height, width = clipped_masked.shape
    x_min = transform[2]
    x_max = transform[2] + transform[0] * width
    y_min = transform[5] + transform[4] * height
    y_max = transform[5]
    extent = [x_min, x_max, y_min, y_max]
    
    field_bounds = field_gdf_proj.total_bounds
    
    # Create vivid pure NDVI map
    fig, ax = plt.subplots(1, 1, figsize=(14, 12))
    
    margin = 50
    ax.set_xlim(field_bounds[0] - margin, field_bounds[2] + margin)
    ax.set_ylim(field_bounds[1] - margin, field_bounds[3] + margin)
    
    # Use full NDVI scale with vivid colors
    im = ax.imshow(clipped_masked, cmap='RdYlGn', 
                   vmin=-0.1, vmax=0.8,
                   extent=extent,
                   origin='upper',
                   interpolation='nearest')  # Nearest for crisp pixels
    
    # Add boundary
    field_gdf_proj.plot(ax=ax, edgecolor='black', linewidth=3, facecolor='none')
    
    # Add thick white outer boundary for contrast
    field_gdf_proj.plot(ax=ax, edgecolor='white', linewidth=5, facecolor='none')
    
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
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02, shrink=0.8)
    cbar.set_label('NDVI Value', fontsize=14, fontweight='bold')
    cbar.ax.tick_params(labelsize=12)
    
    # Add category backgrounds to colorbar
    cbar.ax.set_yticklabels([])
    # Add colored regions
    cbar.ax.axhspan(-0.1, 0.0, color='darkred', alpha=0.3)
    cbar.ax.axhspan(0.0, 0.2, color='red', alpha=0.3)
    cbar.ax.axhspan(0.2, 0.4, color='orange', alpha=0.3)
    cbar.ax.axhspan(0.4, 0.6, color='yellow', alpha=0.3)
    cbar.ax.axhspan(0.6, 0.8, color='green', alpha=0.3)
    
    ax.set_title(f'NDVI Analysis - {field_id}\nPure NDVI Visualization', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Easting (m)', fontsize=12)
    ax.set_ylabel('Northing (m)', fontsize=12)
    
    # Add grid
    ax.grid(True, alpha=0.3, linestyle='--')
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_ndvi_vivid.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_ndvi_vivid.png')


def main():
    fields = [
        ('field_001', 0.121, 'Ely', 'Silt loam', 322.5),
        ('field_002', 0.108, 'Colo', 'Silty clay loam', 146.7),
        ('field_003', 0.106, 'Ely', 'Silt loam', 102.7),
        ('field_004', 0.160, 'Kennebec', 'Silt loam', 290.6),
        ('field_005', 0.135, 'Kennebec', 'Silt loam', 70.7),
    ]
    
    for field_id, mean_ndvi, soil, texture, acres in fields:
        create_pure_ndvi_map(field_id, mean_ndvi, soil, texture, acres)
    
    print('\nDone!')


if __name__ == "__main__":
    main()
