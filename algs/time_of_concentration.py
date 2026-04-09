# -*- coding: utf-8 -*-
"""
Time of Concentration Calculator Tool.
Interactive UI for calculating tc and tp from long section profile.

Features:
- Select alignment from vector layer or draw manually.
- Add and manage raster layers for DEM.
- Display long section profile.
- Calculate slope using equal area method: Sc = 2A/L
- Input parameters: C (channelisation factor) and CN (SCS curve number).
- Calculate and display tc and tp using BCFHH 1999c equations.
- Show note that tp should be used in HEC-HMS.
"""

import math
import numpy as np

try:
    from scipy.integrate import trapezoid
except ImportError:
    from scipy.integrate import trapz as trapezoid
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QMessageBox,
    QDoubleSpinBox,
    QFrame,
    QAbstractSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from qgis.PyQt.QtCore import (
    Qt,
    QSettings,
)
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsCoordinateTransform,
    QgsProcessingAlgorithm,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.utils import iface

# Matplotlib integration
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qtagg import (
        NavigationToolbar2QT as NavigationToolbar,
    )
    from matplotlib.figure import Figure
except ImportError:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import (
        NavigationToolbar2QT as NavigationToolbar,
    )
    from matplotlib.figure import Figure

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

        # Temporary rubber band for dynamic line segment
        self.temp_rubber_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.temp_rubber_band.setColor(Qt.red)
        self.temp_rubber_band.setWidth(1)
        self.temp_rubber_band.setLineStyle(Qt.DashLine)

        # Transformation to Project CRS
        self.canvas_crs = canvas.mapSettings().destinationCrs()
        self.project_crs = QgsProject.instance().crs()
        self.transform = QgsCoordinateTransform(
            self.canvas_crs, self.project_crs, QgsProject.instance()
        )

    def get_project_point(self, pos):
        """Convert a canvas Point (pixel) to a PointXY in Project CRS."""
        pt_canvas = self.toMapCoordinates(pos)
        if self.canvas_crs != self.project_crs:
            return self.transform.transform(pt_canvas)
        return pt_canvas

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt_proj = self.get_project_point(event.pos())
            self.points.append(pt_proj)
            self.rubber_band.addPoint(self.toMapCoordinates(event.pos()))
            self.temp_rubber_band.reset(QgsWkbTypes.LineGeometry)
        elif event.button() == Qt.RightButton:
            self.finish()

    def canvasMoveEvent(self, event):
        if not self.points:
            return

        self.temp_rubber_band.reset(QgsWkbTypes.LineGeometry)
        start_proj = self.points[-1]
        try:
            inv_transform = QgsCoordinateTransform(
                self.project_crs, self.canvas_crs, QgsProject.instance()
            )
            start_canvas = inv_transform.transform(start_proj)
        except Exception:
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


