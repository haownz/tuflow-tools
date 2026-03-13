# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsMessageLog, Qgis, QgsExpressionContextUtils, QgsProject, QgsExpression
from .provider import TuflowProcessingProvider
from .style_manager import StyleManager
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

        # Add Batch Rename button
        self.rename_action = QAction(QgsApplication.getThemeIcon('/mActionReplace.svg'), "Batch Rename", self.iface.mainWindow())
        self.rename_action.setToolTip("Batch rename selected layers")
        self.rename_action.triggered.connect(self.run_batch_rename)
        self.toolbar.addAction(self.rename_action)

        # Add Restore Layer Name button
        icon_layer_name_path = os.path.join(os.path.dirname(__file__), "icon_layer_name.png")
        icon_layer_name = QIcon(icon_layer_name_path) if os.path.exists(icon_layer_name_path) else QIcon()
        self.restore_layer_name_action = QAction(icon_layer_name, "Restore Layer Name", self.iface.mainWindow())
        self.restore_layer_name_action.setToolTip("Restore layer name from source")
        self.restore_layer_name_action.triggered.connect(self.run_restore_layer_name)
        self.toolbar.addAction(self.restore_layer_name_action)

        # Auto-update active layer name variable
        self.iface.currentLayerChanged.connect(self._update_active_layer_name)
        self._update_active_layer_name(self.iface.activeLayer())

        # Register custom expressions
        try:
            from . import expressions
        except Exception as e:
            QgsMessageLog.logMessage(f"Failed to load expressions: {e}", "TUFLOW Tools", Qgis.Warning)

    def _update_active_layer_name(self, layer):
        """Update project variable 'active_layer_name' when active layer changes."""
        name = layer.name() if layer else ''
        project = QgsProject.instance()
        
        # Check if value actually changed to avoid marking project as dirty unnecessarily
        scope = QgsExpressionContextUtils.projectScope(project)
        current_value = scope.variable('active_layer_name')
        
        if current_value != name:
            was_dirty = project.isDirty()
            QgsExpressionContextUtils.setProjectVariable(project, 'active_layer_name', name)
            
            # If the project was clean before, keep it clean (treat this as a transient runtime variable)
            if not was_dirty:
                project.setDirty(False)

    def apply_style_to_selected(self):
        """Apply style to all currently selected layers."""
        layers = self.iface.layerTreeView().selectedLayers()
        if not layers:
            QgsMessageLog.logMessage(
                "No layers selected",
                "TUFLOW Tools",
                Qgis.Warning
            )
            return
        
        for layer in layers:
            StyleManager.apply_style_to_layer(layer)

    def run_batch_rename(self):
        processing.execAlgorithmDialog("tuflow_tools:rename_layers_by_pattern")

    def run_restore_layer_name(self):
        layers = self.iface.layerTreeView().selectedLayers()
        if not layers:
            self.iface.messageBar().pushMessage("Restore Layer Name", "No layers selected", Qgis.Warning, 4)
            return

        res = processing.run("tuflow_tools:restore_layer_name", {'LAYERS': layers})
        count = res.get('RESTORED_COUNT', 0)
        
        if count > 0:
            self.iface.messageBar().pushMessage("Restore Layer Name", f"Successfully restored {count} layer name(s).", Qgis.Success, 4)
        else:
            self.iface.messageBar().pushMessage("Restore Layer Name", "No layer names were changed.", Qgis.Info, 4)

    def unload(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._update_active_layer_name)
        except Exception:
            pass

        # Unregister custom expressions
        try:
            QgsExpression.unregisterFunction('visible_rasters_in_group')
        except Exception:
            pass

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        
        if self.toolbar:
            del self.toolbar
            self.toolbar = None