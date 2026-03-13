# -*- coding: utf-8 -*-
"""
Load Sample Points Algorithm

Reorganized workflow:
1. User Input: Select input points layer, DEM raster, and grid layers (d/h/v variants)
2. Processing: For each grid layer, identify scenario type, find d/h/v rasters, sample values
3. Output: Generate PLOT_P_Sampled.shp with sampled attributes, load into QGIS group
"""
import os
import glob
import gc
import re
import json
import math
from pathlib import Path
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget, 
                                 QTableWidgetItem, QHeaderView, QPushButton, QHBoxLayout,
                                 QComboBox, QMessageBox, QFileDialog, QAbstractItemView,
                                 QTextEdit, QScrollArea, QWidget, QSizePolicy, QCheckBox,
                                 QApplication, QListWidget, QListWidgetItem)
from qgis.PyQt.QtCore import Qt, QVariant, QDateTime
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsProcessingAlgorithm, QgsExpressionContextUtils,
                       QgsVectorLayer, QgsProject, QgsField, QgsVectorFileWriter,
                       QgsFeature, QgsFields, QgsWkbTypes, QgsPointXY, QgsGeometry,
                       QgsRasterLayer, QgsProcessingParameterRasterLayer,
                       QgsProcessingParameterVectorLayer, QgsProcessing, QgsLayerTreeGroup,
                       QgsLayerTreeLayer)
from qgis.utils import iface
from .po_common import derive_poline_path_from_raster, load_vector_with_fallback
from ..style_manager import StyleManager



def extract_scenario_base_from_grid_layer(grid_layer_name):
    """
    Extract scenario base name from grid layer name.
    
    Example: "EX_100YR_CC_24hr_MHWS10+1m_Baseline_003_d_HR_Max" 
    -> ("EX_100YR_CC_24hr_MHWS10+1m_Baseline_003", "d")
    
    Returns:
        (base_name: str, grid_type: str) or (None, None) if not matched
    """
    # Pattern: anything ending with _[dhv]_... or _[dhv].*
    match = re.search(r'^(.+?)_([dhv])(?:_|\.|\Z)', grid_layer_name, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).lower()
    return None, None


def find_corresponding_rasters(grid_layer_name, raster_dir, base_name, grid_type):
    """
    Find d, h, v rasters based on grid layer name and base name.
    
    Args:
        grid_layer_name: Name of the grid layer (e.g., "EX_..._d_HR_Max")
        raster_dir: Directory to search for rasters (normalized path)
        base_name: Scenario base name (e.g., "EX_100YR_CC_24hr_MHWS10+1m_Baseline_003")
        grid_type: Grid type detected (d, h, or v)
    
    Returns:
        {'Depth': path, 'Level': path, 'Velocity': path}
    """
    raster_map = {
        'Depth': None,
        'Level': None,
        'Velocity': None
    }
    
    if not os.path.isdir(raster_dir):
        return raster_map
    
    # Normalize the raster directory path
    raster_dir = os.path.normpath(raster_dir)
    
    # Find d/h/v rasters by replacing the type suffix
    for letter, key in [('d', 'Depth'), ('h', 'Level'), ('v', 'Velocity')]:
        # Replace the original grid type with target type
        search_name = re.sub(rf"_{grid_type}_", f"_{letter}_", grid_layer_name, flags=re.IGNORECASE)
        search_name = re.sub(rf"_{grid_type}\.", f"_{letter}.", search_name, flags=re.IGNORECASE)
        
        # Look for the raster file with same basename structure
        pattern = os.path.join(raster_dir, search_name.replace('.tif', '') + "*.tif")
        matches = glob.glob(pattern, recursive=False)
        
        if matches:
            # Use first match, normalized path
            raster_map[key] = os.path.normpath(matches[0])
    
    return raster_map


