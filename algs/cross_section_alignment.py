# -*- coding: utf-8 -*-
"""
Cross Sections along Alignment Tool.
Interactive UI for viewing long sections and dynamic cross sections.

Features:
- Select alignment from vector layer or draw manually.
- Manage raster layers (Add/Remove/Sort).
- Side-by-side plots: Long Section (Left) and Cross Section (Right).
- Interactive: Hover over Long Section to update Cross Section.
"""

import math
import os
import datetime
import re
import numpy as np
import tempfile
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, 
    QTableWidgetItem, QHeaderView, QLabel, QWidget, QAbstractItemView, QComboBox,
    QMessageBox, QSpinBox, QDoubleSpinBox, QSplitter, QFrame, QToolBar, QProgressDialog
)
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal, QStandardPaths, QUrl, QVariant, QSettings, QSize, QCoreApplication
from qgis.PyQt.QtGui import QColor, QCursor, QIcon, QDesktopServices
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsGeometry, QgsPointXY, QgsPoint,
    QgsWkbTypes, QgsFeature, QgsVectorLayer, QgsRectangle, QgsCoordinateTransform,
    QgsProcessingAlgorithm, QgsVectorLayerSimpleLabeling, QgsPalLayerSettings,
    QgsTextFormat, QgsSymbol, QgsMapSettings, QgsMapRendererSequentialJob, QgsField,
    QgsRuleBasedLabeling, QgsTextBufferSettings, QgsProperty
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.utils import iface

# Matplotlib integration
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.path import Path as MplPath
except ImportError:
    # Fallback for older systems if needed, though QGIS usually has this
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.path import Path as MplPath

# Keep reference to prevent GC
_LIVE_WINDOWS = []

class CapturePolylineTool(QgsMapTool):
    """Map tool to capture a polyline alignment."""
    
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.callback = callback
        self.rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.rubber_band.setColor(Qt.red)
        self.rubber_band.setWidth(2)
        self.points = []
        
        # Temporary rubber band for dynamic line segment (last point to cursor)
        self.temp_rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.temp_rubber_band.setColor(Qt.red)
        self.temp_rubber_band.setWidth(1)
        self.temp_rubber_band.setLineStyle(Qt.DashLine)

        # Transformation to Project CRS
        self.canvas_crs = canvas.mapSettings().destinationCrs()
        self.project_crs = QgsProject.instance().crs()
        self.transform = QgsCoordinateTransform(self.canvas_crs, self.project_crs, QgsProject.instance())

    def get_project_point(self, pos):
        """Convert a canvas Point (pixel) directly to a PointXY in Project CRS."""
        pt_canvas = self.toMapCoordinates(pos)
        if self.canvas_crs != self.project_crs:
            return self.transform.transform(pt_canvas)
        return pt_canvas

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt_proj = self.get_project_point(event.pos())
            self.points.append(pt_proj)
            # Rubberbands natively take the raw canvas points, so transform back or rely on normal QgsRubberBand functionality
            # Note: QgsRubberBand expects coordinates in canvas CRS, so we pass raw totoMapCoordinates
            self.rubber_band.addPoint(self.toMapCoordinates(event.pos()))
            
            # Reset temp band so it starts fresh from the new point
            self.temp_rubber_band.reset(QgsWkbTypes.LineGeometry)
        elif event.button() == Qt.RightButton:
            self.finish()

    def canvasMoveEvent(self, event):
        if not self.points:
            return
        
        # Update dynamic line from last captured point to current mouse position
        self.temp_rubber_band.reset(QgsWkbTypes.LineGeometry)
        
        # self.points[-1] is in Project CRS, convert back to Canvas CRS for rubber band rendering
        start_proj = self.points[-1]
        try:
            inv_transform = QgsCoordinateTransform(self.project_crs, self.canvas_crs, QgsProject.instance())
            start_canvas = inv_transform.transform(start_proj)
        except:
            start_canvas = start_proj
            
        end_canvas = self.toMapCoordinates(event.pos())
        self.temp_rubber_band.addPoint(start_canvas, False)
        self.temp_rubber_band.addPoint(end_canvas, True)
        self.temp_rubber_band.show()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.rubber_band.reset()
            self.temp_rubber_band.reset()
            self.points = []
            iface.mapCanvas().unsetMapTool(self)

    def finish(self):
        self.temp_rubber_band.reset()
        if len(self.points) > 1:
            geom = QgsGeometry.fromPolylineXY(self.points)
            self.callback(geom)
        
        self.rubber_band.reset()
        self.points = []
        iface.mapCanvas().unsetMapTool(self)


