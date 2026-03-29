#!/usr/bin/env python3
"""Download historical NASA POWER weather data for 2000-2024."""

import time

import pandas as pd
import requests
from pathlib import Path

API_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]

# Field coordinates (using field_001 as representative)
FIELD = {"field_id": "field_001", "lat": 40.92336, "lon": -96.3735}

# Years to download
START_YEAR = 2000
END_YEAR = 2024

# Growing season (June 1 - August 31)
START_MONTH, START_DAY = 6, 1
END_MONTH, END_DAY = 8, 31


def query_power(lat: float, lon: float, start: str, end: str,
                parameters: list[str] | None = None) -> pd.DataFrame | None:
    """Query NASA POWER API for one date range."""
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
        print(f"Error querying {start} to {end}: {e}")
        return None


def main():
    print(f"Downloading historical weather data: {START_YEAR}-{END_YEAR}")
    print(f"Growing season: June 1 - August 31")
    print("-" * 50)

    all_dfs = []

    for year in range(START_YEAR, END_YEAR + 1):
        start_date = f"{year}-{START_MONTH:02d}-{START_DAY:02d}"
        end_date = f"{year}-{END_MONTH:02d}-{END_DAY:02d}"

        print(f"Querying {year} ({start_date} to {end_date})...", end=" ")
        
        df = query_power(
            FIELD["lat"],
            FIELD["lon"],
            start_date,
            end_date,
            PARAMS
        )

        if df is not None:
            df.insert(0, "field_id", FIELD["field_id"])
            df.insert(1, "lat", FIELD["lat"])
            df.insert(2, "lon", FIELD["lon"])
            df.insert(3, "year", year)
            all_dfs.append(df)
            print(f"Retrieved {len(df)} days")
        else:
            print("FAILED")

        time.sleep(0.5)  # Rate limiting

    # Combine all years
    result = pd.concat(all_dfs, ignore_index=True)

    # Save to CSV
    output_file = Path("data") / "weather_historical_2000_2024.csv"
    result.to_csv(output_file, index=False)

    print("-" * 50)
    print(f"Total records: {len(result)}")
    print(f"Years: {result['year'].min()} - {result['year'].max()}")
    print(f"Date range: {result['date'].min()} to {result['date'].max()}")
    print(f"Saved to: {output_file}")


if __name__ == "__main__":
    main()
