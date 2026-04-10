# Recovery Plan: Fix farm-dashboard-july-report-fixed.html

## Problem
- File is 45MB (97.7% is embedded base64 image data in NDVI_MAPS object)
- Any edit attempt causes opencode to crash due to memory/process limits
- Need to make the file editable while keeping offline functionality

## Solution
Externalize the NDVI_MAPS data to a separate JS file, then update HTML to load it.

## Files Involved
| File | Size | Action |
|------|------|--------|
| `farm-dashboard-july-report-fixed.html` | 45MB | Trim down by removing NDVI_MAPS |
| `ndvi-maps.js` | ~44MB | NEW - extract NDVI_MAPS here |

## Step-by-Step Execution

### Step 1: Create ndvi-maps.js (Extract NDVI_MAPS)
Use Python to stream-extract the NDVI_MAPS object from HTML.

### Step 2: Update HTML to load external JS
Remove the NDVI_MAPS block from HTML (~44MB), add script tag.

### Step 3: Verify
- Open HTML in browser
- Click a field to verify NDVI maps load correctly

## Recovery Checkpoints

### Checkpoint A: After Step 1
- [ ] `ndvi-maps.js` created (~44MB)
- [ ] File contains valid JS: `const NDVI_MAPS = { "field_001": "iVBOR...", ... };`

### Checkpoint B: After Step 2
- [ ] HTML file is ~1MB
- [ ] HTML contains `<script src="ndvi-maps.js"></script>`
- [ ] HTML no longer contains inline NDVI_MAPS definition

## If Crash Happens Again

### Scenario: Crash during extraction (Step 1)
**Recovery**: Continue from where you left off
```bash
ls -la ndvi-maps.js
# If exists and ~44MB, skip to Step 2
# If partial/corrupted, delete and restart
```

### Scenario: Crash during HTML edit (Step 2)
**Recovery**: Original file is unchanged, retry Step 2

### Scenario: Both files corrupted
**Recovery**: `git checkout -- farm-dashboard-july-report-fixed.html`