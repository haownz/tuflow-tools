# Changelog

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