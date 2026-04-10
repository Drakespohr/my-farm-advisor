#!/usr/bin/env python3
"""
Extract crop types from CDL 2024 for each field using zonal stats.
"""
import json
import pandas as pd
import rasterio
from rasterstats import zonal_stats
from collections import Counter

# Expanded CDL codes (from USDA)
CDL_CODES = {
    0: "No Data", 1: "Corn", 5: "Soybeans", 24: "Winter Wheat",
    27: "Oats", 28: "Alfalfa", 36: "Forest", 37: "Forest/Evergreen",
    38: "Grassland/Herbaceous", 43: "Open Water", 61: "Fallow/Idle",
    63: "Other", 81: "Clouds/No Data", 82: "Developed", 83: "Urban",
    87: "Wetlands", 88: "Nonag/Undefined", 176: "Grass/Pasture",
    204: "Pistachios", 206: "Pomegranates", 207: "Citrus", 209: "Oranges",
    210: "Grapes", 211: "Tomatoes", 213: "Peaches", 214: "Apples",
    216: "Almonds", 217: "Walnuts", 218: "Pears", 219: "Cherries",
    220: "Prunes", 221: "Plums", 222: "Apricots", 223: "Lettuce",
    224: "Broccoli", 225: "Cauliflower", 226: "Bell Peppers",
    227: "Hot Peppers", 228: "Eggplant", 229: "Squash", 230: "Cucumbers",
    231: "Melons", 232: "Watermelons", 233: "Pumpkins", 234: "Sweet Potatoes",
    235: "Potatoes", 236: "Onions", 237: "Garlic", 238: "Legumes",
    239: "Carrots", 240: "Sunflower", 241: "Rape/Canola", 242: "Mustard",
    243: "Safflower", 244: "Flax", 245: "Rye", 246: "Buckwheat",
    247: "Millett", 248: "Sorghum", 249: "Sugarcane", 250: "Cotton",
    251: "Tobacco", 252: "Peanuts", 253: "Mung Beans", 254: "Ryegrass",
    255: "Sudan Grass", 256: "Bermuda Grass", 257: "Clover",
    258: "Vetch", 259: "Rangeland", 260: "Shrubland", 261: "Barley",
    262: "Durum Wheat", 263: "Spring Wheat", 264: "Winter Wheat",
    265: "Other Wheat", 266: "Triticale", 267: "Rape Seed",
    268: "Carrots", 269: "Herbs", 270: "Cantaloupe", 271: "Ginseng",
    272: "Hops", 273: "Mint", 274: "Garlic", 275: "Other Pulses",
    276: " Lentils", 277: "Peas", 278: "Chickpeas", 279: "Lima Beans",
    280: "Other Beans", 281: "Snap Beans", 282: "Kidney Beans",
    283: "Pinto Beans", 284: "Navy Beans", 285: "Black Beans",
    286: "Great Northern Beans", 287: "Light Red Kidney Beans",
    288: "Dark Red Kidney Beans", 289: "Pink Beans", 290: "Cranberries",
    291: "Blueberries", 292: "Raspberries", 293: "Strawberries",
    294: "Other Misc", 295: "Popcorn", 296: "Sweet Corn",
    297: "Pop/Opt", 298: "Rape Seed", 299: "Camelina",
    300: "Biomass Energy", 301: "Gourds", 302: "Speltz", 303: "Plums",
    304: "Prunes", 305: "Dates", 306: "Avocados", 307: "Olives",
    308: "Persimmons", 309: "Papaya", 310: "Kiwi", 311: "Pecans",
    312: "Macadamia", 313: "Chestnuts", 314: "Almonds", 315: "Walnuts",
    316: "Pistachios", 317: "Hazelnuts", 318: "Figs", 319: "Pomegranates",
    320: "Jojoba", 321: "Tangerines", 322: "Tangelos", 323: "Oranges",
    324: "Grapefruit", 325: "Lemons", 326: "Limes", 327: "Other Citrus",
}

# Load field boundaries
with open('data/field_boundaries_001020.geojson', 'r') as f:
    fields_geojson = json.load(f)

# Convert to GeoDataFrame
import geopandas as gpd
fields = gpd.GeoDataFrame.from_features(fields_geojson['features'])
fields = fields.set_crs('EPSG:4326')

# Path to CDL
cdl_path = 'data/cdl/CDL_2024_31.tif'

# Extract crops
results = []
with rasterio.open(cdl_path) as src:
    fields_proj = fields.to_crs(src.crs)
    
    for idx, field in fields_proj.iterrows():
        field_id = field['field_id']
        
        stats = zonal_stats(field.geometry, cdl_path, categorical=True)
        
        if not stats or not stats[0]:
            results.append({
                'field_id': field_id,
                'year': 2024,
                'crop_code': 0,
                'crop_name': 'No Data',
                'dominant_pct': 0.0,
                'total_pixels': 0
            })
            continue
            
        counts = stats[0]
        # Convert keys to int (they might be strings)
        counts = {int(k) if isinstance(k, (int, float)) else 0: v for k, v in counts.items() if isinstance(k, (int, float, str))}
        # Filter out no-data (0) and clouds (81)
        valid_counts = {k: v for k, v in counts.items() if k not in [0, 81, 82, 83, 87, 88, 204]}
        
        if not valid_counts:
            results.append({
                'field_id': field_id,
                'year': 2024,
                'crop_code': 0,
                'crop_name': 'No Data',
                'dominant_pct': 0.0,
                'total_pixels': sum(counts.values())
            })
            continue
            
        total = sum(valid_counts.values())
        dominant_code = max(valid_counts, key=valid_counts.get)
        dominant_pct = valid_counts[dominant_code] / total * 100
        
        results.append({
            'field_id': field_id,
            'year': 2024,
            'crop_code': int(dominant_code),
            'crop_name': CDL_CODES.get(int(dominant_code), f"Code_{dominant_code}"),
            'dominant_pct': round(dominant_pct, 1),
            'total_pixels': total
        })

# Create DataFrame
df = pd.DataFrame(results)
print(df.to_string(index=False))

# Save to CSV
df.to_csv('data/cdl/cdl_2024_fields.csv', index=False)
print(f"\nSaved to: data/cdl/cdl_2024_fields.csv")