class TimeOfConcentrationDialog(QDialog):
    """Main Dialog for Time of Concentration Calculator."""

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
                xform = QgsCoordinateTransform(
                    project_crs, layer_crs, QgsProject.instance()
                )
                sample_pt = xform.transform(pt_xy)
            except Exception:
                return np.nan, False

        val, ok = layer.dataProvider().sample(sample_pt, 1)
        return val, ok

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time of Concentration Calculator")
        self.resize(1000, 800)

        # Data
        self.alignment_geom = None
        self.dem_layers = []  # List of DEM layers (last in list has priority)
        self.long_section_dists = []
        self.long_section_vals = []
        self.crs = QgsProject.instance().crs()

        # Calculated values
        self.alignment_length = 0.0
        self.slope_sc = 0.0
        self.profile_area = 0.0  # Area under the profile curve
        self.tc = 0.0
        self.tp = 0.0

        # UI Components
        self.init_ui()

        # Map Tool
        self.capture_tool = None

        # Connect to QGIS interface
        self.canvas = iface.mapCanvas()

        # Map Marker (Red Cross)
        self.map_marker = QgsVertexMarker(self.canvas)
        self.map_marker.setColor(QColor(255, 0, 0))
        self.map_marker.setIconSize(10)
        self.map_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.map_marker.setPenWidth(3)
        self.map_marker.hide()

        # Persistent Alignment Rubber Band
        self.alignment_rubber_band = QgsRubberBand(
            self.canvas, QgsWkbTypes.LineGeometry
        )
        self.alignment_rubber_band.setColor(QColor(100, 255, 100))
        self.alignment_rubber_band.setWidth(3)

        # Arrow Rubber Band (for direction)
        self.arrow_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.arrow_rubber_band.setFillColor(QColor(100, 255, 100))
        self.arrow_rubber_band.setStrokeColor(Qt.black)
        self.arrow_rubber_band.setWidth(1)

        # Load settings
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # --- Top Toolbar (Alignment Selection) ---
        top_layout = QHBoxLayout()

        btn_select = QPushButton("Select Feature")
        btn_select.setToolTip(
            "Use the selected feature from the active vector layer as alignment"
        )
        btn_select.clicked.connect(self.on_select_feature)
        top_layout.addWidget(btn_select)

        btn_draw = QPushButton("Draw Alignment")
        btn_draw.setToolTip("Draw a polyline on the map")
        btn_draw.clicked.connect(self.on_draw_alignment)
        top_layout.addWidget(btn_draw)

        btn_clear_align = QPushButton("Clear Alignment")
        btn_clear_align.setToolTip("Clear the current alignment")
        btn_clear_align.clicked.connect(self.on_clear_alignment)
        top_layout.addWidget(btn_clear_align)

        top_layout.addStretch()
        main_layout.addLayout(top_layout)

        # --- Middle: Long Section Plot ---
        self.fig = Figure(figsize=(10, 5))
        self.canvas_plot = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas_plot, self)

        self.ax_long = self.fig.add_subplot(111)
        self.ax_long.set_title("Long Section Profile")
        self.ax_long.set_xlabel("Distance (m)")
        self.ax_long.set_ylabel("Elevation (m)")
        self.ax_long.grid(True, alpha=0.3)

        self.fig.tight_layout()

        plot_layout = QVBoxLayout()
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas_plot)

        plot_frame = QFrame()
        plot_frame.setFrameShape(QFrame.StyledPanel)
        plot_frame.setLayout(plot_layout)
        main_layout.addWidget(plot_frame, stretch=2)

        # --- Bottom: Raster Management & Parameters ---
        bottom_layout = QHBoxLayout()

        # Left: DEM Layer Selection (aligned to top)
        left_widget = QFrame()
        left_widget.setFrameShape(QFrame.StyledPanel)
        raster_group_layout = QVBoxLayout()
        raster_group_layout.setContentsMargins(0, 0, 0, 0)
        left_widget.setLayout(raster_group_layout)

        raster_group_layout.addWidget(QLabel("DEM Layers:"))

        # Table for DEM layers
        self.table = QTableWidget()
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels(["Raster Layer"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMaximumHeight(150)
        raster_group_layout.addWidget(self.table)

        # Buttons for layer management
        btn_add = QPushButton("Add Selected")
        btn_add.clicked.connect(self.on_add_raster)
        raster_group_layout.addWidget(btn_add)

        # Layout for move/remove buttons
        move_layout = QHBoxLayout()
        btn_up = QPushButton("Move Up")
        btn_up.clicked.connect(self.on_move_up)
        move_layout.addWidget(btn_up)
        btn_down = QPushButton("Move Down")
        btn_down.clicked.connect(self.on_move_down)
        move_layout.addWidget(btn_down)
        raster_group_layout.addLayout(move_layout)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self.on_remove_raster)
        raster_group_layout.addWidget(btn_remove)

        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self.on_clear_rasters)
        raster_group_layout.addWidget(btn_clear)

        raster_group_layout.addStretch()  # Push everything to top
        bottom_layout.addWidget(left_widget, stretch=1)

        # Right: Parameters & Results (aligned to top)
        right_widget = QFrame()
        right_widget.setFrameShape(QFrame.StyledPanel)
        params_layout = QVBoxLayout()
        params_layout.setContentsMargins(0, 0, 0, 0)
        right_widget.setLayout(params_layout)

        # Parameters Group
        params_layout.addWidget(QLabel("Parameters:"))

        params_layout.addWidget(QLabel("Channelisation Factor C:"))
        self.spin_c = QDoubleSpinBox()
        self.spin_c.setRange(0.6, 1.0)
        self.spin_c.setValue(0.8)
        self.spin_c.setSingleStep(0.1)
        self.spin_c.setDecimals(2)
        self.spin_c.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin_c.valueChanged.connect(self.on_parameter_changed)
        params_layout.addWidget(self.spin_c)

        params_layout.addWidget(QLabel("SCS Curve Number (CN):"))
        self.spin_cn = QDoubleSpinBox()
        self.spin_cn.setRange(30.0, 100.0)
        self.spin_cn.setValue(70.0)
        self.spin_cn.setSingleStep(1.0)
        self.spin_cn.setDecimals(1)
        self.spin_cn.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin_cn.valueChanged.connect(self.on_parameter_changed)
        params_layout.addWidget(self.spin_cn)

        btn_calculate = QPushButton("Calculate tc and tp")
        btn_calculate.clicked.connect(self.on_calculate)
        params_layout.addWidget(btn_calculate)

        # Results Group
        params_layout.addSpacing(20)
        params_layout.addWidget(QLabel("Results:"))

        self.label_L = QLabel("Catchment Length L: - km")
        params_layout.addWidget(self.label_L)

        self.label_Sc = QLabel("Slope Sc: - m/m")
        params_layout.addWidget(self.label_Sc)

        self.label_tc = QLabel("Time of Concentration tc: - hrs")
        params_layout.addWidget(self.label_tc)

        self.label_tp = QLabel("Time to Peak tp: - hrs")
        self.label_tp.setStyleSheet("color: blue; font-weight: bold;")
        params_layout.addWidget(self.label_tp)

        self.label_note = QLabel(
            "(Note: Minimum tc = 10 minutes, tp to be used in HEC-HMS SCS unit hydrograph)"
        )
        self.label_note.setStyleSheet("color: green; font-style: italic;")
        params_layout.addWidget(self.label_note)

        params_layout.addStretch()  # Push everything to top
        bottom_layout.addWidget(right_widget, stretch=1)

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
        self.save_settings()
        super().closeEvent(event)

    def get_canvas_geom(self, geom):
        """Transform a geometry from Project CRS to Canvas CRS for rendering."""
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if self.crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(
                    self.crs, canvas_crs, QgsProject.instance()
                )
                geom_copy = QgsGeometry(geom)
                geom_copy.transform(xform)
                return geom_copy
            except Exception:
                pass
        return geom

    def update_arrow(self, geom):
        """Draw a directional arrow at the end of the alignment."""
        if not geom or geom.isEmpty():
            self.arrow_rubber_band.hide()
            return

        # Extract points from geometry
        if geom.isMultipart():
            lines = geom.asMultiPolyline()
            if not lines:
                return
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
        length = math.sqrt(dx * dx + dy * dy)

        if length == 0:
            self.arrow_rubber_band.hide()
            return

        ux = dx / length
        uy = dy / length

        # Size arrow based on current view scale (e.g 15 pixels long)
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

    def on_parameter_changed(self):
        """Auto-recalculate when C or CN parameters change."""
        if self.alignment_geom and self.dem_layer:
            self._calculate_automatically()

    def _calculate_automatically(self):
        """Internal calculation method (used for both auto and manual calculation)."""
        if not self.dem_layers:
            return

        if not self.alignment_geom:
            return

        if not self.long_section_vals or all(
            np.isnan(v) for v in self.long_section_vals
        ):
            return

        try:
            # Get parameters
            C = self.spin_c.value()
            CN = self.spin_cn.value()

            # Calculate slope
            Sc = self.calculate_slope_equal_area()
            self.slope_sc = Sc

            # Calculate tc using equation 4.3:
            # tc = 0.14 * C * L^0.66 * (CN/(200-CN))^-0.55 * Sc^-0.30
            L = self.alignment_length  # in km

            if CN >= 200:
                return

            if Sc <= 0:
                return

            tc = 0.14 * C * (L**0.66) * ((CN / (200 - CN)) ** -0.55) * (Sc**-0.30)

            # Apply minimum tc = 10 minutes (0.167 hrs)
            MIN_TC = 10.0 / 60.0  # 10 minutes in hours = 0.167 hrs
            tc = max(tc, MIN_TC)

            # Calculate tp using equation 4.1:
            # tp = (2/3) * tc
            tp = (2.0 / 3.0) * tc

            self.tc = tc
            self.tp = tp

            # Update labels
            self.label_L.setText(f"Catchment Length L: {L:.3f} km")
            self.label_Sc.setText(f"Slope Sc: {Sc:.6f} m/m")
            self.label_tc.setText(
                f"Time of Concentration tc: {tc:.3f} hrs ({tc * 60:.1f} min)"
            )
            self.label_tp.setText(f"Time to Peak tp: {tp:.3f} hrs ({tp * 60:.1f} min)")

        except Exception:
            pass

    def on_select_feature(self):
        layer = iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(
                self,
                "Selection Error",
                "Please select a vector layer in the Layers panel.",
            )
            return

        if layer.geometryType() != QgsWkbTypes.LineGeometry:
            QMessageBox.warning(
                self, "Selection Error", "Active layer must be a Line layer."
            )
            return

        selected = layer.selectedFeatures()
        if not selected:
            QMessageBox.warning(
                self, "Selection Error", "No features selected in the active layer."
            )
            return

        feat = selected[0]
        geom = QgsGeometry(feat.geometry())
        layer_crs = layer.crs()
        if layer_crs != self.crs:
            try:
                xform = QgsCoordinateTransform(
                    layer_crs, self.crs, QgsProject.instance()
                )
                geom.transform(xform)
            except Exception:
                pass

        if not geom or geom.isEmpty():
            return

        self.set_alignment(geom)

    def on_draw_alignment(self):
        self.capture_tool = CapturePolylineTool(self.canvas, self.set_alignment)
        self.canvas.setMapTool(self.capture_tool)
        self.activateWindow()

    def set_alignment(self, geom):
        self.alignment_geom = geom
        self.alignment_length = geom.length() / 1000.0  # Convert to km

        canvas_geom = self.get_canvas_geom(geom)
        self.alignment_rubber_band.setToGeometry(canvas_geom, None)
        self.alignment_rubber_band.show()

        self.update_arrow(geom)
        self.refresh_long_section_data()
        self.refresh_plots()
        self.clear_results()
        # Auto-calculate after alignment is set and data is ready
        self._calculate_automatically()

    def on_clear_alignment(self):
        self.alignment_geom = None
        self.alignment_rubber_band.reset()
        self.arrow_rubber_band.reset()
        self.map_marker.hide()
        self.long_section_dists = []
        self.long_section_vals = []
        self.refresh_plots()
        self.clear_results()

    def on_add_raster(self):
        layers = iface.layerTreeView().selectedLayers()
        added = False

        existing_ids = set(layer.id() for layer in self.dem_layers)

        for layer in layers:
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                if layer.id() not in existing_ids:
                    self.dem_layers.append(layer)
                    existing_ids.add(layer.id())
                    added = True

        if added:
            self.refresh_table()
            self.refresh_long_section_data()
            self.refresh_plots()
        else:
            QMessageBox.warning(
                self,
                "Add Raster",
                "Please select valid raster layer(s) in the Layers panel.",
            )

    def on_remove_raster(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.dem_layers[row]
            self.refresh_table()
            self.refresh_long_section_data()
            self.refresh_plots()

    def on_move_up(self):
        row = self.table.currentRow()
        if row > 0:
            self.dem_layers[row], self.dem_layers[row - 1] = (
                self.dem_layers[row - 1],
                self.dem_layers[row],
            )
            self.refresh_table()
            self.table.selectRow(row - 1)
            self.refresh_long_section_data()
            self.refresh_plots()

    def on_move_down(self):
        row = self.table.currentRow()
        if row < len(self.dem_layers) - 1:
            self.dem_layers[row], self.dem_layers[row + 1] = (
                self.dem_layers[row + 1],
                self.dem_layers[row],
            )
            self.refresh_table()
            self.table.selectRow(row + 1)
            self.refresh_long_section_data()
            self.refresh_plots()

    def on_clear_rasters(self):
        self.dem_layers.clear()
        self.refresh_table()
        self.refresh_long_section_data()
        self.refresh_plots()
        self.clear_results()

    def refresh_table(self):
        """Update the DEM layers table display."""
        self.table.setRowCount(len(self.dem_layers))
        self.table.blockSignals(True)
        for i, layer in enumerate(self.dem_layers):
            item = QTableWidgetItem(layer.name())
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, item)
        self.table.blockSignals(False)

    def sample_line(self, geom, rasters, num_points=500):
        """Sample multiple rasters along a geometry.
        Last layer in list has priority (wins on conflict).
        """
        if not geom or not rasters:
            return [], []

        length = geom.length()
        if length == 0:
            return [], []

        step = length / num_points
        dists = [i * step for i in range(num_points + 1)]
        if dists[-1] < length:
            dists.append(length)

        vals = []
        for d in dists:
            pt_geom = geom.interpolate(d)
            if not pt_geom:
                vals.append(np.nan)
                continue

            pt = pt_geom.asPoint()
            pt_xy = QgsPointXY(pt.x(), pt.y())

            # Check layers in reverse order (last layer wins)
            sampled_val = np.nan
            for layer in reversed(rasters):
                val, ok = self._sample_raster(layer, pt_xy)
                if self.is_valid_sample(val, ok, layer):
                    sampled_val = val
                    break  # Use first valid sample from highest priority layer

            vals.append(sampled_val)

        return dists, vals

    def refresh_long_section_data(self):
        """Refresh long section data from the DEM layers."""
        if not self.alignment_geom or not self.dem_layers:
            self.long_section_dists = []
            self.long_section_vals = []
            return

        dists, vals = self.sample_line(
            self.alignment_geom, self.dem_layers, num_points=500
        )
        self.long_section_dists = dists
        self.long_section_vals = vals

    def refresh_plots(self):
        """Refresh the long section plot."""
        self.ax_long.clear()
        self.ax_long.set_title("Long Section Profile")
        self.ax_long.set_xlabel("Distance (m)")
        self.ax_long.set_ylabel("Elevation (m)")
        self.ax_long.grid(True, alpha=0.3)

        if self.long_section_dists and self.long_section_vals:
            # Filter out NaN values for plotting
            valid_indices = ~np.isnan(self.long_section_vals)
            dists_valid = np.array(self.long_section_dists)[valid_indices]
            vals_valid = np.array(self.long_section_vals)[valid_indices]

            if len(dists_valid) > 0:
                # Get label from topmost DEM layer
                label = "Profile"
                if self.dem_layers:
                    label = f"{self.dem_layers[-1].name()} (Top Layer)"

                self.ax_long.plot(
                    dists_valid, vals_valid, "b-", linewidth=2, label=label
                )
                self.ax_long.fill_between(dists_valid, vals_valid, alpha=0.3)

                # Draw equal area slope line (black dashed)
                # Equal area method: straight line with same area under it as the profile
                # Area under line: (h1 + h2) / 2 * distance_range = Area_profile
                # Therefore: h2 = 2A/distance_range - h1
                if len(dists_valid) >= 2:
                    # Use actual sampled distance range
                    end_dist = dists_valid[-1]
                    start_dist = dists_valid[0]
                    distance_range = end_dist - start_dist  # Total distance span

                    if distance_range > 0:
                        # Calculate area under the profile
                        area = trapezoid(vals_valid, dists_valid)
                        h1 = vals_valid[0]
                        # Equal area formula: area = (h1 + h2) / 2 * distance_range
                        # Solve for h2: h2 = 2*area/distance_range - h1
                        h2 = (2.0 * area / distance_range) - h1
                        self.ax_long.plot(
                            [start_dist, end_dist],
                            [h1, h2],
                            "k--",
                            linewidth=2,
                            label="Equal Area Slope",
                        )

                self.ax_long.legend()

        self.canvas_plot.draw()

    def calculate_slope_equal_area(self):
        """Calculate slope using equal area method: Sc = 2A/L^2

        Where:
        - A is the area under the long section profile
        - L is the total length of the alignment

        The equal area slope represents a uniform slope that would have
        the same cumulative distribution as the actual profile.
        """
        if not self.long_section_dists or not self.long_section_vals:
            return 0.0

        # Filter out NaN values
        valid_indices = ~np.isnan(self.long_section_vals)
        dists_valid = np.array(self.long_section_dists)[valid_indices]
        vals_valid = np.array(self.long_section_vals)[valid_indices]

        if len(dists_valid) < 2:
            return 0.0

        # Calculate area using trapezoidal integration
        # This is the area between the profile and x-axis
        area = trapezoid(vals_valid, dists_valid)
        self.profile_area = area  # Store for reference

        # Slope = 2A/L^2 (L is in meters from alignment_length * 1000)
        L = self.alignment_length * 1000.0  # Convert back to meters
        if L == 0:
            return 0.0

        slope = 2.0 * area / (L**2)
        return slope

    def on_calculate(self):
        """Manual calculation button - calls the automatic calculation logic."""
        if not self.dem_layers:
            QMessageBox.warning(
                self, "Calculation Error", "Please add DEM layer(s) first."
            )
            return

        if not self.alignment_geom:
            QMessageBox.warning(
                self, "Calculation Error", "Please set an alignment first."
            )
            return

        if not self.long_section_vals or all(
            np.isnan(v) for v in self.long_section_vals
        ):
            QMessageBox.warning(
                self,
                "Calculation Error",
                "Could not sample elevation data from the DEM layers.",
            )
            return

        # Call the automatic calculation (which performs the actual calculation)
        self._calculate_automatically()

    def clear_results(self):
        """Clear the results display."""
        self.label_L.setText("Catchment Length L: - km")
        self.label_Sc.setText("Slope Sc: - m/m")
        self.label_tc.setText("Time of Concentration tc: - hrs")
        self.label_tp.setText("Time to Peak tp: - hrs")

    def load_settings(self):
        """Load dialog settings from QSettings."""
        settings = QSettings()
        try:
            c_val = float(settings.value("tuflow_tools/time_of_concentration/C", 0.8))
            self.spin_c.setValue(c_val)
            # CN must be float to preserve decimals (e.g., 90.8)
            cn_val = float(
                settings.value("tuflow_tools/time_of_concentration/CN", 70.0)
            )
            self.spin_cn.setValue(cn_val)
        except Exception:
            pass

        # DEM Layers - restore from list
        dem_list = settings.value(
            "tuflow_tools/time_of_concentration/dem_layers", [], type=list
        )
        self.dem_layers = []
        for entry in dem_list:
            parts = entry.split("|")
            layer_id = parts[0]
            source = parts[1] if len(parts) > 1 else ""

            layer = QgsProject.instance().mapLayer(layer_id)
            if not layer and source:
                # Try to find by source
                target_layers = QgsProject.instance().mapLayers().values()
                for tl in target_layers:
                    if (
                        isinstance(tl, QgsRasterLayer)
                        and tl.source() == source
                        and tl.isValid()
                    ):
                        layer = tl
                        break

            if layer and isinstance(layer, QgsRasterLayer) and layer.isValid():
                self.dem_layers.append(layer)

        self.refresh_table()

        # Alignment - restore from WKT (load last to trigger updates)
        alignment_wkt = settings.value(
            "tuflow_tools/time_of_concentration/alignment_wkt", None
        )
        if alignment_wkt:
            geom = QgsGeometry.fromWkt(alignment_wkt)
            if geom and not geom.isEmpty():
                self.set_alignment(geom)

    def save_settings(self):
        """Save dialog settings to QSettings."""
        settings = QSettings()
        settings.setValue("tuflow_tools/time_of_concentration/C", self.spin_c.value())
        settings.setValue("tuflow_tools/time_of_concentration/CN", self.spin_cn.value())

        # Save DEM layers as list of "layer_id|source"
        dem_list = []
        for layer in self.dem_layers:
            try:
                dem_list.append(f"{layer.id()}|{layer.source()}")
            except RuntimeError:
                pass
        settings.setValue("tuflow_tools/time_of_concentration/dem_layers", dem_list)

        # Save alignment geometry
        if self.alignment_geom:
            settings.setValue(
                "tuflow_tools/time_of_concentration/alignment_wkt",
                self.alignment_geom.asWkt(),
            )


def run_time_of_concentration_tool():
    """Launcher function for the tool."""
    dlg = TimeOfConcentrationDialog(iface.mainWindow())
    dlg.show()
    _LIVE_WINDOWS.append(dlg)


class TimeOfConcentrationAlgorithm(QgsProcessingAlgorithm):
    """Processing algorithm to launch the Time of Concentration tool."""

    def createInstance(self):
        return TimeOfConcentrationAlgorithm()

    def name(self):
        return "time_of_concentration"

    def displayName(self):
        return "Time of Concentration"

    def group(self):
        return "3 - Utilities"

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return (
            "Launches the Time of Concentration Calculator tool.\n"
            "Calculate tc (time of concentration) and tp (time to peak) "
            "from a catchment long profile using the BCFHH (1999c) equation.\n"
            "Note: tp should be used in HEC-HMS."
        )

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        run_time_of_concentration_tool()
        return {}
