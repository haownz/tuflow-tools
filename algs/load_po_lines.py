# -*- coding: utf-8 -*-
import os
import glob
import math
import re
import csv
import json
from pathlib import Path
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget, 
                                 QTableWidgetItem, QHeaderView, QPushButton, QHBoxLayout,
                                 QComboBox, QMessageBox, QFileDialog, QAbstractItemView,
                                 QTextEdit, QApplication, QCheckBox)
from qgis.PyQt.QtCore import Qt, QVariant, QDateTime
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsProcessingAlgorithm, QgsExpressionContextUtils, 
                       QgsVectorLayer, QgsProject, QgsField, QgsVectorFileWriter, 
                       QgsLayerTreeGroup, QgsLayerTreeLayer, QgsRasterLayer)
from qgis.utils import iface
from .po_common import (derive_poline_path_from_raster, load_vector_with_fallback, 
                       resolve_csv_paths_from_layer, compute_max_map_for_csv, normalize_id,
                       guess_selected_raster, source_path_from_layer)
from ..style_manager import StyleManager

# ============================================================================
# Helper functions for QP update
# ============================================================================

def _read_csv_rows(path):
    """Read CSV file with automatic encoding and delimiter detection.
    Args:
        path: Path to CSV file
    Returns:
        List of rows (lists), or empty list if file not found or parsing fails
    """
    if not os.path.exists(path):
        return []
    encodings = ["utf-8-sig", "utf-8"]
    delims = [",", ";", "\t"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                text = f.read()
            lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))]
            for d in delims:
                try:
                    reader = csv.reader(lines, delimiter=d)
                    rows = [row for row in reader if row]
                    if any(len(r) > 1 for r in rows):
                        return rows
                except Exception:
                    continue
        except Exception:
            continue
    return []

def _digits_only(s):
    """Extract consecutive digits from string.
    Args:
        s: Input string
    Returns:
        String of consecutive digits, or None if no digits found
    Example: 'Q2D3' -> '23', 'Pump_001' -> '001'
    """
    if s is None: return None
    m = re.findall(r"\d+", str(s))
    return "".join(m) if m else None

def _debracket(s):
    if s is None: return ""
    s2 = re.sub(r"^\s*Q\s+", "", str(s).strip(), flags=re.IGNORECASE)
    s2 = re.split(r"\s*\[", s2)[0].strip()
    s2 = s2.replace("  ", " ").strip()
    return s2

def station_variants(strict_id):
    """Build robust variants for TUFLOW station IDs."""
    s = str(strict_id).strip()
    tokens = {s, s.upper(), s.replace(" ", ""), s.replace("_", ""), s.replace(" ", "_")}
    variants = set(t for t in tokens if t)
    base = list(variants)
    for b in base:
        variants.add("Q" + b)
        variants.add("Q " + b)
    return variants

def id_variants(id_value, pad_widths=(2, 3, 4)):
    variants = set()
    if id_value is None: return variants
    s_raw = str(id_value).strip()
    if s_raw:
        variants.add(s_raw)
        variants.add(s_raw.upper())
    digits = _digits_only(s_raw)
    if digits:
        nonpad = str(int(digits))
        variants.add(nonpad)
        for w in pad_widths: variants.add(nonpad.zfill(w))
        variants.add(digits)
    base_list = list(variants)
    for b in base_list:
        variants.add("Q" + b)
        variants.add("Q " + b)
    return variants

def normalize_id_consistently(raw_id):
    """Apply consistent normalization for all ID lookups (QP, QV, etc).
    Removes 'Q ' prefix, bracket suffix, normalizes spaces.
    Returns normalized string.
    """
    if raw_id is None:
        return ""
    s = str(raw_id).strip()
    # Remove 'Q ' prefix (case-insensitive)
    s = re.sub(r"^\s*Q\s+", "", s, flags=re.IGNORECASE)
    # Remove everything after '[' bracket
    s = re.split(r"\s*\[", s)[0].strip()
    # Normalize multiple spaces to single space
    s = s.replace("  ", " ").strip()
    return s

