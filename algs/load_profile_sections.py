# -*- coding: utf-8 -*-
"""
4 - Load Profile Sections
Generates longitudinal profile PDFs along line features, sampling:
- Terrain rasters (DEM)
- Scenario grid rasters (d/h/v naming), converted to level rasters.

Workflow:
1. Show input dialog to select lines, terrain rasters, and grid layers
2. For each line, sample terrain and grid rasters
3. Generate profile plots and merge into single PDF
"""

import os
import re
import numpy as np

# Matplotlib (headless)
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

# PDF merger
try:
    from PyPDF2 import PdfMerger
except ImportError:
    PdfMerger = None

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QLabel, QHBoxLayout, QPushButton, QSizePolicy, QMessageBox, QComboBox,
    QDoubleSpinBox, QLineEdit, QFileDialog, QGridLayout
)

from qgis.core import (
    QgsProcessingAlgorithm, QgsProject,
    QgsRasterLayer, QgsVectorLayer, QgsWkbTypes,
    QgsProcessing
)

from qgis.utils import iface


# ======================================================================
# Helper functions
# ======================================================================

def extract_scenario_from_layer_name(layer_name: str):
    """
    Extract scenario base name from a grid layer name.

    Matches patterns like:
      Scenario_001_d_HR_Max
      Scenario_001_h_HR_Max
      Scenario_001_v_HR_Max

    Returns the part before the _d/_h/_v.
    """
    match = re.search(r'^(.+?)_([dhv])(?:_.*)?$', layer_name)
    return match.group(1) if match else None


def simplify_scenario_name(scenario_name: str) -> str:
    """Remove trailing _<digits> and replace underscores with spaces."""
    if not scenario_name:
        return ""
    return re.sub(r'_\d+$', '', scenario_name).replace('_', ' ')


def convert_to_level_raster(raster_path: str, grid_type: str):
    """
    Given a raster path and its type (d/h/v), try to locate the corresponding
    'h' (level) raster by replacing '_d_' or '_v_' with '_h_' in the filename.
    If grid_type is 'h', just return the original path.
    """
    if grid_type == 'h':
        return raster_path

    level_path = raster_path.replace(f"_{grid_type}_", "_h_")
    return level_path if os.path.exists(level_path) else None


def sample_raster_along_line(raster_layer: QgsRasterLayer, line_geom, sample_interval: float):
    """
    Sample raster values at fixed interval along a line geometry.
    Returns (distances, values).
    """
    if not raster_layer or not raster_layer.isValid():
        return [], []

    distances, values = [], []
    total_distance = line_geom.length()
    if total_distance == 0:
        return [], []

    num_samples = int(total_distance / sample_interval) + 1
    provider = raster_layer.dataProvider()

    for i in range(num_samples):
        dist = min(i * sample_interval, total_distance)
        pt = line_geom.interpolate(dist).asPoint()
        val, ok = provider.sample(pt, 1)
        if ok and val is not None:
            distances.append(dist)
            values.append(float(val))

    return distances, values


def generate_section_plot(line_id_str, terrain_data_list, level_data_dict, output_pdf_base):
    """
    Generate a profile plot for one line and save it as a PDF.

    terrain_data_list: List of tuples (name, distances, values)
    level_data_dict: {scenario_name: (distances, values)}
    output_pdf_base: base path used to build PDF filename
    """
    if plt is None:
        return None

    try:
        fig, ax = plt.subplots(figsize=(14, 6))
        all_dists = []

        # Plot terrain
        for idx, (name, dists, vals) in enumerate(terrain_data_list):
            if not dists:
                continue

            all_dists.extend([min(dists), max(dists)])

            if idx == 0:
                # Main terrain profile
                ax.plot(dists, vals, 'k-', linewidth=2.5, label=name, zorder=10)
                ax.fill_between(dists, vals, alpha=0.2, color='brown', zorder=5)
            else:
                ax.plot(dists, vals, 'k--', linewidth=1.5, label=name, alpha=0.6)

        # Plot level profiles
        colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(level_data_dict))))
        for idx, (name, (dists, vals)) in enumerate(level_data_dict.items()):
            if not dists:
                continue

            all_dists.extend([min(dists), max(dists)])

            ax.plot(
                dists,
                vals,
                color=colors[idx % 10],
                linewidth=2.0,
                label=simplify_scenario_name(name),
                alpha=0.8
            )

        if all_dists:
            ax.set_xlim(min(all_dists), max(all_dists))

        ax.set_xlabel('Distance (m)')
        ax.set_ylabel('Elevation (m)')
        ax.set_title(f'Section Profile: {line_id_str}')
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=9, loc='best')

        clean_id = re.sub(r'[^a-zA-Z0-9_\-]', '', str(line_id_str))
        pdf_path = f"{output_pdf_base}_{clean_id}.pdf"
        plt.savefig(pdf_path, dpi=120, bbox_inches='tight')
        plt.close(fig)

        return pdf_path
    except Exception as e:
        return None