def sample_rasters_at_points(input_points_layer, raster_map, terrain_layers, feedback=None):
    """
    Sample d/h/v and terrain raster values at provided input point locations.
    
    Args:
        input_points_layer: QgsVectorLayer with point geometries (user-provided sample locations)
        raster_map: Dict of {'Level': path, 'Depth': path, 'Velocity': path}
        terrain_layers: List of QgsRasterLayer objects for terrain elevation sampling
        feedback: Processing feedback object (optional)
    
    Returns:
        QgsVectorLayer with Point geometry containing sampled values, or None on error
    """
    try:
        if not input_points_layer or not input_points_layer.isValid():
            if feedback:
                feedback.reportError("Input points layer is invalid")
            return None
        
        if not terrain_layers or not any(layer.isValid() for layer in terrain_layers):
            if feedback:
                feedback.reportError("Terrain layer(s) are invalid or empty")
            return None
        
        # Create output layer with proper CRS
        fields = QgsFields()
        fields.append(QgsField("ID", QVariant.Int))
        fields.append(QgsField("X", QVariant.Double))
        fields.append(QgsField("Y", QVariant.Double))
        fields.append(QgsField("Terrain", QVariant.Double))
        fields.append(QgsField("Depth", QVariant.Double))
        fields.append(QgsField("Level", QVariant.Double))
        fields.append(QgsField("Velocity", QVariant.Double))
        
        uri = f"Point?crs={input_points_layer.crs().authid()}"
        output_layer = QgsVectorLayer(uri, f"{input_points_layer.name()}_sampled", "memory")
        output_layer.dataProvider().addAttributes(fields)
        output_layer.updateFields()
        
        # Load rasters with path normalization
        rasters = {}
        for key, path in raster_map.items():
            if path and os.path.exists(path):
                path = os.path.normpath(path)
                try:
                    raster = QgsRasterLayer(path, key)
                    if raster.isValid():
                        rasters[key] = raster
                    elif feedback:
                        feedback.pushInfo(f"    ⚠ Warning: Invalid raster {key}: {os.path.basename(path)}")
                except Exception as e:
                    if feedback:
                        feedback.pushInfo(f"    ⚠ Warning: Could not load {key} raster: {e}")
        
        # Sample at each input point with batch processing
        terrain_providers = [layer.dataProvider() for layer in terrain_layers if layer.isValid()]
        batch_size = 500
        batch_features = []
        point_id = 1
        point_count = input_points_layer.featureCount()
        
        if feedback:
            feedback.pushInfo(f"    Sampling d/h/v at {point_count} input points...")
        
        for feat in input_points_layer.getFeatures():
            try:
                geom = feat.geometry()
                if not geom or geom.isEmpty():
                    continue
                
                # Extract point geometry (handle multi-point)
                if geom.isMultipart():
                    pt = geom.asMultiPoint()[0] if geom.asMultiPoint() else None
                else:
                    pt = geom.asPoint()
                
                if not pt:
                    continue
                
                # Create output feature
                out_feat = QgsFeature(fields)
                out_feat.setGeometry(QgsGeometry.fromPointXY(pt))
                
                # Sample values [ID, X, Y, Terrain, Depth, Level, Velocity]
                attrs = [point_id, pt.x(), pt.y()]
                
                # Sample terrain first
                terrain_sampled_val = None
                for provider in reversed(terrain_providers):
                    try:
                        val, ok = provider.sample(pt, 1)
                        if ok:
                            try:
                                f_val = float(val)
                                if not math.isnan(f_val):
                                    terrain_sampled_val = f_val
                                    break
                            except (ValueError, TypeError):
                                pass
                    except Exception:
                        pass
                
                attrs.append(terrain_sampled_val)
                
                # Sample d/h/v
                for key in ['Depth', 'Level', 'Velocity']:
                    if key in rasters:
                        try:
                            val, ok = rasters[key].dataProvider().sample(pt, 1)
                            attrs.append(float(val) if ok else None)
                        except Exception:
                            attrs.append(None)
                    else:
                        attrs.append(None)
                
                out_feat.setAttributes(attrs)
                batch_features.append(out_feat)
                point_id += 1
                
                # Add features in batches to avoid memory issues
                if len(batch_features) >= batch_size:
                    output_layer.dataProvider().addFeatures(batch_features)
                    batch_features = []
                    gc.collect()
                    
            except Exception as e:
                if feedback:
                    feedback.pushInfo(f"    ⚠ Warning: Could not process point: {e}")
        
        # Add remaining features
        if batch_features:
            output_layer.dataProvider().addFeatures(batch_features)
            batch_features = []
        
        output_layer.updateExtents()
        
        if feedback:
            feedback.pushInfo(f"    Sampled {point_id - 1} points")
        
        # Cleanup rasters
        rasters.clear()
        gc.collect()
        
        return output_layer
        
    except Exception as e:
        if feedback:
            feedback.reportError(f"    ✗ Exception in sample_rasters_at_points: {str(e)}")
        return None