def _parse_time_token(token, unit_hint=None):
    """Parse time value from string with optional unit specification.
    Args:
        token: Time token (e.g., '1.5hr', '30min', '1800')
        unit_hint: Optional unit hint ('h', 'min', 's')
    Returns:
        Time in seconds as float, or None if parsing fails
    """
    s = (token or "").strip()
    if not s: return None
    try:
        val = float(s)
        if unit_hint == 'h': return val * 3600.0
        if unit_hint == 'min': return val * 60.0
        return val
    except: pass
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|second|seconds|min|minutes|hr|hour|hours)?\s*$", s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()
        mult = 3600.0 if unit.startswith("hour") or unit.startswith("hr") else (60.0 if unit.startswith("min") else 1.0)
        return val * mult
    return None

def _integrate_trapezoid(times, q_series):
    if not times or len(times) < 2: return 0.0
    n = min(len(times), len(q_series))
    vol = 0.0
    for i in range(n - 1):
        dt = times[i + 1] - times[i]
        qi = q_series[i]; qj = q_series[i+1]
        vol += 0.5 * (qi + qj) * max(0.0, dt)
    return vol

def compute_volume_map_for_csv(path, skip_cols):
    rows = _read_csv_rows(path)
    if not rows: return {}, "Empty CSV"
    header = rows[0]
    col_oriented = (len(header) > 1 and header[1].lower().startswith("time"))
    if col_oriented:
        unit_hint = 'h' if 'h' in header[1].lower() else None
        times = []
        for r in rows[1:]:
            t = _parse_time_token(r[1] if len(r)>1 else None, unit_hint)
            times.append(t if t is not None else (times[-1]+1.0 if times else 0.0))
        vol_map = {}
        for j in range(skip_cols, len(header)):
            q_series = [float(r[j]) if j<len(r) else 0.0 for r in rows[1:]]
            vol = _integrate_trapezoid(times, q_series)
            clean = _debracket(header[j])
            for k in station_variants(clean): vol_map[k] = vol
        return vol_map, "column-oriented"
    return {}, "row-oriented not fully supported for volume"

def update_qp_for_layer(po_layer, rel_dir="../csv", skip_cols=2, feedback=None):
    if not po_layer or not po_layer.isValid(): return None, 0, 0, []
    
    mem_layer = make_memory_copy(po_layer, feedback)
    if not mem_layer: return None, 0, 0, []
    
    mem_layer.startEditing()
    found, tried, csv_dir, base_dir = resolve_csv_paths_from_layer(po_layer, rel_dir)
    csvs = {tag: str(path) for tag, path in found.items() if path}
    
    if not csvs:
        mem_layer.rollBack()
        return None, 0, 0, []

    maps = {tag: compute_max_map_for_csv(path, skip_cols) for tag, path in csvs.items()}
    vol_maps = {tag: compute_volume_map_for_csv(path, skip_cols)[0] for tag, path in csvs.items()}
    
    for f_name in ["QP", "QV"]:
        if mem_layer.fields().indexOf(f_name) == -1:
            mem_layer.dataProvider().addAttributes([QgsField(f_name, QVariant.Double, 'Double', 15, 5)])
    mem_layer.updateFields()
    
    qp_idx = mem_layer.fields().indexOf("QP")
    qv_idx = mem_layer.fields().indexOf("QV")
    id_idx = mem_layer.fields().indexOf("ID")
    
    changes = {}
    u_qp, u_qv = 0, 0
    
    for feat in mem_layer.getFeatures():
        raw_id = feat.attribute(id_idx)
        fid = feat.id(); entry = {}
        
        # Use consistent normalization for both QP and QV lookups
        normalized_id = normalize_id_consistently(raw_id)
        
        # QP Lookup - ID variants (numeric padding options)
        qp_variants = id_variants(normalized_id)
        for tag in ("1d", "2d"):
            for v in qp_variants:
                val = maps.get(tag, {}).get(v)
                if val is not None:
                    entry[qp_idx] = round(float(val), 5); u_qp += 1; break
            if qp_idx in entry: break
            
        # QV Lookup - Station variants (case/format variations)
        qv_variants = station_variants(normalized_id)
        for tag in ("1d", "2d"):
            for v in qv_variants:
                val = vol_maps.get(tag, {}).get(v)
                if val is not None:
                    entry[qv_idx] = round(float(val), 1); u_qv += 1; break
            if qv_idx in entry: break
        
        if entry: changes[fid] = entry

    mem_layer.dataProvider().changeAttributeValues(changes)
    mem_layer.commitChanges()
    return mem_layer, u_qp, u_qv, []

