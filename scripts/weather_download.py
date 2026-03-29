#!/usr/bin/env python3
"""Download NASA POWER weather data for 5 Nebraska fields."""

import json
import time

import pandas as pd
import requests
from shapely.geometry import shape

API_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "RH2M", "WS10M"]

FIELDS = [
    {"field_id": "field_001", "lat": 40.92336, "lon": -96.3735, "location": "Near Crete, NE (Saline County)"},
    {"field_id": "field_002", "lat": 40.92459, "lon": -96.38387, "location": "Near Crete, NE (Saline County)"},
    {"field_id": "field_003", "lat": 40.91862, "lon": -96.36465, "location": "Near Crete, NE (Saline County)"},
    {"field_id": "field_004", "lat": 40.95425, "lon": -96.0716, "location": "Near Ashland, NE (Saunders County)"},
    {"field_id": "field_005", "lat": 41.01814, "lon": -96.07927, "location": "Near Ashland, NE (Saunders County)"},
]

START_DATE = "2025-01-01"
END_DATE = "2025-12-31"


def query_power(lat: float, lon: float, start: str, end: str,
                parameters: list[str] | None = None) -> pd.DataFrame | None:
    """Query NASA POWER API for one point. Returns DataFrame or None."""
    parameters = parameters or PARAMS
    try:
        resp = requests.get(
            API_URL,
            params={
                "parameters": ",".join(parameters),
                "community": "AG",
                "longitude": lon,
                "latitude": lat,
                "start": start.replace("-", ""),
                "end": end.replace("-", ""),
                "format": "JSON",
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        param_data = data["properties"]["parameter"]
        first_param = list(param_data.keys())[0]
        dates = list(param_data[first_param].keys())

        records = []
        for d in dates:
            row = {"date": pd.to_datetime(d, format="%Y%m%d")}
            for p in parameters:
                val = param_data.get(p, {}).get(d, -999.0)
                row[p] = val if val != -999.0 else None
            records.append(row)

        return pd.DataFrame(records)
    except Exception as e:
        print(f"Error querying ({lat}, {lon}): {e}")
        return None


def download_for_fields(fields: list[dict], start: str, end: str,
                       output_csv: str | None = None) -> pd.DataFrame:
    """Download weather for all fields."""
    all_dfs = []
    for field in fields:
        field_id = field["field_id"]
        lat = field["lat"]
        lon = field["lon"]

        print(f"Querying {field_id} at ({lat}, {lon})...")
        df = query_power(lat, lon, start, end)
        if df is not None:
            df.insert(0, "field_id", field_id)
            df.insert(1, "lat", round(lat, 4))
            df.insert(2, "lon", round(lon, 4))
            all_dfs.append(df)
            print(f"  Retrieved {len(df)} days")

        time.sleep(1)

    result = pd.concat(all_dfs, ignore_index=True)
    if output_csv:
        result.to_csv(output_csv, index=False)
        print(f"Saved {len(result)} rows to {output_csv}")
    return result


def main():
    print(f"Downloading weather data for {len(FIELDS)} fields")
    print(f"Date range: {START_DATE} to {END_DATE}")
    print("-" * 50)

    weather = download_for_fields(FIELDS, START_DATE, END_DATE, "data/weather_5fields_2025.csv")

    print("-" * 50)
    print(f"Total records: {len(weather)}")
    print(f"Fields: {weather['field_id'].unique().tolist()}")
    print(f"\nColumns: {weather.columns.tolist()}")
    print(f"\nDate range: {weather['date'].min()} to {weather['date'].max()}")

    print("\nSample data:")
    print(weather.head())


if __name__ == "__main__":
    main()
