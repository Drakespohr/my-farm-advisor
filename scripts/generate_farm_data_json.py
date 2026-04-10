#!/usr/bin/env python3
"""
Generate FARM_DATA JSON from CDL 2024 crop types + NDVI regression yields.
"""
import pandas as pd
import json

# Load the calculated data
df = pd.read_csv('data/yield_cdl_2024.csv')

# Define soil health categories
def soil_health_category(score):
    if score >= 0.8:
        return "High"
    elif score >= 0.6:
        return "Medium"
    else:
        return "Low"

# K-factor estimates by soil series
K_FACTOR = {
    "Kennebec": 0.336,
    "Ely": 0.364,
}

# Bulk density estimates
BULK_DENSITY = {
    "Kennebec": 1.35,
    "Ely": 1.42,
}

# Weather data (shared)
WEATHER = {
    "avg_temp": 20.76,
    "precipitation_mm": 620.49,
    "humidity_pct": 68.39,
    "solar_radiation": 3707.34,
    "temp_f": 69.4,
}

lines = ["    const FARM_DATA = ["]
for idx, row in df.iterrows():
    field_id = row['field_id']
    area = row['area_acres']
    soil_series = row['soil_series']
    texture = row['surface_texture']
    hydrologic = row['hydrologic_group']
    drainage = row['drainage_class']
    om = row['om_pct']
    ph = row['ph']
    sh_score = row['soil_health_score']
    carbon = row['carbon_stock']
    
    ndvi = row['mean_ndvi']
    ndvi_min = row['min_ndvi']
    ndvi_max = row['max_ndvi']
    yield_bu = row['yield_bu_acre']
    predicted = row['predicted_bushels']
    
    crop = row['crop_name'].lower()
    if crop == 'grass/pasture':
        crop = 'soybean'  # Use soybean formula as approximation
    
    k = K_FACTOR.get(soil_series, 0.34)
    bd = BULK_DENSITY.get(soil_series, 1.40)
    sh_cat = soil_health_category(sh_score)
    
    line = f'''      {{field_id: "{field_id}", area_acres: {area}, soil_series: "{soil_series}", surface_texture: "{texture}", hydrologic_group: "{hydrologic}", drainage_class: "{drainage}", om_pct: {om}, ph: {ph}, carbon_stock: {carbon:.1f}, soil_health_score: {sh_score}, soil_health_category: "{sh_cat}", k_factor: {k}, bulk_density: {bd}, mean_ndvi: {ndvi}, min_ndvi: {ndvi_min}, max_ndvi: {ndvi_max}, yield_bu_acre: {yield_bu:.2f}, predicted_bushels: {predicted:.2f}, avg_temp: {WEATHER['avg_temp']}, precipitation_mm: {WEATHER['precipitation_mm']}, humidity_pct: {WEATHER['humidity_pct']}, solar_radiation: {WEATHER['solar_radiation']}, temp_f: {WEATHER['temp_f']}, ndvi_date: "2025-07-17", yield_source: "CDL 2024 + July 2025 NDVI Regression", ndvi_source: "July 2025 Sentinel-2", crop: "{crop}"}},'''
    lines.append(line)

lines.append("    ];")
print("\n".join(lines))
