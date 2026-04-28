# -*- coding: utf-8 -*-

"""
XDMF Output Algorithm
Reads XDMF mesh files via MDAL, lists available dataset groups,
and exports a selected dataset at a chosen timestep as a GeoTIFF raster.
"""

import os

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsMeshLayer,
    QgsMeshDatasetIndex,
    QgsProject,
    QgsSettings,
)

try:
    from processing.gui.wrappers import WidgetWrapper
    from qgis.PyQt.QtWidgets import QComboBox

    HAS_GUI = True
except ImportError:
    HAS_GUI = False


# ---------------------------------------------------------------------------
# Widget Wrapper — Dataset Group Combo Box
# ---------------------------------------------------------------------------
class XdmfDatasetWidgetWrapper(WidgetWrapper):
    """
    Custom widget wrapper that presents a combo box populated with
    dataset group names read from the XDMF file via MDAL.

    Inherits from old-style WidgetWrapper (processing.gui.wrappers)
    required by the widget_wrapper metadata mechanism.
    """

    def __init__(self, param, dialog, row=0, col=0, **kwargs):
        super().__init__(param, dialog, row, col, **kwargs)
        self._combo = None
        self._settings_key = ""

    def createWidget(self):
        self._combo = QComboBox()
        self._combo.setEditable(False)

        # Load last used value from settings
        param_name = self.parameterDefinition().name()
        self._settings_key = f"tuflow_tools/xdmf_output/{param_name}"
        last_value = QgsSettings().value(self._settings_key, "")
        if last_value:
            self._combo.addItem(last_value)
            self._combo.setCurrentText(last_value)

        return self._combo

    def setValue(self, value):
        if self._combo is None:
            return
        idx = self._combo.findText(str(value))
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setCurrentText(str(value))

    def value(self):
        if self._combo is None:
            return ""
        return self._combo.currentText()

    def postInitialize(self, wrappers):
        """
        Connect to the XDMF_FILE parameter widget to refresh the
        combo box when the user picks a different file.
        """
        super().postInitialize(wrappers)
        for wrapper in wrappers:
            if wrapper.parameterDefinition().name() == "XDMF_FILE":
                wrapper.widgetValueHasChanged.connect(self._on_file_changed)
                # Populate immediately if a value is already set
                try:
                    xdmf_path = wrapper.value()
                    if xdmf_path:
                        self._populate_groups(xdmf_path)
                except Exception:
                    pass
                break

    def _on_file_changed(self, wrapper):
        """Called when the XDMF file parameter changes."""
        try:
            xdmf_path = wrapper.value()
            if xdmf_path:
                self._populate_groups(xdmf_path)
        except Exception:
            pass

    def _populate_groups(self, xdmf_path):
        """Load the XDMF via MDAL and populate combo with dataset groups."""
        if self._combo is None:
            return
        if not xdmf_path or not os.path.exists(xdmf_path):
            return

        try:
            mesh = QgsMeshLayer(xdmf_path, "xdmf_probe", "mdal")
            if not mesh.isValid():
                return

            group_count = mesh.datasetGroupCount()
            if group_count == 0:
                return

            groups = []
            for i in range(group_count):
                meta = mesh.datasetGroupMetadata(QgsMeshDatasetIndex(i, 0))
                name = meta.name()
                ds_count = mesh.datasetCount(i)
                groups.append((name, i, ds_count))

            # Remember current selection
            current_text = self._combo.currentText()

            self._combo.blockSignals(True)
            self._combo.clear()
            for name, idx, ds_count in groups:
                label = f"{name}  ({ds_count} timesteps)"
                self._combo.addItem(label, name)  # userData = raw name
            self._combo.blockSignals(False)

            # Restore previous selection
            if current_text:
                raw_current = current_text.split("  (")[0]  # strip the suffix
                for j in range(self._combo.count()):
                    if self._combo.itemData(j) == raw_current:
                        self._combo.setCurrentIndex(j)
                        return

            # Try last saved value
            last_value = QgsSettings().value(self._settings_key, "")
            if last_value:
                for j in range(self._combo.count()):
                    if self._combo.itemData(j) == last_value:
                        self._combo.setCurrentIndex(j)
                        return

            self._combo.setCurrentIndex(0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------
class XdmfOutputAlgorithm(QgsProcessingAlgorithm):
    """
    Reads an XDMF mesh file via MDAL, lets the user pick a dataset group
    and timestep, then exports a rasterised GeoTIFF.
    """

    P_XDMF_FILE = "XDMF_FILE"
    P_DATASET_GROUP = "DATASET_GROUP"
    P_TIMESTEP = "TIMESTEP"
    P_GRID_SIZE = "GRID_SIZE"
    P_OUTPUT = "OUTPUT"

    SETTINGS_PREFIX = "tuflow_tools/xdmf_output/"

    def createInstance(self):
        return XdmfOutputAlgorithm()

    def name(self):
        return "xdmf_output"

    def displayName(self):
        return "XDMF Output"

    def group(self):
        return "2 - Result Analysis"

    def groupId(self):
        return "result_analysis"

    def shortHelpString(self):
        return (
            "Reads an XDMF mesh file using the built-in MDAL driver and exports "
            "a selected dataset group at a chosen timestep as a GeoTIFF raster.\n\n"
            "Steps:\n"
            "1. Select an XDMF file — the available dataset groups will be listed.\n"
            "2. Choose the dataset group to export (e.g. depth, velocity).\n"
            "3. Specify the timestep index (0 = last/maximum available timestep).\n"
            "4. Set the output grid (cell) size in map units (default 1 m).\n"
            "5. Choose an output location.\n\n"
            "If the estimated output file would exceed 1 GB, a confirmation "
            "dialog is shown before processing begins."
        )

    # ------------------------------------------------------------------
    # Parameter definition
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        # --- XDMF file ---
        p_file = QgsProcessingParameterFile(
            self.P_XDMF_FILE,
            "XDMF File",
            extension="xdmf",
        )
        last_file = QgsSettings().value(self.SETTINGS_PREFIX + "XDMF_FILE", "")
        if last_file:
            p_file.setDefaultValue(last_file)
        self.addParameter(p_file)

        # --- Dataset group (dynamic combo via widget wrapper) ---
        p_group = QgsProcessingParameterString(
            self.P_DATASET_GROUP,
            "Dataset Group",
            defaultValue="",
        )
        if HAS_GUI:
            p_group.setMetadata(
                {"widget_wrapper": {"class": XdmfDatasetWidgetWrapper}}
            )
        self.addParameter(p_group)

        # --- Timestep ---
        last_ts = int(QgsSettings().value(self.SETTINGS_PREFIX + "TIMESTEP", 0))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_TIMESTEP,
                "Timestep index (0 = last/maximum)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=last_ts,
                minValue=0,
            )
        )

        # --- Grid size ---
        last_gs = float(QgsSettings().value(self.SETTINGS_PREFIX + "GRID_SIZE", 1.0))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_GRID_SIZE,
                "Grid (cell) size in map units",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=last_gs,
                minValue=0.001,
            )
        )

        # --- Output raster ---
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.P_OUTPUT,
                "Output GeoTIFF",
                QgsProcessing.TEMPORARY_OUTPUT,
            )
        )

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        xdmf_path = self.parameterAsFile(parameters, self.P_XDMF_FILE, context)
        group_raw = self.parameterAsString(
            parameters, self.P_DATASET_GROUP, context
        ).strip()
        timestep = self.parameterAsInt(parameters, self.P_TIMESTEP, context)
        grid_size = self.parameterAsDouble(parameters, self.P_GRID_SIZE, context)
        out_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)

        # --- Validate ---
        if not xdmf_path or not os.path.exists(xdmf_path):
            raise QgsProcessingException("XDMF file not found.")

        # Strip the display suffix "(N timesteps)" if present
        group_name = group_raw.split("  (")[0].strip()
        if not group_name:
            raise QgsProcessingException("No dataset group selected.")

        if grid_size <= 0:
            raise QgsProcessingException("Grid size must be > 0.")

        # --- Load mesh ---
        feedback.pushInfo(f"Loading XDMF: {xdmf_path}")
        mesh = QgsMeshLayer(xdmf_path, "xdmf_export", "mdal")
        if not mesh.isValid():
            raise QgsProcessingException(
                "Failed to load XDMF file. Ensure MDAL support is available."
            )

        # --- Find dataset group ---
        group_count = mesh.datasetGroupCount()
        feedback.pushInfo(f"Found {group_count} dataset group(s):")

        group_index = None
        for i in range(group_count):
            meta = mesh.datasetGroupMetadata(QgsMeshDatasetIndex(i, 0))
            name = meta.name()
            ds_count = mesh.datasetCount(i)
            feedback.pushInfo(f"  [{i}] {name}  ({ds_count} timesteps)")
            if name == group_name:
                group_index = i

        if group_index is None:
            raise QgsProcessingException(
                f"Dataset group '{group_name}' not found in the XDMF file."
            )

        # --- Resolve timestep ---
        ds_count = mesh.datasetCount(group_index)
        if ds_count == 0:
            raise QgsProcessingException(
                f"Dataset group '{group_name}' contains no timesteps."
            )

        if timestep == 0 or timestep >= ds_count:
            # 0 means "last / maximum"
            actual_ts = ds_count - 1
            feedback.pushInfo(
                f"Timestep 0 requested → using last available index: {actual_ts}"
            )
        else:
            actual_ts = timestep

        feedback.pushInfo(
            f"Exporting group '{group_name}' (index {group_index}), "
            f"timestep {actual_ts}/{ds_count - 1}"
        )

        # --- Estimate output size ---
        extent = mesh.extent()
        cols = int(extent.width() / grid_size) + 1
        rows = int(extent.height() / grid_size) + 1
        estimated_bytes = cols * rows * 4  # Float32 = 4 bytes per pixel
        estimated_mb = estimated_bytes / (1024 * 1024)
        estimated_gb = estimated_mb / 1024

        feedback.pushInfo(
            f"Output dimensions: {cols} x {rows} pixels "
            f"(≈ {estimated_mb:.1f} MB uncompressed)"
        )

        if estimated_gb > 1.0:
            # Show warning dialog (runs on the GUI thread via QMessageBox)
            try:
                from qgis.PyQt.QtWidgets import QMessageBox

                reply = QMessageBox.warning(
                    None,
                    "Large Output Warning",
                    f"The estimated output file is approximately "
                    f"{estimated_gb:.2f} GB (uncompressed).\n\n"
                    f"Dimensions: {cols} × {rows} pixels\n"
                    f"Grid size: {grid_size} m\n\n"
                    f"This may take a very long time to generate.\n"
                    f"Do you want to proceed?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    feedback.pushInfo("User cancelled — output too large.")
                    return {}
            except Exception:
                # If GUI is unavailable (e.g. headless), just warn in feedback
                feedback.reportError(
                    f"WARNING: Estimated output is {estimated_gb:.2f} GB. "
                    "Proceeding anyway (no GUI available for confirmation)."
                )

        # --- Rasterise via native algorithm ---
        feedback.pushInfo("Rasterising mesh dataset to GeoTIFF ...")

        if feedback.isCanceled():
            return {}

        import processing

        rasterise_params = {
            "INPUT": mesh,
            "DATASET_GROUPS": [group_index],
            "DATASET_TIME": {
                "type": "dataset-time-step",
                "value": [group_index, actual_ts],
            },
            "EXTENT": extent,
            "PIXEL_SIZE": grid_size,
            "CRS_OUTPUT": mesh.crs(),
            "OUTPUT": out_path,
        }

        result = processing.run(
            "native:rasterizemeshdataset",
            rasterise_params,
            context=context,
            feedback=feedback,
        )

        out_path = result.get("OUTPUT", out_path)
        feedback.pushInfo(f"Output saved to: {out_path}")

        # --- Register for loading ---
        try:
            layer_name = f"{group_name}_ts{actual_ts}"
            project = context.project() or QgsProject.instance()
            details = QgsProcessingContext.LayerDetails(layer_name, project)
            context.addLayerToLoadOnCompletion(out_path, details)
        except Exception as e:
            feedback.reportError(f"Could not register layer for loading: {e}")

        # --- Persist settings ---
        settings = QgsSettings()
        settings.setValue(self.SETTINGS_PREFIX + "XDMF_FILE", xdmf_path)
        settings.setValue(self.SETTINGS_PREFIX + "DATASET_GROUP", group_name)
        settings.setValue(self.SETTINGS_PREFIX + "TIMESTEP", timestep)
        settings.setValue(self.SETTINGS_PREFIX + "GRID_SIZE", grid_size)

        feedback.pushInfo("Done.")
        return {self.P_OUTPUT: out_path}
