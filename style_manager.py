# -*- coding: utf-8 -*-
import os
import fnmatch
from qgis.core import QgsMessageLog, Qgis, QgsVectorLayer, QgsRasterLayer
from .settings import PluginSettings

class StyleManager:
    """Manages layer style application based on wildcard pattern matching."""
    
    @staticmethod
    def get_style_mappings():
        """Get style mappings from settings."""
        mappings = PluginSettings.get_style_mappings()
        return [(pattern, qml_file, layer_type) for pattern, qml_file, layer_type in mappings]
    
    @staticmethod
    def apply_style_to_layer(layer):
        """Apply style to a layer based on pattern matching."""
        if not layer:
            return False
        
        layer_name = layer.name()
        is_vector = isinstance(layer, QgsVectorLayer)
        is_raster = isinstance(layer, QgsRasterLayer)
        style_path = PluginSettings.get_style_path()
        
        if not style_path or not os.path.isdir(style_path):
            QgsMessageLog.logMessage(
                "Style path not configured. Use Plugin Settings to set it.",
                "TUFLOW Tools",
                Qgis.Warning
            )
            return False
        
        # Find matching pattern
        for pattern, qml_file, layer_type in StyleManager.get_style_mappings():
            if fnmatch.fnmatch(layer_name, pattern):
                # Check layer type compatibility
                if layer_type == "vector" and not is_vector:
                    continue
                if layer_type == "raster" and not is_raster:
                    continue
                
                qml_path = os.path.join(style_path, qml_file)
                
                if not os.path.exists(qml_path):
                    QgsMessageLog.logMessage(
                        f"Style file not found: {qml_path}",
                        "TUFLOW Tools",
                        Qgis.Warning
                    )
                    return False
                
                # Load style
                msg, success = layer.loadNamedStyle(qml_path)
                if success:
                    layer.triggerRepaint()
                    QgsMessageLog.logMessage(
                        f"Applied style '{qml_file}' to layer '{layer_name}'",
                        "TUFLOW Tools",
                        Qgis.Info
                    )
                    return True
                else:
                    QgsMessageLog.logMessage(
                        f"Failed to apply style: {msg}",
                        "TUFLOW Tools",
                        Qgis.Warning
                    )
                    return False
        
        QgsMessageLog.logMessage(
            f"No matching style pattern for layer '{layer_name}'",
            "TUFLOW Tools",
            Qgis.Info
        )
        return False
