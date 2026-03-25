# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

from qgis.PyQt.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QDoubleSpinBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QSplitter,
    QLabel,
)
from qgis.PyQt.QtCore import Qt, QCoreApplication, QSettings
from qgis.core import QgsProcessingAlgorithm
from qgis.utils import iface

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

_LIVE_WINDOWS = []


class DepthDischargeCurveDialog(QDialog):
    """Interactive UI for Depth Discharge Curve calculation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Depth Discharge Curve")
        self.resize(1000, 700)
        self.df = None
        self.Q_pipe_full = 0.0

        # Load settings
        self.settings = QSettings()

        def load_setting(key, default):
            val = self.settings.value(f"tuflow_tools/depth_discharge/{key}", default)
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        main_layout = QHBoxLayout(self)

        # Splitter to separate inputs and plot
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- Left Panel: Inputs ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Helper function to create spinboxes
        def create_spinbox(
            value, min_val=-9999.0, max_val=9999.0, decimals=4, step=0.1
        ):
            spin = QDoubleSpinBox()
            spin.setRange(min_val, max_val)
            spin.setDecimals(decimals)
            spin.setSingleStep(step)
            spin.setValue(value)
            spin.valueChanged.connect(self.update_plot)
            return spin

        # 1. Levels Group
        group_levels = QGroupBox("Levels (m)")
        layout_levels = QFormLayout()
        self.spin_base_level = create_spinbox(load_setting("base_level", 14.0))
        self.spin_orifice_invert = create_spinbox(load_setting("orifice_invert", 14.0))
        self.spin_lid_level = create_spinbox(load_setting("lid_level", 15.5))
        self.spin_pipe_invert = create_spinbox(
            load_setting("pipe_invert", 13.8)
        )  # Not used directly in calc but kept for context
        layout_levels.addRow("Base Level (for Depth):", self.spin_base_level)
        layout_levels.addRow("Orifice Invert:", self.spin_orifice_invert)
        layout_levels.addRow("Dome Lid:", self.spin_lid_level)
        layout_levels.addRow("Pipe Invert:", self.spin_pipe_invert)
        group_levels.setLayout(layout_levels)

        # 2. Geometry Group
        group_geom = QGroupBox("Geometry (m)")
        layout_geom = QFormLayout()
        self.spin_d_orifice = create_spinbox(
            load_setting("d_orifice", 0.0564), min_val=0
        )
        self.spin_d_dome = create_spinbox(load_setting("d_dome", 1.05), min_val=0)
        self.spin_d_pipe = create_spinbox(load_setting("d_pipe", 0.75), min_val=0)
        layout_geom.addRow("Orifice Diameter:", self.spin_d_orifice)
        layout_geom.addRow("Dome Diameter:", self.spin_d_dome)
        layout_geom.addRow("Pipe Diameter:", self.spin_d_pipe)
        group_geom.setLayout(layout_geom)

        # 3. Coefficients Group
        group_coeffs = QGroupBox("Coefficients")
        layout_coeffs = QFormLayout()
        self.spin_cd_orifice = create_spinbox(
            load_setting("cd_orifice", 0.65), min_val=0
        )
        self.spin_cd_weir = create_spinbox(load_setting("cd_weir", 1.84), min_val=0)
        layout_coeffs.addRow("Orifice Cd:", self.spin_cd_orifice)
        layout_coeffs.addRow("Weir Cd:", self.spin_cd_weir)
        group_coeffs.setLayout(layout_coeffs)

        # 4. Pipe Parameters Group
        group_pipe = QGroupBox("Pipe Parameters")
        layout_pipe = QFormLayout()
        self.spin_slope = create_spinbox(
            load_setting("slope", 0.01), min_val=0, decimals=4
        )
        self.spin_manning = create_spinbox(
            load_setting("manning", 0.013), min_val=0, decimals=4
        )
        layout_pipe.addRow("Slope (m/m):", self.spin_slope)
        layout_pipe.addRow("Manning's n:", self.spin_manning)
        group_pipe.setLayout(layout_pipe)

        # 5. Range Group
        group_range = QGroupBox("Water Level Range (m)")
        layout_range = QFormLayout()
        self.spin_wl_min = create_spinbox(load_setting("wl_min", 14.0))
        self.spin_wl_max = create_spinbox(load_setting("wl_max", 17.5))
        self.spin_wl_step = create_spinbox(load_setting("wl_step", 0.05), min_val=0.001)
        layout_range.addRow("Min Level:", self.spin_wl_min)
        layout_range.addRow("Max Level:", self.spin_wl_max)
        layout_range.addRow("Step:", self.spin_wl_step)
        group_range.setLayout(layout_range)

        # Add groups to left layout
        left_layout.addWidget(group_levels)
        left_layout.addWidget(group_geom)
        left_layout.addWidget(group_coeffs)
        left_layout.addWidget(group_pipe)
        left_layout.addWidget(group_range)

        self.lbl_capacity = QLabel("Pipe Capacity: -- m³/s")
        self.lbl_capacity.setStyleSheet(
            "font-weight: bold; color: #0055a4; margin-top: 10px;"
        )
        left_layout.addWidget(self.lbl_capacity)

        btn_layout = QHBoxLayout()
        btn_export = QPushButton("Export to CSV")
        btn_export.clicked.connect(self.export_csv)
        btn_default = QPushButton("Restore Defaults")
        btn_default.clicked.connect(self.restore_defaults)

        btn_layout.addWidget(btn_default)
        btn_layout.addWidget(btn_export)
        left_layout.addLayout(btn_layout)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # --- Right Panel: Plot ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(8, 6))
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)

        self.ax = self.fig.add_subplot(111)
        self.fig.tight_layout()

        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)
        splitter.addWidget(right_widget)

        splitter.setSizes([300, 700])

        # Initial plot
        self.update_plot()

    def update_plot(self):
        # Retrieve values
        base_level = self.spin_base_level.value()
        orifice_invert = self.spin_orifice_invert.value()
        lid_level = self.spin_lid_level.value()

        d_orifice = self.spin_d_orifice.value()
        d_dome = self.spin_d_dome.value()
        d_pipe = self.spin_d_pipe.value()

        cd_orifice = self.spin_cd_orifice.value()
        cd_weir = self.spin_cd_weir.value()

        slope = self.spin_slope.value()
        n_manning = self.spin_manning.value()

        wl_min = self.spin_wl_min.value()
        wl_max = self.spin_wl_max.value()
        wl_step = self.spin_wl_step.value()

        g = 9.81

        # Prevent invalid ranges
        if wl_max <= wl_min or wl_step <= 0:
            self.ax.clear()
            self.canvas.draw()
            return

        # Pre-calculations
        A_orifice = np.pi * (d_orifice**2) / 4
        L_weir = np.pi * d_dome
        A_pipe = np.pi * (d_pipe**2) / 4
        R_pipe = d_pipe / 4

        # Manning full pipe capacity
        self.Q_pipe_full = 0.0
        if n_manning > 0:
            self.Q_pipe_full = (
                (1 / n_manning) * A_pipe * (R_pipe ** (2 / 3)) * (slope**0.5)
            )

        self.lbl_capacity.setText(f"Pipe Capacity: {self.Q_pipe_full:.3f} m³/s")

        results = []
        wls = np.arange(wl_min, wl_max + wl_step, wl_step)

        for WL in wls:
            # --- ORIFICE FLOW ---
            if WL > orifice_invert:
                H_orifice = WL - orifice_invert
                Q_orifice = cd_orifice * A_orifice * np.sqrt(2 * g * H_orifice)
            else:
                Q_orifice = 0.0

            # --- WEIR FLOW ---
            if WL > lid_level:
                H_weir = WL - lid_level
                Q_weir = cd_weir * L_weir * (H_weir**1.5)
            else:
                Q_weir = 0.0

            # --- TOTAL INFLOW ---
            Q_total = Q_orifice + Q_weir

            # --- PIPE CAPACITY LIMIT ---
            Q_final = min(Q_total, self.Q_pipe_full)

            # Depth above base level
            depth = WL - base_level

            results.append(
                [WL, depth, Q_orifice, Q_weir, Q_total, self.Q_pipe_full, Q_final]
            )

        self.df = pd.DataFrame(
            results,
            columns=[
                "Water Level (m)",
                "Depth (m)",
                "Q Orifice (m3/s)",
                "Q Weir (m3/s)",
                "Q Total (m3/s)",
                "Pipe Capacity (m3/s)",
                "Q Final (m3/s)",
            ],
        )

        # Render Plot
        self.ax.clear()
        self.ax.plot(
            self.df["Depth (m)"],
            self.df["Q Final (m3/s)"],
            label="Q Final",
            linewidth=3,
            color="blue",
        )
        self.ax.plot(
            self.df["Depth (m)"],
            self.df["Q Orifice (m3/s)"],
            label="Q Orifice",
            linestyle="--",
        )
        self.ax.plot(
            self.df["Depth (m)"],
            self.df["Q Weir (m3/s)"],
            label="Q Weir",
            linestyle="-.",
        )
        self.ax.axhline(
            y=self.Q_pipe_full, color="r", linestyle=":", label="Pipe Capacity"
        )

        self.ax.set_xlabel("Depth (m) [above Base Level]")
        self.ax.set_ylabel("Discharge (m3/s)")
        self.ax.set_title("Composite Depth Discharge Curve")
        self.ax.legend()
        self.ax.grid(True, alpha=0.3)
        self.fig.tight_layout()

        self.canvas.draw()

    def export_csv(self):
        if self.df is None or self.df.empty:
            QMessageBox.warning(self, "No Data", "There is no data to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", "composite_QH_curve.csv", "CSV files (*.csv)"
        )

        if file_path:
            try:
                self.df.to_csv(file_path, index=False)
                QMessageBox.information(
                    self, "Success", f"CSV successfully saved to:\n{file_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save CSV:\n{e}")

    def save_settings(self):
        self.settings.setValue(
            "tuflow_tools/depth_discharge/base_level", self.spin_base_level.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/orifice_invert",
            self.spin_orifice_invert.value(),
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/lid_level", self.spin_lid_level.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/pipe_invert", self.spin_pipe_invert.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/d_orifice", self.spin_d_orifice.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/d_dome", self.spin_d_dome.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/d_pipe", self.spin_d_pipe.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/cd_orifice", self.spin_cd_orifice.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/cd_weir", self.spin_cd_weir.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/slope", self.spin_slope.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/manning", self.spin_manning.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/wl_min", self.spin_wl_min.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/wl_max", self.spin_wl_max.value()
        )
        self.settings.setValue(
            "tuflow_tools/depth_discharge/wl_step", self.spin_wl_step.value()
        )

    def restore_defaults(self):
        # Block signals briefly so we only update the plot once at the end
        self.spin_base_level.blockSignals(True)
        self.spin_orifice_invert.blockSignals(True)
        self.spin_lid_level.blockSignals(True)
        self.spin_pipe_invert.blockSignals(True)
        self.spin_d_orifice.blockSignals(True)
        self.spin_d_dome.blockSignals(True)
        self.spin_d_pipe.blockSignals(True)
        self.spin_cd_orifice.blockSignals(True)
        self.spin_cd_weir.blockSignals(True)
        self.spin_slope.blockSignals(True)
        self.spin_manning.blockSignals(True)
        self.spin_wl_min.blockSignals(True)
        self.spin_wl_max.blockSignals(True)
        self.spin_wl_step.blockSignals(True)

        self.spin_base_level.setValue(14.0)
        self.spin_orifice_invert.setValue(14.0)
        self.spin_lid_level.setValue(15.5)
        self.spin_pipe_invert.setValue(13.8)
        self.spin_d_orifice.setValue(0.0564)
        self.spin_d_dome.setValue(1.05)
        self.spin_d_pipe.setValue(0.75)
        self.spin_cd_orifice.setValue(0.65)
        self.spin_cd_weir.setValue(1.84)
        self.spin_slope.setValue(0.01)
        self.spin_manning.setValue(0.013)
        self.spin_wl_min.setValue(14.0)
        self.spin_wl_max.setValue(17.5)
        self.spin_wl_step.setValue(0.05)

        self.spin_base_level.blockSignals(False)
        self.spin_orifice_invert.blockSignals(False)
        self.spin_lid_level.blockSignals(False)
        self.spin_pipe_invert.blockSignals(False)
        self.spin_d_orifice.blockSignals(False)
        self.spin_d_dome.blockSignals(False)
        self.spin_d_pipe.blockSignals(False)
        self.spin_cd_orifice.blockSignals(False)
        self.spin_cd_weir.blockSignals(False)
        self.spin_slope.blockSignals(False)
        self.spin_manning.blockSignals(False)
        self.spin_wl_min.blockSignals(False)
        self.spin_wl_max.blockSignals(False)
        self.spin_wl_step.blockSignals(False)

        self.update_plot()

    def closeEvent(self, event):
        self.save_settings()
        if self in _LIVE_WINDOWS:
            _LIVE_WINDOWS.remove(self)
        super().closeEvent(event)


def run_depth_discharge_tool():
    """Launcher function for the tool."""
    dlg = DepthDischargeCurveDialog(iface.mainWindow())
    dlg.show()
    _LIVE_WINDOWS.append(dlg)


class DepthDischargeCurveAlgorithm(QgsProcessingAlgorithm):
    """
    Processing algorithm launcher for the Depth Discharge Curve interactive tool.
    """

    def tr(self, message):
        return QCoreApplication.translate("DepthDischargeCurve", message)

    def createInstance(self):
        return DepthDischargeCurveAlgorithm()

    def name(self):
        return "depth_discharge_curve"

    def displayName(self):
        return self.tr("Depth Discharge Curve")

    def group(self):
        return self.tr("1 - Input Processing")

    def groupId(self):
        return "input_processing"

    def shortHelpString(self):
        return self.tr(
            "Opens an interactive tool to build a composite Q–H curve including Orifice flow, "
            "Dome (weir) flow, and Pipe capacity using Manning’s equation. "
            "You can export the results directly to CSV from the UI."
        )

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        run_depth_discharge_tool()
        return {}
