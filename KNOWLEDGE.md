# TUFLOW Tools Knowledge Base

This document provides a technical overview of the **TUFLOW Tools** QGIS plugin, designed for advanced hydraulic modeling workflows.

## 🏗️ Architecture Overview

The plugin follows a standard QGIS Processing Provider architecture:

-   **`plugin.py`**: The main entry point (`TuflowToolsPlugin`). It registers the processing provider, initializes the "TUFLOW Tools" toolbar, and sets up GUI actions like "Apply Style" and "Batch Rename".
-   **`provider.py`**: Defintes the `TuflowProcessingProvider`, which acts as a container for all processing algorithms. It registers them with the QGIS Processing Registry.
-   **`settings.py`**: Implements `PluginSettings` for global configuration management using `QSettings`. Handles model paths, style paths, and customizable style mappings.
-   **`style_manager.py`**: A dedicated `StyleManager` that applies `.qml` styles to layers based on wildcard patterns defined in settings.

## 🧩 Core Algorithm Categories

The plugin includes over 25 algorithms, organized into the following functional areas:

### 🌊 Results Loading & Visualization
Tools for importing TUFLOW results into QGIS with automated styling and selection.
-   **Load Grid Output**: Wizard for loading raster results (depth, velocity, WSE).
-   **Load PO Lines**: Imports Plot Output (PO) line results.
-   **Load Sample Points**: Interactive loader for point results.
-   **Load Profile Sections**: Generates cross-section profiles along lines.

### 📐 Cross Section & Alignment Analysis
Interactive tools for spatial profile analysis.
-   **Cross Section Alignment**: High-end interactive tool for side-by-side long-section and cross-section viewing with live map tracking and PDF reporting.
-   **Sample Rasters at Vertices**: Batch samples multiple rasters at point/vertex locations.
-   **WSE Comparison**: Calculates and visualizes differences between WSE rasters.

### 🗺️ Flood Analysis
Post-processing tools for flood impact mapping.
-   **Flood Hazard Classify**: Classifies depth/velocity rasters into hazard categories.
-   **Inundation Boundary**: Generates closed polygons from flood depth rasters using depth cutoffs.

### 🛠️ Pre-processing & GIS Utilities
Data preparation and management tools.
-   **Land Cover Add Fields**: Adds TUFLOW-specific schemas to land cover layers.
-   **Process Land Cover**: Automated clipping and merging of land cover data.
-   **GIS Location / Append Features**: General spatial utilities for data manipulation.
-   **Batch Rename / Restore Name**: Tools for managing layer naming conventions.

### 📊 Time Series & Monitoring
Real-time simulation tracking and plotting.
-   **Time Series Q Plot**: Interactive plotting of discharge (Q) time series.
-   **TUFLOW Log Monitor**: Real-time parsing and monitoring of simulation log files.

## ⚙️ Development & Standards
-   **Linting**: The project uses **Ruff** for linting and formatting.
-   **API**: Built for QGIS 3.22+ and Python 3.8+.
-   **Patterns**: Uses wildcard pattern matching (`fnmatch`) for dynamic styling and layer identification.
