# Changelog

## Version 1.6.1 (2026-05-05)
### Improvements
- **Refined Style Auto-Apply**: Changed auto-apply style logic to only trigger once per layer using a persistent project property. This prevents manual style changes from being overwritten during project reloads or layer refreshes.
- **Algorithm Cleanup**: Removed the "Process Land Cover" algorithm as part of tool simplification, while retaining the "Land Cover Add Fields" utility.

## Version 1.6.0 (2026-04-29)
### New Features
- **Plugin Settings Export/Import**: Added functionality to save and load plugin configurations to external JSON files.
- **Sort Layers by Name**: New toolbar button and logic to sort QGIS layers within a group alphabetically.
- **Clip Raster by Depth**: New processing algorithm to extract raster regions based on depth thresholds.
- **Extract Field Across Layers**: Utility to pull specific field values from multiple vector layers simultaneously.
- **Quick Access Buttons**: Integrated dedicated ribbon buttons for Batch Theme Export and Layer Sorting into the main toolbar.

### Improvements
- **UI Enhancements**: Added visual highlighting for selected tasks and improved task progress feedback in long-running processes.
- **Theme Management**: Refactored map theme handling to improve reliability during batch PDF exports.
- **Code Cleanup**: Removed obsolete QA consistency scripts and optimized the processing provider's algorithm registration.

## Version 1.5.0 (2026-04-09)
### New Features
- **Batch Theme PDF Export**: Batch export QGIS print layouts to PDF by iterating through map themes.
- **Mapping CSV to Legend**: Apply TUFLOW styling rules and legends to layers using mapping CSVs.
- **Sample Rasters on Grid**: Sample multiple raster layers across a generated grid of points.
- **XMDF Output**: Extract and process TUFLOW XMDF format outputs.

## Version 1.4.0 (2026-03-27)
### New Features
- **Depth Discharge Curve**: Build composite Q–H curves (orifice, weir, pipe capacity) with interactive plotting and CSV export. Includes support for multiple QML fallbacks.
- **Time of Concentration**: Added Time of Concentration algorithm with multi-layer DEM support.
- **Merge Rasters**: Added a new tool to efficiently merge multiple raster files.

### Improvements
- **Code Quality**: Comprehensive code quality and formatting improvements across the plugin utilizing the Ruff formatter.
- **Privacy & Security**: Removed sensitive metadata (emails, company name) and implemented stricter workspace ignoring (`.gitignore` rules for `.github` and AI configurations).
- **Workspace Cleanup**: Tidied up development artifacts, internal dev notes, and unnecessary documentation.

## Version 1.3.6 (2026-01-28)
### New Features
- **Cross Sections along Alignment**:
  - Added interactive tool to view long sections and cross sections side-by-side.
  - Supports drawing alignment on map or selecting existing features.
  - Dynamic cursor tracking: Red cross on map, vertical dash on long section, and cross-section profile update.
  - **PDF Export**: Generate multi-page PDF reports (3x2 grid) of cross sections at user-defined intervals.
  - Visual feedback: Alignment direction arrow and cross-section location indicator on map.

### Improvements
- **Load Sample Points**:
  - Complete refactoring of the algorithm.
  - New dialog UI with checkbox selection for grid layers.
  - Improved path handling and validation.
  - Added state persistence for layer selections.