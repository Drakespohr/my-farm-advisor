#!/usr/bin/env python3
"""
Carbon Storage Potential Analysis Script

Queries NRCS Soil Data Access for organic matter, bulk density,
and topsoil depth to calculate carbon storage potential.
"""

import requests
import numpy as np
import pandas as pd
from pathlib import Path

# NRCS Soil Data Access API
SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

# Field centroids (from existing soil data)
FIELD_CENTROIDS = {
    "field_001": {"lat": 40.92336, "lon": -96.3735},
    "field_002": {"lat": 40.92459, "lon": -96.38387},
    "field_003": {"lat": 40.91862, "lon": -96.36465},
    "field_004": {"lat": 40.95425, "lon": -96.0716},
    "field_005": {"lat": 41.01814, "lon": -96.07927},
}

OUTPUT_DIR = Path("data")
OUTPUT_CSV = OUTPUT_DIR / "carbon_storage_analysis.csv"


def query_sda_for_carbon_data(lat: float, lon: float) -> dict | None:
    """
    Query NRCS Soil Data Access for carbon-related soil properties.
    
    Returns:
        dict with om_pct, bulk_density, topsoil_depth_cm, or None if query fails
    """
    wkt = f"POINT({lon} {lat})"
    
    # Query for OM, bulk density, and horizon depth
    sql = f"""
    SELECT DISTINCT
        c.compname,
        c.comppct_r,
        ch.hzdept_r,
        ch.om_r,
        ch.dbthirdbar_r
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
        response = requests.post(
            SDA_URL,
            data={"query": sql, "format": "JSON"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        
        if "Table" not in result or not result["Table"]:
            return None
        
        # Process results - get topsoil (0-30cm) values
        # Component order: compname, comppct_r, hzdept_r, om_r, dbthirdbar_r
        total_weight = 0
        weighted_om = 0
        weighted_bd = 0
        max_depth = 0
        
        for row in result["Table"]:
            try:
                comp_pct = float(row[1]) if row[1] else 0
                depth = float(row[2]) if row[2] else 0
                om = float(row[3]) if row[3] else 0
                bd = float(row[4]) if row[4] else 0
                
                if depth <= 30 and om > 0 and bd > 0:  # Topsoil only, valid values
                    weighted_om += om * comp_pct
                    weighted_bd += bd * comp_pct
                    total_weight += comp_pct
                
                if depth > max_depth:
                    max_depth = depth
                    
            except (ValueError, TypeError, IndexError):
                continue
        
        if total_weight > 0:
            return {
                'om_pct': weighted_om / total_weight,
                'bulk_density': weighted_bd / total_weight,
                'topsoil_depth_cm': min(max_depth, 30),  # Cap at 30cm for topsoil
            }
        
        return None
        
    except Exception as e:
        print(f"  NRCS query failed for ({lat}, {lon}): {e}")
        return None


def calculate_carbon_stock(om_pct: float, bulk_density: float, depth_cm: float) -> float:
    """
    Calculate carbon stock using the formula:
    carbon_stock = om_pct × bulk_density × depth_cm × 1.724
    
    Args:
        om_pct: Organic matter percentage
        bulk_density: Bulk density in g/cm³
        depth_cm: Topsoil depth in cm
    
    Returns:
        Carbon stock value (arbitrary units)
    """
    if om_pct is None or bulk_density is None or depth_cm is None:
        return None
    
    # Standard conversion factor: OM% to SOC (Soil Organic Carbon)
    CONVERSION_FACTOR = 1.724
    
    carbon_stock = om_pct * bulk_density * depth_cm * CONVERSION_FACTOR
    return carbon_stock


def categorize_carbon(carbon_stock: float | None) -> str:
    """Categorize carbon storage potential."""
    if carbon_stock is None:
        return "Unknown"
    elif carbon_stock > 80:
        return "High"
    elif carbon_stock >= 40:
        return "Moderate"
    else:
        return "Low"


def main():
    """Main function to run carbon storage analysis."""
    print("=" * 60)
    print("CARBON STORAGE POTENTIAL ANALYSIS")
    print("=" * 60)
    
    results = []
    
    print("\n[1/2] Querying NRCS for soil carbon data...")
    
    for field_id, centroid in FIELD_CENTROIDS.items():
        lat = centroid['lat']
        lon = centroid['lon']
        
        print(f"\n  Processing {field_id}...")
        
        # Query NRCS for carbon data
        carbon_data = query_sda_for_carbon_data(lat, lon)
        
        if carbon_data:
            om_pct = carbon_data['om_pct']
            bulk_density = carbon_data['bulk_density']
            depth_cm = carbon_data['topsoil_depth_cm']
            
            # Calculate carbon stock
            carbon_stock = calculate_carbon_stock(om_pct, bulk_density, depth_cm)
            carbon_category = categorize_carbon(carbon_stock)
            
            print(f"    OM: {om_pct:.2f}%, BD: {bulk_density:.2f} g/cm³, Depth: {depth_cm:.0f}cm")
            print(f"    Carbon Stock: {carbon_stock:.1f} ({carbon_category})")
            
            results.append({
                'field_id': field_id,
                'latitude': lat,
                'longitude': lon,
                'om_pct': round(om_pct, 2),
                'bulk_density': round(bulk_density, 2),
                'topsoil_depth_cm': round(depth_cm, 0),
                'carbon_stock': round(carbon_stock, 1) if carbon_stock else None,
                'carbon_category': carbon_category,
            })
        else:
            print(f"    No carbon data retrieved")
            results.append({
                'field_id': field_id,
                'latitude': lat,
                'longitude': lon,
                'om_pct': None,
                'bulk_density': None,
                'topsoil_depth_cm': None,
                'carbon_stock': None,
                'carbon_category': 'Unknown',
            })
    
    print("\n[2/2] Saving results...")
    
    # Create DataFrame
    df = pd.DataFrame(results)
    
    # Display results
    print("\n" + "=" * 60)
    print("CARBON STORAGE RESULTS")
    print("=" * 60)
    print(df.to_string(index=False))
    
    # Save to CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved to: {OUTPUT_CSV}")
    
    return df


if __name__ == "__main__":
    main()
