# Changelog

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