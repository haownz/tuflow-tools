# -*- coding: utf-8 -*-
"""
TUFLOW tools — Flow Plot Viewer (Enhanced multi-layer support)

IMPROVEMENTS:
- Layer Management Table: Select which vector layers (PO Lines) to include in the plot
- Auto-detect selected features across enabled vector layers
- Multiple PO lines overlay in same plot with distinct colors
- Peak flow annotation toggle ("Show Peaks") and Volume toggle ("Show Volume")
- Legend displays "Layer Name • ID" for clarity across multiple layers
- Interactive pan/zoom via Matplotlib NavigationToolbar2QT

FEATURES:
- Exact, full-string ID matching for 'Q <ID>' (NO numeric coercion, NO prefix/fuzzy matching)
- Robust CSV discovery for each layer (2D and optional 1D)
- Auto-toggle behavior: single series enables both toggles; multi-series disables them
- Bottom info shows peak flow and total volume for every series
- Layer table updates automatically when layers are added/removed
- Reference to cross sections along alignment (via PO Line IDs)

Author: Hao Wu
"""

from __future__ import annotations
import csv
import re
from pathlib import Path
from typing import Optional, List, Tuple, Dict

from qgis.PyQt import QtCore
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QAbstractItemView,
    QMessageBox,
)
from qgis.PyQt.QtCore import Qt, QSettings

from qgis.core import QgsMapLayer, QgsProject
from qgis.utils import iface

# Qt-agnostic backend (works for Qt5 and Qt6)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure

# Strong refs to keep windows alive
_LIVE_WINDOWS: List[QDialog] = []

# Matplotlib default color cycle (fallback)
_DEFAULT_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


