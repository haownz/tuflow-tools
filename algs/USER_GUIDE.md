# Load Sample Points - User Guide

## Overview
This algorithm samples raster values (Depth, Level, Velocity, and Terrain) at point locations from a user-provided points vector layer, using grid layer d/h/v rasters as data sources.

## Step-by-Step Usage

### 1. Prepare Your Data in QGIS
Before running the algorithm:
- Load your **input points layer** (Point geometry) - these are the sample locations
- Load your **DEM raster** (for terrain/elevation data)
- Load your **grid layers** - raster layers containing d, h, and/or v variants
  - Examples: `EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max.tif`
  - The algorithm auto-detects layers with `_d_`, `_h_`, `_v_` in their names

### 2. Run the Algorithm
1. Open Processing Toolbox
2. Navigate to: **TUFLOW Tools** → **1 - Result Analysis** → **3 - Load Sample Points**
3. Click Run

### 3. Configure Input Parameters (Dialog)

#### Section 1: Select Input Points Layer
- **What to do:** Choose the vector layer containing point locations where you want to sample raster values
- **Requirements:** Must be a Point geometry layer
- **Example:** "Sample_Points" or "Query_Points"

#### Section 2: Select DEM/Terrain Raster Layer
- **What to do:** Choose the raster layer to use as terrain/elevation data
- **Requirements:** Must be a raster layer
- **Example:** "DEM" or "Terrain_SRTM"

#### Section 3: Grid Layers
- **Auto-Detection:** The dialog automatically scans loaded raster layers and detects those with d/h/v patterns
- **Display:** Shows a table with:
  - Layer Name (full name)
  - Type (Depth, Level, or Velocity)
  - Status (Loaded)

**If grid layers are not detected:**
1. Ensure your grid layers are already loaded in QGIS
2. Ensure their names contain the pattern: `_d_`, `_h_`, or `_v_` (case-sensitive)
3. Click "Select Grid Layers Manually" to browse for files

### 4. Confirm and Run
1. Verify all three selections are correct
2. Click **"Load Sample Points"** button
3. Wait for processing to complete (progress shown in Processing panel)

---

## Output

### Generated Files
For each grid layer processed, the algorithm creates:
- **Filename:** `[SCENARIO_BASE]_PLOT_P_Sampled.shp`
- **Location:** Same directory as PO line shapefile (if found) or raster directory
- **Fields:** ID, X, Y, Terrain, Depth, Level, Velocity

### In QGIS
All generated shapefiles are automatically loaded into a new group:
- **Group Name:** "Sample Points"
- **Placement:** 
  - Above the currently selected layer (if any)
  - At top of current layer's parent group (if selected layer is in a group)
  - At root level (if no layer selected)

### Attribute Values
- Valid raster values are sampled at each point location
- If a raster file is missing, that field is left empty (NULL)
- Point ID values are sequential starting from 1

---

## Troubleshooting

### Grid Layers Not Detected
**Problem:** Table in Section 3 is empty
- **Solution:** Ensure your raster layers are loaded and have d/h/v in their names
- **Example names that work:** `scenario_003_d_HR_Max`, `test_v_grid`, `result_h_max`

### "Input points layer" dropdown is empty
**Problem:** Cannot select points layer
- **Solution:** Load a Point geometry vector layer first
- **Note:** Layers with other geometry types (Polygon, LineString) won't appear

### "Terrain layer" dropdown is empty
**Problem:** Cannot select DEM layer
- **Solution:** Load a raster layer first
- **Note:** Any raster layer is acceptable

### Algorithm runs but no output files created
**Problem:** Shapefiles not generated
- **Causes:**
  - One of the input parameters was not properly selected
  - Sampling failed (check Processing panel messages)
  - Output directory doesn't exist or lacks write permissions
- **Solution:** 
  - Check Processing panel for error messages
  - Verify input layers have valid data
  - Ensure output directories are writable

### Processing seems stuck
**Problem:** Algorithm running but making no progress
- **Cause:** Large number of points or large raster files
- **Solution:**
  - Wait longer (sampling rasters at thousands of points can take time)
  - Check system memory usage
  - Consider reducing input point count

---

## Technical Details

### Raster Type Detection
The algorithm identifies raster types from their filenames:
- **Depth raster:** Contains `_d_` in the name (letter 'd')
- **Level raster:** Contains `_h_` in the name (letter 'h')
- **Velocity raster:** Contains `_v_` in the name (letter 'v')

### Raster Matching
Once a grid layer is identified (e.g., with `_d_`), the algorithm:
1. Extracts the scenario base name: `EX_100YR_CC_24hr_MHWS10+1m_Baseline_003`
2. Searches for matching h and v variants in the same directory
3. Returns paths to all three types (or NULL if not found)

### Memory Management
- Batch processes large point sets (500 features at a time)
- Automatically cleans up layers after saving
- Uses garbage collection to prevent memory leaks

---

## Common Scenarios

### Scenario 1: Multiple flooding events
**Input:**
- Points: "Survey_Points" (200 point locations)
- Terrain: "DEM"
- Grids loaded: 
  - Event_A_d_Max, Event_A_h_Max, Event_A_v_Max
  - Event_B_d_Max, Event_B_h_Max, Event_B_v_Max

**Output:**
- Event_A_PLOT_P_Sampled.shp (200 features with sampled values)
- Event_B_PLOT_P_Sampled.shp (200 features with sampled values)
- Group "Sample Points" containing both layers

### Scenario 2: Only depth available
**Input:**
- Points: "Critical_Infrastructure" (50 points)
- Terrain: "LiDAR_DEM"
- Grids loaded: 
  - Scenario_003_d_Max (only depth available)

**Output:**
- Scenario_003_PLOT_P_Sampled.shp (50 features)
- Fields: Terrain and Depth populated; Level and Velocity are NULL
- Group "Sample Points" containing 1 layer

---

## Field Descriptions

| Field | Type | Description | When Empty |
|-------|------|-------------|-----------|
| ID | Integer | Point identifier (1, 2, 3, ...) | Never |
| X | Double | Point longitude/UTM X | Never |
| Y | Double | Point latitude/UTM Y | Never |
| Terrain | Double | DEM elevation at point location | Never (always sampled) |
| Depth | Double | Water depth from d raster | d raster file not found |
| Level | Double | Water surface level from h raster | h raster file not found |
| Velocity | Double | Flow velocity from v raster | v raster file not found |