class CrossSectionAlignmentDialog(QDialog):
    """
    Main Dialog for Cross Sections along Alignment.
    """

    @staticmethod
    def is_valid_sample(val, ok, layer):
        """Helper to determine if a raster sample is valid and not NoData."""
        if not ok or np.isnan(val):
            return False
        dp = layer.dataProvider()
        if dp:
            src_nodata = dp.sourceNoDataValue(1)
            if not np.isnan(src_nodata) and abs(val - src_nodata) < 1e-6:
                return False
        return True

    def _sample_raster(self, layer, pt_xy):
        """Safely sample a raster layer at a given point in Project CRS."""
        if not layer or not layer.isValid():
            return np.nan, False
            
        project_crs = self.crs
        layer_crs = layer.crs()
        
        sample_pt = pt_xy
        if project_crs != layer_crs:
            try:
                xform = QgsCoordinateTransform(project_crs, layer_crs, QgsProject.instance())
                sample_pt = xform.transform(pt_xy)
            except Exception:
                return np.nan, False
                
        val, ok = layer.dataProvider().sample(sample_pt, 1)
        return val, ok

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cross Sections along Alignment")
        self.resize(1300, 800)
        
        # Data
        self.alignment_geom = None  # QgsGeometry
        self.raster_layers = []     # List of dict: {'layer': QgsRasterLayer, 'style': str}
        self.long_section_data = {} # Cache for long section data {layer_id: (dists, vals)}
        self.crs = QgsProject.instance().crs()
        self.reverse_offset = False # Toggle for cross section direction
        self.show_water_levels = False # Toggle for water level markers
        self.chainage_layer = None # Reference to temporary chainage layer
        
        # UI Components
        self.init_ui()
        
        # Map Tool
        self.capture_tool = None
        
        # Plotting State
        self.current_cursor_dist = None
        self.marker_line = None # Red dot on long section
        
        # Connect to QGIS interface
        self.canvas = iface.mapCanvas()
        
        # Map Marker (Red Cross)
        self.map_marker = QgsVertexMarker(self.canvas)
        self.map_marker.setColor(QColor(255, 0, 0))
        self.map_marker.setIconSize(10)
        self.map_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.map_marker.setPenWidth(3)
        self.map_marker.hide()
        
        # Persistent Alignment Rubber Band (to show alignment after tool finishes)
        self.alignment_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.alignment_rubber_band.setColor(QColor(100, 255, 100))  # Light Green
        self.alignment_rubber_band.setWidth(3)
        
        # Ensure marker is on top of rubber band
        self.map_marker.setZValue(self.alignment_rubber_band.zValue() + 1)
        
        # Arrow Rubber Band (for direction)
        self.arrow_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.arrow_rubber_band.setFillColor(QColor(100, 255, 100))
        self.arrow_rubber_band.setStrokeColor(Qt.black)
        self.arrow_rubber_band.setWidth(1)
        
        # Cross Section Rubber Band (Black Dash)
        self.cs_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.cs_rubber_band.setColor(Qt.black)
        self.cs_rubber_band.setWidth(1)
        self.cs_rubber_band.setLineStyle(Qt.DashLine)
        
        # Cross Section Arrow Rubber Band
        self.cs_arrow_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.cs_arrow_rubber_band.setFillColor(Qt.black)
        self.cs_arrow_rubber_band.setStrokeColor(Qt.black)
        self.cs_arrow_rubber_band.setWidth(1)

        # Load settings after UI initialization
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # --- Top Toolbar (Alignment & Settings) ---
        top_layout = QHBoxLayout()
        
        btn_select = QPushButton("Select Feature")
        btn_select.setToolTip("Use the selected feature from the active vector layer as alignment")
        btn_select.clicked.connect(self.on_select_feature)
        top_layout.addWidget(btn_select)
        
        btn_draw = QPushButton("Draw Alignment")
        btn_draw.setToolTip("Draw a polyline on the map")
        btn_draw.clicked.connect(self.on_draw_alignment)
        top_layout.addWidget(btn_draw)
        
        btn_clear_align = QPushButton("Clear Alignment")
        btn_clear_align.setToolTip("Clear the current alignment from map and plots")
        btn_clear_align.clicked.connect(self.on_clear_alignment)
        top_layout.addWidget(btn_clear_align)
        
        self.btn_chainage = QPushButton("Chainage")
        self.btn_chainage.setCheckable(True)
        self.btn_chainage.setChecked(False)
        self.btn_chainage.setToolTip("Toggle chainage lines along alignment")
        self.btn_chainage.toggled.connect(self.update_chainage)
        top_layout.addWidget(self.btn_chainage)
        
        top_layout.addSpacing(20)
        
        top_layout.addWidget(QLabel("Cross Section Width (m):"))
        self.spin_width = QDoubleSpinBox()
        self.spin_width.setRange(1.0, 10000.0)
        self.spin_width.setValue(100.0)
        self.spin_width.valueChanged.connect(self.on_width_changed)
        top_layout.addWidget(self.spin_width)
        
        top_layout.addSpacing(20)
        
        top_layout.addWidget(QLabel("Interval (m):"))
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.1, 10000.0)
        self.spin_interval.setValue(50.0)
        self.spin_interval.valueChanged.connect(self.update_chainage)
        top_layout.addWidget(self.spin_interval)
        
        self.btn_reverse = QPushButton("Reverse View")
        self.btn_reverse.setCheckable(True)
        self.btn_reverse.setToolTip("Reverse the direction of the cross section view")
        self.btn_reverse.toggled.connect(self.on_toggle_reverse)
        top_layout.addWidget(self.btn_reverse)
        
        self.btn_wl = QPushButton("Water Levels")
        self.btn_wl.setCheckable(True)
        self.btn_wl.setToolTip("Show water level markers at the center line")
        self.btn_wl.toggled.connect(self.on_toggle_water_levels)
        top_layout.addWidget(self.btn_wl)
        
        btn_output = QPushButton("Output Plots")
        btn_output.clicked.connect(self.on_output_cross_sections)
        top_layout.addWidget(btn_output)
        
        top_layout.addStretch()
        main_layout.addLayout(top_layout)

        # --- Middle: Matplotlib Plots ---
        # We use one Figure with 2 subplots to easily manage layout
        self.fig = Figure(figsize=(12, 6))
        self.canvas_plot = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas_plot, self)
        
        # Use GridSpec to set width ratio (3:2 for Long Section : Cross Section)
        gs = self.fig.add_gridspec(1, 2, width_ratios=[3, 2])
        self.ax_long = self.fig.add_subplot(gs[0, 0])
        self.ax_cross = self.fig.add_subplot(gs[0, 1])
        
        self.ax_long.set_title("Long Section Profile")
        self.ax_long.set_xlabel("Distance (m)")
        self.ax_long.set_ylabel("Elevation (m)")
        
        self.ax_cross.set_title("Cross Section Profile")
        self.ax_cross.set_xlabel("Offset (m)")
        self.ax_cross.set_ylabel("Elevation (m)")
        
        self.fig.tight_layout()
        
        # Event connection for interactivity
        self.canvas_plot.mpl_connect('motion_notify_event', self.on_plot_hover)
        
        plot_layout = QVBoxLayout()
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas_plot)
        
        # Use a frame for plots
        plot_frame = QFrame()
        plot_frame.setFrameShape(QFrame.StyledPanel)
        plot_frame.setLayout(plot_layout)
        main_layout.addWidget(plot_frame, stretch=2)

        # --- Bottom: Raster Management ---
        bottom_layout = QHBoxLayout()
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Raster Layer", "Elevation (m)", "Style", "Sample"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 100)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 100)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(3, 60)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemChanged.connect(self.on_sample_checked)
        bottom_layout.addWidget(self.table)
        
        # Buttons
        btn_col = QVBoxLayout()
        
        btn_add = QPushButton("Add Selected")
        btn_add.clicked.connect(self.on_add_raster)
        btn_col.addWidget(btn_add)
        
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self.on_remove_raster)
        btn_col.addWidget(btn_remove)
        
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self.on_clear_rasters)
        btn_col.addWidget(btn_clear)
        
        btn_col.addSpacing(10)
        
        btn_up = QPushButton("Move Up")
        btn_up.clicked.connect(self.on_move_up)
        btn_col.addWidget(btn_up)
        
        btn_down = QPushButton("Move Down")
        btn_down.clicked.connect(self.on_move_down)
        btn_col.addWidget(btn_down)
        
        btn_col.addStretch()
        bottom_layout.addLayout(btn_col)
        
        main_layout.addLayout(bottom_layout, stretch=1)

    def closeEvent(self, event):
        if self.map_marker:
            self.canvas.scene().removeItem(self.map_marker)
            self.map_marker = None
        if self.alignment_rubber_band:
            self.canvas.scene().removeItem(self.alignment_rubber_band)
            self.alignment_rubber_band = None
        if self.arrow_rubber_band:
            self.canvas.scene().removeItem(self.arrow_rubber_band)
            self.arrow_rubber_band = None
        if self.cs_rubber_band:
            self.canvas.scene().removeItem(self.cs_rubber_band)
            self.cs_rubber_band = None
        if self.cs_arrow_rubber_band:
            self.canvas.scene().removeItem(self.cs_arrow_rubber_band)
            self.cs_arrow_rubber_band = None
        self.save_settings()
        super().closeEvent(event)

    def get_canvas_geom(self, geom):
        """Transform a geometry from Project CRS back to Canvas CRS for rendering."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if self.crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(self.crs, canvas_crs, QgsProject.instance())
                geom_copy = QgsGeometry(geom)
                geom_copy.transform(xform)
                return geom_copy
            except Exception:
                pass
        return geom
        
    def get_canvas_pt(self, pt):
        """Transform a PointXY from Project CRS back to Canvas CRS for rendering."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if self.crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(self.crs, canvas_crs, QgsProject.instance())
                return xform.transform(pt)
            except Exception:
                pass
        return pt

    # --- Alignment Handling ---

    def on_select_feature(self):
        layer = iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(self, "Selection Error", "Please select a vector layer in the Layers panel.")
            return
        
        if layer.geometryType() != QgsWkbTypes.LineGeometry:
            QMessageBox.warning(self, "Selection Error", "Active layer must be a Line layer.")
            return
            
        selected = layer.selectedFeatures()
        if not selected:
            QMessageBox.warning(self, "Selection Error", "No features selected in the active layer.")
            return
            
        # Use the first selected feature
        feat = selected[0]
        
        # Transform from layer CRS to Project CRS so internal distance scaling works
        geom = QgsGeometry(feat.geometry())
        layer_crs = layer.crs()
        if layer_crs != self.crs:
            try:
                xform = QgsCoordinateTransform(layer_crs, self.crs, QgsProject.instance())
                geom.transform(xform)
            except Exception:
                pass
                
        if not geom or geom.isEmpty():
            return
            
        self.set_alignment(geom)

    def on_draw_alignment(self):
        # Create and activate map tool
        self.capture_tool = CapturePolylineTool(self.canvas, self.set_alignment)
        self.canvas.setMapTool(self.capture_tool)
        # We don't hide the dialog, assuming it's non-modal or user can move it
        self.activateWindow()

    def set_alignment(self, geom):
        self.alignment_geom = geom
        
        # Update persistent rubber band on map
        canvas_geom = self.get_canvas_geom(geom)
        self.alignment_rubber_band.setToGeometry(canvas_geom, None)
        self.alignment_rubber_band.show()
        
        self.update_arrow(geom)
        self.refresh_long_section_data()
        self.refresh_plots()
        self.update_chainage()

    def update_arrow(self, geom):
        """Draw a directional arrow at the end of the alignment."""
        if not geom or geom.isEmpty():
            self.arrow_rubber_band.hide()
            return
            
        # Extract points from geometry
        if geom.isMultipart():
            lines = geom.asMultiPolyline()
            if not lines: return
            points = lines[-1]
        else:
            points = geom.asPolyline()
            
        if len(points) < 2:
            self.arrow_rubber_band.hide()
            return
            
        p_end = points[-1]
        p_prev = points[-2]
        
        dx = p_end.x() - p_prev.x()
        dy = p_end.y() - p_prev.y()
        length = math.sqrt(dx*dx + dy*dy)
        
        if length == 0:
            self.arrow_rubber_band.hide()
            return
            
        ux = dx / length
        uy = dy / length
        
        # Size arrow based on current view scale (e.g. 15 pixels long)
        scale = self.canvas.mapUnitsPerPixel()
        arrow_len = 15 * scale
        arrow_half_width = 6 * scale
        
        # Arrow bottom middle (base center) stick to polyline end point
        bx = p_end.x()
        by = p_end.y()
        
        # Tip (projected forward)
        tx = bx + ux * arrow_len
        ty = by + uy * arrow_len
        
        # Perpendicular vector (-uy, ux)
        lx = bx - (-uy) * arrow_half_width
        ly = by - (ux) * arrow_half_width
        
        rx = bx + (-uy) * arrow_half_width
        ry = by + (ux) * arrow_half_width
        
        # Create polygon
        poly = [QgsPointXY(tx, ty), QgsPointXY(lx, ly), QgsPointXY(rx, ry)]
        arrow_geom = QgsGeometry.fromPolygonXY([poly])
        
        canvas_arrow = self.get_canvas_geom(arrow_geom)
        self.arrow_rubber_band.setToGeometry(canvas_arrow, None)
        self.arrow_rubber_band.show()

    def on_clear_alignment(self):
        """Clear the current alignment and reset plots."""
        self.alignment_geom = None
        self.alignment_rubber_band.reset()
        self.arrow_rubber_band.reset()
        self.cs_rubber_band.reset()
        self.cs_arrow_rubber_band.reset()
        self.map_marker.hide()
        self.refresh_long_section_data()
        self.refresh_plots()
        self.update_chainage()

    def on_width_changed(self):
        self.refresh_cross_section_plot()
        self.update_chainage()

    def update_chainage(self):
        # If toggle is off, remove layer if it exists
        if not self.btn_chainage.isChecked():
            try:
                if self.chainage_layer:
                    QgsProject.instance().removeMapLayer(self.chainage_layer)
            except RuntimeError:
                pass
            self.chainage_layer = None
            return

        if not self.alignment_geom:
            return

        interval = self.spin_interval.value()
        width = self.spin_width.value()
        
        # Ensure layer exists and has the correct schema (3 fields)
        layer_valid = False
        try:
            if self.chainage_layer and self.chainage_layer.isValid():
                field_names = [f.name() for f in self.chainage_layer.fields()]
                if "RightLabel" in field_names:
                    layer_valid = True
                else:
                    # Old layer missing RightLabel - remove and recreate
                    QgsProject.instance().removeMapLayer(self.chainage_layer)
                    self.chainage_layer = None
        except RuntimeError:
            self.chainage_layer = None

        if not layer_valid:
            crs = self.crs.authid()
            self.chainage_layer = QgsVectorLayer(f"LineString?crs={crs}", "Chainage Lines", "memory")
            
            # Add fields
            pr = self.chainage_layer.dataProvider()
            pr.addAttributes([
                QgsField("Chainage", QVariant.Double),
                QgsField("Label", QVariant.String),
                QgsField("RightLabel", QVariant.String),
            ])
            self.chainage_layer.updateFields()
            
            # Styling (Black Dash)
            symbol = QgsSymbol.defaultSymbol(self.chainage_layer.geometryType())
            symbol.setColor(QColor("black"))
            sl = symbol.symbolLayer(0)
            if hasattr(sl, 'setPenStyle'):
                sl.setPenStyle(Qt.DashLine)
                sl.setWidth(0.4)
            self.chainage_layer.renderer().setSymbol(symbol)
            self.chainage_layer.setLabelsEnabled(True)
            
            QgsProject.instance().addMapLayer(self.chainage_layer, False)
            tree_root = QgsProject.instance().layerTreeRoot()
            tree_root.insertLayer(0, self.chainage_layer)
                
            node = tree_root.findLayer(self.chainage_layer.id())
            if node:
                node.setItemVisibilityChecked(True)
        
        # --- Determine sample layer ---
        sample_layer = None
        sample_layer_id = self.get_sample_layer_id()
        if sample_layer_id:
            for item_data in self.raster_layers:
                if item_data['layer'].id() == sample_layer_id:
                    sample_layer = item_data['layer']
                    break
        
        # --- Generate features ---
        pr = self.chainage_layer.dataProvider()
        pr.truncate()  # Clear existing
        
        length = self.alignment_geom.length()
        dists = list(np.arange(0, length, interval))
        if not dists or abs(dists[-1] - length) > 1e-6:
            dists.append(length)  # Always include endpoint
        
        feats = []
        for d in dists:
            geom = self.get_cross_section_geom(d, width, self.reverse_offset)
            if geom:
                # Sample raster at this chainage point
                right_label = f"{d:.0f}"  # default: chainage distance
                if sample_layer and sample_layer.isValid():
                    pt_geom = self.alignment_geom.interpolate(d)
                    if pt_geom:
                        pt = pt_geom.asPoint()
                        val, ok = self._sample_raster(sample_layer, QgsPointXY(pt.x(), pt.y()))
                        if self.is_valid_sample(val, ok, sample_layer):
                            right_label = f"{val:.2f} m"
                        else:
                            right_label = ""  # NoData: show empty
                
                f = QgsFeature()
                f.setGeometry(geom)
                f.setAttributes([float(d), f"{d:.0f}", right_label])
                feats.append(f)
        
        pr.addFeatures(feats)
        self.chainage_layer.updateExtents()
        
        # (Re)apply labeling — updates colour whenever sample layer changes
        self._apply_chainage_labeling(use_blue_right=(sample_layer is not None))
        
        self.chainage_layer.triggerRepaint()

    def _apply_chainage_labeling(self, use_blue_right: bool):
        """(Re)apply rule-based labels to the chainage layer.
        Left label = 'Label' (chainage distance, black).
        Right label = 'RightLabel' (blue when raster sample active, else black).
        """
        if not self.chainage_layer:
            return

        # Shared buffer
        buf_settings = QgsTextBufferSettings()
        buf_settings.setEnabled(True)
        buf_settings.setSize(1.0)
        buf_settings.setColor(QColor("white"))

        def _make_format(color):
            fmt = QgsTextFormat()
            fmt.setSize(10)
            fmt.setColor(color)
            fmt.setBuffer(buf_settings)
            return fmt

        rotation_expr = "main_angle($geometry) - 90"

        # Rule 1: Left label — chainage distance, always black
        s1 = QgsPalLayerSettings()
        s1.setFormat(_make_format(QColor("black")))
        s1.fieldName = "Label"
        s1.geometryGenerator = "start_point($geometry)"
        s1.geometryGeneratorType = QgsWkbTypes.PointGeometry
        s1.geometryGeneratorEnabled = True
        s1.placement = QgsPalLayerSettings.AroundPoint
        s1.quadOffset = QgsPalLayerSettings.QuadrantLeft
        s1.dist = 0.5
        p1 = s1.dataDefinedProperties()
        p1.setProperty(QgsPalLayerSettings.LabelRotation, QgsProperty.fromExpression(rotation_expr))
        s1.setDataDefinedProperties(p1)

        # Rule 2: Right label — sampled value (blue) or chainage (black)
        right_color = QColor("blue") if use_blue_right else QColor("black")
        s2 = QgsPalLayerSettings()
        s2.setFormat(_make_format(right_color))
        s2.fieldName = "RightLabel"
        s2.geometryGenerator = "end_point($geometry)"
        s2.geometryGeneratorType = QgsWkbTypes.PointGeometry
        s2.geometryGeneratorEnabled = True
        s2.placement = QgsPalLayerSettings.AroundPoint
        s2.quadOffset = QgsPalLayerSettings.QuadrantRight
        s2.dist = 0.5
        p2 = s2.dataDefinedProperties()
        p2.setProperty(QgsPalLayerSettings.LabelRotation, QgsProperty.fromExpression(rotation_expr))
        s2.setDataDefinedProperties(p2)

        root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
        root.appendChild(QgsRuleBasedLabeling.Rule(s1))
        root.appendChild(QgsRuleBasedLabeling.Rule(s2))

        self.chainage_layer.setLabeling(QgsRuleBasedLabeling(root))
        self.chainage_layer.setLabelsEnabled(True)

    # --- Raster Management ---

    def on_add_raster(self):
        layers = iface.layerTreeView().selectedLayers()
        added = False
        
        existing_ids = set(item['layer'].id() for item in self.raster_layers)
        
        for layer in layers:
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                if layer.id() not in existing_ids:
                    self.raster_layers.append({'layer': layer, 'style': 'Default'})
                    existing_ids.add(layer.id())
                    added = True
        
        if added:
            self.refresh_table()
            self.refresh_long_section_data()
            self.refresh_plots()
        else:
            QMessageBox.warning(self, "Add Raster", "Please select valid raster layer(s) in the Layers panel.")

    def on_remove_raster(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.raster_layers[row]
            self.refresh_table()
            self.refresh_long_section_data()
            self.refresh_plots()

    def on_clear_rasters(self):
        self.raster_layers.clear()
        self.refresh_table()
        self.refresh_long_section_data()
        self.refresh_plots()

    def on_move_up(self):
        row = self.table.currentRow()
        if row > 0:
            self.raster_layers[row], self.raster_layers[row-1] = self.raster_layers[row-1], self.raster_layers[row]
            self.refresh_table()
            self.table.selectRow(row-1)
            self.refresh_plots() # Legend order changes

    def on_move_down(self):
        row = self.table.currentRow()
        if row < len(self.raster_layers) - 1:
            self.raster_layers[row], self.raster_layers[row+1] = self.raster_layers[row+1], self.raster_layers[row]
            self.refresh_table()
            self.table.selectRow(row+1)
            self.refresh_plots()

    def refresh_table(self):
        self.table.setRowCount(len(self.raster_layers))
        self.table.blockSignals(True)
        for i, item_data in enumerate(self.raster_layers):
            layer = item_data['layer']
            style = item_data['style']
            is_sample = item_data.get('is_sample', False)
            
            item = QTableWidgetItem(layer.name())
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, item)
            
            # Elevation column
            elev_item = QTableWidgetItem("-")
            elev_item.setFlags(elev_item.flags() & ~Qt.ItemIsEditable)
            elev_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 1, elev_item)
            
            # Style column
            combo = QComboBox()
            combo.addItems(["Default", "EGL", "PGL"])
            combo.setCurrentText(style)
            combo.currentTextChanged.connect(lambda text, row=i: self.on_style_changed(row, text))
            self.table.setCellWidget(i, 2, combo)
            
            # Sample checkbox column (centred)
            sample_item = QTableWidgetItem()
            sample_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sample_item.setCheckState(Qt.Checked if is_sample else Qt.Unchecked)
            sample_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 3, sample_item)

        self.table.blockSignals(False)

    def on_sample_checked(self, item):
        """Enforce single-selection for the Sample column (col 3)."""
        if item.column() != 3:
            return
        checked_row = item.row()
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 3)
            if cell:
                new_state = Qt.Checked if r == checked_row and item.checkState() == Qt.Checked else Qt.Unchecked
                cell.setCheckState(new_state)
                if r < len(self.raster_layers):
                    self.raster_layers[r]['is_sample'] = (new_state == Qt.Checked)
        self.table.blockSignals(False)
        self.refresh_cross_section_plot()
        self.update_chainage()  # Refresh right labels on the map

    def get_sample_layer_id(self):
        """Return the layer id of the currently checked Sample row, or None."""
        for r, item_data in enumerate(self.raster_layers):
            if item_data.get('is_sample', False):
                return item_data['layer'].id()
        return None

    def on_style_changed(self, row, text):
        if 0 <= row < len(self.raster_layers):
            self.raster_layers[row]['style'] = text
            self.refresh_plots()
            if self.current_cursor_dist is not None:
                self.refresh_cross_section_plot()

    def update_elevation_column(self, dist):
        """Update the Elevation column in the table with values at the given distance."""
        if not self.alignment_geom:
            return
            
        pt_geom = self.alignment_geom.interpolate(dist)
        if not pt_geom:
            return
        pt = pt_geom.asPoint()
        pt_xy = QgsPointXY(pt.x(), pt.y())
        
        for i, item_data in enumerate(self.raster_layers):
            layer = item_data['layer']
            if not layer.isValid():
                continue
                
            val, ok = self._sample_raster(layer, pt_xy)
            
            text = "-"
            if self.is_valid_sample(val, ok, layer):
                text = f"{val:.3f}"
            
            item = self.table.item(i, 1)
            if item:
                item.setText(text)

    # --- Sampling Logic ---

    def sample_line(self, geom, rasters, num_points=200):
        """Sample multiple rasters along a geometry."""
        if not geom or not rasters:
            return {}
        
        length = geom.length()
        if length == 0:
            return {}
            
        step = length / num_points
        dists = [i * step for i in range(num_points + 1)]
        # Ensure end point
        if dists[-1] < length:
            dists.append(length)
            
        results = {layer.id(): [] for layer in rasters}
        
        for d in dists:
            pt_geom = geom.interpolate(d)
            if not pt_geom:
                continue
            pt = pt_geom.asPoint()
            pt_xy = QgsPointXY(pt.x(), pt.y())
            
            for layer in rasters:
                val, ok = self._sample_raster(layer, pt_xy)
                
                if self.is_valid_sample(val, ok, layer):
                    results[layer.id()].append(val)
                else:
                    results[layer.id()].append(np.nan)
                    
        return dists, results

    def refresh_long_section_data(self):
        """Pre-calculate long section data for all rasters."""
        if not self.alignment_geom or not self.raster_layers:
            self.long_section_data = {}
            return

        # Sample along alignment
        layers = [x['layer'] for x in self.raster_layers]
        dists, results = self.sample_line(self.alignment_geom, layers, num_points=500)
        
        self.long_section_data = {
            'dists': dists,
            'values': results
        }

    def get_cross_section_geom(self, distance, width, reverse=False):
        """Calculate perpendicular line geometry at distance."""
        if not self.alignment_geom:
            return None
            
        # Get point at distance
        pt = self.alignment_geom.interpolate(distance).asPoint()
        
        # Calculate angle (tangent)
        # We sample a bit before and after to get tangent
        length = self.alignment_geom.length()
        delta = min(0.1, length * 0.001) if length > 0 else 0.1
        
        p1 = self.alignment_geom.interpolate(max(0, distance - delta)).asPoint()
        p2 = self.alignment_geom.interpolate(min(length, distance + delta)).asPoint()
        
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        
        # Normal vector (-dy, dx)
        length = math.sqrt(dx*dx + dy*dy)
        if length == 0:
            return None
            
        nx = -dy / length
        ny = dx / length
        
        if reverse:
            nx, ny = -nx, -ny
        
        # Create line
        half_w = width / 2.0
        start = QgsPoint(pt.x() + nx * half_w, pt.y() + ny * half_w)
        end = QgsPoint(pt.x() - nx * half_w, pt.y() - ny * half_w)
        
        return QgsGeometry.fromPolyline([start, end])

    # --- Plotting ---

    def refresh_plots(self):
        self.ax_long.clear()
        self.ax_cross.clear()
        self.marker_line = None
        
        # Setup Axes
        self.ax_long.set_title("Long Section Profile")
        self.ax_long.set_xlabel("Distance (m)")
        self.ax_long.set_ylabel("Elevation (m)")
        self.ax_long.grid(True, alpha=0.3)
        
        self.ax_cross.set_title("Cross Section Profile")
        self.ax_cross.set_xlabel("Offset (m)")
        self.ax_cross.set_ylabel("Elevation (m)")
        self.ax_cross.grid(True, alpha=0.3)
        
        if not self.alignment_geom:
            self.canvas_plot.draw()
            return

        # Plot Long Section
        if self.long_section_data:
            dists = self.long_section_data.get('dists', [])
            values = self.long_section_data.get('values', {})
            
            # Color cycle
            colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(self.raster_layers))))
            
            for i, item_data in enumerate(self.raster_layers):
                layer = item_data['layer']
                style = item_data['style']
                
                if layer.id() in values:
                    vals = values[layer.id()]
                    
                    # Determine style
                    if style == 'EGL':
                        c, ls, lw = 'dimgray', '--', 1.5
                    elif style == 'PGL':
                        c, ls, lw = 'darkblue', '-', 1.5
                    else:
                        c, ls, lw = colors[i % 10], '-', 1.5
                        
                    self.ax_long.plot(dists, vals, label=layer.name(), color=c, linestyle=ls, linewidth=lw)
            
            if self.raster_layers:
                self.ax_long.legend(loc='best', fontsize='small')

        # Initialize marker (vertical dashed line)
        self.marker_line = self.ax_long.axvline(x=0, color='r', linestyle='--', alpha=0.8, zorder=10)
        self.marker_line.set_visible(False)
        
        self.ax_long.relim()
        self.ax_long.autoscale_view()
        self.ax_cross.relim()
        self.ax_cross.autoscale_view()
        
        self.fig.tight_layout()
        self.canvas_plot.draw()

    def on_toggle_reverse(self, checked):
        self.reverse_offset = checked
        self.refresh_cross_section_plot()

    def on_toggle_water_levels(self, checked):
        self.show_water_levels = checked
        self.refresh_cross_section_plot()

    def refresh_cross_section_plot(self):
        """Update only the cross section plot based on current cursor distance."""
        if self.current_cursor_dist is None or not self.alignment_geom:
            return
            
        self.ax_cross.clear()
        self.ax_cross.set_xlabel("Offset (m)")
        self.ax_cross.set_ylabel("Elevation (m)")
        self.ax_cross.grid(True, alpha=0.3)
        
        width = self.spin_width.value()
        cs_geom = self.get_cross_section_geom(self.current_cursor_dist, width, self.reverse_offset)
        
        if cs_geom:
            # Sample along cross section
            # We map offset from -width/2 to +width/2
            layers = [x['layer'] for x in self.raster_layers]
            dists, results = self.sample_line(cs_geom, layers, num_points=100)
            
            # Convert dists (0 to width) to offsets (-width/2 to width/2)
            offsets = [d - (width/2.0) for d in dists]
            
            colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(self.raster_layers))))
            
            wls_to_draw = []
            max_plot_val = -float('inf')
            for i, item_data in enumerate(self.raster_layers):
                layer = item_data['layer']
                style = item_data['style']
                
                if layer.id() in results:
                    vals = results[layer.id()]
                    
                    valid_vals = [v for v in vals if not np.isnan(v)]
                    if valid_vals:
                        max_plot_val = max(max_plot_val, max(valid_vals))
                    
                    if style == 'EGL':
                        c, ls, lw = 'dimgray', '--', 1.5
                    elif style == 'PGL':
                        c, ls, lw = 'darkblue', '-', 1.5
                    else:
                        c, ls, lw = colors[i % 10], '-', 1.5
                        
                    self.ax_cross.plot(offsets, vals, label=layer.name(), color=c, linestyle=ls, linewidth=lw)
                    
                    if self.show_water_levels and style not in ('EGL', 'PGL'):
                        idx = np.abs(np.array(offsets)).argmin()
                        z_val = vals[idx]
                        if not np.isnan(z_val):
                            wls_to_draw.append({'val': z_val, 'color': c, 'name': layer.name()})
            
            if wls_to_draw:
                self._draw_water_levels(self.ax_cross, wls_to_draw)
            
            # Vertical line at center
            self.ax_cross.axvline(0, color='r', linestyle='--', alpha=0.5)
            
            # Update Map Rubber Band
            canvas_cs_geom = self.get_canvas_geom(cs_geom)
            self.cs_rubber_band.setToGeometry(canvas_cs_geom, None)
            self.cs_rubber_band.show()
            
            # Update Arrow
            self.update_cs_arrow(cs_geom)
            
            if max_plot_val > -float('inf') and max_plot_val > 0:
                self.ax_cross.set_ylim(top=max_plot_val * 1.1)

        # --- Build title: left=chainage, right=sample raster value or chainage ---
        chainage_str = f"Ch: {self.current_cursor_dist:.1f} m"
        
        sample_layer_id = self.get_sample_layer_id()
        right_str = f"{self.current_cursor_dist:.1f} m"  # default: chainage distance
        right_color = 'black'
        
        if sample_layer_id and self.alignment_geom and cs_geom:
            # Find the layer object
            sample_layer = None
            for item_data in self.raster_layers:
                if item_data['layer'].id() == sample_layer_id:
                    sample_layer = item_data['layer']
                    break
            
            if sample_layer and sample_layer.isValid():
                # Sample at the alignment point (centre of cross section)
                pt_geom = self.alignment_geom.interpolate(self.current_cursor_dist)
                if pt_geom:
                    pt = pt_geom.asPoint()
                    val, ok = self._sample_raster(sample_layer, QgsPointXY(pt.x(), pt.y()))
                    if self.is_valid_sample(val, ok, sample_layer):
                        right_str = f"{val:.2g} m"
                    else:
                        right_str = ""  # NoData: show empty
                    right_color = 'blue'
        
        # Set title with two-part text: chainage (black, left) and raster value (coloured, right)
        self.ax_cross.set_title(chainage_str, loc='left', fontsize=9)
        self.ax_cross.set_title(right_str, loc='right', fontsize=9, color=right_color)

        self.ax_cross.relim()
        self.ax_cross.autoscale_view()
        self.canvas_plot.draw()

    def _draw_water_levels(self, ax, wls_list):
        """
        Draw water level markers with collision detection to avoid label overlap
        and overlap with plotted lines.
        wls_list: list of dicts {'val': float, 'color': str}
        """
        if not wls_list: return
        
        # Sort by Z descending (top to bottom)
        wls_list.sort(key=lambda x: x['val'], reverse=True)
        
        # Update limits to ensure transform is valid
        ax.relim()
        ax.autoscale_view()
        
        trans = ax.transData
        
        # Calculate marker height in meters (shift for annotation)
        # Marker size 12 pts, path height 1.6 (-0.8 to 0.8), top at 0.8
        # Shift = 0.8 * (12 / 1.6) = 6 points
        marker_height = 0.0
        try:
            # Get pixels per meter (y-axis)
            pts = trans.transform([(0, 0), (0, 1)])
            dy_pix = abs(pts[1][1] - pts[0][1])
            if dy_pix > 0:
                marker_height_px = 6.0 * (ax.figure.dpi / 72.0)
                marker_height = marker_height_px / dy_pix
        except Exception:
            pass

        # Get axes bounding box in display pixels for boundary check
        ax_bbox = ax.bbox
        ax_xmin, ax_ymin = ax_bbox.xmin, ax_bbox.ymin
        ax_xmax, ax_ymax = ax_bbox.xmax, ax_bbox.ymax
        
        # Custom marker: Inverted triangle with tip at (0,0)
        # We add an invisible point at (0, -0.8) to center the tip at (0,0) in the bbox
        # Verts: (-0.5, 0.8), (0.5, 0.8), (0.0, 0.0) -> Triangle
        # Invisible: (0, -0.8)
        marker_verts = [
            (-0.5, 0.8), (0.5, 0.8), (0.0, 0.0), (0.0, 0.0), # Triangle + Close
            (0.0, -0.8) # Invisible balance point
        ]
        marker_codes = [
            MplPath.MOVETO, MplPath.LINETO, MplPath.LINETO, MplPath.CLOSEPOLY,
            MplPath.MOVETO
        ]
        marker_path = MplPath(marker_verts, marker_codes)

        placed_labels = [] # Stores tuples (x1, x2, y1, y2) in pixels
        
        # Search parameters: 8 directions, increasing distances
        angles = np.deg2rad([45, 135, 225, 315, 90, 270, 0, 180])
        radii = [25, 40, 60, 80, 100]

        for item in wls_list:
            z = item['val']
            c = item['color']
            label_text = f"{z:.2f}"
            
            # 1. Draw Marker (Tip at 0,z)
            ax.plot(0, z, marker=marker_path, markersize=12, 
                    markeredgecolor='black', markeredgewidth=0.5, 
                    markerfacecolor=c, linestyle='None', zorder=20)
            
            # 2. Find Label Position
            try:
                target_pix = trans.transform([(0, z)])[0]
                tx, ty = target_pix
            except:
                continue

            # Estimate text size (pixels)
            lines = label_text.split('\n')
            w_text = max(len(l) for l in lines) * 6 + 10
            h_text = len(lines) * 10 + 8
            
            best_args = None
            found = False
            
            for r in radii:
                for theta in angles:
                    dx = math.cos(theta) * r
                    dy = math.sin(theta) * r
                    
                    # Alignment based on direction
                    ha = 'left' if dx >= 0 else 'right'
                    va = 'bottom' if dy >= 0 else 'top'
                    
                    # Calculate bounding box
                    x_anchor = tx + dx
                    y_anchor = ty + dy
                    
                    x1 = x_anchor if ha == 'left' else x_anchor - w_text
                    x2 = x_anchor + w_text if ha == 'left' else x_anchor
                    y1 = y_anchor if va == 'bottom' else y_anchor - h_text
                    y2 = y_anchor + h_text if va == 'bottom' else y_anchor
                    
                    # Check if label is within axes bounds (with padding)
                    pad = 2
                    if (x1 < ax_xmin + pad or x2 > ax_xmax - pad or 
                        y1 < ax_ymin + pad or y2 > ax_ymax - pad):
                        continue
                    
                    # Collision check against other labels
                    if not any(not (x2 < px1 or x1 > px2 or y2 < py1 or y1 > py2) for (px1, px2, py1, py2) in placed_labels):
                        best_args = (dx, dy, ha, va, x1, x2, y1, y2)
                        found = True
                        break
                if found: break
            
            if not best_args:
                # Fallback
                best_args = (30, 30, 'left', 'bottom', tx+30, tx+30+w_text, ty+30, ty+30+h_text)
            
            dx, dy, ha, va, x1, x2, y1, y2 = best_args
            
            ax.annotate(
                label_text,
                xy=(0, z + marker_height),
                xycoords='data',
                xytext=(dx, dy),
                textcoords='offset pixels',
                arrowprops=dict(
                    arrowstyle='-', # Just a line
                    color='black',
                    linewidth=0.5,
                    shrinkA=0, shrinkB=0, # No shortening
                ),
                color='black',
                fontsize=7,
                fontweight='normal',
                horizontalalignment=ha,
                verticalalignment=va,
                bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none", alpha=0.6),
                zorder=20
            )
            
            placed_labels.append((x1, x2, y1, y2))

    def update_cs_arrow(self, geom):
        """Draw a directional arrow at the end of the cross section line."""
        if not geom:
            self.cs_arrow_rubber_band.hide()
            return
            
        points = geom.asPolyline()
        if len(points) < 2:
            self.cs_arrow_rubber_band.hide()
            return
            
        p_start = points[0]
        p_end = points[-1]
        
        dx = p_end.x() - p_start.x()
        dy = p_end.y() - p_start.y()
        length = math.sqrt(dx*dx + dy*dy)
        if length == 0:
            self.cs_arrow_rubber_band.hide()
            return
            
        ux = dx / length
        uy = dy / length
        
        scale = self.canvas.mapUnitsPerPixel()
        arrow_len = 10 * scale
        arrow_half_width = 4 * scale
        
        bx, by = p_end.x(), p_end.y()
        tx, ty = bx + ux * arrow_len, by + uy * arrow_len
        lx, ly = bx - (-uy) * arrow_half_width, by - (ux) * arrow_half_width
        rx, ry = bx + (-uy) * arrow_half_width, by + (ux) * arrow_half_width
        
        poly = [QgsPointXY(tx, ty), QgsPointXY(lx, ly), QgsPointXY(rx, ry)]
        arrow_geom = QgsGeometry.fromPolygonXY([poly])
        canvas_arrow = self.get_canvas_geom(arrow_geom)
        self.cs_arrow_rubber_band.setToGeometry(canvas_arrow, None)
        self.cs_arrow_rubber_band.show()

    def on_plot_hover(self, event):
        """Handle mouse hover on matplotlib canvas."""
        if event.inaxes == self.ax_long:
            if not self.alignment_geom:
                return
                
            x_dist = event.xdata
            if x_dist is None:
                return
                
            # Clamp to geometry length
            length = self.alignment_geom.length()
            x_dist = max(0, min(x_dist, length))
            
            self.current_cursor_dist = x_dist
            
            # Update Vertical Line on Long Section
            self.marker_line.set_xdata([x_dist, x_dist])
            self.marker_line.set_visible(True)
            
            # Update Map Marker
            pt_geom = self.alignment_geom.interpolate(x_dist)
            if pt_geom:
                pt = pt_geom.asPoint()
                pt_canvas = self.get_canvas_pt(QgsPointXY(pt.x(), pt.y()))
                self.map_marker.setCenter(pt_canvas)
                self.map_marker.show()
            
            # Update Cross Section
            self.refresh_cross_section_plot()
            
            # Update Elevation Column
            self.update_elevation_column(x_dist)

    def on_output_cross_sections(self):
        """Generate PDF with cross sections at intervals."""
        if not self.alignment_geom:
            QMessageBox.warning(self, "Error", "No alignment defined.")
            return
        
        if not self.raster_layers:
            QMessageBox.warning(self, "Error", "No raster layers added.")
            return

        interval = self.spin_interval.value()
        width = self.spin_width.value()
        length = self.alignment_geom.length()
        
        # Generate distances
        dists = np.arange(0, length, interval)
        # Ensure we don't miss the very end if it's close to an interval, 
        # but typically cross sections are at fixed chainages. 
        # We'll stick to strict intervals starting at 0.
        
        # Output Path
        docs_dir = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Cross_Sections_{timestamp}.pdf"
        filepath = os.path.join(docs_dir, filename)
        
        # Plotting configuration (3x2)
        rows, cols = 3, 2
        plots_per_page = rows * cols
        
        try:
            with PdfPages(filepath) as pdf:
                # Progress Dialog
                progress = QProgressDialog("Generating PDF...", "Cancel", 0, len(dists) + 2, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.show()

                # --- Page 1: Plan View ---
                progress.setValue(1)
                QCoreApplication.processEvents()
                if progress.wasCanceled(): return

                # Create temp layer for alignment to show in map
                crs_auth = self.crs.authid()
                align_layer = QgsVectorLayer(f"LineString?crs={crs_auth}", "Alignment", "memory")
                af = QgsFeature()
                af.setGeometry(self.alignment_geom)
                align_layer.dataProvider().addFeatures([af])
                
                # Style alignment (Red)
                asym = QgsSymbol.defaultSymbol(align_layer.geometryType())
                asym.setColor(QColor("red"))
                asym.setWidth(0.8)
                align_layer.renderer().setSymbol(asym)
                
                # Capture Map
                settings = self.canvas.mapSettings()
                settings.setOutputSize(QSize(1754, 1240)) # A4 Landscape ~150 DPI
                settings.setOutputDpi(150)
                
                # Add alignment layer to rendering layers (on top)
                current_layers = settings.layers()
                settings.setLayers([align_layer] + current_layers)
                
                extent = self.alignment_geom.boundingBox()
                # Buffer extent
                buf = width 
                extent.grow(buf)
                settings.setExtent(extent)
                settings.setBackgroundColor(QColor("white"))
                
                job = QgsMapRendererSequentialJob(settings)
                job.start()
                job.waitForFinished()
                img = job.renderedImage()
                if img.isNull():
                    raise Exception("Map rendering failed (image is null)")
                
                # Save to temp
                tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                img.save(tf.name)
                tf.close()
                
                # Plot
                fig_map = Figure(figsize=(11.69, 8.27))
                ax_map = fig_map.add_subplot(111)
                ax_map.imshow(plt.imread(tf.name))
                ax_map.axis('off')
                ax_map.set_title("Plan View")
                fig_map.tight_layout()
                
                pdf.savefig(fig_map)
                os.unlink(tf.name)

                # --- Page 2: Long Section ---
                progress.setValue(2)
                QCoreApplication.processEvents()
                if progress.wasCanceled(): return

                fig_long = Figure(figsize=(11.69, 8.27)) # A4 Landscape
                ax_long = fig_long.add_subplot(111)
                
                if self.long_section_data:
                    ls_dists = self.long_section_data.get('dists', [])
                    ls_values = self.long_section_data.get('values', {})
                    colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(self.raster_layers))))
                    
                    for i, item_data in enumerate(self.raster_layers):
                        layer = item_data['layer']
                        style = item_data['style']
                        
                        if layer.id() in ls_values:
                            vals = ls_values[layer.id()]
                            if style == 'EGL':
                                c, ls, lw = 'dimgray', '--', 1.5
                            elif style == 'PGL':
                                c, ls, lw = 'blue', '-', 1.5
                            else:
                                c, ls, lw = colors[i % 10], '-', 1.0
                            ax_long.plot(ls_dists, vals, label=layer.name(), color=c, linestyle=ls, linewidth=lw)
                    
                    ax_long.set_title("Long Section Profile")
                    ax_long.set_xlabel("Distance (m)")
                    ax_long.set_ylabel("Elevation (m)")
                    ax_long.grid(True, alpha=0.3)
                    if self.raster_layers:
                        ax_long.legend(loc='best')
                
                fig_long.tight_layout()
                pdf.savefig(fig_long)

                # --- Pages 2+: Cross Sections ---
                fig = None
                axes = []
                
                for i, d in enumerate(dists):
                    if progress.wasCanceled():
                        return
                    progress.setValue(i + 3)
                    QCoreApplication.processEvents()

                    page_idx = i % plots_per_page
                    
                    # Start new page
                    if page_idx == 0:
                        if fig:
                            fig.tight_layout()
                            pdf.savefig(fig)
                        
                        fig = Figure(figsize=(11.69, 8.27)) # A4 Landscape
                        axes = [fig.add_subplot(rows, cols, k+1) for k in range(rows*cols)]
                    
                    ax = axes[page_idx]
                    
                    # Get Geometry & Sample
                    cs_geom = self.get_cross_section_geom(d, width, self.reverse_offset)
                    if cs_geom:
                        layers = [x['layer'] for x in self.raster_layers]
                        s_dists, results = self.sample_line(cs_geom, layers, num_points=100)
                        offsets = [sd - (width/2.0) for sd in s_dists]
                        
                        colors = plt.cm.tab10(np.linspace(0, 1, max(1, len(self.raster_layers))))
                        
                        wls_to_draw = []
                        max_plot_val = -float('inf')
                        for j, item_data in enumerate(self.raster_layers):
                            layer = item_data['layer']
                            style = item_data['style']
                            
                            if layer.id() in results:
                                vals = results[layer.id()]
                                
                                valid_vals = [v for v in vals if not np.isnan(v)]
                                if valid_vals:
                                    max_plot_val = max(max_plot_val, max(valid_vals))
                                
                                if style == 'EGL':
                                    c, ls, lw = 'dimgray', '--', 1.0
                                elif style == 'PGL':
                                    c, ls, lw = 'darkblue', '-', 1.0
                                else:
                                    c, ls, lw = colors[j % 10], '-', 1.0
                                
                                ax.plot(offsets, vals, label=layer.name(), color=c, linestyle=ls, linewidth=lw)
                                
                                if self.show_water_levels and style not in ('EGL', 'PGL'):
                                    idx = np.abs(np.array(offsets)).argmin()
                                    z_val = vals[idx]
                                    if not np.isnan(z_val):
                                        wls_to_draw.append({'val': z_val, 'color': c, 'name': layer.name()})
                        
                        if wls_to_draw:
                            self._draw_water_levels(ax, wls_to_draw)
                        
                        ax.set_title(f"Cross Section - Chainage: {d:.1f} m", fontsize=10)
                        ax.set_xlabel("Offset (m)", fontsize=8)
                        ax.set_ylabel("Elevation (m)", fontsize=8)
                        ax.grid(True, alpha=0.3)
                        ax.axvline(0, color='r', linestyle='--', alpha=0.5, linewidth=0.8)
                        
                        if max_plot_val > -float('inf') and max_plot_val > 0:
                            ax.set_ylim(top=max_plot_val * 1.1)
                        
                        if page_idx == 0 and self.raster_layers:
                            ax.legend(loc='best', fontsize='x-small')

                # Save last page
                if fig:
                    # Hide unused axes
                    filled_slots = len(dists) % plots_per_page
                    if filled_slots == 0:
                        filled_slots = plots_per_page
                        
                    for j in range(filled_slots, plots_per_page):
                        axes[j].axis('off')
                        
                    fig.tight_layout()
                    pdf.savefig(fig)
            
            # Open PDF
            QDesktopServices.openUrl(QUrl.fromLocalFile(filepath))
            
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save PDF:\n{str(e)}")

    def save_settings(self):
        s = QSettings()
        s.setValue("tuflow_tools/cross_section/width", self.spin_width.value())
        s.setValue("tuflow_tools/cross_section/interval", self.spin_interval.value())
        s.setValue("tuflow_tools/cross_section/reverse", self.btn_reverse.isChecked())
        s.setValue("tuflow_tools/cross_section/water_levels", self.btn_wl.isChecked())
        s.setValue("tuflow_tools/cross_section/chainage_on", self.btn_chainage.isChecked())
        
        if self.alignment_geom:
            s.setValue("tuflow_tools/cross_section/alignment_wkt", self.alignment_geom.asWkt())
        
        # Rasters: Store as list of strings "layer_id|style|source"
        r_list = []
        for item in self.raster_layers:
            try:
                r_list.append(f"{item['layer'].id()}|{item['style']}|{item['layer'].source()}")
            except (RuntimeError, AttributeError):
                pass
        s.setValue("tuflow_tools/cross_section/rasters", r_list)
        
        if self.chainage_layer:
            try:
                s.setValue("tuflow_tools/cross_section/chainage_layer_id", self.chainage_layer.id())
            except RuntimeError:
                pass

    def load_settings(self):
        s = QSettings()
        try:
            self.spin_width.setValue(float(s.value("tuflow_tools/cross_section/width", 100.0)))
            self.spin_interval.setValue(float(s.value("tuflow_tools/cross_section/interval", 50.0)))
            self.btn_reverse.setChecked(s.value("tuflow_tools/cross_section/reverse", False, type=bool))
            self.btn_wl.setChecked(s.value("tuflow_tools/cross_section/water_levels", False, type=bool))
            self.btn_chainage.setChecked(s.value("tuflow_tools/cross_section/chainage_on", False, type=bool))
        except:
            pass

        # Chainage Layer
        c_id = s.value("tuflow_tools/cross_section/chainage_layer_id", None)
        if c_id:
            l = QgsProject.instance().mapLayer(c_id)
            if l and isinstance(l, QgsVectorLayer):
                self.chainage_layer = l

        # Rasters
        r_list = s.value("tuflow_tools/cross_section/rasters", [], type=list)
        self.raster_layers = []
        for entry in r_list:
            parts = entry.split("|")
            lid = parts[0]
            style = parts[1] if len(parts) > 1 else 'Default'
            source = parts[2] if len(parts) > 2 else ''
            
            l = QgsProject.instance().mapLayer(lid)
            if not l and source:
                # try to find by source
                target_layers = QgsProject.instance().mapLayers().values()
                for tl in target_layers:
                    if isinstance(tl, QgsRasterLayer) and tl.source() == source:
                         l = tl
                         break
                         
            if l and isinstance(l, QgsRasterLayer) and l.isValid():
                self.raster_layers.append({'layer': l, 'style': style})
        self.refresh_table()

        # Alignment (Load last to trigger updates)
        wkt = s.value("tuflow_tools/cross_section/alignment_wkt", None)
        if wkt:
            geom = QgsGeometry.fromWkt(wkt)
            if geom and not geom.isEmpty():
                self.set_alignment(geom)

def run_cross_section_tool():
    """Launcher function for the tool."""
    dlg = CrossSectionAlignmentDialog(iface.mainWindow())
    dlg.show()
    _LIVE_WINDOWS.append(dlg)


class CrossSectionAlignmentAlgorithm(QgsProcessingAlgorithm):
    """
    Processing algorithm to launch the Cross Section tool.
    """

    def createInstance(self):
        return CrossSectionAlignmentAlgorithm()

    def name(self):
        return 'cross_section_alignment'

    def displayName(self):
        return 'Cross Sections along Alignment'

    def group(self):
        return '2 - Result Analysis'

    def groupId(self):
        return 'result_analysis'

    def shortHelpString(self):
        return "Launches the interactive Cross Sections along Alignment tool."

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        run_cross_section_tool()
        return {}