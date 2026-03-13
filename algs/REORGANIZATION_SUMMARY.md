# Load Sample Points - Code Reorganization Summary

## Overview
The `load_sample_points.py` algorithm has been completely reorganized to follow your new workflow structure with three main stages: Input, Processing, and Output.

---

## New Workflow Architecture

### Stage 1: User Input Dialog (NEW)
**Class:** `LoadSamplePointsInputDialog`

The dialog prompts users for:
1. **Input Points Layer** - Vector layer with point geometries for sampling
2. **Terrain/DEM Raster** - Raster layer used as terrain data source
3. **Grid Layers Detection** - Auto-detects loaded grid layers with d/h/v patterns
   - If grid layers are found, displays them in a table with type and status
   - If not found, provides "Select Grid Layers Manually" button

**Key Features:**
- Validates all required inputs before proceeding
- Shows layer names in a table format with type detection (d=Depth, h=Level, v=Velocity)
- Auto-detects grid layers from currently loaded raster layers
- Allows manual grid layer selection (extensible)

---

### Stage 2: Processing Grid Layers (REORGANIZED)
**Main Function:** `processAlgorithm()` in `LoadSamplePointsAlgorithm`

#### Step 2A: Extract Scenario Information
**Function:** `extract_scenario_base_from_grid_layer(grid_layer_name)`

- Extracts base scenario name from grid layer name using regex pattern
- Identifies grid type (d, h, or v) from the layer name
- Example: 
  - Input: `"EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max"`
  - Output: `("EX_100YR_CC_24hr_MHWS10+1m_Baseline_003", "d")`

#### Step 2B: Find Corresponding d/h/v Rasters
**Function:** `find_corresponding_rasters(grid_layer_name, raster_dir, base_name, grid_type)`

- For each grid layer, finds corresponding d (Depth), h (Level), v (Velocity) rasters
- Uses grid layer name as template and replaces the type suffix
- Returns dict: `{'Depth': path, 'Level': path, 'Velocity': path}`
- Handles missing files gracefully (sets to None)

#### Step 2C: Sample Raster Values at Points
**Function:** `sample_rasters_at_points(input_points_layer, raster_map, terrain_layer, feedback)`

- Samples raster values at each point location from input points layer
- Creates output layer with fields: `[ID, X, Y, Terrain, Depth, Level, Velocity]`
- Field order: Terrain first, then d/h/v values
- Batch processing (500 features at a time) for memory efficiency
- Returns empty fields if source raster is missing

#### Step 2D: Save to Shapefile
**Function:** `save_layer_to_shapefile(layer, output_path, feedback)`

- Saves sampled points layer to shapefile
- Output path: `<scenario_base>_PLOT_P_Sampled.shp`
- Saves in same directory as PO line (if available) or raster directory
- Cleans up existing files before writing

---

### Stage 3: Load into QGIS (IMPROVED)
**Location:** End of `processAlgorithm()`

**New Features:**
1. **Smart Group Placement**
   - Creates "Sample Points" group
   - If a layer is currently selected:
     - If in a group: inserts "Sample Points" group at the top of that group
     - If not in group: adds to root at position 0
   - Uses `insert_group(0, ...)` for correct placement above current layer

2. **Layer Loading**
   - Loads each PLOT_P_Sampled.shp file
   - Adds to "Sample Points" group with `insertLayer(0, layer)`
   - Applies style using StyleManager
   - Includes proper error handling for missing files

---

## Key Helper Functions

### `extract_scenario_base_from_grid_layer(grid_layer_name)`
```
Regex: r'^(.+?)_([dhv])(?:_|\.|\Z)'
Matches: layer_d_*, layer_h_*, layer_v_*
Returns: (base_name, type_character)
```

### `find_corresponding_rasters(grid_layer_name, raster_dir, base_name, grid_type)`
- Replaces type suffix in grid layer name to create search pattern
- Uses glob to find matching raster files
- Returns dictionary with all three types (some may be None)

---

## Data Flow

```
User Dialog Input
    ↓
├─ Input Points Layer
├─ Terrain Layer (DEM)
└─ Grid Layers List (auto-detected or manual)
    ↓
For Each Grid Layer:
    ├─ Extract base name & type (d/h/v)
    ├─ Find corresponding d/h/v rasters in directory
    ├─ Sample values at each input point
    ├─ Create output layer with fields [ID, X, Y, Terrain, Depth, Level, Velocity]
    └─ Save as PLOT_P_Sampled.shp
    ↓
Load All Generated Files
    ├─ Create "Sample Points" group (above current layer)
    ├─ Load each shapefile into group
    └─ Apply styling
```

---

## Field Structure (Output Shapefile)

| Field | Type | Description |
|-------|------|-------------|
| ID | Integer | Point ID (sequential from 1) |
| X | Double | X coordinate |
| Y | Double | Y coordinate |
| Terrain | Double | Elevation from DEM raster (can be NULL) |
| Depth | Double | Depth from d raster (can be NULL) |
| Level | Double | Water level from h raster (can be NULL) |
| Velocity | Double | Velocity from v raster (can be NULL) |

---

## Error Handling

1. **Input Validation**
   - Dialog validates all required inputs
   - Shows user-friendly error messages in dialog

2. **Processing**
   - Logs all found/missing rasters
   - Continues if individual rasters missing (fills field with NULL)
   - Stops processing scenario only if sampling fails

3. **File Operations**
   - Checks file existence before loading
   - Removes old files before writing
   - Handles directory creation automatically

4. **Memory Management**
   - Batch processing for large point sets
   - Explicit garbage collection after major operations
   - Layer cleanup to prevent QGIS crashes

---

## Logging Output

The algorithm provides detailed feedback through Processing feedback panel:

```
[1/N] Processing: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max
  Base name: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003
  Grid type: d (Depth)
  → Finding d/h/v rasters in: results/003
    ✓ Depth: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max.tif
    ✓ Level: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_h_HR_Max.tif
    ✓ Velocity: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_v_HR_Max.tif
  → Sampling raster values at 150 points...
    Sampled 150 points
  → Saving to: f:/path/to/EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_PLOT_P_Sampled.shp
  ✓ Saved to: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_PLOT_P_Sampled.shp
  ✓ Successfully generated: EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_PLOT_P_Sampled
```

---

## Changes Summary

| Aspect | Before | After |
|--------|--------|-------|
| Input Method | Global variables + preview dialog | User dialog with auto-detection |
| Grid Layer Detection | Derived from global latest_files | Auto-detected from loaded layers |
| Raster Finding | Generic d/h/v search in directory | Specific search using grid layer name |
| Output Location | Derived from PO line path | PO line path OR raster directory |
| Group Placement | Always at root level | Smart placement (above current layer or within group) |
| Field Order | [ID, X, Y, Level, Depth, Velocity, Terrain] | [ID, X, Y, Terrain, Depth, Level, Velocity] |
| Regex Patterns | Not used | Used for name extraction |
| Error Messages | Processing feedback | Dialog + Processing feedback |

---

## Files Modified

- **File:** `load_sample_points.py`
- **Lines:** 657 total (significantly reorganized)
- **New Classes:** `LoadSamplePointsInputDialog`
- **New Functions:** 
  - `extract_scenario_base_from_grid_layer()`
  - `find_corresponding_rasters()` (replaces `find_dhv_rasters()`)
- **Removed Classes:** `PreviewDialog`
- **Removed Functions:** `find_dhv_rasters()`

