# Quick Reference - Load Sample Points

## 🎯 Three-Stage Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1: USER INPUT                                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Dialog appears when algorithm runs                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Load Sample Points - Input Parameters                  │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │                                                         │   │
│  │ 1. Select input points layer (for sampling):          │   │
│  │    [Dropdown: loaded Point layers ▼]                  │   │
│  │                                                         │   │
│  │ 2. Select DEM/Terrain raster layer:                   │   │
│  │    [Dropdown: loaded Raster layers ▼]                 │   │
│  │                                                         │   │
│  │ 3. Grid layers (d/h/v rasters):                       │   │
│  │    [Table: Auto-detected grid layers]                 │   │
│  │    ┌────────────────┬───────┬────────┐                │   │
│  │    │ Layer Name     │ Type  │ Status │                │   │
│  │    ├────────────────┼───────┼────────┤                │   │
│  │    │ scenario_d_Max │ Depth │ Loaded │                │   │
│  │    │ scenario_h_Max │ Level │ Loaded │                │   │
│  │    │ scenario_v_Max │ Vel   │ Loaded │                │   │
│  │    └────────────────┴───────┴────────┘                │   │
│  │                                                         │   │
│  │    [Select Grid Layers Manually]                      │   │
│  │                                                         │   │
│  │                    [Cancel] [Load Sample Points]      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2: PROCESSING                                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  For each grid layer:                                          │
│                                                                 │
│  [1/3] Processing: scenario_d_HR_Max                          │
│    Base name: scenario                                         │
│    Grid type: d (Depth)                                        │
│    → Finding d/h/v rasters in: results                        │
│      ✓ Depth: scenario_d_HR_Max.tif                           │
│      ✓ Level: scenario_h_HR_Max.tif                           │
│      ✓ Velocity: scenario_v_HR_Max.tif                        │
│    → Sampling raster values at 150 points...                  │
│      Sampled 150 points                                        │
│    → Saving to: f:/path/scenario_PLOT_P_Sampled.shp          │
│    ✓ Successfully generated: scenario_PLOT_P_Sampled         │
│                                                                 │
│  [Repeat for each grid layer...]                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3: LOAD INTO QGIS                                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Layers Panel:                                                  │
│                                                                 │
│  ▼ Map (Layers)                                                 │
│    ▼ Sample Points  ← NEW GROUP (above current layer)         │
│      ✓ scenario_1_PLOT_P_Sampled                              │
│      ✓ scenario_2_PLOT_P_Sampled                              │
│      ✓ scenario_3_PLOT_P_Sampled                              │
│    ▼ [Previously selected layer]                              │
│      ...                                                        │
│                                                                 │
│  Result: All PLOT_P_Sampled layers loaded and styled          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 Field Order in Output Shapefile

```
Position  Field       Type      Description
────────────────────────────────────────────────────────────────
1         ID          Integer   Point identifier (sequential)
2         X           Double    X coordinate (Easting)
3         Y           Double    Y coordinate (Northing)
4         Terrain     Double    Elevation from DEM
5         Depth       Double    Water depth (from d raster)
6         Level       Double    Water level (from h raster)
7         Velocity    Double    Flow velocity (from v raster)

NULL Values:
├─ Depth = NULL if d raster not found
├─ Level = NULL if h raster not found
└─ Velocity = NULL if v raster not found
  (Terrain never NULL - always sampled from user-provided DEM)
```

---

## 🔄 Name Extraction Logic

```
Grid Layer Name (as shown in QGIS):
    ↓
"EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max"
    ↓
    Regex: r'^(.+?)_([dhv])(?:_|\.|\Z)'
    ↓
    Captures:
    ├─ Group 1: "EX_100YR_CC_24hr_MHWS10+1m_Baseline_003"
    └─ Group 2: "d"
    ↓
Returns:
    ├─ base_name = "EX_100YR_CC_24hr_MHWS10+1m_Baseline_003"
    └─ grid_type = "d"
```

---

## 🔍 Raster Matching Algorithm

