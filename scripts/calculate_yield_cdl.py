#!/usr/bin/env python3
"""
Calculate yields using CDL 2024 crop types + industry-standard NDVI regression formulas.

Formulas (per industry research):
- Corn: Yield = 180 × NDVI + 30
- Soybeans: Yield = 60 × NDVI + 20

Assumes crop rotation pattern continues, so CDL 2024 -> 2025.
"""
import pandas as pd

# Load NDVI data
ndvi = pd.read_csv('data/ndvi_july_stats.csv')

# Load CDL crop data (2024)
cdl = pd.read_csv('data/cdl/cdl_2024_fields.csv')

# Load field metadata
fields = pd.read_csv('data/farm_20fields.csv')

# Merge
df = ndvi.merge(cdl[['field_id', 'crop_name', 'dominant_pct']], on='field_id')
df = df.merge(fields[['field_id', 'area_acres', 'soil_series', 'surface_texture', 
                      'hydrologic_group', 'drainage_class', 'om_pct', 'ph',
                      'soil_health_score']], on='field_id')

# Add carbon stock (estimated from OM%)
df['carbon_stock'] = df['area_acres'] * df['om_pct'] * 3.5  # rough estimate

# Apply yield formulas based on CDL crop type
def calc_yield(row):
    ndvi = row['mean_ndvi']
    crop = row['crop_name']
    
    if crop == 'Corn':
        # Corn: Yield = 180 × NDVI + 30
        return 180 * ndvi + 30
    elif crop == 'Soybeans':
        # Soybeans: Yield = 60 × NDVI + 20
        return 60 * ndvi + 20
    elif crop == 'Grass/Pasture':
        # For grass/pasture, use soybean formula as approximation
        return 60 * ndvi + 20
    else:
        # Unknown crop - use soybean formula as fallback
        return 60 * ndvi + 20

df['yield_bu_acre'] = df.apply(calc_yield, axis=1)
df['predicted_bushels'] = df['yield_bu_acre'] * df['area_acres']

# Display results
print("=" * 80)
print("YIELD CALCULATIONS USING CDL 2024 CROP TYPES + NDVI REGRESSION")
print("=" * 80)
print()
print(f"{'Field':<10} {'Crop (CDL 2024)':<15} {'NDVI':<10} {'Yield (bu/acre)':<18} {'Pred. Bushels':<15}")
print("-" * 80)
for _, row in df.iterrows():
    print(f"{row['field_id']:<10} {row['crop_name']:<15} {row['mean_ndvi']:<10.4f} {row['yield_bu_acre']:<18.2f} {row['predicted_bushels']:<15.2f}")

print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
for crop in df['crop_name'].unique():
    crop_df = df[df['crop_name'] == crop]
    acres = crop_df['area_acres'].sum()
    bushels = crop_df['predicted_bushels'].sum()
    avg_yield = bushels / acres if acres > 0 else 0
    print(f"{crop}: {acres:.1f} acres, {bushels:.0f} bushels, avg yield: {avg_yield:.1f} bu/acre")

# Save to CSV
df.to_csv('data/yield_cdl_2024.csv', index=False)
print(f"\nSaved to: data/yield_cdl_2024.csv")
