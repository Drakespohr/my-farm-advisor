# Quick Test for NDVI Alert Feature

Follow these exact steps to verify the alert is working:

## Step 1: Navigate to Farm Tab
1. Look at the left sidebar navigation
2. Find the **leaf icon** (🌿) labeled **"Farm"** 
3. **Click on it** to switch to the Farm dashboard

## Step 2: Open Browser Console
1. Press **F12** or **Right-click → Inspect**
2. Go to the **Console** tab
3. Keep this open while testing

## Step 3: Look for These Signs of Life

### A. Module Load Confirmation
In the console, you should see:
```
🔧 farm-dashboard.ts module loaded
```

### B. Function Call Logs
When you click the Farm tab, you should see:
```
🚜 renderFarmDashboard called with: { selectedFieldId: null, selectedSoilType: null }
🚜 Farm summary: { totalAcres: 1137.47, avgNdvi: 0.35945, totalBushels: 148634.56, fieldCount: 20 }
```

### C. Visual Alert on Screen
You should see a yellow warning banner near the top that says:
```
Alert: low vegetation index detected. Recommend immediate scouting for nitrogen deficiency or pest pressure.
```

## Step 4: If You Don't See Anything

### Check for Errors
Look in the console for any red error messages. Common issues:
- "Failed to load module" 
- "Cannot get property 'avgNdvi' of undefined"
- "getFarmSummary is not a function"

### Hard Refresh
Sometimes browsers cache old JavaScript:
- **Windows/Linux**: Press **Ctrl + Shift + R**
- **Mac**: Press **Cmd + Shift + R**

### Verify You're on the Right Tab
The Farm tab should show:
- Header: "Farm Overview"
- Filters: "Field ID" and "Soil Type" dropdowns
- Three KPI cards: "Average NDVI", "Total Acreage", "Predicted Bushels"

## Step 5: Test Different Selections (Optional)
Once you see the alert working with "All" selected:
1. Try selecting "field_001" from the Field ID dropdown
2. Observe that alert still shows (NDVI = 0.121)
3. Try selecting "field_006" (highest NDVI at 0.475)
4. Alert should still show since 0.475 < 0.5

## Expected Result
Since **ALL fields in the dataset have NDVI < 0.5**, you should **ALWAYS see the alert** regardless of your field/soil selections.

If you've done all this and still don't see the alert, please:
1. Take a screenshot of your browser console
2. Note what tab you're viewing
3. Tell me exactly what you see on screen