# ======================================================================
# Input Dialog (similar to load_sample_points)
# ======================================================================

class LoadProfileSectionsInputDialog(QDialog):
    """
    Dialog for user input parameters.
    Selects:
    1. Profile lines vector layer
    2. Terrain (DEM) raster layers
    3. Grid layers (d/h/v scenario rasters)
    4. Sample interval
    5. Output PDF path
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Profile Sections - Input Parameters")
        self.setMinimumWidth(800)
        self.setMinimumHeight(650)

        # Results
        self.input_lines_layer = None
        self.terrain_layers = []
        self.grid_layers = []
        self.sample_interval = 1.0
        self.output_pdf_path = ""

        self.init_ui()
        self.load_available_layers()

    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()

        # Step 1: Profile lines layer selection
        layout.addWidget(QLabel("Step 1: Select input profile lines layer"))
        self.lines_combo = QComboBox()
        self.lines_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.lines_combo)

        # Step 2: Terrain raster layers selection
        layout.addWidget(QLabel("Step 2: Select terrain (DEM) raster layer(s)"))
        self.terrain_table = QTableWidget()
        self.terrain_table.setColumnCount(2)
        self.terrain_table.setHorizontalHeaderLabels(["Select", "Layer Name"])
        self.terrain_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.terrain_table.setColumnWidth(0, 60)
        self.terrain_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.terrain_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.terrain_table)
        layout.setStretchFactor(self.terrain_table, 1)
        
        # Terrain table controls
        terrain_btn_layout = QHBoxLayout()
        terrain_all_btn = QPushButton("Select All")
        terrain_none_btn = QPushButton("Clear All")
        terrain_all_btn.clicked.connect(lambda: self._set_table_all(self.terrain_table, True))
        terrain_none_btn.clicked.connect(lambda: self._set_table_all(self.terrain_table, False))
        terrain_btn_layout.addWidget(terrain_all_btn)
        terrain_btn_layout.addWidget(terrain_none_btn)
        terrain_btn_layout.addStretch()
        layout.addLayout(terrain_btn_layout)

        # Step 3: Grid layers selection
        layout.addWidget(QLabel("Step 3: Select grid layers (d/h/v rasters)"))
        self.grid_table = QTableWidget()
        self.grid_table.setColumnCount(2)
        self.grid_table.setHorizontalHeaderLabels(["Select", "Layer Name"])
        self.grid_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.grid_table.setColumnWidth(0, 60)
        self.grid_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.grid_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.grid_table)
        layout.setStretchFactor(self.grid_table, 1)
        
        # Grid table controls
        grid_btn_layout = QHBoxLayout()
        grid_all_btn = QPushButton("Select All")
        grid_none_btn = QPushButton("Clear All")
        grid_all_btn.clicked.connect(lambda: self._set_table_all(self.grid_table, True))
        grid_none_btn.clicked.connect(lambda: self._set_table_all(self.grid_table, False))
        grid_btn_layout.addWidget(grid_all_btn)
        grid_btn_layout.addWidget(grid_none_btn)
        grid_btn_layout.addStretch()
        layout.addLayout(grid_btn_layout)

        # Step 4: Sampling interval
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Step 4: Sampling interval (meters):"))
        self.interval_spinbox = QDoubleSpinBox()
        self.interval_spinbox.setMinimum(0.1)
        self.interval_spinbox.setMaximum(1000.0)
        self.interval_spinbox.setValue(1.0)
        self.interval_spinbox.setDecimals(1)
        self.interval_spinbox.setSingleStep(0.5)
        self.interval_spinbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        interval_layout.addWidget(self.interval_spinbox)
        interval_layout.addStretch()
        layout.addLayout(interval_layout)

        # Step 5: Output PDF path
        layout.addWidget(QLabel("Step 5: Output PDF file path"))
        pdf_layout = QHBoxLayout()
        self.pdf_path_edit = QLineEdit()
        self.pdf_path_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pdf_layout.addWidget(self.pdf_path_edit)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        browse_btn.clicked.connect(self.browse_output_file)
        pdf_layout.addWidget(browse_btn)
        layout.addLayout(pdf_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("Process Profile Sections")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def load_available_layers(self):
        """Load available vector and raster layers."""
        project = QgsProject.instance()

        # Populate lines layer combo (auto-select current active layer if it's a Line layer)
        self.lines_combo.clear()
        current_layer = iface.activeLayer()
        selected_index = 0

        for index, layer in enumerate(project.mapLayers().values()):
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.LineGeometry:
                self.lines_combo.addItem(layer.name(), layer)
                if current_layer and layer.id() == current_layer.id():
                    selected_index = self.lines_combo.count() - 1

        if self.lines_combo.count() > 0:
            self.lines_combo.setCurrentIndex(selected_index)

        # Populate terrain rasters table (all raster layers)
        self._populate_raster_table(self.terrain_table, select_all=True)

        # Populate grid layers table (rasters with d/h/v pattern)
        self._populate_grid_table()

    def _populate_raster_table(self, table, select_all=True):
        """Populate a raster layer table."""
        project = QgsProject.instance()
        rasters = [layer for layer in project.mapLayers().values() if isinstance(layer, QgsRasterLayer)]

        table.setRowCount(len(rasters))
        for row, layer in enumerate(rasters):
            cb = QCheckBox()
            cb.setChecked(select_all)
            table.setCellWidget(row, 0, cb)

            item = QTableWidgetItem(layer.name())
            item.setData(Qt.UserRole, layer)
            table.setItem(row, 1, item)

    def _populate_grid_table(self):
        """Detect and populate grid layers table."""
        project = QgsProject.instance()
        grid_layers = []

        for layer in project.mapLayers().values():
            if isinstance(layer, QgsRasterLayer):
                if re.search(r'_[dhv]_', layer.name()) or re.search(r'_[dhv]\.', layer.name()):
                    grid_layers.append(layer)

        self.grid_table.setRowCount(len(grid_layers))
        for row, layer in enumerate(grid_layers):
            cb = QCheckBox()
            cb.setChecked(True)
            self.grid_table.setCellWidget(row, 0, cb)

            item = QTableWidgetItem(layer.name())
            item.setData(Qt.UserRole, layer)
            self.grid_table.setItem(row, 1, item)

    def _set_table_all(self, table, state):
        """Set all checkboxes in a table to checked or unchecked."""
        for row in range(table.rowCount()):
            cb = table.cellWidget(row, 0)
            if cb:
                cb.setChecked(state)

    def _collect_selected_layers(self, table):
        """Collect selected layers from a table."""
        layers = []
        for row in range(table.rowCount()):
            cb = table.cellWidget(row, 0)
            if cb and cb.isChecked():
                item = table.item(row, 1)
                lyr = item.data(Qt.UserRole) if item else None
                if isinstance(lyr, QgsRasterLayer):
                    layers.append(lyr)
        return layers

    def browse_output_file(self):
        """Browse for output PDF file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Profile Sections PDF",
            self.pdf_path_edit.text() or os.path.expanduser("~"),
            "PDF Files (*.pdf)"
        )
        if file_path:
            self.pdf_path_edit.setText(file_path)

    def accept(self):
        """Validate and accept dialog."""
        self.input_lines_layer = self.lines_combo.currentData()
        self.terrain_layers = self._collect_selected_layers(self.terrain_table)
        self.grid_layers = self._collect_selected_layers(self.grid_table)
        self.sample_interval = self.interval_spinbox.value()
        self.output_pdf_path = self.pdf_path_edit.text().strip()

        # Validate
        if not self.input_lines_layer:
            QMessageBox.warning(self, "Error", "Please select an input profile lines layer")
            return

        if not self.terrain_layers:
            QMessageBox.warning(self, "Error", "Please select at least one terrain (DEM) layer")
            return

        if not self.grid_layers:
            QMessageBox.warning(self, "Error", "Please select at least one grid layer (d/h/v raster)")
            return

        if not self.output_pdf_path:
            QMessageBox.warning(self, "Error", "Please specify an output PDF file path")
            return

        if not self.output_pdf_path.lower().endswith('.pdf'):
            QMessageBox.warning(self, "Error", "Output file must have .pdf extension")
            return

        super().accept()


