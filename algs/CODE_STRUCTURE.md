# Code Structure - TUFLOW Tools Algorithms

## Executive Summary

This document outlines the code structure for key algorithms in the TUFLOW Tools plugin.

### load_sample_points.py (Refactored)
The algorithm has been completely reorganized to implement a three-stage workflow:
1. **Input Stage** → User dialog for layer selection + auto-detection
2. **Processing Stage** → Grid layer analysis and point sampling  
3. **Output Stage** → Smart group placement and QGIS integration

### cross_section_alignment.py (New)
An interactive tool for viewing and exporting long sections and cross sections along an alignment.

---

## Function Organization

### New Helper Functions

#### `extract_scenario_base_from_grid_layer(grid_layer_name: str) → (str, str)`
**Location:** Lines 31-44

Extracts scenario base name and grid type from grid layer name using regex.

```python
# Example
"EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max"
↓
("EX_100YR_CC_24hr_MHWS10+1m_Baseline_003", "d")
```

**Regex Pattern:** `r'^(.+?)_([dhv])(?:_|\.|\Z)'`
- Captures everything before the type marker
- Captures the type marker (d/h/v)

---

#### `find_corresponding_rasters(grid_layer_name, raster_dir, base_name, grid_type) → dict`
**Location:** Lines 47-79

Finds corresponding d, h, v rasters for a given grid layer.

**Logic:**
1. Takes grid layer name (e.g., `layer_d_Max`)
2. Replaces type suffix with target type (d→h, d→v)
3. Uses glob to find matching files
4. Returns `{'Depth': path, 'Level': path, 'Velocity': path}`

**Example:**
```
Grid layer: "Scenario_003_d_HR_Max.tif"
↓ Replace _d_ with _h_
Search: "Scenario_003_h_HR_Max*.tif"
↓
Find: "Scenario_003_h_HR_Max.tif"
```

---

#### `sample_rasters_at_points(input_points_layer, raster_map, terrain_layer, feedback) → QgsVectorLayer`
**Location:** Lines 82-195

**Completely Reorganized Field Order:**
```
BEFORE: [ID, X, Y, Level, Depth, Velocity, Terrain]
AFTER:  [ID, X, Y, Terrain, Depth, Level, Velocity]
```

**Key Changes:**
- Terrain field moved to position 3 (right after X, Y)
- Follows logical order: Position → Terrain → Flood metrics
- Sample order: Terrain first, then d/h/v values

---

#### `save_layer_to_shapefile(layer, output_path, feedback) → (bool, str)`
**Location:** Lines 198-245

No logic changes, but used consistently throughout new workflow.

---

### New Dialog Class

#### `LoadSamplePointsInputDialog` 
**Location:** Lines 248-393

**Purpose:** Collect all input parameters before processing starts

**Components:**

1. **Points Layer Selection (Lines 293-294)**
   - Combo box populated with Point geometry layers
   - User selects which points to sample

2. **Terrain Layer Selection (Lines 296-297)**
   - Combo box populated with all Raster layers
   - User selects DEM/elevation source

3. **Grid Layers Auto-Detection (Lines 299-306)**
   - Table showing detected grid layers
   - Columns: Layer Name | Type (d/h/v) | Status
   - Auto-scans loaded layers for `_d_`, `_h_`, `_v_` patterns

4. **Manual Selection (Lines 308-309)**
   - Button for user to manually select grid layer files
   - Extensible for future enhancement

5. **Validation (Lines 377-392)**
   - `get_selected_layers()` method
   - Checks: Points layer selected, Terrain selected, Grid layers found
   - Shows user-friendly error dialogs

**Key Methods:**

- `__init__()` - Initialize UI and load layers
- `init_ui()` - Build dialog layout with three sections
- `load_available_layers()` - Populate combo boxes from QGIS project
- `detect_grid_layers()` - Find layers with d/h/v patterns using regex
- `update_grid_table()` - Display detected grid layers with type detection
- `select_grid_layers_manually()` - Extensible hook for manual selection
- `get_selected_layers()` - Validate and return selected layers

---

### Main Algorithm Class

#### `LoadSamplePointsAlgorithm`
**Location:** Lines 396-657

**Method Changes:**

##### `initAlgorithm(self, config=None)` → REMOVED
- Previously added parameters via QgsProcessingParameter
- Now parameters collected from user dialog instead
- Method body empty (comment: "Parameters set via dialog, not here")

##### `processAlgorithm(self, parameters, context, feedback) → dict`
**COMPLETELY REORGANIZED** (Lines 416-657)

