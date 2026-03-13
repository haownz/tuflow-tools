<div align="center">

<img src="flood-icon.png" alt="TUFLOW Tools" width="80"/>

# TUFLOW Tools

**A comprehensive QGIS plugin for TUFLOW hydraulic modelling — pre-processing, post-processing, and analysis.**

[![QGIS](https://img.shields.io/badge/QGIS-3.22%2B-green?logo=qgis)](https://qgis.org)
[![Version](https://img.shields.io/badge/version-1.3.6-blue)](https://github.com/haownz/tuflow-tools/releases)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-informational)]()

[📖 Documentation](https://github.com/haownz/tuflow-tools/wiki) · [🐛 Report a Bug](https://github.com/haownz/tuflow-tools/issues) · [💬 Discussions](https://github.com/haownz/tuflow-tools/discussions)

</div>

---

## Overview

**TUFLOW Tools** is a QGIS Processing plugin that extends QGIS with a rich suite of algorithms designed to streamline hydraulic modelling workflows using [TUFLOW](https://www.tuflow.com/). It covers the full modelling lifecycle — from preparing land cover and model inputs, to loading and visualising results, to generating professional reports.

---

## ✨ Features

### 🌊 Results Loading & Visualisation
| Tool | Description |
|---|---|
| **Load Grid Output** | Wizard to load TUFLOW raster grid outputs (depth, velocity, WSE, etc.) with automatic styling |
| **Load PO Lines** | Import and visualise TUFLOW Plot Output (PO) line results |
| **Load Sample Points** | Load TUFLOW sample point outputs with interactive layer selection UI |
| **Load Profile Sections** | Generate cross-section profiles along selected line features |

### 📐 Cross Section & Alignment Analysis
| Tool | Description |
|---|---|
| **Cross Sections along Alignment** | Interactive tool for long-section and cross-section viewing side-by-side; supports drawing or selecting alignment on map with real-time cursor tracking |
| **Sample Rasters at Vertices** | Sample multiple raster layers at point/vertex locations simultaneously |
| **WSE Comparison** | Compare Water Surface Elevation (WSE) rasters between scenarios |

### 🗺️ Flood Analysis
| Tool | Description |
|---|---|
| **Flood Hazard Classify** | Classify flood hazard levels (0–3) from depth and velocity rasters |
| **Inundation Boundary** | Trace closed polygon flood extents from a depth raster using a user-defined cutoff depth |

### 🛠️ Pre-processing & GIS Utilities
| Tool | Description |
|---|---|
| **Land Cover Add Fields** | Add TUFLOW-specific material and soil fields to land cover layers |
| **Process Land Cover** | Clip, process, and merge land cover data from geodatabases for TUFLOW input |
| **GIS Location** | Utilities for spatial referencing and coordinate location |
| **Append Features** | Merge features between vector layers |
| **Batch Rename Layers** | Rename multiple map layers using pattern matching |
| **Restore Layer Name** | Restore layer names from their source file paths |

### 📊 Time Series & Monitoring
| Tool | Description |
|---|---|
| **Time Series Q Plot** | Plot TUFLOW time series (Q) outputs interactively |
| **TUFLOW Log Monitor** | Monitor and parse TUFLOW simulation log files in real time |

### ⚙️ Plugin Utilities
| Tool | Description |
|---|---|
| **Plugin Settings** | Configure global plugin settings and defaults |
| **Clear Memory / File Locks** | Release memory layers and file handles locked by QGIS |

---

## 📦 Installation

### From QGIS Plugin Manager (Recommended)
1. Open QGIS → **Plugins** → **Manage and Install Plugins**
2. Search for **"TUFLOW Tools"**
3. Click **Install Plugin**

### Manual Installation
1. Download the latest release `.zip` from [Releases](https://github.com/haownz/tuflow-tools/releases)
2. In QGIS: **Plugins** → **Manage and Install Plugins** → **Install from ZIP**
3. Select the downloaded `.zip` and click **Install Plugin**

### Requirements
- **QGIS**: 3.22 or later
- **Python**: 3.8 or later (bundled with QGIS)

---

## 🚀 Quick Start

After installation, all tools are accessible via:

**Processing Toolbox** → **TUFLOW Tools**

Or search for any tool by name in the Processing Toolbox search bar.

### Example: Load TUFLOW Grid Output
1. Open **Processing Toolbox** → **TUFLOW Tools** → **Load Grid Output**
2. Point to your TUFLOW results folder (`.tcf` location)
3. Select the output scenarios and time steps to load
4. Layers will be added to the map with automatic TUFLOW-style symbology

### Example: Cross Section Alignment
1. Open **TUFLOW Tools** → **Cross Sections along Alignment**
2. Draw an alignment on the map canvas or select an existing line feature
3. View the long section and cross-section plots side-by-side with live cursor tracking
4. Export a multi-page PDF report of cross sections at defined intervals

---

## 📋 Changelog

### Version 1.3.6 (2026-01-28)
#### New Features
- **Cross Sections along Alignment**
  - Interactive side-by-side long section and cross-section viewer
  - Draw alignment on map or select existing features
  - Dynamic cursor tracking: red cross on map, vertical dash on long section, live cross-section profile
  - **PDF Export**: multi-page PDF reports (3×2 grid) at user-defined chainage intervals
  - Visual feedback: alignment direction arrow and cross-section location indicator on map

#### Improvements
- **Load Sample Points**: full refactoring with new dialog UI, checkbox layer selection, improved path handling and state persistence

See [CHANGELOG.md](CHANGELOG.md) for full history.

---

## 🤝 Contributing

Contributions, bug reports, and feature suggestions are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m "Add my feature"`
4. Push to GitHub: `git push origin feature/my-feature`
5. Open a Pull Request

Please use [GitHub Issues](https://github.com/haownz/tuflow-tools/issues) for bug reports.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 👤 Author

**Dr. Hao Wu**  
🐙 [@haownz](https://github.com/haownz)

---

<div align="center">
<sub>Built for the TUFLOW hydraulic modelling community 🌊</sub>
</div>