# ======================================================================
# Main Processing Algorithm
# ======================================================================

class LoadProfileSectionsAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm to generate profile section PDFs from line features.
    """

    def createInstance(self):
        return LoadProfileSectionsAlgorithm()

    def name(self):
        return 'load_profile_sections'

    def displayName(self):
        return '4 - Load Profile Sections'

    def group(self):
        return '2 - Result Analysis'

    def groupId(self):
        return 'result_analysis'

    def shortHelpString(self):
        return (
            "Generates longitudinal profile PDFs along line features.\n"
            "For each line, terrain rasters are sampled as base profiles and "
            "selected grid (scenario) rasters are converted to level rasters "
            "and overlaid as water level profiles.\n"
            "A dialog will prompt you to select input parameters before processing."
        )

    def initAlgorithm(self, config=None):
        """No standard parameters - dialog handles all inputs."""
        pass

    def processAlgorithm(self, parameters, context, feedback):
        """
        Main algorithm workflow.
        Shows input dialog for all parameters before processing.
        """

        # Show input dialog
        feedback.pushInfo("Opening input parameters dialog...")
        dlg = LoadProfileSectionsInputDialog(iface.mainWindow())

        if dlg.exec_() != QDialog.Accepted:
            feedback.reportError("Operation cancelled by user")
            return {}

        # Get inputs from dialog
        input_lines_layer = dlg.input_lines_layer
        terrain_layers = dlg.terrain_layers
        grid_layers = dlg.grid_layers
        sample_interval = dlg.sample_interval
        output_path = dlg.output_pdf_path

        feedback.pushInfo(f"✓ Profile lines layer: {input_lines_layer.name()}")
        feedback.pushInfo(f"✓ Terrain layers: {len(terrain_layers)}")
        feedback.pushInfo(f"✓ Grid layers: {len(grid_layers)}")
        feedback.pushInfo(f"✓ Sample interval: {sample_interval} m")

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        temp_base = os.path.splitext(output_path)[0] + "_temp"

        # Process features
        pdf_files = []
        features = list(input_lines_layer.getFeatures())
        total = len(features)

        if total == 0:
            feedback.reportError("Input line layer contains no features.")
            return {}

        feedback.pushInfo(f"Processing {total} line feature(s)...")

        for i, feat in enumerate(features):
            if feedback.isCanceled():
                break

            feedback.setProgress(int((i / total) * 85))

            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue

            # Sample terrain rasters
            t_data = [
                (lyr.name(), *sample_raster_along_line(lyr, geom, sample_interval))
                for lyr in terrain_layers
            ]
            t_data = [d for d in t_data if d[1]]

            if not t_data:
                continue

            # Build level data from grid rasters
            l_data = {}
            for lyr in grid_layers:
                g_name = lyr.name()

                # Grid type detection – _d_/_h_/_v_
                type_m = re.search(r'_([dhv])_', g_name)
                g_type = type_m.group(1) if type_m else 'h'

                h_path = convert_to_level_raster(lyr.source(), g_type)
                if h_path:
                    level_rl = QgsRasterLayer(h_path, "tmp")
                    dists, vals = sample_raster_along_line(level_rl, geom, sample_interval)
                    if dists:
                        scenario_name = extract_scenario_from_layer_name(g_name) or g_name
                        l_data[scenario_name] = (dists, vals)

            # Determine line ID
            line_id = feat.id()
            for fld in feat.fields():
                if 'ID' in fld.name().upper():
                    try:
                        line_id = feat[fld.name()]
                        break
                    except KeyError:
                        pass

            pdf = generate_section_plot(str(line_id), t_data, l_data, temp_base)
            if pdf:
                pdf_files.append(pdf)

        # Merge PDFs if possible
        if pdf_files and PdfMerger and not feedback.isCanceled():
            feedback.pushInfo(f"Merging {len(pdf_files)} profiles into single PDF...")
            merger = PdfMerger()
            try:
                for p in pdf_files:
                    merger.append(p)
                merger.write(output_path)
            finally:
                merger.close()

            # Remove temp PDFs
            for p in pdf_files:
                try:
                    os.remove(p)
                except Exception:
                    pass

            feedback.setProgress(100)
            feedback.pushInfo(f"✓ Profile PDF saved: {output_path}")

            # Auto-open on Windows
            if os.name == 'nt':
                try:
                    os.startfile(output_path)
                except Exception:
                    pass

            return {"OUTPUT": output_path}

        if not pdf_files:
            feedback.reportError("No profile PDFs were generated.")
        elif not PdfMerger:
            feedback.reportError("PyPDF2 (PdfMerger) not available; cannot merge PDFs.")

        return {}