**Three Distinct Steps (with clear separators):**

**STEP 1: Get User Input (Lines 425-449)**
- Show `LoadSamplePointsInputDialog`
- User selects: points layer, terrain layer, grid layers
- Validate selections
- Log selected parameters

```
Output variables:
- input_points_layer: QgsVectorLayer
- terrain_layer: QgsRasterLayer
- grid_layers: [(name, layer), ...]
```

**STEP 2: Process Grid Layers (Lines 452-597)**
- For each grid layer:
  1. Extract base name and type (d/h/v)
  2. Find corresponding d/h/v rasters in directory
  3. Sample raster values at input points
  4. Save to PLOT_P_Sampled.shp
- Track successful saves

```
Logic flow:
├─ Extract name components (regex)
├─ Get raster directory from grid layer
├─ Find d/h/v rasters (glob pattern matching)
├─ Sample at all points (batch processing)
├─ Save shapefile (cleanup + write)
└─ Collect output path for loading
```

**STEP 3: Load into QGIS (Lines 600-643)**
- Create "Sample Points" group
- Smart placement: 
  - If layer selected → above it
  - If in group → insert into that group
  - If not → add at root level 0
- Load each PLOT_P_Sampled.shp
- Apply styling

```
Group placement logic:
if current_layer_selected:
    if current_layer_in_group:
        insert_group(group, 0, "Sample Points")
    else:
        insert_group(root, 0, "Sample Points")
else:
    insert_group(root, 0, "Sample Points")
```

---

## Class Hierarchy

```
QgsProcessingAlgorithm
└─ LoadSamplePointsAlgorithm
   ├─ createInstance() → LoadSamplePointsAlgorithm
   ├─ name() → "load_sample_points"
   ├─ displayName() → "3 - Load Sample Points"
   ├─ processAlgorithm() → {} (main workflow)
   └─ [other metadata methods]

QDialog
└─ LoadSamplePointsInputDialog
   ├─ init_ui() → build dialog
   ├─ load_available_layers() → populate dropdowns
   ├─ detect_grid_layers() → scan for d/h/v patterns
   ├─ update_grid_table() → show detected layers
   ├─ select_grid_layers_manually() → extensible
   └─ get_selected_layers() → validate & return
```

---

## Data Flow Diagram

```
LoadSamplePointsAlgorithm.processAlgorithm()
├─ Show LoadSamplePointsInputDialog
├─ User selects:
│  ├─ input_points_layer (Point vector)
│  ├─ terrain_layer (Raster)
│  └─ grid_layers (list of detected rasters)
│
├─ FOR EACH grid_layer IN grid_layers:
│  ├─ extract_scenario_base_from_grid_layer(name)
│  │  └─ base_name, type_char (d/h/v)
│  │
│  ├─ find_corresponding_rasters(name, dir, base, type)
│  │  └─ raster_map = {Depth: path, Level: path, Velocity: path}
│  │
│  ├─ sample_rasters_at_points(points, raster_map, terrain)
│  │  └─ sample_layer = layer with [ID, X, Y, Terrain, Depth, Level, Velocity]
│  │
│  └─ save_layer_to_shapefile(sample_layer, output_path)
│     └─ PLOT_P_Sampled.shp
│
├─ Create "Sample Points" group
├─ Load all PLOT_P_Sampled.shp files into group
└─ Apply styling to all layers
```

---

## Import Changes

**Added:**
```python
import re  # For regex pattern matching in name extraction
```

**Added to QDialog imports:**
```python
QComboBox        # For layer selection dropdowns
QMessageBox      # For user-friendly error dialogs  
QFileDialog      # Future use for manual file selection
QAbstractItemView  # For table row selection
```

---

## Processing Output Format

