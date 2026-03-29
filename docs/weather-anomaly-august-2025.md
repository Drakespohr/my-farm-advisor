# Weather Anomaly: Extreme Precipitation Event - Early August 2025

## Overview

This analysis identifies and visualizes the most significant weather anomaly during the 2025 growing season for the Nebraska agricultural fields. The anomaly was an extreme precipitation event in early August 2025.

---

## The Anomaly

**Early August 2025** experienced an extreme precipitation event with **3+ times normal rainfall**.

### Week-by-Week Breakdown

| Date | 2025 Precipitation | Historical Average | Ratio |
|------|---------------------|---------------------|-------|
| Aug 2, 2025 | 1.69 in | 0.55 in | **3.1x** |
| Aug 3, 2025 | 1.77 in | 0.57 in | **3.1x** |
| Aug 4, 2025 | 1.90 in | 0.56 in | **3.4x** |
| Aug 5, 2025 | 1.86 in | 0.56 in | **3.3x** |

### Statistical Significance

| Metric | Value |
|--------|-------|
| Total 4-day precipitation | 7.22 inches |
| Historical average for same period | 2.24 inches |
| Excess rainfall | 4.98 inches |
| **Percentage above normal** | **222%** |

This was a **1-in-10+ year event** based on the 25-year historical data (2000-2024).

---

## Extended Analysis: Wettest Period of 2025

The anomaly was part of an extended wet period from late June through early August:

### Weekly Precipitation Ratios

| Period | 2025 | Historical | Ratio |
|--------|------|------------|--------|
| June 25-29 | 2.32-2.68 in | 0.89-1.19 in | 2.0-3.0x |
| July 9-13 | 1.79-2.39 in | 0.72-0.85 in | 2.4-2.9x |
| **Aug 2-5** | **1.69-1.90 in** | **0.55-0.57 in** | **3.1-3.4x** |
| Aug 9-10 | 1.60-2.14 in | 0.74-0.76 in | 2.1-2.9x |

---

## Context and Impact

### Weather Pattern

The extreme precipitation event was likely caused by:
- Persistent moisture-laden air masses from the Gulf of Mexico
- Frontal boundaries stationary over the region
- Above-average atmospheric instability

### Agricultural Impact

**Positive impacts:**
- Recharged soil moisture profile
- Reduced irrigation needs
- Beneficial for late-season crop development

**Potential concerns:**
- Possible nitrogen leaching from soils
- Field work delays
- Disease pressure in wet conditions

---

## Visualization

![Precipitation Anomaly Chart](../data/weather_anomaly_precipitation.png)

The chart above highlights:
- **Red bars**: Extreme anomaly (3x+ normal)
- **Orange bars**: High precipitation (2-3x normal)
- **Yellow bars**: Above normal (1.5-2x normal)
- **Green bars**: Normal or slightly above
- **Blue bars**: Below normal

---

## Comparison to Other Anomalies

### Temperature Anomalies (For Reference)

While precipitation was the most significant anomaly, temperature also showed notable patterns:

| Anomaly Type | Date | Value | Deviation |
|-------------|------|--------|-----------|
| Coolest high temp | Aug 5 | 79.4°F | -11.6°F below normal |
| Warmest high temp | June 25 | 93.9°F | +8.0°F above normal |
| Coolest low temp | June 9 | 55.9°F | -4.6°F below normal |

---

## Data Source

- **Provider**: NASA POWER (Prediction of Worldwide Energy Resources)
- **API**: https://power.larc.nasa.gov/api/temporal/daily/point
- **Parameters**: PRECTOTCORR (bias-corrected precipitation)
- **Historical baseline**: 25-year average (2000-2024)

---

## Methodology

1. Downloaded daily precipitation data for 2025 growing season (June 1 - August 31)
2. Calculated weekly rolling averages for both 2025 and historical data
3. Computed precipitation ratios (2025 / historical) for each week
4. Identified weeks with ratio > 2x as significant anomalies
5. Validated against historical records to confirm statistical significance

---

## Conclusion

The early August 2025 precipitation anomaly was the most significant weather event of the growing season. With precipitation levels exceeding 3 times the historical average, this represents a rare meteorological event that would have had substantial impacts on agricultural operations in the region.

---

*Document Created: 2026-03-28*
*Branch: feature-assignment-06-weather*
