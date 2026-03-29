#!/usr/bin/env python3
"""Generate NDVI field maps with 4 different fix options for artifacts."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import rasterio
import contextily as ctx
from rasterio.mask import mask as raster_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.features import geometry_mask
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')
import rasterio.transform
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.path import Path as MplPath


def create_composite_map_option(field_id: str, mean_ndvi: float, soil: str, texture: str, acres: float, option: str):
    """Create map with specified fix option."""
    
    ndvi_path = Path(f'data/ndvi/{field_id}/ndvi_july.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not ndvi_path.exists():
        print(f'  No NDVI file: {field_id}')
        return
    
    print(f'Processing {field_id} with Option {option}...')
    
    field_gdf = gpd.read_file(boundary_file)
    field_gdf_3857 = field_gdf.to_crs(epsg=3857)
    bounds_3857 = field_gdf_3857.total_bounds
    
    with rasterio.open(ndvi_path) as src:
        src_crs = src.crs
        field_gdf_proj = field_gdf.to_crs(src_crs)
        
        clipped, clip_transform = raster_mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        clipped_masked = np.ma.masked_invalid(clipped[0])
        
        valid = clipped_masked[~clipped_masked.mask]
        if len(valid) > 0:
            ndvi_min = float(np.min(valid))
            ndvi_max = float(np.max(valid))
            ndvi_mean = float(np.mean(valid))
        else:
            ndvi_min = ndvi_max = ndvi_mean = 0
        
        if option == 'A':
            target_width = int((bounds_3857[2] - bounds_3857[0]) / 10)
            target_height = int((bounds_3857[3] - bounds_3857[1]) / 10)
            target_transform = rasterio.transform.from_bounds(
                bounds_3857[0], bounds_3857[1], bounds_3857[2], bounds_3857[3],
                target_width, target_height
            )
            reprojected = np.full((target_height, target_width), np.nan, dtype=np.float32)
            reproject(
                source=clipped_masked.filled(np.nan),
                destination=reprojected,
                src_transform=clip_transform,
                src_crs=src_crs,
                dst_transform=target_transform,
                dst_crs='EPSG:3857',
                resampling=Resampling.nearest  # OPTION A: nearest neighbor
            )
            reprojected_masked = np.ma.masked_invalid(reprojected)
            ndvi_extent = [bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]]
            
        elif option == 'B':
            cx = (bounds_3857[0] + bounds_3857[2]) / 2
            cy = (bounds_3857[1] + bounds_3857[3]) / 2
            w = bounds_3857[2] - bounds_3857[0]
            h = bounds_3857[3] - bounds_3857[1]
            shrink_x = 0.95
            shrink_y = 0.98
            new_bounds = [cx - w/2*shrink_x, cy - h/2*shrink_y, cx + w/2*shrink_x, cy + h/2*shrink_y]
            target_width = int((new_bounds[2] - new_bounds[0]) / 10)
            target_height = int((new_bounds[3] - new_bounds[1]) / 10)
            target_transform = rasterio.transform.from_bounds(
                new_bounds[0], new_bounds[1], new_bounds[2], new_bounds[3],
                target_width, target_height
            )
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
            reprojected_masked = np.ma.masked_invalid(reprojected)
            bounds_3857 = new_bounds
            ndvi_extent = [new_bounds[0], new_bounds[2], new_bounds[1], new_bounds[3]]
            
        elif option == 'C':
            target_width = int((bounds_3857[2] - bounds_3857[0]) / 10)
            target_height = int((bounds_3857[3] - bounds_3857[1]) / 10)
            target_transform = rasterio.transform.from_bounds(
                bounds_3857[0], bounds_3857[1], bounds_3857[2], bounds_3857[3],
                target_width, target_height
            )
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
            reprojected_masked = np.ma.masked_invalid(reprojected)
            ndvi_extent = [bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]]
            
        elif option == 'D':
            target_width = int((bounds_3857[2] - bounds_3857[0]) / 10)
            target_height = int((bounds_3857[3] - bounds_3857[1]) / 10)
            target_transform = rasterio.transform.from_bounds(
                bounds_3857[0], bounds_3857[1], bounds_3857[2], bounds_3857[3],
                target_width, target_height
            )
            source_height, source_width = clipped.shape[1], clipped.shape[2]
            source_mask = geometry_mask(
                [field_gdf_proj.iloc[0].geometry],
                out_shape=(source_height, source_width),
                transform=clip_transform,
                all_touched=False
            )
            masked_source = clipped_masked.filled(np.nan).copy()
            masked_source[~source_mask] = np.nan
            reprojected = np.full((target_height, target_width), np.nan, dtype=np.float32)
            reproject(
                source=masked_source,
                destination=reprojected,
                src_transform=clip_transform,
                src_crs=src_crs,
                dst_transform=target_transform,
                dst_crs='EPSG:3857',
                resampling=Resampling.bilinear
            )
            reprojected_masked = np.ma.masked_invalid(reprojected)
            ndvi_extent = [bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]]
    
    fig, ax = plt.subplots(1, 1, figsize=(16, 14))
    
    margin = 500
    ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
    ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
    
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
    
    im = ax.imshow(reprojected_masked, cmap='RdYlGn', 
                   vmin=-0.1, vmax=0.8,
                   extent=ndvi_extent,
                   origin='upper',
                   alpha=1.0,
                   interpolation='nearest')
    
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=4, facecolor='none')
    field_gdf_3857.plot(ax=ax, edgecolor='white', linewidth=6, facecolor='none')
    
    label = f'''{field_id}
{soil} {texture}
{acres:.1f} acres

NDVI Stats:
  Mean: {ndvi_mean:.3f}
  Min:  {ndvi_min:.3f}
  Max:  {ndvi_max:.3f}

Option {option}:'''
    if option == 'A':
        label += '\nNearest-neighbor\nresampling'
    elif option == 'B':
        label += '\nShrink 5%/2%\n(E/W - N/S)'
    elif option == 'C':
        label += '\nNo mask (original)'
    elif option == 'D':
        label += '\nMask before\nreproject'
    
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=12, fontweight='bold',
               bbox=dict(facecolor='white', alpha=0.9, edgecolor='black', linewidth=2),
               linespacing=1.5)
    
    cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02, shrink=0.8)
    cbar.set_label('NDVI Value', fontsize=14, fontweight='bold')
    cbar.ax.tick_params(labelsize=11)
    
    ax.set_title(f'NDVI Analysis - {field_id} Option {option}\nNDVI (field) + Satellite (background)', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_axis_off()
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_ndvi_option{option}.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_ndvi_option{option}.png')


def main():
    field_id = 'field_001'
    soil = 'Ely'
    texture = 'Silt loam'
    acres = 322.5
    mean_ndvi = 0.658
    
    for option in ['A', 'B', 'C', 'D']:
        create_composite_map_option(field_id, mean_ndvi, soil, texture, acres, option)
    
    print('\nDone!')


if __name__ == "__main__":
    main()
