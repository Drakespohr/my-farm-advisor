#!/usr/bin/env python3
"""Generate NIR band maps - grayscale and false color."""

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


def create_nir_map(field_id: str, soil: str, texture: str, acres: float):
    """Create NIR band maps - grayscale and false color."""
    
    nir_path = Path(f'data/ndvi/{field_id}/B08_july.tif')
    boundary_file = Path(f'data/fields/{field_id}_boundary.geojson')
    
    if not nir_path.exists():
        print(f'  No NIR file: {field_id}')
        return
    
    print(f'Processing {field_id}...')
    
    # Read boundary
    field_gdf = gpd.read_file(boundary_file)
    field_gdf_3857 = field_gdf.to_crs(epsg=3857)
    bounds_3857 = field_gdf_3857.total_bounds
    
    # Apply asymmetric shrink (5% E/W, 2% N/S)
    cx = (bounds_3857[0] + bounds_3857[2]) / 2
    cy = (bounds_3857[1] + bounds_3857[3]) / 2
    w = bounds_3857[2] - bounds_3857[0]
    h = bounds_3857[3] - bounds_3857[1]
    shrink_x = 0.95  # E/W - 5% shrink
    shrink_y = 0.98  # N/S - 2% shrink
    bounds_3857 = [cx - w/2*shrink_x, cy - h/2*shrink_y, cx + w/2*shrink_x, cy + h/2*shrink_y]
    
    # Get source CRS and project boundary
    with rasterio.open(nir_path) as src:
        src_crs = src.crs
        field_gdf_proj = field_gdf.to_crs(src_crs)
        
        # Clip NIR to boundary
        clipped, clip_transform = raster_mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        nir_raw = clipped[0].astype('float32')
        nir_raw[nir_raw == 0] = np.nan
        nir_masked = np.ma.masked_invalid(nir_raw)
        
        # Get stats from valid data
        valid = nir_masked[~nir_masked.mask]
        if len(valid) > 0:
            nir_min = float(np.min(valid))
            nir_max = float(np.max(valid))
            nir_mean = float(np.mean(valid))
        else:
            nir_min = nir_max = nir_mean = 0
        
        # Use shrunken field bounds
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
            source=nir_masked.filled(np.nan),
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
        
        # Normalize NIR for display (0-1)
        nir_norm = (reprojected_masked - nir_min) / (nir_max - nir_min)
        nir_norm = np.ma.filled(nir_norm, np.nan)
        
        # Exact extent matches field bounds
        nir_extent = [bounds_3857[0], bounds_3857[2], bounds_3857[1], bounds_3857[3]]
    
    # ============ GRAYSCALE NIR MAP ============
    fig, ax = plt.subplots(1, 1, figsize=(16, 14))
    
    margin = 500
    ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
    ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
    
    # Add satellite basemap
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
    
    # Overlay NIR grayscale
    im = ax.imshow(nir_norm, cmap='gray', 
                   vmin=0, vmax=1,
                   extent=nir_extent,
                   origin='upper',
                   alpha=0.7,
                   interpolation='nearest')
    
    # Add field boundary outlines
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=4, facecolor='none')
    field_gdf_3857.plot(ax=ax, edgecolor='white', linewidth=6, facecolor='none')
    
    # Add label
    label = f'''{field_id}
 {soil} {texture}
 {acres:.1f} acres

 NIR Stats:
   Mean: {nir_mean:.1f}
   Min:  {nir_min:.1f}
   Max:  {nir_max:.1f}'''
    
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=12, fontweight='bold',
               bbox=dict(facecolor='white', alpha=0.9, edgecolor='black', linewidth=2),
               linespacing=1.5)
    
    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02, shrink=0.8)
    cbar.set_label('NIR Reflectance (normalized)', fontsize=14, fontweight='bold')
    cbar.ax.tick_params(labelsize=11)
    
    ax.set_title(f'NIR Band - {field_id}\nGrayscale NIR (bright = healthy vegetation)', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_axis_off()
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_nir_grayscale.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_nir_grayscale.png')
    
    # ============ FALSE COLOR MAP ============
    # Read red band too for false color composite
    red_path = Path(f'data/ndvi/{field_id}/B04_july.tif')
    
    with rasterio.open(red_path) as src:
        field_gdf_proj = field_gdf.to_crs(src.crs)
        clipped_red, clip_transform_red = raster_mask(src, [field_gdf_proj.iloc[0].geometry], crop=True)
        red_raw = clipped_red[0].astype('float32')
        red_raw[red_raw == 0] = np.nan
        red_masked = np.ma.masked_invalid(red_raw)
        
        # Reproject red band
        red_reprojected = np.full((target_height, target_width), np.nan, dtype=np.float32)
        reproject(
            source=red_masked.filled(np.nan),
            destination=red_reprojected,
            src_transform=clip_transform_red,
            src_crs=src.crs,
            dst_transform=target_transform,
            dst_crs='EPSG:3857',
            resampling=Resampling.bilinear
        )
    
    # Apply mask to red band too
    red_reprojected[~field_mask] = np.nan
    
    # Create false color: NIR=red, Red=green, Green=blue
    # Normalize bands to 0-1 range
    nir_display = (reprojected_masked - nir_min) / (nir_max - nir_min)
    red_display = (np.ma.masked_invalid(red_reprojected) - nir_min) / (nir_max - nir_min)
    
    # Stack into RGB - NIR in red channel, NIR in green, blue channel = 0
    false_color = np.zeros((target_height, target_width, 3), dtype=np.float32)
    false_color[:, :, 0] = np.ma.filled(nir_display, 0)  # NIR -> Red
    false_color[:, :, 1] = np.ma.filled(red_display, 0)  # Red -> Green  
    false_color[:, :, 2] = 0.1  # Blue channel low
    
    # Apply mask to false color (set alpha-like effect by making outside black)
    false_color[~field_mask] = [0, 0, 0]
    
    false_color = np.clip(false_color, 0, 1)
    
    fig, ax = plt.subplots(1, 1, figsize=(16, 14))
    
    ax.set_xlim(bounds_3857[0] - margin, bounds_3857[2] + margin)
    ax.set_ylim(bounds_3857[1] - margin, bounds_3857[3] + margin)
    
    # Add satellite basemap
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, zoom=14)
    
    # Overlay false color
    ax.imshow(false_color, extent=nir_extent, origin='upper', alpha=0.7, interpolation='nearest')
    
    # Add field boundary outlines
    field_gdf_3857.plot(ax=ax, edgecolor='yellow', linewidth=4, facecolor='none')
    field_gdf_3857.plot(ax=ax, edgecolor='white', linewidth=6, facecolor='none')
    
    # Add label
    label = f'''{field_id}
 {soil} {texture}
 {acres:.1f} acres

 False Color Composite:
 NIR (Band 8) → Red
 Red (Band 4) → Green'''
    
    ax.annotate(label, (0.02, 0.98), ha='left', va='top',
               transform=ax.transAxes, fontsize=12, fontweight='bold',
               bbox=dict(facecolor='white', alpha=0.9, edgecolor='black', linewidth=2),
               linespacing=1.5)
    
    ax.set_title(f'False Color Composite - {field_id}\nVegetation appears red', 
                 fontsize=16, fontweight='bold', pad=20)
    ax.set_axis_off()
    
    plt.tight_layout()
    plt.savefig(f'data/{field_id}_nir_falsecolor.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f'  Saved: {field_id}_nir_falsecolor.png')


def main():
    fields = [
        ('field_001', 'Ely', 'Silt loam', 322.5),
        ('field_002', 'Colo', 'Silty clay loam', 146.7),
        ('field_003', 'Ely', 'Silt loam', 102.7),
        ('field_004', 'Kennebec', 'Silt loam', 290.6),
        ('field_005', 'Kennebec', 'Silt loam', 70.7),
    ]
    
    for field_id, soil, texture, acres in fields:
        create_nir_map(field_id, soil, texture, acres)
    
    print('\nDone!')


if __name__ == "__main__":
    main()
