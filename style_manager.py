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
        return [
            (pattern, qml_file, layer_type)
            for pattern, qml_file, layer_type in mappings
        ]

    @staticmethod
    def apply_style_to_layer(layer):
        """Apply style to a layer based on pattern matching."""
        if not layer:
            return False

        layer_name = layer.name()
        is_vector = isinstance(layer, QgsVectorLayer)
        is_raster = isinstance(layer, QgsRasterLayer)
        style_path = PluginSettings.get_style_path()

        if not style_path:
            return False

        if not os.path.isdir(style_path):
            QgsMessageLog.logMessage(
                f"Style path not found: {style_path}. Use Plugin Settings to set it.",
                "TUFLOW Tools",
                Qgis.Warning,
            )
            return False

        # Find matching pattern
        for pattern_str, qml_file_str, layer_type in StyleManager.get_style_mappings():
            patterns = [p.strip() for p in pattern_str.split(",")]

            # Check if any pattern matches the layer name (case-insensitive)
            if any(
                fnmatch.fnmatch(layer_name.upper(), p.upper()) for p in patterns if p
            ):
                # Check layer type compatibility
                if layer_type == "vector" and not is_vector:
                    continue
                if layer_type == "raster" and not is_raster:
                    continue

                qml_files = [q.strip() for q in qml_file_str.split(",")]
                style_applied = False

                for qml_file in qml_files:
                    if not qml_file:
                        continue

                    qml_path = os.path.join(style_path, qml_file)

                    if not os.path.exists(qml_path):
                        continue

                    # Load style
                    msg, success = layer.loadNamedStyle(qml_path)
                    if success:
                        layer.triggerRepaint()
                        QgsMessageLog.logMessage(
                            f"Applied style '{qml_file}' to layer '{layer_name}'",
                            "TUFLOW Tools",
                            Qgis.Info,
                        )
                        style_applied = True
                        return True
                    else:
                        QgsMessageLog.logMessage(
                            f"Failed to apply style '{qml_file}': {msg}",
                            "TUFLOW Tools",
                            Qgis.Warning,
                        )

                if not style_applied:
                    QgsMessageLog.logMessage(
                        f"Style files not found or failed to load for layer '{layer_name}': {qml_file_str}",
                        "TUFLOW Tools",
                        Qgis.Warning,
                    )
                    return False

        return False
