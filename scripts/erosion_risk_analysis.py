#!/usr/bin/env python3
"""
Erosion Risk Analysis Script

Queries NRCS Soil Data Access for K-factor and calculates slope from DEM data.
Outputs erosion risk assessment for all fields.
"""

import json
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
import requests

# Field data paths
FIELDS_DIR = Path("data/fields")
SOIL_DIR = Path("data/soil")
OUTPUT_DIR = Path("data")
OUTPUT_CSV = OUTPUT_DIR / "erosion_risk_analysis.csv"

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


def query_sda_for_kfactor(lat: float, lon: float) -> float | None:
    """
    Query NRCS Soil Data Access for K-factor at a point.
    """
    # Build WKT point
    wkt = f"POINT({lon} {lat})"
    
    # SQL query to get K-factor
    sql = f"""
    SELECT DISTINCT
        c.compname,
        c.comppct_r,
        ch.kwfact
    FROM mapunit mu
    INNER JOIN component c ON mu.mukey = c.mukey
    LEFT JOIN chorizon ch ON c.cokey = ch.cokey
    WHERE mu.mukey IN (
        SELECT * FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')
    )
    AND ch.kwfact IS NOT NULL
    ORDER BY c.comppct_r DESC
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
        
        # Calculate weighted average K-factor
        total_weight = 0
        weighted_sum = 0
        
        for row in result["Table"]:
            if row[2] is not None:  # kwfact
                try:
                    comp_pct = float(row[1]) if row[1] else 0
                    kfact = float(row[2])
                    weighted_sum += kfact * comp_pct
                    total_weight += comp_pct
                except (ValueError, TypeError):
                    pass
        
        if total_weight > 0:
            return weighted_sum / total_weight
        
        return None
        
    except Exception as e:
        print(f"  NRCS query failed for ({lat}, {lon}): {e}")
        return None
        
        # Get weighted average K-factor by component percentage
        if 'kwfact' in df.columns and 'comppct_r' in df.columns:
            valid = df['kwfact'].notna() & df['comppct_r'].notna()
            if valid.any():
                k_values = df.loc[valid, 'kwfact']
                weights = df.loc[valid, 'comppct_r']
                return (k_values * weights).sum() / weights.sum()
        
        return None
    except Exception as e:
        print(f"  NRCS query failed for ({lat}, {lon}): {e}")
        return None


OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"


def fetch_dem_from_open_elevation(lat: float, lon: float, buffer: float = 0.001) -> np.ndarray | None:
    """
    Fetch elevation data from Open-Elevation API for a grid around the given coordinates.
    
    Args:
        lat: Latitude of center point
        lon: Longitude of center point  
        buffer: Grid extent in degrees (default 0.001 ≈ ~100m)
    
    Returns:
        2D numpy array of elevation values, or None if fetch fails
    """
    # Create grid of points around the center
    locations = []
    grid_size = 5  # 5x5 grid
    step = buffer / (grid_size - 1)
    
    for dy in range(grid_size):
        for dx in range(grid_size):
            lat_point = lat - buffer/2 + dy * step
            lon_point = lon - buffer/2 + dx * step
            locations.append({'latitude': lat_point, 'longitude': lon_point})
    
    try:
        response = requests.post(
            OPEN_ELEVATION_URL,
            json={'locations': locations},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        
        if 'results' not in result or not result['results']:
            print(f"  No elevation results returned")
            return None
        
        # Extract elevations and reshape to 2D grid
        elevations = [r['elevation'] for r in result['results']]
        dem_array = np.array(elevations).reshape(grid_size, grid_size)
        
        return dem_array
        
    except Exception as e:
        print(f"  DEM fetch error: {e}")
        return None


def calculate_slope_from_elevation_grid(elevations: np.ndarray, lat_span: float, lon_span: float) -> float:
    """
    Calculate slope percentage from a grid of elevation values.
    
    Args:
        elevations: 2D numpy array of elevation values in meters
        lat_span: Latitude extent of the grid in degrees
        lon_span: Longitude extent of the grid in degrees
    
    Returns:
        Slope percentage (0-100)
    """
    if elevations is None or elevations.size == 0:
        return 0.0
    
    # Get grid dimensions
    rows, cols = elevations.shape
    
    if rows < 2 or cols < 2:
        return 0.0
    
    # Convert spans to meters (approximate)
    # 1 degree latitude ≈ 111,000 meters
    # 1 degree longitude ≈ 111,000 * cos(latitude) meters
    avg_lat = np.mean(np.linspace(0, lat_span, rows))
    lat_meters = lat_span * 111000
    lon_meters = lon_span * 111000 * np.cos(np.radians(avg_lat))
    
    # Cell sizes in meters
    cell_size_lat = lat_meters / (rows - 1) if rows > 1 else 1
    cell_size_lon = lon_meters / (cols - 1) if cols > 1 else 1
    
    # Calculate gradient in meters
    gradient_y = np.gradient(elevations, axis=0) / cell_size_lat
    gradient_x = np.gradient(elevations, axis=1) / cell_size_lon
    
    # Calculate slope (rise/run) as a ratio, convert to percentage
    # slope_pct = sqrt(dz/dx^2 + dz/dy^2) * 100
    slope_ratio = np.sqrt(gradient_x**2 + gradient_y**2)
    slope_pct = slope_ratio * 100
    
    # Remove invalid values and calculate mean
    valid_slopes = slope_pct[~np.isnan(slope_pct) & ~np.isinf(slope_pct)]
    
    if len(valid_slopes) == 0:
        return 0.0
    
    return float(np.mean(valid_slopes))


def load_field_boundaries() -> gpd.GeoDataFrame:
    """Load all field boundary GeoJSON files."""
    fields = []
    
    for field_id in FIELD_CENTROIDS.keys():
        boundary_file = FIELDS_DIR / f"{field_id}_boundary.geojson"
        
        if boundary_file.exists():
            gdf = gpd.read_file(boundary_file)
            gdf['field_id'] = field_id
            fields.append(gdf)
    
    if not fields:
        raise FileNotFoundError(f"No field boundaries found in {FIELDS_DIR}")
    
    return pd.concat(fields, ignore_index=True)


def calculate_composite_risk(k_factor: float | None, slope_pct: float | None) -> tuple:
    """
    Calculate K-factor risk and composite risk score.
    
    Returns:
        (k_risk, composite_score, composite_category)
    """
    # K-factor risk (standalone)
    k_risk = "Unknown"
    if k_factor is not None:
        if k_factor > 0.35:
            k_risk = "High"
        elif k_factor > 0.25:
            k_risk = "Moderate"
        else:
            k_risk = "Low"
    
    # Normalize values
    normalized_k = k_factor / 0.43 if k_factor else 0  # 0.43 is max typical K
    normalized_slope = slope_pct / 15 if slope_pct else 0  # 15% is max tillable
    
    # Composite score (50% K-factor, 50% slope)
    composite_score = 0.5 * normalized_k + 0.5 * normalized_slope
    
    # Composite category
    if composite_score > 0.5:
        composite_category = "High"
    elif composite_score > 0.3:
        composite_category = "Moderate"
    else:
        composite_category = "Low"
    
    return k_risk, composite_score, composite_category


def main():
    """Main function to run erosion risk analysis."""
    print("=" * 60)
    print("EROSION RISK ANALYSIS")
    print("=" * 60)
    
    # Step 1: Load field boundaries
    print("\n[1/4] Loading field boundaries...")
    fields_gdf = load_field_boundaries()
    print(f"  Loaded {len(fields_gdf)} fields")
    
    results = []
    
    # Step 2: Query NRCS for K-factor and calculate slope
    print("\n[2/4] Querying NRCS for K-factor...")
    print("[3/4] Downloading DEM and calculating slope...")
    
    for field_id, centroid in FIELD_CENTROIDS.items():
        lat = centroid['lat']
        lon = centroid['lon']
        
        print(f"\n  Processing {field_id}...")
        
        # Query NRCS for K-factor
        k_factor = query_sda_for_kfactor(lat, lon)
        
        # Fetch DEM from Open-Elevation API and calculate slope
        dem_array = fetch_dem_from_open_elevation(lat, lon, buffer=0.001)
        if dem_array is None:
            raise RuntimeError(f"Failed to fetch DEM for {field_id} at ({lat}, {lon})")
        
        # Calculate slope from elevation grid
        slope_pct = calculate_slope_from_elevation_grid(dem_array, lat_span=0.001, lon_span=0.001)
        
        # Calculate risk scores
        k_risk, composite_score, composite_category = calculate_composite_risk(
            k_factor, slope_pct
        )
        
        results.append({
            'field_id': field_id,
            'latitude': lat,
            'longitude': lon,
            'k_factor': round(k_factor, 3) if k_factor else None,
            'k_risk': k_risk,
            'slope_pct': round(slope_pct, 1) if slope_pct else None,
            'composite_risk': round(composite_score, 3),
            'composite_category': composite_category,
        })
        
        print(f"    K-factor: {k_factor}, Slope: {slope_pct:.1f}%, Composite: {composite_score:.3f}")
    
    # Step 4: Create output DataFrame
    print("\n[4/4] Saving results...")
    df = pd.DataFrame(results)
    
    # Display results
    print("\n" + "=" * 60)
    print("EROSION RISK RESULTS")
    print("=" * 60)
    print(df.to_string(index=False))
    
    # Save to CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved to: {OUTPUT_CSV}")
    
    return df


if __name__ == "__main__":
    main()