def save_layer_to_shapefile(layer, output_path, feedback=None, overwrite_mode='skip'):
    """
    Save a vector layer to a shapefile with robust path handling.
    
    Args:
        layer: QgsVectorLayer to save
        output_path: Full path to output shapefile (e.g., /path/to/file.shp)
        feedback: Processing feedback object (optional)
        overwrite_mode: 'overwrite' to replace existing files, 'skip' to keep existing
    
    Returns:
        (success: bool, error_msg: str or None, was_skipped: bool)
    """
    try:
        if not layer or not layer.isValid():
            return False, "Invalid layer", False
        
        # Normalize the output path
        output_path = os.path.normpath(output_path)
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                return False, f"Could not create directory: {e}", False
        
        # Check if file already exists
        base_path = os.path.splitext(output_path)[0]
        if os.path.exists(output_path):
            if overwrite_mode == 'skip':
                if feedback:
                    feedback.pushInfo(f"  ⊘ Skipping (file exists): {os.path.basename(output_path)}")
                return True, None, True
            elif overwrite_mode == 'overwrite':
                if feedback:
                    feedback.pushInfo(f"  ↻ Overwriting existing file...")
                
                # Remove existing shapefile and all related files
                extensions = ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.qpj']
                for ext in extensions:
                    existing = base_path + ext
                    if os.path.exists(existing):
                        try:
                            os.remove(existing)
                        except Exception:
                            pass
        
        # Write layer to shapefile with proper options
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'ESRI Shapefile'
        options.fileEncoding = 'UTF-8'
        
        error_code, error_msg = QgsVectorFileWriter.writeAsVectorFormat(
            layer,
            output_path,
            options
        )
        
        if error_code == QgsVectorFileWriter.WriterError.NoError:
            if feedback:
                feedback.pushInfo(f"  ✓ Saved: {os.path.basename(output_path)}")
            return True, None, False
        else:
            error_string = f"VectorFileWriter error: {error_msg if error_msg else f'Code {error_code}'}"
            if feedback:
                feedback.reportError(f"  ✗ Save failed: {error_string}")
            return False, error_string, False
            
    except Exception as e:
        error_msg = str(e)
        if feedback:
            feedback.reportError(f"  ✗ Exception: {error_msg}")
        return False, error_msg, False




class FileOverwriteDialog(QDialog):
    """
    Custom dialog for file overwrite confirmation (consistent with load_po_lines.py).
    Displays all existing files in a scrollable text area.
    """
    
    def __init__(self, existing_files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Output Files Already Exist")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        self.user_choice = None
        self.init_ui(existing_files)
    
    def init_ui(self, existing_files):
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Found {len(existing_files)} existing output file(s):"))
        
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setText("\n".join(existing_files))
        layout.addWidget(txt)
        
        btn_lyt = QHBoxLayout()
        btn_lyt.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.on_cancel)
        btn_lyt.addWidget(cancel_btn)
        
        skip_btn = QPushButton("Skip All")
        skip_btn.clicked.connect(self.on_skip)
        btn_lyt.addWidget(skip_btn)
        
        overwrite_btn = QPushButton("Overwrite All")
        overwrite_btn.setStyleSheet("background-color: #ff9999;")
        overwrite_btn.clicked.connect(self.on_overwrite)
        btn_lyt.addWidget(overwrite_btn)
        
        layout.addLayout(btn_lyt)
        self.setLayout(layout)
    
    def on_cancel(self):
        self.user_choice = 'cancel'
        self.reject()
    
    def on_skip(self):
        self.user_choice = 'skip'
        self.accept()
    
    def on_overwrite(self):
        self.user_choice = 'overwrite'
        self.accept()


def create_table_controls(table_widget, parent_layout):
    """Helper function to create Select/Clear buttons for tables (consistent with load_grid_output.py)."""
    btn_layout = QHBoxLayout()
    all_btn = QPushButton("Select All")
    none_btn = QPushButton("Clear All")
    
    def set_all(state):
        for r in range(table_widget.rowCount()):
            cb = table_widget.cellWidget(r, 0)
            if cb:
                cb.setChecked(state)
    
    all_btn.clicked.connect(lambda: set_all(True))
    none_btn.clicked.connect(lambda: set_all(False))
    btn_layout.addWidget(all_btn)
    btn_layout.addWidget(none_btn)
    btn_layout.addStretch()
    parent_layout.addLayout(btn_layout)


