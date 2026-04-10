#!/usr/bin/env python3
"""
NDVI-to-Yield Regression Calculator (v2)
Uses ratio-based normalization approach instead of raw NDVI regression.
This is more appropriate when baseline NDVI was measured at different times than July NDVI.
"""

import csv
from typing import List, Tuple, Dict

def read_csv(filepath: str) -> List[dict]:
    """Read CSV file and return list of dictionaries."""
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)

def main():
    # Read baseline data
    baseline_data = read_csv('data/farm_20fields.csv')
    july_ndvi_data = read_csv('data/ndvi_july_stats.csv')
    
    # Create lookup for July NDVI
    july_ndvi_lookup = {row['field_id']: float(row['mean_ndvi']) for row in july_ndvi_data}
    
    # Separate corn fields (001-005) and soybean fields (006-020)
    corn_fields = []
    soybean_fields = []
    
    for row in baseline_data:
        field_id = row['field_id']
        baseline_yield = float(row['yield_bu_acre'])
        area_acres = float(row['area_acres'])
        
        field_num = int(field_id.split('_')[1])
        
        if field_num <= 5:
            corn_fields.append({
                'field_id': field_id,
                'baseline_yield': baseline_yield,
                'area_acres': area_acres,
                'july_ndvi': july_ndvi_lookup.get(field_id, 0)
            })
        else:
            soybean_fields.append({
                'field_id': field_id,
                'baseline_yield': baseline_yield,
                'area_acres': area_acres,
                'july_ndvi': july_ndvi_lookup.get(field_id, 0)
            })
    
    # Calculate min/max July NDVI for each crop
    corn_july_ndvi = [f['july_ndvi'] for f in corn_fields]
    soy_july_ndvi = [f['july_ndvi'] for f in soybean_fields]
    
    corn_ndvi_min = min(corn_july_ndvi)
    corn_ndvi_max = max(corn_july_ndvi)
    soy_ndvi_min = min(soy_july_ndvi)
    soy_ndvi_max = max(soy_july_ndvi)
    
    # Calculate yield range for each crop (using baseline yields)
    corn_yields = [f['baseline_yield'] for f in corn_fields]
    soy_yields = [f['baseline_yield'] for f in soybean_fields]
    
    corn_yield_min = min(corn_yields)
    corn_yield_max = max(corn_yields)
    soy_yield_min = min(soy_yields)
    soy_yield_max = max(soy_yields)
    
    print("=" * 80)
    print("YIELD PARAMETERS BY CROP")
    print("=" * 80)
    print(f"\nCORN (001-005):")
    print(f"  July NDVI range: {corn_ndvi_min:.4f} - {corn_ndvi_max:.4f}")
    print(f"  Baseline yield range: {corn_yield_min:.2f} - {corn_yield_max:.2f} bu/acre")
    
    print(f"\nSOYBEANS (006-020):")
    print(f"  July NDVI range: {soy_ndvi_min:.4f} - {soy_ndvi_max:.4f}")
    print(f"  Baseline yield range: {soy_yield_min:.2f} - {soy_yield_max:.2f} bu/acre")
    
    # Calculate new yields using normalized NDVI scaling
    print("\n" + "=" * 80)
    print("RECALCULATED YIELDS USING JULY 2025 NDVI")
    print("=" * 80)
    print(f"\n{'Field':<12} {'Crop':<10} {'July NDVI':<12} {'Old Yield':<12} {'New Yield':<12} {'Predicted Bu':<15}")
    print("-" * 80)
    
    results = []
    
    # Process corn fields
    for f in corn_fields:
        # Normalize July NDVI to 0-1 scale
        ndvi_range = corn_ndvi_max - corn_ndvi_min
        if ndvi_range > 0:
            normalized_ndvi = (f['july_ndvi'] - corn_ndvi_min) / ndvi_range
        else:
            normalized_ndvi = 0.5
        
        # Scale to yield range
        yield_range = corn_yield_max - corn_yield_min
        new_yield = corn_yield_min + (normalized_ndvi * yield_range)
        
        predicted_bushels = new_yield * f['area_acres']
        print(f"{f['field_id']:<12} {'CORN':<10} {f['july_ndvi']:<12.4f} {f['baseline_yield']:<12.2f} {new_yield:<12.2f} {predicted_bushels:<15.2f}")
        results.append({
            'field_id': f['field_id'],
            'crop': 'corn',
            'july_ndvi': f['july_ndvi'],
            'old_yield': f['baseline_yield'],
            'new_yield': new_yield,
            'area_acres': f['area_acres'],
            'predicted_bushels': predicted_bushels
        })
    
    # Process soybean fields
    for f in soybean_fields:
        # Normalize July NDVI to 0-1 scale
        ndvi_range = soy_ndvi_max - soy_ndvi_min
        if ndvi_range > 0:
            normalized_ndvi = (f['july_ndvi'] - soy_ndvi_min) / ndvi_range
        else:
            normalized_ndvi = 0.5
        
        # Scale to yield range
        yield_range = soy_yield_max - soy_yield_min
        new_yield = soy_yield_min + (normalized_ndvi * yield_range)
        
        predicted_bushels = new_yield * f['area_acres']
        print(f"{f['field_id']:<12} {'SOY':<10} {f['july_ndvi']:<12.4f} {f['baseline_yield']:<12.2f} {new_yield:<12.2f} {predicted_bushels:<15.2f}")
        results.append({
            'field_id': f['field_id'],
            'crop': 'soybean',
            'july_ndvi': f['july_ndvi'],
            'old_yield': f['baseline_yield'],
            'new_yield': new_yield,
            'area_acres': f['area_acres'],
            'predicted_bushels': predicted_bushels
        })
    
    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    corn_results = [r for r in results if r['crop'] == 'corn']
    soy_results = [r for r in results if r['crop'] == 'soybean']
    
    print(f"\nCORN (001-005):")
    print(f"  Average old yield: {sum(r['old_yield'] for r in corn_results)/len(corn_results):.2f} bu/acre")
    print(f"  Average new yield: {sum(r['new_yield'] for r in corn_results)/len(corn_results):.2f} bu/acre")
    print(f"  Total predicted bushels: {sum(r['predicted_bushels'] for r in corn_results):.2f}")
    
    print(f"\nSOYBEANS (006-020):")
    print(f"  Average old yield: {sum(r['old_yield'] for r in soy_results)/len(soy_results):.2f} bu/acre")
    print(f"  Average new yield: {sum(r['new_yield'] for r in soy_results)/len(soy_results):.2f} bu/acre")
    print(f"  Total predicted bushels: {sum(r['predicted_bushels'] for r in soy_results):.2f}")
    
    # Output JSON-formatted data for HTML update
    print("\n" + "=" * 80)
    print("JSON DATA FOR HTML UPDATE")
    print("=" * 80)
    print("\nconst YIELD_REGRESSION = {")
    print(f"  corn: {{ ndvi_min: {corn_ndvi_min:.6f}, ndvi_max: {corn_ndvi_max:.6f}, yield_min: {corn_yield_min:.2f}, yield_max: {corn_yield_max:.2f} }},")
    print(f"  soybean: {{ ndvi_min: {soy_ndvi_min:.6f}, ndvi_max: {soy_ndvi_max:.6f}, yield_min: {soy_yield_min:.2f}, yield_max: {soy_yield_max:.2f} }}")
    print("};")
    
    print("\nconst FARM_DATA_UPDATED = [")
    for r in results:
        print(f"  {{ field_id: \"{r['field_id']}\", mean_ndvi: {r['july_ndvi']:.8f}, yield_bu_acre: {r['new_yield']:.8f}, predicted_bushels: {r['predicted_bushels']:.8f}, crop: \"{r['crop']}\" }},", end="")
        print()
    print("];")

if __name__ == '__main__':
    main()
