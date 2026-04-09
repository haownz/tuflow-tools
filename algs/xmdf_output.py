# -*- coding: utf-8 -*-

"""
XMDF Output Algorithm
Reads XMDF mesh files via MDAL, lists available dataset groups,
and exports a selected dataset at a chosen timestep as a GeoTIFF raster.

Uses a popup selection dialog (instead of widget wrappers) because
the QGIS Processing parameter panel does not support dynamic updates.
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
    QgsMessageLog,
    Qgis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _log(msg):
    """Log to QGIS message log for debugging."""
    try:
        QgsMessageLog.logMessage(f"[XMDF Output] {msg}", "TUFLOW Tools", Qgis.Info)
    except Exception:
        pass


def _find_2dm_file(xmdf_path):
    """
    Try to find a .2dm mesh geometry file associated with the XMDF file.

    Search strategy:
      1. Same directory, same base name with .2dm extension
      2. Any .2dm file in the same directory
      3. Look in parent directory
    """
    xmdf_dir = os.path.dirname(xmdf_path)
    xmdf_base = os.path.splitext(os.path.basename(xmdf_path))[0]

    # 1. Exact match: same name but .2dm
    exact = os.path.join(xmdf_dir, xmdf_base + ".2dm")
    if os.path.exists(exact):
        return exact

    # 2. Any .2dm in the same directory
    for f in os.listdir(xmdf_dir):
        if f.lower().endswith(".2dm"):
            return os.path.join(xmdf_dir, f)

    # 3. Look in parent directory
    parent_dir = os.path.dirname(xmdf_dir)
    if os.path.exists(parent_dir):
        for f in os.listdir(parent_dir):
            if f.lower().endswith(".2dm"):
                return os.path.join(parent_dir, f)

    return None


def _load_xmdf_mesh(xmdf_path):
    """
    Load an XMDF file as a QgsMeshLayer.

    XMDF files store temporal datasets but need mesh geometry.
    Approach:
      1. Try loading the XMDF directly
      2. If that fails, find companion .2dm, load it, add XMDF datasets on top
    """
    # Approach 1: Try loading XMDF directly
    _log(f"Trying direct load: {xmdf_path}")
    mesh = QgsMeshLayer(xmdf_path, "xmdf_probe", "mdal")
    if mesh.isValid() and mesh.datasetGroupCount() > 0:
        _log(f"Direct load succeeded: {mesh.datasetGroupCount()} groups")
        return mesh

    _log("Direct XMDF load failed, searching for .2dm mesh file...")

    # Approach 2: Find companion .2dm file
    mesh_2dm = _find_2dm_file(xmdf_path)
    if not mesh_2dm:
        _log("No .2dm mesh file found")
        return None

    _log(f"Found .2dm file: {mesh_2dm}")
    mesh = QgsMeshLayer(mesh_2dm, "xmdf_probe", "mdal")
    if not mesh.isValid():
        _log(f".2dm mesh is not valid: {mesh_2dm}")
        return None

    # Add the XMDF datasets on top of the mesh geometry
    added = mesh.dataProvider().addDataset(xmdf_path)
    _log(f"addDataset: returned {added}")

    if mesh.datasetGroupCount() > 0:
        _log(f"Mesh now has {mesh.datasetGroupCount()} dataset groups")
        return mesh

    _log("Still 0 dataset groups after adding XMDF")
    return None


def _get_dataset_groups(mesh):
    """
    Read all dataset groups from a loaded mesh layer.
    Returns list of (name, group_index, timestep_count).
    """
    groups = []
    for i in range(mesh.datasetGroupCount()):
        meta = mesh.datasetGroupMetadata(QgsMeshDatasetIndex(i, 0))
        name = meta.name()
        ds_count = mesh.datasetCount(i)
        groups.append((name, i, ds_count))
    return groups


def _show_group_selection_dialog(groups, last_value=""):
    """
    Show a dialog for the user to pick a dataset group.
    Returns the selected group name, or None if cancelled.
    """
    try:
        from qgis.PyQt.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QDialogButtonBox,
        )
        from qgis.PyQt.QtCore import Qt

        dlg = QDialog()
        dlg.setWindowTitle("Select Dataset Group")
        dlg.setMinimumWidth(450)
        dlg.setMinimumHeight(350)
        layout = QVBoxLayout(dlg)

        layout.addWidget(
            QLabel(
                "The following dataset groups are available in the XMDF file.\n"
                "Select one to export as GeoTIFF:"
            )
        )

        list_widget = QListWidget()
        selected_idx = 0
        for idx, (name, _gi, ds_count) in enumerate(groups):
            item = QListWidgetItem(f"{name}  ({ds_count} timesteps)")
            item.setData(Qt.UserRole, name)
            list_widget.addItem(item)
            if name == last_value:
                selected_idx = idx

        list_widget.setCurrentRow(selected_idx)
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        # Double-click = accept
        list_widget.itemDoubleClicked.connect(dlg.accept)

        if dlg.exec_() == QDialog.Accepted:
            current = list_widget.currentItem()
            if current:
                return current.data(Qt.UserRole)
        return None
    except Exception as exc:
        _log(f"Dialog error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------
class XmdfOutputAlgorithm(QgsProcessingAlgorithm):
    """
    Reads an XMDF mesh file via MDAL, lets the user pick a dataset group
    and timestep, then exports a rasterised GeoTIFF.
    """

    P_XMDF_FILE = "XMDF_FILE"
    P_DATASET_GROUP = "DATASET_GROUP"
    P_TIMESTEP = "TIMESTEP"
    P_GRID_SIZE = "GRID_SIZE"
    P_OUTPUT = "OUTPUT"

    SETTINGS_PREFIX = "tuflow_tools/xmdf_output/"

    def createInstance(self):
        return XmdfOutputAlgorithm()

    def name(self):
        return "xmdf_output"

    def displayName(self):
        return "XMDF Output"

    def group(self):
        return "2 - Result Analysis"

    def groupId(self):
        return "result_analysis"

    def shortHelpString(self):
        return (
            "Reads an XMDF mesh file using the built-in MDAL driver and exports "
            "a selected dataset group at a chosen timestep as a GeoTIFF raster.\n\n"
            "A .2dm mesh geometry file must exist in the same directory as the "
            "XMDF file (TUFLOW places both in the results folder).\n\n"
            "Steps:\n"
            "1. Select an XMDF file.\n"
            "2. Leave Dataset Group empty to see a selection dialog on Run,\n"
            "   or type a known group name directly.\n"
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
        # --- XMDF file ---
        p_file = QgsProcessingParameterFile(
            self.P_XMDF_FILE,
            "XMDF File",
            extension="xmdf",
        )
        last_file = QgsSettings().value(self.SETTINGS_PREFIX + "XMDF_FILE", "")
        if last_file:
            p_file.setDefaultValue(last_file)
        self.addParameter(p_file)

        # --- Dataset group (user can type or leave empty for popup) ---
        last_group = QgsSettings().value(self.SETTINGS_PREFIX + "DATASET_GROUP", "")
        p_group = QgsProcessingParameterString(
            self.P_DATASET_GROUP,
            "Dataset Group (leave empty to select from list)",
            defaultValue=last_group,
            optional=True,
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
        xmdf_path = self.parameterAsFile(parameters, self.P_XMDF_FILE, context)
        group_raw = self.parameterAsString(
            parameters, self.P_DATASET_GROUP, context
        ).strip()
        timestep = self.parameterAsInt(parameters, self.P_TIMESTEP, context)
        grid_size = self.parameterAsDouble(parameters, self.P_GRID_SIZE, context)
        out_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)

        # --- Validate ---
        if not xmdf_path or not os.path.exists(xmdf_path):
            raise QgsProcessingException("XMDF file not found.")

        if grid_size <= 0:
            raise QgsProcessingException("Grid size must be > 0.")

        # --- Load mesh ---
        feedback.pushInfo(f"Loading XMDF: {xmdf_path}")
        mesh = _load_xmdf_mesh(xmdf_path)
        if mesh is None:
            raise QgsProcessingException(
                "Failed to load XMDF file.\n\n"
                "Make sure:\n"
                "  • A .2dm mesh geometry file exists in the same directory\n"
                "  • MDAL support is available in your QGIS installation"
            )

        # --- Discover dataset groups ---
        groups = _get_dataset_groups(mesh)
        feedback.pushInfo(f"Found {len(groups)} dataset group(s):")
        for name, gi, ds_count in groups:
            feedback.pushInfo(f"  [{gi}] {name}  ({ds_count} timesteps)")

        if not groups:
            raise QgsProcessingException("No dataset groups found in the XMDF file.")

        # --- Resolve group name ---
        group_name = group_raw.split("  (")[0].strip() if group_raw else ""

        if not group_name:
            # Show selection dialog
            last_saved = QgsSettings().value(self.SETTINGS_PREFIX + "DATASET_GROUP", "")
            group_name = _show_group_selection_dialog(groups, last_saved)
            if not group_name:
                feedback.pushInfo("User cancelled dataset group selection.")
                return {}

        feedback.pushInfo(f"Selected dataset group: '{group_name}'")

        # --- Find group index ---
        group_index = None
        for name, gi, ds_count in groups:
            if name == group_name:
                group_index = gi
                break

        if group_index is None:
            raise QgsProcessingException(
                f"Dataset group '{group_name}' not found in the XMDF file.\n\n"
                f"Available groups: {', '.join(g[0] for g in groups)}"
            )

        # --- Resolve timestep ---
        ds_count = mesh.datasetCount(group_index)
        if ds_count == 0:
            raise QgsProcessingException(
                f"Dataset group '{group_name}' contains no timesteps."
            )

        if timestep == 0 or timestep >= ds_count:
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
        settings.setValue(self.SETTINGS_PREFIX + "XMDF_FILE", xmdf_path)
        settings.setValue(self.SETTINGS_PREFIX + "DATASET_GROUP", group_name)
        settings.setValue(self.SETTINGS_PREFIX + "TIMESTEP", timestep)
        settings.setValue(self.SETTINGS_PREFIX + "GRID_SIZE", grid_size)

        feedback.pushInfo("Done.")
        return {self.P_OUTPUT: out_path}