**Console Output Example:**
```
======================================================================
LOAD SAMPLE POINTS - Starting Process
======================================================================

STEP 1: Getting user input parameters...
  Input points: Survey_Points
  Terrain layer: DEM_2023
  Grid layers to process: 3

======================================================================
STEP 2: Processing grid layers and generating sample point files...
======================================================================

[1/3] Processing: Scenario_001_d_HR_Max
  Base name: Scenario_001
  Grid type: d (Depth)
  → Finding d/h/v rasters in: results/001
    ✓ Depth: Scenario_001_d_HR_Max.tif
    ✓ Level: Scenario_001_h_HR_Max.tif
    ✓ Velocity: Scenario_001_v_HR_Max.tif
  → Sampling raster values at 150 points...
    Sampled 150 points
  → Saving to: /path/to/Scenario_001_PLOT_P_Sampled.shp
  ✓ Saved to: Scenario_001_PLOT_P_Sampled.shp
  ✓ Successfully generated: Scenario_001_PLOT_P_Sampled

[2/3] Processing: Scenario_002_h_HR_Max
  Base name: Scenario_002
  Grid type: h (Level)
  → Finding d/h/v rasters in: results/002
    ✓ Depth: Scenario_002_d_HR_Max.tif
    ✓ Level: Scenario_002_h_HR_Max.tif
    ⚠ Velocity: NOT FOUND (field will be empty)
  ...

✓ Generated 3/3 sample point files

======================================================================
STEP 3: Loading sample point layers into QGIS...
======================================================================

[1/3] Loading: Scenario_001_PLOT_P_Sampled
  ✓ Loaded
[2/3] Loading: Scenario_002_PLOT_P_Sampled
  ✓ Loaded
[3/3] Loading: Scenario_003_PLOT_P_Sampled
  ✓ Loaded

======================================================================
✓ Successfully generated and loaded 3 sample point layers
======================================================================
```

---

## Testing Checklist

- [ ] Dialog appears when algorithm runs
- [ ] Dialog correctly populates point layers combo (Point geometry only)
- [ ] Dialog correctly populates terrain layers combo (all rasters)
- [ ] Grid layers are auto-detected from loaded layers
- [ ] Grid layer table shows correct type (d/h/v) detection
- [ ] Manual selection button doesn't crash (currently shows placeholder)
- [ ] Cancel button exits without processing
- [ ] Load button validates all selections
- [ ] Processing shows progress in console
- [ ] Correct rasters are found and matched
- [ ] PLOT_P_Sampled.shp files created in correct location
- [ ] Shapefiles have correct fields: [ID, X, Y, Terrain, Depth, Level, Velocity]
- [ ] Files loaded into QGIS in "Sample Points" group
- [ ] Group placement is correct (above current layer)
- [ ] Missing rasters result in NULL fields (not errors)

---

## Backward Compatibility

**Breaking Changes:**
- Algorithm signature unchanged (still takes no parameters)
- Output field order changed (may break existing scripts)
- Removed dependency on global variable `tuflow_latest_raster_files`

---

## Module: cross_section_alignment.py

### Overview
An interactive tool (`CrossSectionAlignmentAlgorithm`) that provides side-by-side visualization of longitudinal profiles and cross-sections derived from raster layers.

### Class Hierarchy

```
QgsProcessingAlgorithm
└─ CrossSectionAlignmentAlgorithm
   └─ processAlgorithm() → Launches CrossSectionAlignmentDialog

QDialog
└─ CrossSectionAlignmentDialog
   ├─ init_ui() → Setup Matplotlib figures, toolbar, and layer table
   ├─ on_draw_alignment() → Activates CapturePolylineTool
   ├─ refresh_plots() → Updates Long Section and Cross Section plots
   ├─ on_plot_hover() → Handles dynamic cursor tracking
   └─ on_output_cross_sections() → Generates multi-page PDF report

QgsMapTool
└─ CapturePolylineTool
   └─ canvasPressEvent/MoveEvent → Handles interactive polyline drawing
```

### Key Components

#### Interactive Plotting
- **Library**: `matplotlib` embedded via `FigureCanvasQTAgg`.
- **Layout**: Split view (Long Section vs Cross Section) using `GridSpec` (3:2 ratio).
- **Interactivity**:
  - `motion_notify_event`: Tracks mouse over Long Section.
  - Updates `map_marker` (Red Cross) on QGIS Canvas.
  - Updates `cs_rubber_band` (Black Dash) on QGIS Canvas.
  - Updates Cross Section plot dynamically.

#### PDF Export
- **Function**: `on_output_cross_sections()`
- **Features**:
  - Page 1: Long Section Profile.
  - Page 2+: 3x2 grid of Cross Sections at user-defined intervals.
  - Uses `matplotlib.backends.backend_pdf.PdfPages`.

#### Map Tools
- **CapturePolylineTool**: Custom `QgsMapTool` for drawing alignments.
- **Visual Feedback**:
  - `alignment_rubber_band`: Persistent alignment line (Magenta).
  - `arrow_rubber_band`: Directional arrow at end of alignment.

**Migration for Users:**
- No migration needed - completely new user interface
- Old workflow no longer supported
