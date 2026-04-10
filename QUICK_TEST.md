# QUICK VERIFICATION: NDVI Alert Feature

## If you see ANY of these, the feature is working:

### ✅ SIGN 1: Console Messages
Open DevTools (F12) → Console tab, look for:
```
🔧 farm-dashboard.ts module loaded
🚜 renderFarmDashboard called with: { selectedFieldId: null, selectedSoilType: null }
🚜 Farm summary: { avgNdvi: 0.35945, ... }
```

### ✅ SIGN 2: On-Screen Alert Banner
On the Farm tab, look for a yellow warning box that says:
```
Alert: low vegetation index detected. Recommend immediate scouting for nitrogen deficiency or pest pressure.
```

## If you see NEITHER:

### 🔧 TROUBLESHOOTING STEPS:

1. **ARE YOU ON THE FARM TAB?**
   - Left sidebar → click the 🌿 "Farm" icon (leaf symbol)
   - Header should say "Farm Overview"

2. **HARD REFRESH THE PAGE**
   - Windows/Linux: **Ctrl + Shift + R**
   - Mac: **Cmd + Shift + R**

3. **CHECK FOR ERRORS**
   - In Console tab (F12), look for RED error messages
   - If you see errors, share them here

4. **STILL NOTHING?**
   - Take a screenshot showing:
     - Browser tab with Farm Overview visible
     - Open Console tab (F12)
     - Any visible content on page

## WHAT SHOULD HAPPEN:
Since ALL fields have NDVI < 0.5 (average = 0.359), you **WILL ALWAYS SEE THE ALERT** on the Farm tab regardless of your field/soil selections.

The feature implementation is complete and correct in:
`/workspaces/my-farm-advisor/ui/src/ui/views/farm-dashboard.ts`