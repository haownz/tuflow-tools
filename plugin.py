# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import (
    QgsApplication,
    QgsMessageLog,
    Qgis,
    QgsExpressionContextUtils,
    QgsProject,
    QgsExpression,
)
from .provider import TuflowProcessingProvider
from .style_manager import StyleManager
from .settings import PluginSettings
import os
import processing


class TuflowToolsPlugin(QObject):
    """
    QGIS plugin entry point. Registers the 'TUFLOW tools' Processing provider.
    """

    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.provider = None
        self.toolbar = None
        self.style_action = None
        self.rename_action = None
        self.restore_layer_name_action = None
        self.toggle_labels_action = None
        self.duplicate_layer_action = None
        self.collapse_action = None
        self.sort_group_action = None
        self.theme_manager_action = None
        self.batch_theme_export_action = None
        self.select_layers_action = None

    def initGui(self):
        self.provider = TuflowProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # Create toolbar
        self.toolbar = self.iface.addToolBar("TUFLOW Tools")
        self.toolbar.setObjectName("TuflowToolsToolbar")

        # Add style button
        icon_path = os.path.join(os.path.dirname(__file__), "icon_style.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.style_action = QAction(icon, "Apply Style", self.iface.mainWindow())
        self.style_action.setToolTip("Apply style to selected layers")
        self.style_action.triggered.connect(self.apply_style_to_selected)
        self.toolbar.addAction(self.style_action)

        # Add Select Layers by Search button
        self.select_layers_action = QAction(
            QgsApplication.getThemeIcon("/mActionFilter2.svg"),
            "Select Layers by Search",
            self.iface.mainWindow(),
        )
        self.select_layers_action.setToolTip(
            "Select layers by fuzzy search or wildcard pattern (e.g. 2d_bc* or *result*)"
        )
        self.select_layers_action.triggered.connect(self.run_select_layers_by_search)
        self.toolbar.addAction(self.select_layers_action)

        # Add Batch Rename button
        self.rename_action = QAction(
            QgsApplication.getThemeIcon("/mActionReplace.svg"),
            "Batch Rename",
            self.iface.mainWindow(),
        )
        self.rename_action.setToolTip("Batch rename selected layers")
        self.rename_action.triggered.connect(self.run_batch_rename)
        self.toolbar.addAction(self.rename_action)

        # Add Restore Layer Name button
        icon_layer_name_path = os.path.join(
            os.path.dirname(__file__), "icon_layer_name.png"
        )
        icon_layer_name = (
            QIcon(icon_layer_name_path)
            if os.path.exists(icon_layer_name_path)
            else QIcon()
        )
        self.restore_layer_name_action = QAction(
            icon_layer_name, "Restore Layer Name", self.iface.mainWindow()
        )
        self.restore_layer_name_action.setToolTip("Restore layer name from source")
        self.restore_layer_name_action.triggered.connect(self.run_restore_layer_name)
        self.toolbar.addAction(self.restore_layer_name_action)

        # Add Toggle Labels button
        icon_toggle_path = os.path.join(
            os.path.dirname(__file__), "icon_toggle_labels.png"
        )
        icon_toggle = (
            QIcon(icon_toggle_path) if os.path.exists(icon_toggle_path) else QIcon()
        )
        self.toggle_labels_action = QAction(
            icon_toggle,
            "Toggle Labels",
            self.iface.mainWindow(),
        )
        self.toggle_labels_action.setCheckable(True)
        self.toggle_labels_action.setToolTip("Toggle labels for selected layer")
        self.toggle_labels_action.triggered.connect(self.toggle_selected_layer_labels)
        self.toolbar.addAction(self.toggle_labels_action)

        # Add Duplicate Layer button
        self.duplicate_layer_action = QAction(
            QgsApplication.getThemeIcon("/mActionDuplicateLayer.svg"),
            "Duplicate Layer Data",
            self.iface.mainWindow(),
        )
        self.duplicate_layer_action.setToolTip(
            "Duplicate source data of selected vector layer"
        )
        self.duplicate_layer_action.triggered.connect(self.duplicate_vector_layer)
        self.toolbar.addAction(self.duplicate_layer_action)

        # Add Collapse All Sub-layers button
        icon_collapse_path = os.path.join(os.path.dirname(__file__), "icon_collapse.png")
        icon_collapse = QIcon(icon_collapse_path) if os.path.exists(icon_collapse_path) else QIcon()
        self.collapse_action = QAction(
            icon_collapse,
            "Collapse All Sub-layers",
            self.iface.mainWindow(),
        )
        self.collapse_action.setToolTip("Collapse all sub-layers and sub-groups")
        self.collapse_action.triggered.connect(self.collapse_all_sub_items)
        self.toolbar.addAction(self.collapse_action)

        # Add Sort Group button
        icon_sort_path = os.path.join(os.path.dirname(__file__), "icon_sort.png")
        icon_sort = QIcon(icon_sort_path) if os.path.exists(icon_sort_path) else QIcon()
        self.sort_group_action = QAction(
            icon_sort,
            "Sort Group",
            self.iface.mainWindow(),
        )
        self.sort_group_action.setToolTip("Sort layers and sub-groups in selected group alphabetically")
        self.sort_group_action.triggered.connect(self.sort_group_layers)
        self.toolbar.addAction(self.sort_group_action)

        # Add Theme Manager button
        self.theme_manager_action = QAction(
            QgsApplication.getThemeIcon("/mActionShowAllLayers.svg"),
            "Layer Theme Manager",
            self.iface.mainWindow(),
        )
        self.theme_manager_action.setToolTip("Manage map themes: list, remove, edit, and wildcard replace theme names")
        self.theme_manager_action.triggered.connect(self.run_theme_manager)
        self.toolbar.addAction(self.theme_manager_action)

        # Add Batch Theme Export button
        self.batch_theme_export_action = QAction(
            QgsApplication.getThemeIcon("/mActionSaveAsPDF.svg"),
            "Batch Theme Export",
            self.iface.mainWindow(),
        )
        self.batch_theme_export_action.setToolTip("Batch export layout by map themes to PDF")
        self.batch_theme_export_action.triggered.connect(self.run_batch_theme_export)
        self.toolbar.addAction(self.batch_theme_export_action)

        # Auto-update active layer name variable and label button state
        self.iface.currentLayerChanged.connect(self._update_active_layer_name)
        self.iface.currentLayerChanged.connect(self._update_label_button_state)
        self._update_active_layer_name(self.iface.activeLayer())
        self._update_label_button_state(self.iface.activeLayer())

        # Auto-apply style when layers are added
        QgsProject.instance().layersAdded.connect(self._on_layers_added)

        # Register custom expressions
        try:
            pass
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Failed to load expressions: {e}", "TUFLOW Tools", Qgis.Warning
            )

    def _update_active_layer_name(self, layer):
        """Update project variable 'active_layer_name' when active layer changes."""
        name = layer.name() if layer else ""
        project = QgsProject.instance()

        # Check if value actually changed to avoid marking project as dirty unnecessarily
        scope = QgsExpressionContextUtils.projectScope(project)
        current_value = scope.variable("active_layer_name")

        if current_value != name:
            was_dirty = project.isDirty()
            QgsExpressionContextUtils.setProjectVariable(
                project, "active_layer_name", name
            )

            # If the project was clean before, keep it clean (treat this as a transient runtime variable)
            if not was_dirty:
                project.setDirty(False)

    def _update_label_button_state(self, layer):
        """Update toggle labels button state based on current layer's label visibility."""
        if not self.toggle_labels_action:
            return

        if not layer or layer.type() != Qgis.LayerType.VectorLayer:
            self.toggle_labels_action.setEnabled(False)
            self.toggle_labels_action.setChecked(False)
            return

        self.toggle_labels_action.setEnabled(True)
        self.toggle_labels_action.setChecked(layer.labelsEnabled())

    def run_theme_manager(self):
        from .algs.theme_manager import show_theme_manager_dialog
        show_theme_manager_dialog()

    def run_batch_theme_export(self):
        from .algs.batch_theme_export import show_batch_theme_export_dialog
        show_batch_theme_export_dialog()

    def apply_style_to_selected(self):
        """Apply style to all currently selected layers."""
        layers = self.iface.layerTreeView().selectedLayers()
        if not layers:
            QgsMessageLog.logMessage("No layers selected", "TUFLOW Tools", Qgis.Warning)
            return

        for layer in layers:
            StyleManager.apply_style_to_layer(layer)

    def run_select_layers_by_search(self):
        from .algs.select_layers_by_search import show_select_layers_dialog
        show_select_layers_dialog(self.iface)

    def run_batch_rename(self):
        processing.execAlgorithmDialog("tuflow_tools:rename_layers_by_pattern")

    def run_restore_layer_name(self):
        layers = self.iface.layerTreeView().selectedLayers()
        if not layers:
            self.iface.messageBar().pushMessage(
                "Restore Layer Name", "No layers selected", Qgis.Warning, 4
            )
            return

        res = processing.run("tuflow_tools:restore_layer_name", {"LAYERS": layers})
        count = res.get("RESTORED_COUNT", 0)

        if count > 0:
            self.iface.messageBar().pushMessage(
                "Restore Layer Name",
                f"Successfully restored {count} layer name(s).",
                Qgis.Success,
                4,
            )
        else:
            self.iface.messageBar().pushMessage(
                "Restore Layer Name", "No layer names were changed.", Qgis.Info, 4
            )

    def toggle_selected_layer_labels(self):
        """Toggle labels for the currently active vector layer."""
        layer = self.iface.activeLayer()
        if not layer:
            self.iface.messageBar().pushMessage(
                "Toggle Labels", "No layer selected", Qgis.Warning, 4
            )
            return

        if layer.type() != Qgis.LayerType.VectorLayer:
            self.iface.messageBar().pushMessage(
                "Toggle Labels",
                "Selected layer is not a vector layer",
                Qgis.Warning,
                4,
            )
            return

        enabled = not layer.labelsEnabled()
        layer.setLabelsEnabled(enabled)
        layer.triggerRepaint()

        # Update button state to reflect new label visibility
        self._update_label_button_state(layer)

    def duplicate_vector_layer(self):
        """Duplicate the active vector layer's data source and load it."""
        from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox
        from qgis.core import QgsVectorFileWriter, QgsProject, QgsVectorLayer, Qgis
        import re

        def get_default_name(current_name):
            match = re.search(r'(?<!\d)(\d{3})([^0-9]*)$', current_name)
            if match:
                num_str = match.group(1)
                suffix = match.group(2)
                prefix = current_name[:match.start(1)]
                next_num = int(num_str) + 1
                return f"{prefix}{next_num:0{len(num_str)}d}{suffix}"
            return f"{current_name}_copy"

        layer = self.iface.activeLayer()
        if not layer or layer.type() != Qgis.LayerType.VectorLayer:
            self.iface.messageBar().pushMessage(
                "Duplicate Layer", "Please select a vector layer.", Qgis.Warning, 4
            )
            return

        provider = layer.dataProvider()
        if not provider or provider.name() != "ogr":
            self.iface.messageBar().pushMessage(
                "Duplicate Layer",
                "Only OGR vector layers are supported.",
                Qgis.Warning,
                4,
            )
            return

        # Extract source path (handle QGIS OGR source string which may contain pipe)
        source_parts = layer.source().split("|")
        file_path = source_parts[0].strip()

        if not os.path.exists(file_path):
            self.iface.messageBar().pushMessage(
                "Duplicate Layer",
                "Source file does not exist or is not a local file.",
                Qgis.Warning,
                4,
            )
            return

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".gpkg":
            # Prompt for new table name
            new_name, ok = QInputDialog.getText(
                self.iface.mainWindow(),
                "Duplicate Layer Data",
                "Enter new table name for GeoPackage:",
                text=get_default_name(layer.name()),
            )
            if not ok or not new_name.strip():
                return

            new_name = new_name.strip()

            # Check if table already exists in GPKG
            check_layer = QgsVectorLayer(f"{file_path}|layername={new_name}", "check", "ogr")
            if check_layer.isValid():
                ans = QMessageBox.question(
                    self.iface.mainWindow(),
                    "Table Exists",
                    f"The table '{new_name}' already exists in the GeoPackage. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if ans == QMessageBox.No:
                    return

            # To avoid slow performance on NAS due to SQLite concurrent read/write locks,
            # we first export to a temporary local file, then append it to the target gpkg.
            import tempfile

            temp_dir = tempfile.mkdtemp()
            temp_file = os.path.join(temp_dir, "temp_duplicate.gpkg")

            temp_options = QgsVectorFileWriter.SaveVectorOptions()
            temp_options.driverName = "GPKG"
            temp_options.layerName = new_name
            context = QgsProject.instance().transformContext()

            res_temp = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, temp_file, context, temp_options
            )

            if res_temp[0] == QgsVectorFileWriter.NoError:
                temp_layer = QgsVectorLayer(
                    f"{temp_file}|layername={new_name}", "temp", "ogr"
                )

                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                options.layerName = new_name
                options.actionOnExistingFile = (
                    QgsVectorFileWriter.CreateOrOverwriteLayer
                )

                res = QgsVectorFileWriter.writeAsVectorFormatV3(
                    temp_layer, file_path, context, options
                )

                # Clean up temp layer
                temp_layer = None
                try:
                    import shutil

                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
            else:
                res = res_temp

            if res[0] == QgsVectorFileWriter.NoError:
                uri = f"{file_path}|layername={new_name}"
                new_layer = QgsVectorLayer(uri, new_name, "ogr")
                if new_layer.isValid():
                    QgsProject.instance().addMapLayer(new_layer)
                    self.iface.messageBar().pushMessage(
                        "Duplicate Layer",
                        f"Successfully duplicated to table '{new_name}'.",
                        Qgis.Success,
                        4,
                    )
                else:
                    self.iface.messageBar().pushMessage(
                        "Duplicate Layer",
                        "Failed to load the new layer.",
                        Qgis.Critical,
                        4,
                    )
            else:
                self.iface.messageBar().pushMessage(
                    "Duplicate Layer",
                    f"Error saving layer: {res[1] if len(res) > 1 else 'Unknown'}",
                    Qgis.Critical,
                    4,
                )

        elif ext == ".shp":
            # Prompt for new file name
            base_dir = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)
            name_only, current_ext = os.path.splitext(base_name)

            new_name, ok = QInputDialog.getText(
                self.iface.mainWindow(),
                "Duplicate Layer Data",
                f"Enter new file name (without {current_ext} extension):",
                text=get_default_name(name_only),
            )

            if not ok or not new_name.strip():
                return

            new_name = new_name.strip()
            new_file_path = os.path.join(base_dir, f"{new_name}{current_ext}")

            if os.path.exists(new_file_path):
                ans = QMessageBox.question(
                    self.iface.mainWindow(),
                    "File Exists",
                    f"The file {new_name}{current_ext} already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if ans == QMessageBox.No:
                    return

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "ESRI Shapefile"
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            if hasattr(layer.dataProvider(), "encoding"):
                options.fileEncoding = layer.dataProvider().encoding()

            context = QgsProject.instance().transformContext()
            res = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, new_file_path, context, options
            )

            if res[0] == QgsVectorFileWriter.NoError:
                new_layer = QgsVectorLayer(new_file_path, new_name, "ogr")
                if new_layer.isValid():
                    QgsProject.instance().addMapLayer(new_layer)
                    self.iface.messageBar().pushMessage(
                        "Duplicate Layer",
                        f"Successfully duplicated to '{new_name}'.",
                        Qgis.Success,
                        4,
                    )
                else:
                    self.iface.messageBar().pushMessage(
                        "Duplicate Layer",
                        "Failed to load the new layer.",
                        Qgis.Critical,
                        4,
                    )
            else:
                self.iface.messageBar().pushMessage(
                    "Duplicate Layer",
                    f"Error saving layer: {res[1] if len(res) > 1 else 'Unknown'}",
                    Qgis.Critical,
                    4,
                )

        else:
            self.iface.messageBar().pushMessage(
                "Duplicate Layer",
                f"Unsupported file format '{ext}'. Only GPKG and SHP are supported.",
                Qgis.Warning,
                4,
            )

    def collapse_all_sub_items(self):
        """Collapse all sub-layers and sub-groups of the selected item in the Layers panel."""
        from qgis.core import QgsLayerTreeGroup

        layer_tree_view = self.iface.layerTreeView()
        current_node = layer_tree_view.currentNode()

        if not current_node:
            self.iface.messageBar().pushMessage(
                "Collapse All Sub-layers",
                "No item selected in Layers panel",
                Qgis.Info,
                2,
            )
            return

        if not isinstance(current_node, QgsLayerTreeGroup):
            self.iface.messageBar().pushMessage(
                "Collapse All Sub-layers",
                "Selected item is not a group",
                Qgis.Warning,
                2,
            )
            return

        # Collapse all children
        count = self._collapse_children(current_node, layer_tree_view)

        self.iface.messageBar().pushMessage(
            "Collapse All Sub-layers",
            f"Collapsed {count} sub-group(s)",
            Qgis.Success,
            2,
        )

    def _collapse_children(self, node, layer_tree_view):
        """Recursively collapse all children and return count of collapsed items."""
        from qgis.core import QgsLayerTreeGroup

        count = 0
        if isinstance(node, QgsLayerTreeGroup):
            for child in node.children():
                # Try to collapse any child (group or layer)
                try:
                    model_index = layer_tree_view.node2index(child)
                    if model_index.isValid():
                        layer_tree_view.collapse(model_index)
                        count += 1
                except Exception:
                    pass

                # Recursively collapse nested children
                if isinstance(child, QgsLayerTreeGroup):
                    count += self._collapse_children(child, layer_tree_view)

        return count

    def sort_group_layers(self):
        """Sort layers in the selected group alphabetically by name."""
        from qgis.core import QgsLayerTreeGroup

        layer_tree_view = self.iface.layerTreeView()
        current_node = layer_tree_view.currentNode()

        if not current_node:
            self.iface.messageBar().pushMessage(
                "Sort Group",
                "No item selected in Layers panel",
                Qgis.Info,
                2,
            )
            return

        if not isinstance(current_node, QgsLayerTreeGroup):
            self.iface.messageBar().pushMessage(
                "Sort Group",
                "Selected item is not a group",
                Qgis.Warning,
                2,
            )
            return

        children = current_node.children()
        if not children:
            return

        sorted_children = sorted(children, key=lambda n: n.name().lower())

        if children == sorted_children:
            return  # Already sorted

        # To prevent QGIS from auto-deleting map layers from the project when they leave the layer tree,
        # we must insert the clones FIRST before removing the original nodes.
        clones = [c.clone() for c in sorted_children]
        
        # Append the sorted clones to the end of the group
        for c in clones:
            current_node.addChildNode(c)
            
        # Safely remove the original unsorted nodes from the beginning
        # Map layers are preserved because the clones now hold tree references
        for child in children:
            current_node.removeChildNode(child)

        self.iface.messageBar().pushMessage(
            "Sort Group",
            f"Sorted {len(clones)} items alphabetically.",
            Qgis.Success,
            2,
        )

    def _on_layers_added(self, layers):
        """Automatically apply style when layers are added to the project."""
        if not PluginSettings.get_auto_apply_style():
            return

        for layer in layers:
            # Only apply style once per layer to prevent overriding manual style 
            # changes when the project is loaded or layer is refreshed.
            if layer.customProperty("tuflow_auto_style_applied"):
                continue

            StyleManager.apply_style_to_layer(layer)
            layer.setCustomProperty("tuflow_auto_style_applied", True)

    def unload(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._update_active_layer_name)
            QgsProject.instance().layersAdded.disconnect(self._on_layers_added)
        except Exception:
            pass

        # Unregister custom expressions
        try:
            QgsExpression.unregisterFunction("visible_rasters_in_group")
        except Exception:
            pass

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

        if self.toolbar:
            del self.toolbar
            self.toolbar = None
