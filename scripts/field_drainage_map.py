#!/usr/bin/env python3
"""
Field Drainage Spatial Map

Creates a spatial plot showing soil drainage classes across the 20-field cluster
with field boundaries, metric labels, and north orientation.
"""

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
from pathlib import Path
import numpy as np

# Data paths
DATA_DIR = Path("data")
OUTPUT_MAP = DATA_DIR / "field_drainage_map.png"

# Color scheme for drainage classes
DRAINAGE_COLORS = {
    'Well drained': '#27ae60',           # Green
    'Moderately well drained': '#f39c12', # Orange/Yellow
    'Poorly drained': '#e74c3c',          # Red
    'Somewhat poorly drained': '#9b59b6', # Purple
    'Unknown': '#95a5a6'                  # Gray
}

# Field data
df = pd.read_csv(DATA_DIR / "field_comparison_analysis.csv")


def load_osm_boundaries():
    """Load OSM field boundaries for the area."""
    osm_files = [
        'data/osm_fields_ashland.geojson',
        'data/osm_fields_crete.geojson',
        'data/osm_fields_nebraska.geojson',
    ]
    
    all_fields = []
    for f in osm_files:
        try:
            gdf = gpd.read_file(f)
            all_fields.append(gdf)
        except Exception:
            continue
    
    if not all_fields:
        return None
    
    combined = pd.concat(all_fields, ignore_index=True)
    
    # Filter to analysis area
    filtered = combined[
        (combined.geometry.centroid.y >= 40.85) & 
        (combined.geometry.centroid.y <= 41.15) &
        (combined.geometry.centroid.x >= -96.45) & 
        (combined.geometry.centroid.x <= -95.9)
    ]
    
    return filtered


def create_drainage_map():
    """Create the spatial drainage map."""
    print("=" * 60)
    print("FIELD DRAINAGE SPATIAL MAP")
    print("=" * 60)
    
    # Step 1: Load OSM boundaries
    print("\n[1/3] Loading OSM field boundaries...")
    osm_fields = load_osm_boundaries()
    print(f"  Loaded {len(osm_fields)} OSM field polygons")
    
    # Step 2: Create figure
    print("\n[2/3] Creating map visualization...")
    fig, ax = plt.subplots(figsize=(14, 11))
    
    # Plot OSM field boundaries as background
    if osm_fields is not None:
        osm_fields.plot(ax=ax, facecolor='#ecf0f1', edgecolor='#bdc3c7', 
                       linewidth=0.5, alpha=0.7)
    
    # Plot each field colored by drainage class
    for drainage, color in DRAINAGE_COLORS.items():
        subset = df[df['drainage_class'] == drainage]
        if len(subset) > 0:
            ax.scatter(subset['longitude'], subset['latitude'],
                      c=color, s=350, label=drainage, 
                      edgecolors='black', linewidths=1.5,
                      zorder=5)
    
    # Add labels with metrics
    print("\n[3/3] Adding field labels...")
    for _, row in df.iterrows():
        # Create label text
        label_text = f"{row['field_id']}\n"
        label_text += f"OM: {row['om_pct']:.1f}% | C: {row['carbon_stock']:.0f}"
        
        # Position label above the point
        ax.annotate(label_text,
                   xy=(row['longitude'], row['latitude']),
                   xytext=(0, 45),
                   textcoords='offset points',
                   fontsize=7,
                   ha='center',
                   va='bottom',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', 
                            edgecolor='gray', alpha=0.85),
                   zorder=10)
        
        # Add field ID directly below point
        ax.annotate(row['field_id'].replace('field_', 'F'),
                   xy=(row['longitude'], row['latitude']),
                   xytext=(0, -18),
                   textcoords='offset points',
                   fontsize=6,
                   ha='center',
                   va='top',
                   fontweight='bold',
                   color='white',
                   bbox=dict(boxstyle='circle,pad=0.2', facecolor='#2c3e50', 
                            edgecolor='none'),
                   zorder=6)
    
    # Add north arrow
    ax.annotate('', xy=(0.96, 0.92), xytext=(0.96, 0.82),
               xycoords='axes fraction',
               arrowprops=dict(arrowstyle='->', lw=2, color='#2c3e50'))
    ax.text(0.96, 0.90, 'N', transform=ax.transAxes,
           fontsize=14, fontweight='bold', ha='center', va='center',
           color='#2c3e50')
    
    # Set axis labels and title
    ax.set_xlabel('Longitude', fontsize=12)
    ax.set_ylabel('Latitude', fontsize=12)
    ax.set_title('Soil Drainage Classes - Field Cluster (n=20)\n'
                 'Labels: OM% | Carbon Stock', fontsize=14, fontweight='bold')
    
    # Add legend
    legend = ax.legend(loc='lower left', title='Drainage Class', 
                      fontsize=10, title_fontsize=11,
                      framealpha=0.95)
    
    # Set equal aspect and limits
    ax.set_aspect('equal')
    ax.set_xlim(-96.45, -95.85)
    ax.set_ylim(40.85, 41.15)
    
    # Add grid
    ax.grid(alpha=0.3, linestyle='--')
    
    # Tight layout
    plt.tight_layout()
    
    # Save
    plt.savefig(OUTPUT_MAP, dpi=150, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    print(f"\n  Saved map to: {OUTPUT_MAP}")
    
    plt.close()
    
    print("\n" + "=" * 60)
    print("MAP COMPLETE")
    print("=" * 60)
    
    return OUTPUT_MAP


if __name__ == "__main__":
    create_drainage_map()