```
Starting with:
├─ grid_layer_name = "EX_..._003_d_HR_Max.tif"
├─ grid_type = "d"
├─ raster_dir = "f:/project/results/003/"
└─ base_name = "EX_..._003"

FOR EACH type IN [d, h, v]:
├─ Replace type in name:
│  "EX_..._003_d_HR_Max.tif" → "EX_..._003_h_HR_Max*.tif"
│  (search pattern)
│
├─ Glob search in directory:
│  glob("f:/project/results/003/EX_..._003_h_HR_Max*.tif")
│
└─ Result:
   [Found] → Add to raster_map['Level']
   [Not found] → raster_map['Level'] = None

Returns:
├─ raster_map['Depth'] = "f:/project/results/003/EX_..._003_d_HR_Max.tif"
├─ raster_map['Level'] = "f:/project/results/003/EX_..._003_h_HR_Max.tif"
└─ raster_map['Velocity'] = None (not found)
```

---

## 🎯 Auto-Detection Criteria

```
Grid Layer Detection:
────────────────────────────────────────────────────────

Loaded Layers:
├─ "scenario_001_d_HR_Max"          ✓ DETECTED (has "_d_")
├─ "scenario_001_h_HR_Max"          ✓ DETECTED (has "_h_")
├─ "scenario_001_v_HR_Max"          ✓ DETECTED (has "_v_")
├─ "DEM"                            ✗ NOT (no d/h/v)
├─ "Terrain_Survey"                 ✗ NOT (no d/h/v)
├─ "depth_data.tif"                 ✗ NOT (needs underscore)
├─ "model_d_output"                 ✓ DETECTED (has "_d_")
├─ "hydraulic_hmax"                 ✗ NOT (needs underscore)
└─ "velocity_v_results"             ✓ DETECTED (has "_v_")

Detected Regex Pattern:
r'^(.+?)_([dhv])(?:_|\.|\Z)'
    ↑      ↑      ↑
    |      |      └─ followed by: underscore, dot, or end
    |      └────────── capture d, h, or v
    └───────────────── capture everything before
```

---

## 💾 Output File Structure

```
Original Data:
├─ Input Points Layer: "Survey_Points.shp"
│  └─ Geometry: Points (150 features)
│
├─ Terrain Layer: "DEM.tif"
│  └─ Raster: elevation data
│
└─ Grid Layers: 
   ├─ "Scenario_d_Max.tif" (depth)
   ├─ "Scenario_h_Max.tif" (level)
   └─ "Scenario_v_Max.tif" (velocity)

Processing:
├─ For each point in Survey_Points
├─ Sample Scenario_d_Max → value_d
├─ Sample Scenario_h_Max → value_h
├─ Sample Scenario_v_Max → value_v
├─ Sample DEM → value_terrain
└─ Create feature with [ID, X, Y, Terrain, Depth, Level, Velocity]

Output:
├─ File: "f:/path/to/Scenario_PLOT_P_Sampled.shp"
├─ Geometry: Points (150 features, same as input)
└─ Fields:
   ├─ ID: 1, 2, 3, ..., 150
   ├─ X: coordinate values
   ├─ Y: coordinate values
   ├─ Terrain: sampled DEM values
   ├─ Depth: sampled d_Max values
   ├─ Level: sampled h_Max values
   └─ Velocity: sampled v_Max values
```

---

## 🛠️ Error Handling Flowchart

```
User Inputs Valid?
├─ YES ↓
│  └─ Grid layers found?
│     ├─ YES ↓
│     │  └─ Processing starts
│     └─ NO ↓
│        └─ Show error: "No grid layers detected"
│           Return to dialog
│
└─ NO ↓
   └─ Show error in dialog
      (e.g., "Select input points layer")
      Return to dialog for correction

During Processing:
├─ Grid layer directory found?
│  ├─ YES ↓
│  │  └─ Search for d/h/v rasters
│  └─ NO ↓
│     └─ Log warning, skip layer
│
├─ Raster files found?
│  ├─ Depth found? → Sample it
│  ├─ Depth not found? → Field = NULL (continue)
│  ├─ Level found? → Sample it
│  ├─ Level not found? → Field = NULL (continue)
│  └─ [same for Velocity]
│
├─ Sampling successful?
│  ├─ YES ↓
│  │  └─ Save shapefile
│  └─ NO ↓
│     └─ Report error, skip layer
│
└─ File save successful?
   ├─ YES ↓
   │  └─ Add to loading queue
   └─ NO ↓
      └─ Report error, skip file

After Processing:
├─ Any files generated?
│  ├─ YES ↓
│  │  └─ Load into QGIS
│  └─ NO ↓
│     └─ Show message: "No files generated"
```

