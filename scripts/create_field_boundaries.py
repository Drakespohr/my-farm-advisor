#!/usr/bin/env python3
"""
Create approximate field boundary polygons from lat/lon points.
Buffer each point by ~200m to create field polygons.
"""
import json
import math
import pandas as pd

# Read field data
df = pd.read_csv('data/farm_20fields.csv')

def create_buffer_polygon(lat, lon, buffer_meters=200):
    """
    Create approximate square polygon around a point.
    Buffer in degrees: ~111km per degree lat, varies by longitude
    """
    lat_delta = buffer_meters / 111000  # ~111km per degree lat
    lon_delta = buffer_meters / (111000 * math.cos(math.radians(lat)))
    
    # Create square polygon
    coords = [
        [lon - lon_delta, lat - lat_delta],
        [lon + lon_delta, lat - lat_delta],
        [lon + lon_delta, lat + lat_delta],
        [lon - lon_delta, lat + lat_delta],
        [lon - lon_delta, lat - lat_delta]  # close the ring
    ]
    return coords

features = []
for _, row in df.iterrows():
    field_id = row['field_id']
    lat = row['latitude']
    lon = row['longitude']
    area = row['area_acres']
    
    # Adjust buffer based on field size (larger fields need larger buffers)
    # Rough estimate: 1 acre ~ 4047 m^2, so side ~ 64m for 1 acre
    # For 320 acres ~ 1.3M m^2, side ~ 1140m
    side_meters = math.sqrt(area * 4047)
    buffer = max(50, min(500, side_meters * 0.3))  # 30% of side, clamped 50-500m
    
    coords = create_buffer_polygon(lat, lon, buffer)
    
    feature = {
        "type": "Feature",
        "properties": {
            "field_id": field_id,
            "latitude": lat,
            "longitude": lon,
            "area_acres": area,
            "buffer_meters": buffer
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords]
        }
    }
    features.append(feature)

geojson = {
    "type": "FeatureCollection",
    "crs": {
        "type": "name",
        "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}
    },
    "features": features
}

# Save to file
with open('data/field_boundaries_001020.geojson', 'w') as f:
    json.dump(geojson, f, indent=2)

print(f"Created {len(features)} field boundary polygons")
print(f"Saved to: data/field_boundaries_001020.geojson")
