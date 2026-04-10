#!/usr/bin/env python3
"""
Field Comparison Analysis Script

Analyzes 20 fields (5 existing + 15 new from OSM) for:
- Organic Matter %
- K-Factor (erosion)
- Carbon Storage
- Soil Health Score (composite)

Creates bar chart comparison and saves to CSV.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pathlib import Path

# NRCS Soil Data Access API
SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

OUTPUT_DIR = Path("data")
OUTPUT_CSV = OUTPUT_DIR / "field_comparison_analysis.csv"
OUTPUT_CHART = OUTPUT_DIR / "field_comparison_bar_chart.png"

# Existing 5 fields
EXISTING_FIELDS = {
    "field_001": {"lat": 40.92336, "lon": -96.3735},
    "field_002": {"lat": 40.92459, "lon": -96.38387},
    "field_003": {"lat": 40.91862, "lon": -96.36465},
    "field_004": {"lat": 40.95425, "lon": -96.0716},
    "field_005": {"lat": 41.01814, "lon": -96.07927},
}


def get_osm_field_centroids(n: int = 15):
    """Get n additional field centroids from OSM data."""
    files = [
        'data/osm_fields_ashland.geojson',
        'data/osm_fields_crete.geojson',
        'data/osm_fields_nebraska.geojson',
    ]
    
    all_fields = []
    for f in files:
        try:
            gdf = gpd.read_file(f)
            gdf['centroid_lat'] = gdf.geometry.centroid.y
            gdf['centroid_lon'] = gdf.geometry.centroid.x
            all_fields.append(gdf)
        except Exception:
            continue
    
    if not all_fields:
        return {}
    
    combined = pd.concat(all_fields, ignore_index=True)
    
    # Filter to our area
    filtered = combined[
        (combined['centroid_lat'] >= 40.85) & 
        (combined['centroid_lat'] <= 41.1) &
        (combined['centroid_lon'] >= -96.5) & 
        (combined['centroid_lon'] <= -95.9)
    ]
    
    # Get first n fields
    new_fields = {}
    for i in range(min(n, len(filtered))):
        row = filtered.iloc[i]
        new_fields[f"field_{i+6:03d}"] = {
            "lat": row['centroid_lat'],
            "lon": row['centroid_lon']
        }
    
    return new_fields


def query_sda_for_soil_data(lat: float, lon: float) -> dict | None:
    """Query NRCS for comprehensive soil data."""
    wkt = f"POINT({lon} {lat})"
    
    sql = f"""
    SELECT DISTINCT
        c.compname,
        c.comppct_r,
        c.drainagecl,
        ch.hzdept_r,
        ch.om_r,
        ch.dbthirdbar_r,
        ch.ph1to1h2o_r,
        ch.kwfact
    FROM mapunit mu
    INNER JOIN component c ON mu.mukey = c.mukey
    LEFT JOIN chorizon ch ON c.cokey = ch.cokey
    WHERE mu.mukey IN (
        SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')
    )
    AND ch.hzdept_r IS NOT NULL
    ORDER BY c.comppct_r DESC, ch.hzdept_r ASC
    """
    
    try:
        response = requests.post(SDA_URL, data={"query": sql, "format": "JSON"}, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if "Table" not in result or not result["Table"]:
            return None
        
        # Process results - get topsoil (0-30cm) values weighted by component %
        total_weight = 0
        weighted_om = 0
        weighted_bd = 0
        weighted_ph = 0
        weighted_k = 0
        max_depth = 0
        
        for row in result["Table"]:
            try:
                comp_pct = float(row[1]) if row[1] else 0
                drainage = str(row[2]) if row[2] else ""
                depth = float(row[3]) if row[3] else 0
                om = float(row[4]) if row[4] else 0
                bd = float(row[5]) if row[5] else 0
                ph = float(row[6]) if row[6] else 0
                k = float(row[7]) if row[7] else 0
                
                if depth <= 30 and om > 0 and bd > 0:
                    weighted_om += om * comp_pct
                    weighted_bd += bd * comp_pct
                    weighted_ph += ph * comp_pct
                    weighted_k += k * comp_pct
                    total_weight += comp_pct
                
                if depth > max_depth:
                    max_depth = depth
                    
            except (ValueError, TypeError, IndexError):
                continue
        
        if total_weight > 0:
            # Get drainage class (most common from dominant components)
            drainage_classes = [str(row[2]) for row in result["Table"] if row[2]]
            most_common_drainage = max(set(drainage_classes), key=drainage_classes.count) if drainage_classes else "Unknown"
            
            return {
                'om_pct': weighted_om / total_weight,
                'bulk_density': weighted_bd / total_weight,
                'ph': weighted_ph / total_weight,
                'k_factor': weighted_k / total_weight,
                'topsoil_depth_cm': min(max_depth, 30),
                'drainage_class': most_common_drainage,
            }
        
        return None
        
    except Exception as e:
        print(f"    NRCS query failed: {e}")
        return None


def calculate_carbon_stock(om_pct: float, bulk_density: float, depth_cm: float) -> float:
    """Calculate carbon stock: OM% × BD × depth × 1.724"""
    if om_pct is None or bulk_density is None or depth_cm is None:
        return None
    return om_pct * bulk_density * depth_cm * 1.724


def calculate_soil_health_score(om_pct: float, ph: float, drainage: str, carbon_stock: float) -> float:
    """
    Calculate composite soil health score:
    - Carbon normalized (0-1): 40% weight
    - pH optimal (6.0-7.0): 30% weight  
    - Drainage good: 30% weight
    """
    # Carbon component (normalize to 0-1, max expected ~200)
    carbon_norm = min(1.0, (carbon_stock / 200) if carbon_stock else 0)
    
    # pH component (optimal 6.0-7.0)
    if ph and 6.0 <= ph <= 7.0:
        ph_score = 1.0
    elif ph and 5.5 <= ph < 6.0 or 7.0 < ph <= 7.5:
        ph_score = 0.5
    else:
        ph_score = 0.2
    
    # Drainage component
    drainage_lower = drainage.lower() if drainage else ""
    if "well" in drainage_lower or "moderately well" in drainage_lower:
        drain_score = 1.0
    elif "somewhat" in drainage_lower:
        drain_score = 0.5
    elif "poorly" in drainage_lower:
        drain_score = 0.2
    else:
        drain_score = 0.5
    
    # Weighted composite
    health_score = (carbon_norm * 0.4) + (ph_score * 0.3) + (drain_score * 0.3)
    return health_score


def categorize_soil_health(score: float | None) -> str:
    """Categorize soil health score."""
    if score is None:
        return "Unknown"
    elif score > 0.7:
        return "High"
    elif score >= 0.4:
        return "Moderate"
    else:
        return "Low"


def main():
    print("=" * 60)
    print("FIELD COMPARISON ANALYSIS")
    print("=" * 60)
    
    # Step 1: Get all 20 field centroids
    print("\n[1/4] Loading field locations...")
    
    # Get existing 5 fields
    all_fields = dict(EXISTING_FIELDS)
    
    # Get 15 new fields from OSM
    new_fields = get_osm_field_centroids(15)
    all_fields.update(new_fields)
    
    print(f"  Total fields: {len(all_fields)}")
    print(f"  - Existing: {len(EXISTING_FIELDS)}")
    print(f"  - New from OSM: {len(new_fields)}")
    
    # Step 2: Query NRCS for each field
    print("\n[2/4] Querying NRCS for soil data...")
    
    results = []
    for field_id, centroid in all_fields.items():
        lat = centroid['lat']
        lon = centroid['lon']
        
        print(f"\n  Processing {field_id}...")
        
        # Query NRCS
        soil_data = query_sda_for_soil_data(lat, lon)
        
        if soil_data:
            om_pct = soil_data['om_pct']
            bulk_density = soil_data['bulk_density']
            ph = soil_data['ph']
            k_factor = soil_data['k_factor']
            depth_cm = soil_data['topsoil_depth_cm']
            drainage = soil_data['drainage_class']
            
            # Calculate carbon stock
            carbon_stock = calculate_carbon_stock(om_pct, bulk_density, depth_cm)
            
            # Calculate soil health score
            soil_health = calculate_soil_health_score(om_pct, ph, drainage, carbon_stock)
            health_category = categorize_soil_health(soil_health)
            
            print(f"    OM: {om_pct:.2f}%, K: {k_factor:.3f}, Carbon: {carbon_stock:.1f}, Health: {soil_health:.2f}")
            
            results.append({
                'field_id': field_id,
                'latitude': round(lat, 5),
                'longitude': round(lon, 5),
                'om_pct': round(om_pct, 2),
                'k_factor': round(k_factor, 3),
                'bulk_density': round(bulk_density, 2),
                'topsoil_depth_cm': round(depth_cm, 0),
                'carbon_stock': round(carbon_stock, 1) if carbon_stock else None,
                'ph': round(ph, 2),
                'drainage_class': drainage,
                'soil_health_score': round(soil_health, 3) if soil_health else None,
                'soil_health_category': health_category,
            })
        else:
            print(f"    No data retrieved")
            results.append({
                'field_id': field_id,
                'latitude': round(lat, 5),
                'longitude': round(lon, 5),
                'om_pct': None,
                'k_factor': None,
                'bulk_density': None,
                'topsoil_depth_cm': None,
                'carbon_stock': None,
                'ph': None,
                'drainage_class': None,
                'soil_health_score': None,
                'soil_health_category': 'Unknown',
            })
    
    # Step 3: Create DataFrame and sort by OM%
    print("\n[3/4] Processing results...")
    
    df = pd.DataFrame(results)
    df = df.sort_values('om_pct', ascending=False).reset_index(drop=True)
    
    # Display top 10 and bottom 10
    print("\n" + "=" * 60)
    print("TOP 10 FIELDS (by Organic Matter %)")
    print("=" * 60)
    print(df.head(10)[['field_id', 'om_pct', 'k_factor', 'carbon_stock', 'soil_health_category']].to_string(index=False))
    
    print("\n" + "=" * 60)
    print("BOTTOM 10 FIELDS (by Organic Matter %)")
    print("=" * 60)
    print(df.tail(10)[['field_id', 'om_pct', 'k_factor', 'carbon_stock', 'soil_health_category']].to_string(index=False))
    
    # Step 4: Create visualization
    print("\n[4/4] Creating visualization...")
    
    try:
        import matplotlib.pyplot as plt
        
        # Normalize values for comparison (0-1 scale)
        df['om_norm'] = df['om_pct'] / df['om_pct'].max()
        df['k_norm'] = 1 - (df['k_factor'] / df['k_factor'].max())  # Invert - lower is better
        df['carbon_norm'] = df['carbon_stock'] / df['carbon_stock'].max()
        df['health_norm'] = df['soil_health_score'] / df['soil_health_score'].max()
        
        # Create horizontal bar chart
        fig, ax = plt.subplots(figsize=(14, 12))
        
        y_pos = np.arange(len(df))
        bar_height = 0.2
        
        # Plot bars for each metric
        bars1 = ax.barh(y_pos - 1.5*bar_height, df['om_norm'], bar_height, 
                       label='Organic Matter %', color='#2ecc71', alpha=0.8)
        bars2 = ax.barh(y_pos - 0.5*bar_height, df['k_norm'], bar_height,
                       label='K-Factor (inverted)', color='#e74c3c', alpha=0.8)
        bars3 = ax.barh(y_pos + 0.5*bar_height, df['carbon_norm'], bar_height,
                       label='Carbon Storage', color='#3498db', alpha=0.8)
        bars4 = ax.barh(y_pos + 1.5*bar_height, df['health_norm'], bar_height,
                       label='Soil Health Score', color='#9b59b6', alpha=0.8)
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels(df['field_id'], fontsize=9)
        ax.set_xlabel('Normalized Score (0-1)', fontsize=12)
        ax.set_title('Field Comparison: Soil Metrics\n(Sorted by Organic Matter %, descending)', fontsize=14)
        ax.legend(loc='lower right', fontsize=10)
        ax.set_xlim(0, 1.1)
        ax.grid(axis='x', alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(OUTPUT_CHART, dpi=150, bbox_inches='tight')
        print(f"  Saved chart to: {OUTPUT_CHART}")
        
        # Create 2x2 box plot for distribution analysis
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        
        metrics = [
            ('om_pct', 'Organic Matter %', '#2ecc71'),
            ('k_factor', 'K-Factor (Erosion)', '#e74c3c'),
            ('carbon_stock', 'Carbon Stock', '#3498db'),
            ('soil_health_score', 'Soil Health Score', '#9b59b6')
        ]
        
        ref_lines = {
            'k_factor': 0.35,  # High erosion risk threshold
            'soil_health_score': 0.7  # High/Moderate boundary
        }
        
        for idx, (col, title, color) in enumerate(metrics):
            ax = axes[idx // 2, idx % 2]
            data = df[col].dropna()
            
            bp = ax.boxplot(data, patch_artist=True, widths=0.6)
            for patch in bp['boxes']:
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            for median in bp['medians']:
                median.set_color('white')
                median.set_linewidth(2)
            
            ax.set_title(title, fontsize=12, fontweight='bold')
            ax.set_ylabel('Value', fontsize=10)
            ax.grid(axis='y', alpha=0.3)
            
            # Add reference line if threshold exists
            if col in ref_lines:
                ax.axhline(y=ref_lines[col], color='red', linestyle='--', 
                          alpha=0.7, linewidth=1.5, label=f'Threshold ({ref_lines[col]})')
                ax.legend(loc='upper right', fontsize=8)
            
            # Add stats annotation
            stats_text = f'n={len(data)}\nmedian={data.median():.2f}'
            ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, 
                   fontsize=9, verticalalignment='top', 
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        plt.suptitle('Distribution of Soil Metrics Across 20 Fields', 
                    fontsize=14, fontweight='bold', y=1.02)
        plt.tight_layout()
        
        boxplot_path = OUTPUT_DIR / 'field_comparison_boxplot.png'
        plt.savefig(boxplot_path, dpi=150, bbox_inches='tight')
        print(f"  Saved boxplot to: {boxplot_path}")
        
    except Exception as e:
        print(f"  Chart creation failed: {e}")
    
    # Save to CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"  Saved CSV to: {OUTPUT_CSV}")
    
    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE")
    print("=" * 60)
    
    return df


if __name__ == "__main__":
    main()