---

## 🎨 Color/Status Legend

```
In Processing Output:
─────────────────────
✓ = Success (green)
✗ = Error/Failure (red)  
⚠ = Warning (yellow)
→ = Processing step (neutral)

In Grid Table:
──────────────
Status: "Loaded" (green text)
Type: "Depth" | "Level" | "Velocity"
Status: "Loaded" or "Not Found"
```

---

## 📊 Processing Output Example

```
======================================================================
LOAD SAMPLE POINTS - Starting Process
======================================================================

STEP 1: Getting user input parameters...
  Input points: Survey_Points                    [from dialog]
  Terrain layer: DEM_LiDAR                       [from dialog]
  Grid layers to process: 3                      [auto-detected]

======================================================================
STEP 2: Processing grid layers and generating sample point files...
======================================================================

[1/3] Processing: Event_001_d_HR_Max
  Base name: Event_001
  Grid type: d (Depth)
  → Finding d/h/v rasters in: results/001
    ✓ Depth: Event_001_d_HR_Max.tif             [FOUND]
    ✓ Level: Event_001_h_HR_Max.tif             [FOUND]
    ✓ Velocity: Event_001_v_HR_Max.tif          [FOUND]
  → Sampling raster values at 150 points...
    Sampled 150 points                           [SUCCESS]
  → Saving to: f:/project/Event_001_PLOT_P_Sampled.shp
  ✓ Saved to: Event_001_PLOT_P_Sampled.shp     [SUCCESS]
  ✓ Successfully generated: Event_001_PLOT_P_Sampled

[2/3] Processing: Event_002_h_HR_Max
  Base name: Event_002
  Grid type: h (Level)
  → Finding d/h/v rasters in: results/002
    ✓ Depth: Event_002_d_HR_Max.tif             [FOUND]
    ✓ Level: Event_002_h_HR_Max.tif             [FOUND]
    ⚠ Velocity: NOT FOUND                       [MISSING - field will be empty]
  → Sampling raster values at 150 points...
    Sampled 150 points                           [SUCCESS]
  → Saving to: f:/project/Event_002_PLOT_P_Sampled.shp
  ✓ Saved to: Event_002_PLOT_P_Sampled.shp     [SUCCESS]
  ✓ Successfully generated: Event_002_PLOT_P_Sampled

[3/3] Processing: Event_003_v_HR_Max
  Base name: Event_003
  Grid type: v (Velocity)
  → Finding d/h/v rasters in: results/003
    ✓ Depth: Event_003_d_HR_Max.tif             [FOUND]
    ✓ Level: Event_003_h_HR_Max.tif             [FOUND]
    ✓ Velocity: Event_003_v_HR_Max.tif          [FOUND]
  → Sampling raster values at 150 points...
    Sampled 150 points                           [SUCCESS]
  → Saving to: f:/project/Event_003_PLOT_P_Sampled.shp
  ✓ Saved to: Event_003_PLOT_P_Sampled.shp     [SUCCESS]
  ✓ Successfully generated: Event_003_PLOT_P_Sampled

✓ Generated 3/3 sample point files

======================================================================
STEP 3: Loading sample point layers into QGIS...
======================================================================

[1/3] Loading: Event_001_PLOT_P_Sampled
  ✓ Loaded
[2/3] Loading: Event_002_PLOT_P_Sampled
  ✓ Loaded
[3/3] Loading: Event_003_PLOT_P_Sampled
  ✓ Loaded

======================================================================
✓ Successfully generated and loaded 3 sample point layers
======================================================================
```

---

## 🚀 Quick Start

1. **Load Data** in QGIS
   - Points layer (Point geometry)
   - DEM raster
   - Grid layers (with d/h/v in names)

2. **Run Algorithm**
   - Processing → TUFLOW Tools → Load Sample Points

3. **Configure Dialog**
   - Select points layer
   - Select terrain layer
   - Review grid layers (auto-detected)
   - Click "Load Sample Points"

4. **Wait for Completion**
   - Watch progress in Processing panel
   - Check Layers panel for new "Sample Points" group

5. **View Results**
   - New shapefiles appear in "Sample Points" group
   - Fields: ID, X, Y, Terrain, Depth, Level, Velocity
   - Missing rasters result in NULL fields