class TimeSeriesPlotWindow(QDialog):
    """
    TUFLOW tools — Flow Plot Viewer.
    Auto-scans selected features across all vector layers and overlays their Q time series.
    """

    def __init__(
        self,
        layer,
        # Explicit 2D (required by algorithm for initial load)
        csv_path_2d: str,
        time_header_2d: Optional[str],
        q_headers_2d: Optional[List[str]],
        parent=None,
        # Explicit 1D (optional; if absent we try discovery)
        csv_path_1d: Optional[str] = None,
        time_header_1d: Optional[str] = None,
        q_headers_1d: Optional[List[str]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("TUFLOW tools — Flow Plot Viewer")
        self.resize(1200, 900)  # Increased height for better space

        # Inputs (initial)
        self.layer: Optional[QgsMapLayer] = layer
        self.csv_path_2d = Path(csv_path_2d).resolve() if csv_path_2d else None
        self.time_header_2d = time_header_2d or ""
        self.q_headers_2d = q_headers_2d or []

        self._explicit_1d: Dict[str, object] = {
            "path": Path(csv_path_1d).resolve() if csv_path_1d else None,
            "time": time_header_1d,
            "headers": q_headers_1d,
        }

        # ----- Matplotlib figure/canvas -----
        self._fig = Figure(constrained_layout=True)
        self._ax = self._fig.add_subplot(111)  # left y-axis: Flow (Q)
        self._ax2 = None  # right y-axis (created when needed)
        self._canvas = FigureCanvas(self._fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)  # pan/zoom/home/save

        # ----- Top controls -----
        self._chk_volume: QCheckBox = QCheckBox("Show Volume")
        self._chk_volume.setChecked(True)
        self._chk_volume.stateChanged.connect(self._on_toggle_changed)

        self._chk_peak: QCheckBox = QCheckBox("Show Peaks")
        self._chk_peak.setChecked(True)
        self._chk_peak.stateChanged.connect(self._on_toggle_changed)

        # ----- Bottom info label -----
        self._hint = QLabel(
            "Select features (with exact ID strings) in one or more vector layers."
        )
        self._hint.setWordWrap(True)

        # ----- Layer management table -----
        self._layer_table = QTableWidget(0, 5)
        self._layer_table.setHorizontalHeaderLabels(
            ["Include", "Vector Layer (PO Lines)", "First ID", "Qp (m³/s)", "V (m³)"]
        )
        self._layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._layer_table.setColumnWidth(0, 60)
        self._layer_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._layer_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._layer_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self._layer_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeToContents
        )
        self._layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._layer_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._layer_table.setMaximumHeight(150)  # Fixed height for layer list

        # Buttons for layer management
        btn_add = QPushButton("Add Selected")
        btn_add.clicked.connect(self._on_add_layer)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._on_remove_layer)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_remove)
        btn_layout.addStretch()

        # Layout (no layer panel)
        right = QWidget()
        right_lay = QVBoxLayout()

        ctrl = QHBoxLayout()
        ctrl.addWidget(self._chk_volume)
        ctrl.addWidget(self._chk_peak)
        ctrl.addStretch(1)

        right_lay.addLayout(ctrl)
        right_lay.addWidget(self._toolbar)  # interactive toolbar
        right_lay.addWidget(self._canvas, stretch=1)  # Give canvas stretch factor
        right_lay.addWidget(QLabel("PO Lines / Vector Layers:"))  # Section label
        right_lay.addLayout(btn_layout)  # Add/Remove buttons
        right_lay.addWidget(self._layer_table)  # Layer management table
        right_lay.addWidget(self._hint)
        # Bottom stretch to push everything to top
        right_lay.addStretch()
        right.setLayout(right_lay)

        main_lay = QVBoxLayout()
        main_lay.addWidget(right)
        self.setLayout(main_lay)

        # Keep a strong ref to avoid garbage collection after Processing returns
        _LIVE_WINDOWS.append(self)

        # Debounce timer for refreshes to avoid heavy repeated IO blocking the UI
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)  # ms
        self._refresh_timer.timeout.connect(self._refresh_plot)

        # Guard flags to prevent re-entrant refreshes
        self._refresh_in_progress = False
        self._pending_refresh = False

        # Sources cache per layer: {layer_id: [ {path, time, headers}, ... ] }
        self._layer_sources: Dict[str, List[Dict[str, object]]] = {}

        # Track which layers are enabled/included in the plot
        self._layer_enabled: Dict[str, bool] = {}

        # Store layer metrics: {layer_id: {first_id, peak_q, total_vol_m3}}
        self._layer_metrics: Dict[str, Dict[str, object]] = {}

        # Last plot payloads (one dict per series) for redraw
        self._last_plot_payloads: List[Dict[str, object]] = []

        # Hover annotation structures
        self._hover_lines = []
        self._annot = None
        self._canvas.mpl_connect("motion_notify_event", self._on_plot_hover)

        # Load saved settings
        self._load_settings()

        # Prepare initial sources
        self._prepare_sources_initial()

        # Bind selectionChanged for ALL vector layers in the project
        self._bind_selection_listeners_for_all_layers()

        # Listen for ACTIVE layer changes (refresh sources & redraw)
        try:
            iface.currentLayerChanged.connect(self._on_current_layer_changed)
        except Exception:
            pass

        # Initial refresh (debounced)
        self._refresh_timer.start()

    # --------------------- SOURCES (initial active layer) ---------------------
    def _prepare_sources_initial(self):
        """
        Build sources for the initial active layer:
        - 2D from explicit inputs
        - 1D from explicit inputs (if provided)
        - Otherwise, try *_1d_Q.csv discovery
        """
        if self.layer and self.layer.type() == self.layer.VectorLayer:
            layer_id = self.layer.id()
            self._layer_sources[layer_id] = []

            # 2D source (provided)
            if self.csv_path_2d and self.csv_path_2d.is_file():
                time_hdr = self.time_header_2d or self._detect_time_header(
                    self.csv_path_2d
                )
                headers = self.q_headers_2d or self._read_headers(self.csv_path_2d)
                self._layer_sources[layer_id].append(
                    {"path": self.csv_path_2d, "time": time_hdr, "headers": headers}
                )

            # 1D — prefer explicit inputs, else fallback to discovery near the 2D path
            explicit = self._explicit_1d
            if (
                explicit["path"]
                and isinstance(explicit["path"], Path)
                and explicit["path"].is_file()
            ):
                time_hdr_1d = explicit["time"] or self._detect_time_header(
                    explicit["path"]
                )
                headers_1d = explicit["headers"] or self._read_headers(explicit["path"])
                self._layer_sources[layer_id].append(
                    {
                        "path": explicit["path"],
                        "time": time_hdr_1d,
                        "headers": headers_1d,
                    }
                )
                return  # explicit found; skip discovery

            if self.csv_path_2d:
                name = self.csv_path_2d.name
                if name.lower().endswith("_2d_q.csv"):
                    csv_path_1d = self.csv_path_2d.with_name(name[:-8] + "1d_Q.csv")
                else:
                    csv_path_1d = self.csv_path_2d.with_name(
                        self.csv_path_2d.stem + "_1d_Q.csv"
                    )

                if csv_path_1d and csv_path_1d.is_file():
                    t1d = self._detect_time_header(csv_path_1d)
                    h1d = self._read_headers(csv_path_1d)
                    self._layer_sources[layer_id].append(
                        {"path": csv_path_1d.resolve(), "time": t1d, "headers": h1d}
                    )
                    return

                # Ancestor search for *_1d_Q.csv
                for ancestor in [
                    self.csv_path_2d.parent,
                    self.csv_path_2d.parent.parent,
                    self.csv_path_2d.parent.parent.parent,
                ]:
                    for cand in ancestor.glob("*_1d_Q.csv"):
                        if cand.is_file():
                            self._layer_sources[layer_id].append(
                                {
                                    "path": cand.resolve(),
                                    "time": self._detect_time_header(cand),
                                    "headers": self._read_headers(cand),
                                }
                            )
                            return

    # --------------------- LISTENER BINDING ---------------------
    def _bind_selection_listeners_for_all_layers(self):
        """
        Bind selectionChanged for ALL vector layers so any selection update triggers a redraw.
        """
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() != lyr.VectorLayer:
                continue
            try:
                lyr.selectionChanged.connect(
                    self._on_any_selection_changed, Qt.QueuedConnection
                )
            except Exception:
                pass

    def _on_any_selection_changed(self, *args):
        """
        Any layer's selection change triggers a redraw.
        """
        # Debounced refresh to avoid heavy IO on every selectionChanged signal
        try:
            self._refresh_timer.start()
        except Exception:
            QtCore.QTimer.singleShot(0, self._refresh_plot)

    # --------------------- LAYER TABLE MANAGEMENT ---------------------
    def _on_add_layer(self):
        """
        Add selected vector layers from the Layers panel to the plot.
        """
        layers = iface.layerTreeView().selectedLayers()
        added = False

        for layer in layers:
            if layer.type() == layer.VectorLayer and layer.isValid():
                if layer.id() not in self._layer_enabled:
                    self._layer_enabled[layer.id()] = True
                    added = True

        if added:
            self._refresh_layer_table()
            # Trigger refresh
            try:
                self._refresh_timer.start()
            except Exception:
                QtCore.QTimer.singleShot(0, self._refresh_plot)
        else:
            QMessageBox.warning(
                self,
                "Add Layer",
                "Please select valid vector layer(s) in the Layers panel.",
            )

    def _on_remove_layer(self):
        """
        Remove the selected layer from the table.
        """
        row = self._layer_table.currentRow()
        if row >= 0:
            # Get layer ID from table and remove
            layer_item = self._layer_table.item(row, 1)
            if layer_item:
                layer_id = layer_item.data(QtCore.Qt.UserRole)
                if layer_id in self._layer_enabled:
                    del self._layer_enabled[layer_id]
                self._refresh_layer_table()
                # Trigger refresh
                try:
                    self._refresh_timer.start()
                except Exception:
                    QtCore.QTimer.singleShot(0, self._refresh_plot)
            # Trigger refresh
            try:
                self._refresh_timer.start()
            except Exception:
                QtCore.QTimer.singleShot(0, self._refresh_plot)

    def _refresh_layer_table(self):
        """
        Refresh the layer management table to show currently selected layers with checkboxes and metrics.
        """
        self._layer_table.blockSignals(True)
        self._layer_table.setRowCount(0)

        for row, layer_id in enumerate(self._layer_enabled.keys()):
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer and layer.type() == layer.VectorLayer:
                # Checkbox column
                chk = QCheckBox()
                chk.setChecked(self._layer_enabled[layer_id])
                chk.layer_id = layer_id
                chk.stateChanged.connect(self._on_layer_checkbox_changed)

                self._layer_table.insertRow(row)
                self._layer_table.setCellWidget(row, 0, chk)

                # Layer name column
                item = QTableWidgetItem(layer.name())
                item.setData(QtCore.Qt.UserRole, layer_id)
                self._layer_table.setItem(row, 1, item)

                # Get metrics for this layer
                metrics = self._layer_metrics.get(layer_id, {})
                first_id = metrics.get("first_id", "-")
                peak_q = metrics.get("peak_q", "-")
                total_vol = metrics.get("total_vol_m3", "-")

                # First ID column
                id_item = QTableWidgetItem(str(first_id))
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                self._layer_table.setItem(row, 2, id_item)

                # Qp column
                if isinstance(peak_q, (int, float)):
                    qp_text = f"{peak_q:.3f}"
                else:
                    qp_text = str(peak_q)
                qp_item = QTableWidgetItem(qp_text)
                qp_item.setFlags(qp_item.flags() & ~Qt.ItemIsEditable)
                self._layer_table.setItem(row, 3, qp_item)

                # V column
                if isinstance(total_vol, (int, float)):
                    v_text = f"{total_vol:,.0f}"
                else:
                    v_text = str(total_vol)
                v_item = QTableWidgetItem(v_text)
                v_item.setFlags(v_item.flags() & ~Qt.ItemIsEditable)
                self._layer_table.setItem(row, 4, v_item)

        self._layer_table.blockSignals(False)

    def _on_layer_checkbox_changed(self, state):
        """
        Handle layer checkbox changes and trigger a refresh.
        """
        sender = self.sender()
        if hasattr(sender, "layer_id"):
            layer_id = sender.layer_id
            self._layer_enabled[layer_id] = sender.isChecked()
            # Trigger refresh
            try:
                self._refresh_timer.start()
            except Exception:
                QtCore.QTimer.singleShot(0, self._refresh_plot)

    # --------------------- SETTINGS PERSISTENCE ---------------------
    def _load_settings(self):
        """
        Load saved UI settings (Show Volume, Show Peaks toggle states and layer list).
        """
        settings = QSettings("QGIS", "TUFLOW_Tools")
        self._user_show_volume = settings.value("qplot/show_volume", True, type=bool)
        self._user_show_peaks = settings.value("qplot/show_peaks", True, type=bool)

        self._chk_volume.blockSignals(True)
        self._chk_peak.blockSignals(True)
        self._chk_volume.setChecked(self._user_show_volume)
        self._chk_peak.setChecked(self._user_show_peaks)
        self._chk_volume.blockSignals(False)
        self._chk_peak.blockSignals(False)

        # Restore enabled layers
        saved_layers = settings.value("qplot/layer_enabled", {}, type=dict)
        if isinstance(saved_layers, dict):
            # Only keep layers that still exist in the current project
            self._layer_enabled = {}
            for layer_id, enabled in saved_layers.items():
                if QgsProject.instance().mapLayer(layer_id):
                    self._layer_enabled[layer_id] = bool(enabled)
        else:
            self._layer_enabled = {}

    def _save_settings(self):
        """
        Save UI settings (Show Volume, Show Peaks toggle states and layer list).
        """
        settings = QSettings("QGIS", "TUFLOW_Tools")
        settings.setValue("qplot/show_volume", getattr(self, "_user_show_volume", True))
        settings.setValue("qplot/show_peaks", getattr(self, "_user_show_peaks", True))
        settings.setValue("qplot/layer_enabled", self._layer_enabled)

    # --------------------- ACTIVE LAYER CHANGE ---------------------
    def _on_current_layer_changed(self, new_layer: Optional[QgsMapLayer]):
        """
        When the user switches active layer in QGIS:
        - Update self.layer
        - Prepare sources for the new layer
        - (Re)bind selection listeners for all layers
        - Redraw
        """
        self.layer = new_layer

        # Ensure sources for the new active layer
        if self.layer and self.layer.type() == self.layer.VectorLayer:
            self._prepare_sources_for_layer(self.layer)

        # (Re)bind listeners (defensive)
        self._bind_selection_listeners_for_all_layers()

        # Redraw (debounced)
        try:
            self._refresh_timer.start()
        except Exception:
            QtCore.QTimer.singleShot(0, self._refresh_plot)

    # --------------------- SOURCES FOR A GIVEN LAYER ---------------------
    def _prepare_sources_for_layer(self, layer: Optional[QgsMapLayer]):
        """
        Prepare sources for a given layer:
        - Guess <scenario>_2d_Q.csv relative to layer.source()
        - Derive/guess <scenario>_1d_Q.csv
        """
        if not layer or layer.type() != layer.VectorLayer:
            return

        layer_id = layer.id()
        self._layer_sources[layer_id] = []

        # Guess CSVs
        csv_2d = self._guess_2d_csv_from_layer(layer)
        csv_1d = self._guess_1d_csv_from_2d(csv_2d)

        # 2D headers/time
        if csv_2d and csv_2d.is_file():
            t2d, h2d = self._peek_headers(csv_2d)
            if t2d:
                self._layer_sources[layer_id].append(
                    {"path": csv_2d, "time": t2d, "headers": h2d}
                )

        # 1D headers/time (optional)
        if csv_1d and csv_1d.is_file():
            t1d, h1d = self._peek_headers(csv_1d)
            if t1d:
                self._layer_sources[layer_id].append(
                    {"path": csv_1d, "time": t1d, "headers": h1d}
                )

        # Fallback: search ancestors for *_1d_Q.csv if not already added
        has_1d = any(
            p["path"].name.lower().endswith("_1d_q.csv")
            for p in self._layer_sources[layer_id]
        )
        if not has_1d:
            roots = []
            if csv_2d:
                roots = [
                    csv_2d.parent,
                    csv_2d.parent.parent,
                    csv_2d.parent.parent.parent,
                ]
            else:
                p = Path(layer.source().split("\n")[0]).resolve()
                roots = [p.parent, p.parent.parent, p.parent.parent.parent]
            for root in roots:
                for cand in root.glob("*_1d_Q.csv"):
                    if cand.is_file():
                        t, h = self._peek_headers(cand)
                        if t:
                            self._layer_sources[layer_id].append(
                                {"path": cand.resolve(), "time": t, "headers": h}
                            )
                            return

    # --------------------- REFRESH / DRAW ---------------------
    def _refresh_plot(self):
        """
        Scan all added layers, collect selected feature IDs, then plot them from all CHECKED layers.
        Strategy:
        1. Collect all selected feature IDs from all added layers
        2. For each CHECKED layer, find features with matching IDs and plot them
        """
        # Re-entrancy guard: if a refresh is already running, mark pending and return.
        if getattr(self, "_refresh_in_progress", False):
            self._pending_refresh = True
            return

        self._refresh_in_progress = True

        # Reset layer metrics at the start of each refresh
        self._layer_metrics = {}

        payloads: List[Dict[str, object]] = []
        selected_ids: Dict[str, QgsMapLayer] = {}  # {ID: layer}
        for layer_id in self._layer_enabled.keys():
            map_layer = QgsProject.instance().mapLayer(layer_id)
            if not map_layer or map_layer.type() != map_layer.VectorLayer:
                continue

            sel = map_layer.selectedFeatures()
            if not sel:
                continue

            # Validate ID field
            if "ID" not in map_layer.fields().names():
                continue

            # Collect all selected IDs
            for f in sel:
                id_val = str(f["ID"]).strip()
                if id_val:
                    selected_ids[id_val] = map_layer

        if not selected_ids:
            self._refresh_layer_table()
            try:
                self._last_plot_payloads = payloads
                self._draw_plot()
            finally:
                self._refresh_in_progress = False
            return

        # Step 2: For each CHECKED layer, find and plot features with matching IDs
        for layer_id, enabled in self._layer_enabled.items():
            if not enabled:
                continue  # Skip unchecked layers

            map_layer = QgsProject.instance().mapLayer(layer_id)
            if not map_layer or map_layer.type() != map_layer.VectorLayer:
                continue

            # Validate ID field
            if "ID" not in map_layer.fields().names():
                continue

            # Ensure sources ready for this layer
            self._prepare_sources_for_layer(map_layer)
            sources = self._layer_sources.get(map_layer.id(), [])
            if not sources:
                continue

            # For each selected ID, try to find it in this layer and plot
            for id_val in selected_ids.keys():
                # Find feature with this ID in current layer
                found = None
                try:
                    for f in map_layer.getFeatures():
                        if str(f["ID"]).strip() == id_val:
                            found = f
                            break
                except Exception:
                    continue

                if not found:
                    continue  # This ID doesn't exist in this layer

                # Resolve 'Q <ID>' header using EXACT token match
                src, q_header = self._find_q_column_exact(sources, id_val)
                if src is None or q_header is None:
                    continue

                # Read time + Q
                try:
                    t_vals, q_vals = self._read_two_columns(
                        src["path"], src["time"], q_header
                    )
                except Exception:
                    continue

                if not t_vals:
                    continue

                # Sort by time
                pairs = sorted(zip(t_vals, q_vals), key=lambda x: x[0])
                t_vals, q_vals = zip(*pairs)

                # Metrics
                peak_q = max(q_vals, key=abs)
                i_peak = q_vals.index(peak_q)
                t_peak = t_vals[i_peak]
                cum_vol_m3, total_vol_m3 = self._compute_cumulative_volume(
                    t_vals, q_vals
                )

                # Assign color based on global series index
                color = _DEFAULT_COLORS[len(payloads) % len(_DEFAULT_COLORS)]

                payloads.append(
                    {
                        "layer": map_layer,
                        "t_vals": t_vals,
                        "q_vals": q_vals,
                        "cum_vol_m3": cum_vol_m3,
                        "total_vol_m3": total_vol_m3,
                        "id_val": id_val,
                        "peak_q": peak_q,
                        "t_peak": t_peak,
                        "color": color,
                    }
                )

                # Store metrics for this layer (use first ID encountered)
                if layer_id not in self._layer_metrics:
                    self._layer_metrics[layer_id] = {
                        "first_id": id_val,
                        "peak_q": peak_q,
                        "total_vol_m3": total_vol_m3,
                    }

        # Auto toggles based on TOTAL series count
        n_series = len(payloads)
        if n_series > 1:
            # Disable & turn OFF both toggles
            for chk in (self._chk_volume, self._chk_peak):
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.setEnabled(False)
                chk.blockSignals(False)
        else:
            # Enable & restore user preferred state for both toggles
            self._chk_volume.blockSignals(True)
            self._chk_volume.setEnabled(True)
            self._chk_volume.setChecked(getattr(self, "_user_show_volume", True))
            self._chk_volume.blockSignals(False)

            self._chk_peak.blockSignals(True)
            self._chk_peak.setEnabled(True)
            self._chk_peak.setChecked(getattr(self, "_user_show_peaks", True))
            self._chk_peak.blockSignals(False)

        # Update layer table with metrics
        self._refresh_layer_table()

        # Cache & draw
        try:
            self._last_plot_payloads = payloads
            self._draw_plot()
        finally:
            # Clear in-progress flag and handle pending refresh
            self._refresh_in_progress = False
            if getattr(self, "_pending_refresh", False):
                self._pending_refresh = False
                # schedule another refresh shortly
                try:
                    self._refresh_timer.start()
                except Exception:
                    QtCore.QTimer.singleShot(50, self._refresh_plot)

    def _on_toggle_changed(self, state: int):
        """
        Redraw when the user toggles volume or peaks.
        In multi-series mode, toggles are disabled+OFF via _refresh_plot.
        """
        sender = self.sender()
        if sender == self._chk_volume:
            self._user_show_volume = self._chk_volume.isChecked()
        elif sender == self._chk_peak:
            self._user_show_peaks = self._chk_peak.isChecked()

        if not self._last_plot_payloads:
            self._show_empty("Select features with 'ID' in vector layers")
            return
        self._draw_plot()  # Keep current payloads; just redraw

    # --------------------- PLOTTING ---------------------

    def _draw_plot(self):
        # Guard
        if not self._last_plot_payloads:
            self._show_empty("Select features with 'ID' in vector layers")
            return

        payloads = self._last_plot_payloads
        n_series = len(payloads)

        # --- NEW: determine number of UNIQUE layers involved ---
        unique_layers = {p["layer"].id() for p in payloads}
        n_layers_involved = len(unique_layers)
        # -------------------------------------------------------

        # Clear axes
        self._ax.clear()
        self._remove_secondary_axis()

        handles, labels = [], []
        self._hover_lines = []

        for p in payloads:
            t_vals = p["t_vals"]
            q_vals = p["q_vals"]
            color = p["color"]
            lyr = p["layer"]
            id_val = p["id_val"]
            peak_q = p["peak_q"]
            t_peak = p["t_peak"]

            # --- NEW LEGEND RULES ---
            # If features are all from a single layer -> legend shows ID only with Qp and V.
            # If features span multiple layers -> legend shows "Layer • ID" with Qp and V.
            legend_label = (
                f"{id_val} (Qp={peak_q:.3f}, V={p['total_vol_m3']:,.0f})"
                if n_layers_involved == 1
                else f"{lyr.name()} • {id_val} (Qp={peak_q:.3f}, V={p['total_vol_m3']:,.0f})"
            )
            # ------------------------

            line = self._ax.plot(
                t_vals, q_vals, color=color, linewidth=1.8, label=legend_label, picker=5
            )[0]
            self._hover_lines.append((line, legend_label))

            # Peak markers/annotations ONLY if peaks toggle is ON
            if self._chk_peak.isChecked():
                self._ax.scatter(
                    [t_peak],
                    [peak_q],
                    facecolors="white",
                    edgecolors=color,
                    linewidths=1.5,
                    zorder=3,
                )
                self._ax.annotate(
                    f"{peak_q:.3f} m³/s",
                    xy=(t_peak, peak_q),
                    xytext=(5, 6),
                    textcoords="offset points",
                    color=color,
                    fontsize=9,
                    bbox=dict(
                        boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.7
                    ),
                )

            handles.append(line)
            labels.append(legend_label)

        # Axes labels/title/grid
        self._ax.set_xlabel("Time (h)")
        self._ax.set_ylabel("Flow (m³/s)")
        self._ax.set_title("Flow time series")
        self._ax.grid(True, alpha=0.4)

        # Tight axes margins
        self._ax.margins(x=0, y=0.15)

        # Common x-limits across series
        all_t = [t for p in payloads for t in p["t_vals"]]
        if all_t:
            self._ax.set_xlim(min(all_t), max(all_t))

        # Left axis baseline at 0
        top_q = self._ax.get_ylim()[1]
        self._ax.set_ylim(0.0, top_q)
        self._ax.axhline(0.0, color="#888", linewidth=0.8, alpha=0.6, zorder=0)

        # Right axis (Volume) — only if SINGLE series AND toggle ON
        if n_series == 1 and self._chk_volume.isChecked():
            p = payloads[0]
            cum_vol_km3 = [v / 1000.0 for v in p["cum_vol_m3"]]  # thousands (10^3 m³)
            self._ax2 = self._ax.twinx()
            self._ax2.plot(
                p["t_vals"],
                cum_vol_km3,
                color="#ff7f0e",
                linewidth=1.4,
                alpha=0.9,
                label="Accumulated Volume (10³ m³)",
            )
            self._ax2.set_ylabel("Accumulated Volume (10³ m³)")
            self._ax2.grid(False)
            self._ax2.set_xlim(self._ax.get_xlim())
            # Align baseline at 0
            top_v = self._ax2.get_ylim()[1]
            self._ax2.set_ylim(0.0, top_v)

        # Legend (top-left) with smaller font size
        self._ax.legend(
            handles,
            labels,
            loc="upper left",
            frameon=True,
            framealpha=0.85,
            borderpad=0.4,
            fontsize=8,
        )

        # Recreate hover annotation
        self._annot = self._ax.annotate(
            "",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="white", alpha=0.9, ec="gray", linewidth=1.5),
            fontsize=9,
            zorder=10,
            multialignment="left",
            linespacing=1.5,
        )
        self._annot.set_visible(False)

        # Draw
        self._canvas.draw_idle()

        # Clear bottom info (no info text display)
        self._hint.setText("")

    def _on_plot_hover(self, event):
        """Handle mouse hover on plot curves to show legend name in tooltip."""
        if not self._ax or not getattr(self, "_annot", None):
            return

        vis = self._annot.get_visible()
        if event.inaxes == self._ax:
            for line, label in self._hover_lines:
                cont, ind = line.contains(event)
                if cont:
                    # Text up to 30 characters (including punctuations and symbols)
                    label_text = label[:30] + "..." if len(label) > 30 else label
                    
                    # Get the closest data point coordinates on the curve
                    xdata, ydata = line.get_data()
                    ind_val = ind["ind"][0]  # first index matching the event
                    hover_x = xdata[ind_val]
                    hover_y = ydata[ind_val]
                    
                    text = f"{label_text}\nTime: {hover_x:.2f} h\nFlow: {hover_y:.3f} m³/s"
                    
                    self._annot.xy = (event.xdata, event.ydata)
                    self._annot.set_text(text)
                    self._annot.get_bbox_patch().set_edgecolor(line.get_color())
                    self._annot.get_bbox_patch().set_alpha(0.9)
                    self._annot.get_bbox_patch().set_linewidth(2)
                    
                    # Avoid boundary collision
                    xlim = self._ax.get_xlim()
                    ylim = self._ax.get_ylim()
                    x_mid = (xlim[0] + xlim[1]) / 2
                    y_mid = (ylim[0] + ylim[1]) / 2
                    
                    if event.xdata > x_mid:
                        ha = "right"
                        offset_x = -10
                    else:
                        ha = "left"
                        offset_x = 10
                        
                    if event.ydata > y_mid:
                        va = "top"
                        offset_y = -10
                    else:
                        va = "bottom"
                        offset_y = 10
                        
                    self._annot.set_position((offset_x, offset_y))
                    self._annot.set_horizontalalignment(ha)
                    self._annot.set_verticalalignment(va)
                    
                    self._annot.set_visible(True)
                    self._canvas.draw_idle()
                    return

            if vis:
                self._annot.set_visible(False)
                self._canvas.draw_idle()

    def _show_empty(self, title: str):
        self._ax.clear()
        self._remove_secondary_axis()
        self._hover_lines = []
        self._annot = None
        self._ax.set_title(title)
        self._ax.set_xlabel("Time (h)")
        self._ax.set_ylabel("Flow (m³/s)")
        self._ax.grid(True, alpha=0.4)
        self._canvas.draw_idle()

    # --------------------- EXACT ID MATCHING ---------------------
    def _candidate_id_variants(self, id_val: str) -> List[str]:
        """
        ID is a string and can be any characters.
        NO numeric-only coercion; NO case folding; ONLY trimmed full string.
        """
        s = id_val.strip()
        return [s] if s else []

    def _find_q_column_exact(
        self, sources: List[Dict[str, object]], id_val: str
    ) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
        """
        Exact token match:
        - Extract the token immediately after 'Q ' (up to first space or '[').
        - Compare EXACTLY (case-sensitive) to the selected ID (trimmed).
        """
        variants = self._candidate_id_variants(id_val)  # just [exact_id]
        if not variants:
            return None, None
        exact_id = variants[0]

        for src in sources:
            headers: List[str] = src.get("headers", [])
            time_hdr: Optional[str] = src.get("time")
            if not headers or not time_hdr:
                continue

            # Build token map: token after 'Q ' -> full header
            token_map = {}
            for h in headers:
                m = re.match(
                    r"^Q\s+([^\s\[]+)", h
                )  # token can include hyphens/underscores/digits/letters
                if m:
                    token_map[m.group(1)] = h

            q_header = token_map.get(exact_id)
            if q_header:
                return src, q_header

        return None, None

    # --------------------- CSV IO / HEADERS ---------------------
    def _read_headers(self, path: Path) -> List[str]:
        try:
            with open(path, "r", newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                return [h.strip() for h in headers]
        except Exception:
            return []

    def _detect_time_header(self, path: Path) -> Optional[str]:
        for h in self._read_headers(path):
            hl = h.lower()
            if hl.startswith("time") and "(h" in hl:
                return h
        return None

    def _peek_headers(self, csv_path: Path) -> Tuple[Optional[str], List[str]]:
        """
        Returns (time_header, q_headers_list)
        - time_header: header that looks like 'Time (h)'
        - q_headers_list: headers starting with 'Q '
        """
        try:
            with open(
                csv_path, "r", newline="", encoding="utf-8", errors="ignore"
            ) as f:
                reader = csv.reader(f)
                headers = next(reader, [])
        except Exception:
            return None, []
        headers = [h.strip() for h in headers]
        time_header = next(
            (h for h in headers if h.lower().startswith("time") and "(h" in h.lower()),
            None,
        )
        q_headers = [h for h in headers if h.startswith("Q ")]
        return time_header, q_headers

    def _read_two_columns(
        self, path: Path, time_header: Optional[str], q_header: str
    ) -> Tuple[List[float], List[float]]:
        if not time_header:
            raise ValueError(f"Could not detect a time column in: {path}")
        t_vals: List[float] = []
        q_vals: List[float] = []
        with open(path, "r", newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = float(row[time_header])
                    qstr = row.get(q_header, "").strip()
                    if qstr == "" or qstr.lower() == "nan":
                        continue
                    q = float(qstr)
                    t_vals.append(t)
                    q_vals.append(q)
                except Exception:
                    # Skip malformed rows silently
                    continue
        return t_vals, q_vals

    # --------------------- INTEGRATION ---------------------
    def _compute_cumulative_volume(
        self, t_vals: Tuple[float, ...], q_vals: Tuple[float, ...]
    ) -> Tuple[List[float], float]:
        """
        Compute accumulated volume (m³) via trapezoidal rule.
        Time in hours (per 'Time (h)'), Q in m³/s.
        V_i = 0.5*(Q[i-1]+Q[i]) * Δt_hours * 3600
        Returns (cum_vol_series, total_volume).
        """
        if len(t_vals) < 2:
            return [0.0], 0.0
        cum = [0.0]
        total = 0.0
        for i in range(1, len(t_vals)):
            dt_h = t_vals[i] - t_vals[i - 1]
            if dt_h < 0:
                continue
            step = 0.5 * (q_vals[i - 1] + q_vals[i]) * dt_h * 3600.0  # hours→seconds
            total += step
            cum.append(total)
        return cum, total

    # --------------------- AXES HOUSEKEEPING ---------------------
    def _remove_secondary_axis(self):
        if hasattr(self, "_ax2") and self._ax2 is not None:
            try:
                self._fig.delaxes(self._ax2)
            except Exception:
                pass
        self._ax2 = None

    # --------------------- CSV path guessing ---------------------
    def _guess_2d_csv_from_layer(self, layer: QgsMapLayer) -> Optional[Path]:
        """
        Try to locate '<scenario>_2d_Q.csv' relative to layer.source().
        """
        try:
            p = Path(layer.source().split("\n")[0]).resolve()
        except Exception:
            return None
        base_no_ext = p.stem
        scenario = re.sub(r"_PLOT_.*$", "", base_no_ext)
        csv_name = f"{scenario}_2d_Q.csv"
        candidates = [
            p.parent.parent / "csv" / csv_name,
            p.parent / "csv" / csv_name,
            p.parent / csv_name,
        ]
        for c in candidates:
            if c.is_file():
                return c.resolve()
        # Walk up ancestors looking for 'csv/<scenario>_2d_Q.csv'
        for ancestor in [p.parent, p.parent.parent, p.parent.parent.parent]:
            c = ancestor / "csv" / csv_name
            if c.is_file():
                return c.resolve()
        return None

    def _guess_1d_csv_from_2d(self, csv_2d: Optional[Path]) -> Optional[Path]:
        """
        Derive/locate '<scenario>_1d_Q.csv':
        - Sibling swap if *_2d_Q.csv exists
        - Otherwise search for '*_1d_Q.csv' in nearby folders
        """
        if csv_2d and csv_2d.name.lower().endswith("_2d_q.csv"):
            csv_1d = csv_2d.with_name(
                csv_2d.name[:-8] + "1d_Q.csv"
            )  # replace '2d_Q.csv' with '1d_Q.csv'
            if csv_1d.is_file():
                return csv_1d.resolve()
            csv_1d = csv_2d.with_name(csv_2d.stem[:-3] + "_1d_Q.csv")
            if csv_1d.is_file():
                return csv_1d.resolve()

        # Nearby search
        search_roots: List[Path] = []
        if csv_2d:
            base_folder = csv_2d.parent
            search_roots += [base_folder, base_folder.parent, base_folder.parent.parent]
        for root in search_roots:
            for cand in root.glob("*_1d_Q.csv"):
                if cand.is_file():
                    return cand.resolve()
        return None

    # --------------------- CLOSE ---------------------
    def closeEvent(self, e):
        self._save_settings()

        # Disconnect safely
        try:
            iface.currentLayerChanged.disconnect(self._on_current_layer_changed)
        except Exception:
            pass

        # Disconnect selectionChanged for all vector layers
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() != lyr.VectorLayer:
                continue
            try:
                lyr.selectionChanged.disconnect(self._on_any_selection_changed)
            except Exception:
                pass

        # Remove strong ref
        try:
            _LIVE_WINDOWS.remove(self)
        except ValueError:
            pass

        super().closeEvent(e)


# EOF
