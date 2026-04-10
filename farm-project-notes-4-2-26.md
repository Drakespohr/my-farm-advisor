# Farm Project Notes - April 2, 2026

## Current State

### Dashboard Created
- **File:** `farm-dashboard-july-report.html` (33KB)
- **Location:** `/workspaces/my-farm-advisor/farm-dashboard-july-report.html`
- **Purpose:** Offline farm dashboard for July 2025 data

### Data Status

| Data Type | Fields | Source |
|-----------|--------|--------|
| July 2025 NDVI | 11 fields (001-011) | Downloaded from Planetary Computer |
| Historical NDVI | 9 fields (012-020) | Not yet downloaded |
| Yield | All 20 fields | Uses baseline data (NOT July 2025) |
| Weather | All 20 fields | Growing season 2025 |

### Files for Reference
- Dashboard: `/workspaces/my-farm-advisor/farm-dashboard-july-report.html`
- July NDVI: `/workspaces/my-farm-advisor/data/ndvi_july_stats.csv`
- Baseline: `/workspaces/my-farm-advisor/data/farm_20fields.csv`

---

## Next Task: Recalculate Yield Using July 2025 NDVI

### Problem
- Current yield values in dashboard use baseline NDVI (old March/October values)
- Need to recalculate using July 2025 NDVI for fields 001-011
- Fields 012-020 should keep baseline yield (no July NDVI yet)

### Formula to Use
Normalize July NDVI to yield range (60-155 bu/acre):

```
Normalized NDVI = (July NDVI - 0.564) / (0.707 - 0.564)
New Yield = 60 + (Normalized NDVI × 95)
```

Where:
- Min July NDVI = 0.564 (fields 006-011)
- Max July NDVI = 0.707 (field_003)

### Steps
1. Read `ndvi_july_stats.csv` for July 2025 NDVI values
2. Read `farm_20fields.csv` for baseline yields and acres
3. Calculate new yield for fields 001-011 using formula
4. Calculate new predicted bushels (yield × acres)
5. Keep baseline yield for fields 012-020
6. Update dashboard HTML with new values

### Fields with July NDVI (need recalculation)
- field_001: 0.658
- field_002: 0.647
- field_003: 0.707
- field_004: 0.628
- field_005: 0.632
- field_006: 0.564
- field_007: 0.564
- field_008: 0.564
- field_009: 0.567
- field_010: 0.567
- field_011: 0.567

### Fields Without July NDVI (keep baseline)
- field_012 through field_020

---

## To Resume Tomorrow

Prompt: "Read the farm project notes and recalculate yield using July 2025 NDVI values for the dashboard"