def make_memory_copy(src_layer, feedback=None):
    """Create in-memory copy of vector layer with validated CRS.
    Returns memory layer, or None if CRS is invalid.
    """
    crs = src_layer.crs()
    if not crs or not crs.isValid():
        if feedback:
            feedback.reportError(f"Source layer '{src_layer.name()}' has invalid CRS")
        return None
    
    uri = f"LineString?crs={crs.authid()}"
    mem_layer = QgsVectorLayer(uri, f"{src_layer.name()}_temp", "memory")
    mem_dp = mem_layer.dataProvider()
    mem_dp.addAttributes(src_layer.fields())
    mem_layer.updateFields()
    mem_dp.addFeatures([f for f in src_layer.getFeatures()])
    return mem_layer

def save_layer_to_shapefile(layer, output_path, feedback=None):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'ESRI Shapefile'; options.fileEncoding = 'UTF-8'
    ret, msg = QgsVectorFileWriter.writeAsVectorFormat(layer, output_path, options)
    return ret == QgsVectorFileWriter.NoError, msg

# ============================================================================
# Helper for suffix detection
# ============================================================================
def _guess_suffix(path):
    fname = os.path.basename(path)
    for t in ['d', 'h', 'v', 'q', 'dt']:
        if f"_{t}_" in fname: return f"_{t}_*.tif"
        if fname.lower().endswith(f"_{t}.tif"): return f"_{t}.tif"
    return "_d_*.tif"

# ============================================================================
# Dialogs
# ============================================================================