class LoadSamplePointsInputDialog(QDialog):
    """
    Dialog for user input (consistent with TCFSelectionWizard in load_grid_output.py).
    Selects:
    1. Points vector layer (for sampling)
    2. DEM raster layer (terrain)
    3. Grid layers (d/h/v rasters)
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Sample Points - Input Parameters")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)
        
        self.input_points_layer = None
        self.terrain_layers = []
        self.grid_layers = []
        
        self.init_ui()
        self.load_available_layers()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        # Step 1: Points layer selection
        layout.addWidget(QLabel("Step 1: Select input points layer for sampling"))
        self.points_combo = QComboBox()
        self.points_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.points_combo)
        
        # Step 2: Terrain layer selection
        layout.addWidget(QLabel("Step 2: Select DEM/Terrain raster layers (Drag to reorder, Last wins!)"))
        self.terrain_list = QListWidget()
        self.terrain_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.terrain_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.terrain_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.terrain_list.setMinimumHeight(100)
        self.terrain_list.setMaximumHeight(150)
        layout.addWidget(self.terrain_list)
        
        # Step 3: Grid layers selection
        layout.addWidget(QLabel("Step 3: Select grid layers (d/h/v rasters)"))
        
        self.grid_table = QTableWidget()
        self.grid_table.setColumnCount(3)
        self.grid_table.setHorizontalHeaderLabels(["Select", "Layer Name", "Type"])
        self.grid_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.grid_table.setColumnWidth(0, 60)
        self.grid_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.grid_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.grid_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.grid_table)
        layout.setStretchFactor(self.grid_table, 1)
        
        # Grid layer table controls
        create_table_controls(self.grid_table, layout)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        load_btn = QPushButton("Process Sample Points")
        load_btn.clicked.connect(self.accept)
        btn_layout.addWidget(load_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def load_available_layers(self):
        """Load available vector and raster layers into combo boxes."""
        project = QgsProject.instance()
        
        # Populate points layer combo (auto-select current active layer if it's a Point layer)
        self.points_combo.clear()
        current_layer = iface.activeLayer()
        selected_index = 0
        
        for index, layer in enumerate(project.mapLayers().values()):
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PointGeometry:
                self.points_combo.addItem(layer.name(), layer)
                if current_layer and layer.id() == current_layer.id():
                    selected_index = self.points_combo.count() - 1
        
        if self.points_combo.count() > 0:
            self.points_combo.setCurrentIndex(selected_index)
        
        # Populate terrain list (raster layers)
        self.terrain_list.clear()
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsRasterLayer):
                item = QListWidgetItem(layer.name())
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, layer)
                self.terrain_list.addItem(item)
        
        # Auto-detect grid layers (raster layers with d/h/v patterns)
        self.detect_and_populate_grid_layers()
    
    def detect_and_populate_grid_layers(self):
        """Detect and populate grid layers table."""
        project = QgsProject.instance()
        self.grid_layers = []
        
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsRasterLayer):
                # Check if layer name matches d/h/v pattern
                if re.search(r'_[dhv]_', layer.name(), re.IGNORECASE) or re.search(r'_[dhv]\.', layer.name(), re.IGNORECASE):
                    self.grid_layers.append((layer.name(), layer))
        
        # Populate table with checkboxes
        self.grid_table.setRowCount(len(self.grid_layers))
        for row, (layer_name, layer) in enumerate(self.grid_layers):
            # Checkbox (select column)
            cb = QCheckBox()
            cb.setChecked(True)
            self.grid_table.setCellWidget(row, 0, cb)
            
            # Layer name
            self.grid_table.setItem(row, 1, QTableWidgetItem(layer_name))
            
            # Grid type (d/h/v)
            _, grid_type = extract_scenario_base_from_grid_layer(layer_name)
            type_map = {'d': 'Depth', 'h': 'Level', 'v': 'Velocity'}
            self.grid_table.setItem(row, 2, QTableWidgetItem(type_map.get(grid_type, 'Unknown')))
    
    def get_selected_layers(self):
        """
        Retrieve selected layers and validate.
        Returns True if all selections valid, False otherwise.
        """
        self.input_points_layer = self.points_combo.currentData()
        
        self.terrain_layers = []
        for i in range(self.terrain_list.count()):
            item = self.terrain_list.item(i)
            if item.checkState() == Qt.Checked:
                self.terrain_layers.append(item.data(Qt.UserRole))
        
        # Collect checked grid layers
        selected_grid_layers = []
        for row in range(self.grid_table.rowCount()):
            cb = self.grid_table.cellWidget(row, 0)
            if cb and cb.isChecked():
                # Get the corresponding layer from self.grid_layers
                if row < len(self.grid_layers):
                    selected_grid_layers.append(self.grid_layers[row])
        
        # Validate selections
        if not self.input_points_layer:
            QMessageBox.warning(self, "Error", "Please select an input points layer")
            return False
        
        if not self.terrain_layers:
            QMessageBox.warning(self, "Error", "Please select at least one terrain (DEM) layer")
            return False
        
        if not selected_grid_layers:
            QMessageBox.warning(self, "Error", "Please select at least one grid layer (d/h/v raster)")
            return False
        
        self.grid_layers = selected_grid_layers
        return True



class LoadSamplePointsAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm to generate and load sample points for grid layer scenarios.
    
    Workflow (consistent with load_grid_output.py and load_po_lines.py):
    1. Show input dialog: points layer, DEM, grid layers
    2. Pre-scan for existing output files and ask overwrite preference
    3. For each grid layer, find d/h/v rasters and sample values at points
    4. Save as PLOT_P_Sampled.shp and load into QGIS group
    5. Store file paths in global variables for downstream use
    """
    
    def createInstance(self):
        return LoadSamplePointsAlgorithm()
    
    def name(self):
        return "load_sample_points"
    
    def displayName(self):
        return "3 - Load Sample Points"
    
    def group(self):
        return "2 - Result Analysis"
    
    def groupId(self):
        return "result_analysis"
    
    def shortHelpString(self):
        return (
            "Generate sample points from d/h/v grid rasters. "
            "Samples Level, Depth, Velocity, and Terrain values at point locations. "
            "Creates PLOT_P_Sampled shapefiles with sampled attributes."
        )
    
    def initAlgorithm(self, config=None):
        # No standard parameters - dialog handles all inputs
        pass
    
    def processAlgorithm(self, parameters, context, feedback):
        """
        Main algorithm workflow with state persistence.
        """
        
        feedback.pushInfo("=" * 70)
        feedback.pushInfo("LOAD SAMPLE POINTS - Starting Process")
        feedback.pushInfo("=" * 70)
        
        # ====================================================================
        # STEP 1: Show input dialog to get parameters
        # ====================================================================
        feedback.pushInfo("\nStep 1: Getting user input parameters...")
        
        dialog = LoadSamplePointsInputDialog(iface.mainWindow())
        if dialog.exec_() != QDialog.Accepted:
            feedback.pushInfo("  ⊘ Operation cancelled by user")
            return {}
        
        if not dialog.get_selected_layers():
            feedback.reportError("Failed to retrieve selected layers")
            return {}
        
        input_points_layer = dialog.input_points_layer
        terrain_layers = dialog.terrain_layers
        grid_layers = dialog.grid_layers
        
        feedback.pushInfo(f"  ✓ Input points: {input_points_layer.name()}")
        layer_names = [l.name() for l in terrain_layers]
        feedback.pushInfo(f"  ✓ Terrain layers: {', '.join(layer_names)}")
        feedback.pushInfo(f"  ✓ Grid layers to process: {len(grid_layers)}")
        
        # ====================================================================
        # STEP 2: Pre-scan for existing files and get overwrite preference
        # ====================================================================
        feedback.pushInfo("\nStep 2: Scanning for existing output files...")
        
        all_output_paths = []
        existing_files = []
        
        for grid_layer_name, grid_layer in grid_layers:
            try:
                base_name, grid_type = extract_scenario_base_from_grid_layer(grid_layer_name)
                if not base_name or not grid_type:
                    continue
                
                # Get raster directory from grid layer (normalized path)
                raster_uri = grid_layer.dataProvider().dataSourceUri()
                if not raster_uri:
                    continue
                raster_path = os.path.normpath(raster_uri)
                raster_dir = os.path.dirname(raster_path)
                
                # Derive output directory
                try:
                    po_path = derive_poline_path_from_raster(raster_path, "_d_*.tif")
                    output_dir = os.path.dirname(po_path) if os.path.exists(po_path) else raster_dir
                except Exception:
                    output_dir = raster_dir
                
                # Build output path
                plot_p_basename = f"{base_name}_PLOT_P_Sampled"
                output_path = os.path.normpath(os.path.join(output_dir, plot_p_basename + ".shp"))
                all_output_paths.append(output_path)
                
                # Check if file exists
                if os.path.exists(output_path):
                    existing_files.append(os.path.basename(output_path))
            except Exception:
                pass
        
        # Ask user for overwrite preference if needed
        overwrite_mode = 'skip'  # Default: skip existing files
        if existing_files:
            feedback.pushInfo(f"  ⚠ Found {len(existing_files)} existing output file(s)")
            
            dialog_ow = FileOverwriteDialog(existing_files, iface.mainWindow())
            if dialog_ow.exec_() != QDialog.Accepted:
                feedback.pushInfo("  ⊘ Operation cancelled by user")
                return {}
            
            if dialog_ow.user_choice == 'overwrite':
                overwrite_mode = 'overwrite'
                feedback.pushInfo("  ➤ Will overwrite existing files")
            elif dialog_ow.user_choice == 'skip':
                overwrite_mode = 'skip'
                feedback.pushInfo("  ➤ Will keep existing files")
            else:  # cancel
                feedback.pushInfo("  ⊘ Operation cancelled by user")
                return {}
        
        # ====================================================================
        # STEP 3: Process each grid layer - generate PLOT_P_Sampled files
        # ====================================================================
        feedback.pushInfo("\n" + "=" * 70)
        feedback.pushInfo("Step 3: Processing grid layers and generating sample point files")
        feedback.pushInfo("=" * 70)
        
        newly_generated_files = []
        all_files_to_load = []
        processed_count = 0
        processed_bases = set()
        
        for idx, (grid_layer_name, grid_layer) in enumerate(grid_layers):
            current = idx + 1
            total = len(grid_layers)
            
            try:
                feedback.pushInfo(f"\n[{current}/{total}] Processing: {grid_layer_name}")
                
                # Extract scenario base and grid type
                base_name, grid_type = extract_scenario_base_from_grid_layer(grid_layer_name)
                if not base_name or not grid_type:
                    feedback.pushInfo(f"  ⚠ Could not extract scenario base from layer name")
                    continue
                
                if base_name in processed_bases:
                    feedback.pushInfo(f"  → Skipping: Scenario '{base_name}' already processed in this batch")
                    continue
                processed_bases.add(base_name)
                
                type_names = {'d': 'Depth', 'h': 'Level', 'v': 'Velocity'}
                feedback.pushInfo(f"  Scenario: {base_name}")
                feedback.pushInfo(f"  Grid type: {grid_type} ({type_names.get(grid_type, 'Unknown')})")
                
                # Get raster directory
                raster_uri = grid_layer.dataProvider().dataSourceUri()
                if not raster_uri:
                    feedback.pushInfo(f"  ⚠ Could not determine raster directory")
                    continue
                raster_path = os.path.normpath(raster_uri)
                raster_dir = os.path.dirname(raster_path)
                
                # Find corresponding d/h/v rasters
                raster_map = find_corresponding_rasters(
                    grid_layer_name, raster_dir, base_name, grid_type
                )
                
                feedback.pushInfo(f"  → Searching for d/h/v rasters in: {os.path.basename(raster_dir)}")
                for key, path in raster_map.items():
                    if path:
                        feedback.pushInfo(f"    ✓ {key}: {os.path.basename(path)}")
                    else:
                        feedback.pushInfo(f"    ⚠ {key}: NOT FOUND (field will be empty)")
                
                # Sample raster values at input points
                feedback.pushInfo(f"  → Sampling raster values at {input_points_layer.featureCount()} points...")
                sample_layer = sample_rasters_at_points(
                    input_points_layer,
                    raster_map,
                    terrain_layers,
                    feedback=feedback
                )
                
                if not sample_layer:
                    feedback.reportError(f"    ✗ Failed to sample raster values")
                    continue
                
                # Determine output path
                try:
                    po_path = derive_poline_path_from_raster(raster_path, "_d_*.tif")
                    output_dir = os.path.dirname(po_path) if os.path.exists(po_path) else raster_dir
                except Exception:
                    output_dir = raster_dir
                
                plot_p_basename = f"{base_name}_PLOT_P_Sampled"
                output_path = os.path.normpath(os.path.join(output_dir, plot_p_basename + ".shp"))
                
                feedback.pushInfo(f"  → Saving to: {output_path}")
                success, error_msg, was_skipped = save_layer_to_shapefile(
                    sample_layer, output_path, feedback=feedback, overwrite_mode=overwrite_mode
                )
                
                # Cleanup
                sample_layer = None
                gc.collect()
                
                if not success:
                    feedback.reportError(f"    ✗ Failed to save: {error_msg}")
                    continue
                
                if not was_skipped:
                    newly_generated_files.append(output_path)
                    processed_count += 1
                
                all_files_to_load.append(output_path)
                feedback.pushInfo(f"  ✓ Complete: {plot_p_basename}")
                
            except Exception as e:
                feedback.reportError(f"  ✗ Error processing [{current}/{total}]: {str(e)}")
                gc.collect()
        
        # Verify files exist
        all_files_to_load = [f for f in all_files_to_load if os.path.exists(f)]
        
        if not all_files_to_load:
            feedback.reportError("No sample point files found or generated")
            return {}
        
        feedback.pushInfo(f"\n✓ Generated/Found {len(all_files_to_load)} sample point file(s)")
        if newly_generated_files:
            feedback.pushInfo(f"  ({processed_count} newly generated, {len(all_files_to_load) - processed_count} existing)")
        
        # ====================================================================
        # STEP 4: Load PLOT_P_Sampled files into QGIS
        # ====================================================================
        feedback.pushInfo("\n" + "=" * 70)
        feedback.pushInfo("Step 4: Loading sample point layers into QGIS")
        feedback.pushInfo("=" * 70)
        
        # Determine insertion point in layer tree
        root = QgsProject.instance().layerTreeRoot()
        insert_target = root
        insert_index = 0
        
        try:
            layer_tree_view = iface.layerTreeView()
            current_node = layer_tree_view.currentNode()
            
            if current_node:
                if isinstance(current_node, QgsLayerTreeLayer):
                    insert_target = current_node.parent()
                    insert_index = insert_target.children().index(current_node)
                elif isinstance(current_node, QgsLayerTreeGroup):
                    insert_target = current_node
                    insert_index = 0
        except Exception:
            pass
        
        # Sort files for consistent order (alphabetically, reversed for insertion at top)
        sorted_files = sorted(all_files_to_load, key=lambda x: os.path.basename(x).lower(), reverse=True)
        
        loaded_layers = []
        loaded_count = 0
        
        for idx, plot_p_path in enumerate(sorted_files):
            current = idx + 1
            total = len(sorted_files)
            
            try:
                if os.path.exists(plot_p_path):
                    layer_name = os.path.splitext(os.path.basename(plot_p_path))[0]
                    feedback.pushInfo(f"[{current}/{total}] Loading: {layer_name}")
                    
                    lyr = load_vector_with_fallback(plot_p_path, layer_name)
                    
                    if lyr and lyr.isValid():
                        QgsProject.instance().addMapLayer(lyr, False)
                        insert_target.insertLayer(insert_index, lyr)
                        loaded_layers.append(lyr.name())
                        
                        # Apply style if available
                        try:
                            StyleManager.apply_style_to_layer(lyr)
                            feedback.pushInfo(f"  ✓ Loaded with style")
                        except Exception as style_error:
                            feedback.pushInfo(f"  ✓ Loaded (style skipped: {str(style_error)})")
                        
                        loaded_count += 1
                    else:
                        feedback.reportError(f"  ✗ Failed to load: {plot_p_path}")
                else:
                    feedback.reportError(f"  ✗ File not found: {plot_p_path}")
                    
            except Exception as e:
                feedback.reportError(f"  ✗ Error loading [{current}/{total}]: {str(e)}")
        
        # ====================================================================
        # STEP 5: Store state in global variables for downstream tools
        # ====================================================================
        try:
            QgsExpressionContextUtils.setGlobalVariable(
                'tuflow_latest_sample_layers',
                json.dumps(loaded_layers)
            )
            QgsExpressionContextUtils.setGlobalVariable(
                'tuflow_latest_sample_files',
                json.dumps(all_files_to_load)
            )
            QgsExpressionContextUtils.setGlobalVariable(
                'tuflow_latest_sample_time',
                QDateTime.currentDateTime().toString()
            )
        except Exception:
            pass
        
        # Final cleanup
        gc.collect()
        
        feedback.pushInfo("\n" + "=" * 70)
        feedback.pushInfo(f"✓ Successfully loaded {loaded_count} sample point layer(s)")
        feedback.pushInfo("=" * 70)
        
        return {}
