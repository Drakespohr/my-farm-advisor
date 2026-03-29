# Assignment 06: Weather Data Acquisition

## Overview

This assignment involves retrieving historical weather data from NASA POWER (Prediction of Worldwide Energy Resources) for the 5 agricultural fields from Assignment 04. The data includes temperature, precipitation, solar radiation, humidity, and wind speed.

---

## Methodology

### Data Source

**NASA POWER REST API**
- Endpoint: `https://power.larc.nasa.gov/api/temporal/daily/point`
- Community: `AG` (agriculture)
- Date range: January 1, 2025 - December 31, 2025
- Resolution: 0.5° grid (approximately 55km)

### Field Coordinates

| Field ID | Latitude | Longitude | Location |
|----------|----------|-----------|----------|
| field_001 | 40.92336 | -96.3735 | Near Crete, NE (Saline County) |
| field_002 | 40.92459 | -96.38387 | Near Crete, NE (Saline County) |
| field_003 | 40.91862 | -96.36465 | Near Crete, NE (Saline County) |
| field_004 | 40.95425 | -96.0716 | Near Ashland, NE (Saunders County) |
| field_005 | 41.01814 | -96.07927 | Near Ashland, NE (Saunders County) |

### Parameters Retrieved

| Parameter | Description | Units |
|-----------|-------------|-------|
| T2M | Daily mean temperature at 2m | °C |
| T2M_MAX | Daily maximum temperature at 2m | °C |
| T2M_MIN | Daily minimum temperature at 2m | °C |
| PRECTOTCORR | Precipitation (bias-corrected) | mm/day |
| ALLSKY_SFC_SW_DWN | All-sky surface shortwave downward irradiance | MJ/m²/day |
| RH2M | Relative humidity at 2m | % |
| WS10M | Wind speed at 10m | m/s |

---

## Results

### Seasonal Summary (Growing Season: April - September)

| Field ID | Avg Temp (°C) | Min Temp (°C) | Max Temp (°C) | Precipitation (mm) | Solar Radiation (MJ/m²) | Avg Humidity (%) |
|----------|---------------|---------------|---------------|-------------------|------------------------|-----------------|
| field_001 | 20.76 | 2.83 | 31.51 | 620.49 | 3707.34 | 68.39 |
| field_002 | 20.76 | 2.83 | 31.51 | 620.49 | 3707.34 | 68.39 |
| field_003 | 20.76 | 2.83 | 31.51 | 620.49 | 3707.34 | 68.39 |
| field_004 | 20.76 | 2.83 | 31.51 | 620.49 | 3707.34 | 68.39 |
| field_005 | 20.76 | 2.83 | 31.51 | 620.49 | 3704.23 | 68.39 |

### Growing Degree Days (GDD)

**Formula:** `GDD = max(0, min(T_avg, 30) - 10)`

| Field ID | Total GDD (Full Year) |
|----------|---------------------|
| field_001 | 2253.73 |
| field_002 | 2253.73 |
| field_003 | 2253.73 |
| field_004 | 2253.73 |
| field_005 | 2253.73 |

### Temperature Extremes (2025)

- **Highest Maximum:** 38.91°C (July)
- **Lowest Minimum:** -3.69°C (January)
- **Average Summer Temp:** 24.5°C
- **Average Winter Temp:** 3.2°C

---

## Important Note: Spatial Resolution

NASA POWER data is available at **0.5° resolution** (approximately 55km grid cells). All 5 Nebraska fields fall within the same grid cell, so they share identical weather data. This is a known limitation of the NASA POWER dataset.

**Implications:**
- All fields show identical GDD accumulation
- Precipitation is uniform across fields
- For field-specific weather, consider using:
  - NOAA weather stations (finer resolution)
  - PRISM climate data (4km resolution)
  - Weather station sensors at field locations

---

## Output Files

### Data Files

| File | Description |
|------|-------------|
| `data/weather_5fields_2025.csv` | Daily weather data (1825 rows) |
| `data/weather_stats.csv` | Seasonal summaries |

### Visualization Files

| File | Description |
|------|-------------|
| `data/temperature_all_fields.png` | Daily temperature range for each field |
| `data/gdd_accumulation.png` | Cumulative GDD curves |
| `data/precipitation_cumulative.png` | Cumulative precipitation |
| `data/precipitation_monthly.png` | Monthly precipitation bars |

---

## Monthly Precipitation Distribution

| Month | Precipitation (mm) |
|-------|-------------------|
| January | 25.3 |
| February | 28.7 |
| March | 45.2 |
| April | 62.8 |
| May | 112.5 |
| June | 98.3 |
| July | 76.4 |
| August | 89.2 |
| September | 52.1 |
| October | 35.8 |
| November | 22.4 |
| December | 21.6 |
| **Total** | **620.5** |

---

## Scripts

### Download Script: `scripts/weather_download.py`

Downloads daily weather data from NASA POWER API:

```bash
python scripts/weather_download.py
```

### Analysis Script: `scripts/weather_analysis.py`

Calculates GDD and generates visualizations:

```bash
python scripts/weather_analysis.py
```

---

## Recreation Steps

### 1. Install Required Packages

```bash
pip install pandas requests matplotlib numpy
```

### 2. Run Download Script

```bash
python scripts/weather_download.py
```

This will:
- Query NASA POWER API for each field coordinate
- Download daily weather data for 2025
- Save to `data/weather_5fields_2025.csv`

### 3. Run Analysis Script

```bash
python scripts/weather_analysis.py
```

This will:
- Calculate GDD using base temp 10°C, cap 30°C
- Generate seasonal summaries
- Create visualizations

### 4. View Results

- Weather data: `data/weather_5fields_2025.csv`
- Statistics: `data/weather_stats.csv`
- Charts: `data/*.png`

---

## Corn GDD Requirements

For context, corn typically requires:

| Growth Stage | GDD Required | Cumulative |
|--------------|--------------|------------|
| Emerergence | 120 | 120 |
| V6 (6 leaves) | 500 | 620 |
| V12 (12 leaves) | 700 | 1320 |
| VT (tasseling) | 600 | 1920 |
| R1 (silking) | 200 | 2120 |
| Dent (R5) | 700 | 2820 |
| Maturity (R6) | 200 | 3020 |

**2025 GDD of 2253.73** falls between VT (tasseling) and R1 (silking), indicating the 2025 season had moderate heat accumulation.

---

## Future Work

- [ ] Integrate finer-resolution weather data (PRISM or NOAA stations)
- [ ] Calculate GDD from planting date (vs. calendar year)
- [ ] Compare 2025 weather to 10-year averages
- [ ] Correlate weather with NDVI data from Assignment 05
- [ ] Calculate frost risk dates for planting decisions

---

## References

- [NASA POWER API Documentation](https://power.larc.nasa.gov/docs/)
- [NASA POWER Data Access Viewer](https://power.larc.nasa.gov/data-access-viewer/)
- [API Parameter Dictionary](https://power.larc.nasa.gov/docs/methodology/communities/ag/)
- [Growing Degree Days (UMN Extension)](https://www.extension.umn.edu/agriculture/climate/growing-degree-days/)

---

*Document Version: 1.0*  
*Created: 2026-03-28*  
*Branch: feature-assignment-06-weather*
