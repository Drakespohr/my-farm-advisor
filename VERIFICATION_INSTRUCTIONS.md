# Verification Steps for NDVI Alert Feature

## To Verify the Feature is Working:

### 1. Navigate to the Farm Tab
- In the left sidebar, look for the "leaf" icon (🌿) labeled "Farm"
- Click on it to switch to the Farm dashboard tab

### 2. Check for Visual Indicators (if debugging is enabled)
You should see ONE of these:
- **Red fixed banner** at top-left: "🚜 FARM DASHBOARD ACTIVE - Checking NDVI..."
- **Yellow banner** below the header: "🚜 FARM DASHBOARD RENDERING - Field: [value], Soil: [value]"
- **Actual alert banner** (if NDVI < 0.5): 
  ```
  🚨 ALERT: LOW VEGETATION INDEX DETECTED
  Recommend immediate scouting for nitrogen deficiency or pest pressure.
  Current average NDVI: 0.359 (threshold: 0.5)
  ```

### 3. Check Browser Console for Logs
Open browser developer tools (F12) → Console tab and look for:
- `🔧 farm-dashboard.ts module loaded` (on module load)
- `🚜 renderFarmDashboard called with: { selectedFieldId: null, selectedSoilType: null }` 
- `🚜 Farm summary: { totalAcres: 1137.47, avgNdvi: 0.35945, ... }`

### 4. Expected Behavior Based on Data
- **Overall average NDVI**: 0.35945 (below 0.5 threshold) → **Alert should SHOW**
- **Any single field selection**: All fields have NDVI < 0.5 → **Alert should SHOW**
- **Only way to NOT see alert**: If somehow average NDVI ≥ 0.5 (not possible with current data)

### 5. To Test Different Selections
Use the dropdown filters above the KPI cards:
- **Field ID**: Try selecting individual fields like "field_001" (NDVI: 0.121)
- **Soil Type**: Try "Silt loam" or "All"
- Observe that alert persists since all combinations result in avg NDVI < 0.5

## Technical Details
- **File modified**: `/workspaces/my-farm-advisor/ui/src/ui/views/farm-dashboard.ts`
- **Function**: `renderNdviAlert(avgNdvi: number)`
- **Condition**: `if (avgNdvi < 0.5)` 
- **Alert Message**: "Alert: low vegetation index detected. Recommend immediate scouting for nitrogen deficiency or pest pressure."

## Troubleshooting
If you still don't see the alert:
1. **Confirm you're on the Farm tab** (leaf icon)
2. **Check for JavaScript errors** in browser console
3. **Hard refresh the page** (Ctrl+Shift+R or Cmd+Shift+R)
4. **Verify the file was saved correctly** - the implementation is in place