class FileOverwriteDialog(QDialog):
    def __init__(self, existing_files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Files Exist")
        self.setMinimumSize(600, 400); self.user_choice = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Found {len(existing_files)} existing files:"))
        txt = QTextEdit(); txt.setReadOnly(True); txt.setText("\n".join(existing_files))
        layout.addWidget(txt)
        btn_lyt = QHBoxLayout()
        for t, c in [("Cancel", self.reject), ("Skip All", self.accept), ("Overwrite All", self.accept)]:
            btn = QPushButton(t); btn.clicked.connect(c)
            if t == "Overwrite All": btn.setStyleSheet("background-color: #ff9999;")
            btn_lyt.addWidget(btn)
            btn.clicked.connect(lambda checked, arg=t.split()[0].lower(): setattr(self, 'user_choice', arg))
        layout.addLayout(btn_lyt)

class PreviewDialog(QDialog):
    def __init__(self, latest_files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview PO Lines"); self.setMinimumSize(600, 400)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(latest_files), 3)
        self.table.setHorizontalHeaderLabels(["Select", "Scenario", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 50)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        
        for i, f in enumerate(latest_files):
            cb = QCheckBox()
            cb.setChecked(False)
            self.table.setCellWidget(i, 0, cb)
            
            name = os.path.splitext(os.path.basename(f))[0]
            item_name = QTableWidgetItem(name)
            item_name.setData(Qt.UserRole, f)
            self.table.setItem(i, 1, item_name)
            
            try:
                path = derive_poline_path_from_raster(f, _guess_suffix(f))
                status = "Ready" if os.path.exists(path) else "Missing"
            except Exception:
                status = "Skipped"
            item = QTableWidgetItem(status)
            if status == "Missing": item.setForeground(QColor(200, 0, 0))
            self.table.setItem(i, 2, item)
            
        self.table.resizeColumnToContents(2)
        self.table.setColumnWidth(2, int(self.table.columnWidth(2) * 1.5))
            
        layout.addWidget(self.table)
        
        sel_lyt = QHBoxLayout()
        btn_all = QPushButton("Select All"); btn_all.clicked.connect(lambda: self.set_all(True))
        btn_none = QPushButton("Clear All"); btn_none.clicked.connect(lambda: self.set_all(False))
        sel_lyt.addWidget(btn_all); sel_lyt.addWidget(btn_none); sel_lyt.addStretch()
        layout.addLayout(sel_lyt)
        
        btn_lyt = QHBoxLayout(); btn_lyt.addStretch()
        ok = QPushButton("Load"); ok.clicked.connect(self.accept); btn_lyt.addWidget(ok)
        layout.addLayout(btn_lyt)

    def set_all(self, state):
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb: cb.setChecked(state)

    def get_selected_files(self):
        selected = []
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            if cb and cb.isChecked():
                item = self.table.item(i, 1)
                if item: selected.append(item.data(Qt.UserRole))
        return selected

# ============================================================================
# Main Algorithm
# ============================================================================

class LoadPOLinesAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self): return LoadPOLinesAlgorithm()
    def name(self): return 'load_po_lines'
    def displayName(self): return '2 - Load PO Lines'
    def group(self): return '2 - Result Analysis'
    def groupId(self): return 'result_analysis'

    def initAlgorithm(self, config=None):
        """
        Must be overridden. 
        Even if you don't have standard input parameters, this method cannot be absent.
        """
        pass

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo("Loading TUFLOW plot output (PO) lines...")
        
        # 1. Get history from global variable using the corrected scope method
        latest_files_str = QgsExpressionContextUtils.globalScope().variable('tuflow_latest_raster_files')
        
        if not latest_files_str:
            feedback.reportError("No TUFLOW raster history found. Please run 'Load Grid Output' first.")
            return {}
            
        try:
            # Parse the JSON list stored in the global variable
            latest_files = json.loads(latest_files_str)
        except Exception as e:
            feedback.pushInfo(f"Note: Could not parse history data: {str(e)}")
            latest_files = []

        # Filter latest_files to only include those currently loaded in the project
        loaded_paths = set()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                src = layer.source()
                if os.path.exists(src):
                    loaded_paths.add(os.path.normpath(src).lower())
        
        latest_files = [f for f in latest_files if os.path.normpath(f).lower() in loaded_paths]
        
        # 1b. Fallback: If no history files are active, try the current selected raster layer
        if not latest_files:
            feedback.pushInfo("No active TUFLOW history files found. Checking selected layout layer...")
            sel_lyr = guess_selected_raster()
            if sel_lyr:
                src = str(source_path_from_layer(sel_lyr))
                if os.path.exists(src):
                    latest_files = [src]
                    feedback.pushInfo(f"Using selected raster layer: {sel_lyr.name()}")

        if not latest_files:
            feedback.reportError("None of the previously loaded grid files are currently active in the Layers panel, "
                               "and no raster layer is currently selected.")
            return {}

        # 2. Show the preview dialog
        dialog = PreviewDialog(latest_files)
        if dialog.exec_() != QDialog.Accepted:
            feedback.pushInfo("Operation cancelled by user")
            return {}
        
        latest_files = dialog.get_selected_files()
        if not latest_files:
            feedback.pushInfo("No files selected.")
            return {}

        # 3. Scan for existing files and handle overwrite logic
        all_qp_paths = []
        existing_names = []
        for rf in latest_files:
            try:
                po_p = derive_poline_path_from_raster(rf, _guess_suffix(rf))
                if os.path.exists(po_p):
                    qp_p = os.path.join(os.path.dirname(po_p), os.path.splitext(os.path.basename(po_p))[0] + "_QP.shp")
                    all_qp_paths.append(qp_p)
                    if os.path.exists(qp_p):
                        existing_names.append(os.path.basename(qp_p))
            except Exception:
                continue

        mode = 'skip'
        if existing_names:
            ov_dlg = FileOverwriteDialog(existing_names, iface.mainWindow())
            if ov_dlg.exec_() != QDialog.Accepted:
                feedback.pushInfo("Operation cancelled by user")
                return {}
            mode = ov_dlg.user_choice
            feedback.pushInfo(f"Mode: {mode.upper()} existing files")

        # 4. Process and generate QP files with progress reporting
        loaded_results = []
        total_files = len(latest_files)
        for idx, rf in enumerate(latest_files):
            progress = int((idx / total_files * 100)) if total_files > 0 else 0
            feedback.setProgress(progress)
            feedback.pushInfo(f"[{idx+1}/{total_files}] Processing: {os.path.basename(rf)}")
            
            try:
                po_p = derive_poline_path_from_raster(rf, _guess_suffix(rf))
            except Exception:
                feedback.pushInfo(f"  ⚠ Could not derive PO path (pattern mismatch)")
                continue

            if not os.path.exists(po_p):
                feedback.pushInfo(f"  ⚠ PO file not found")
                continue
            
            qp_p = os.path.join(os.path.dirname(po_p), os.path.splitext(os.path.basename(po_p))[0] + "_QP.shp")
            if os.path.exists(qp_p) and mode == 'skip':
                feedback.pushInfo(f"  ⊘ Skipping existing file")
                loaded_results.append(qp_p)
                continue

            try:
                po_lyr = load_vector_with_fallback(po_p, "temp")
                upd_lyr, u_qp, u_qv, _ = update_qp_for_layer(po_lyr, feedback=feedback)
                if upd_lyr:
                    if save_layer_to_shapefile(upd_lyr, qp_p, feedback)[0]:
                        feedback.pushInfo(f"  ✓ Updated {u_qp} QP and {u_qv} QV fields")
                        loaded_results.append(qp_p)
                    else:
                        feedback.reportError(f"  ✗ Failed to save QP file")
                else:
                    feedback.reportError(f"  ✗ Failed to process layer")
            except Exception as e:
                feedback.reportError(f"  ✗ Error: {str(e)}")

        # 5. Layer Tree Insertion Logic (Identical to load_grid_output.py)
        feedback.pushInfo(f"\nLoading {len(loaded_results)} QP layers into project...")
        
        root = QgsProject.instance().layerTreeRoot()
        view = iface.layerTreeView()
        current_node = view.currentNode()
        
        target_parent = root
        insert_index = 0
        
        if current_node:
            if isinstance(current_node, QgsLayerTreeLayer):
                target_parent = current_node.parent()
                insert_index = target_parent.children().index(current_node)
            elif isinstance(current_node, QgsLayerTreeGroup):
                target_parent = current_node
                insert_index = 0
        
        # Sort files alphabetically to maintain consistent visual order
        sorted_qp_files = sorted(loaded_results, key=lambda x: os.path.basename(x).lower())
        for qp_path in reversed(sorted_qp_files):
            layer_name = os.path.splitext(os.path.basename(qp_path))[0]
            lyr = load_vector_with_fallback(qp_path, layer_name)
            
            if lyr and lyr.isValid():
                QgsProject.instance().addMapLayer(lyr, False)
                target_parent.insertLayer(insert_index, lyr)
                feedback.pushInfo(f"  ✓ Loaded: {layer_name}")
                try:
                    StyleManager.apply_style_to_layer(lyr)
                except Exception as style_error:
                    feedback.pushInfo(f"Style skipped for {lyr.name()}: {str(style_error)}")
        
        feedback.pushInfo(f"\n✓ Complete: Loaded {len(loaded_results)} QP layer(s)")
        return {}