# -*- coding: utf-8 -*-
import os
import re
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsMapLayer,
    QgsProject
)

class RestoreLayerNameAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm to restore layer names based on their source.
    For GeoPackages, it restores the name to the imported layer's table name.
    For other files, it restores to the physical file name without extension.
    """
    PARAM_LAYERS = 'LAYERS'
    OUTPUT_COUNT = 'RESTORED_COUNT'
    OUTPUT_LOG = 'RESTORE_LOG'

    def tr(self, message):
        return QCoreApplication.translate('RestoreLayerName', message)

    def createInstance(self):
        return RestoreLayerNameAlgorithm()

    def name(self):
        return 'restore_layer_name'

    def displayName(self):
        return self.tr('Restore Layer Name')

    def group(self):
        return self.tr('General Tools')

    def groupId(self):
        return 'general_tools'

    def shortHelpString(self):
        return self.tr(
            "Restores the selected layer names to their original source names. "
            "For GeoPackage layers, it restores to the actual table/layer name within the database. "
            "For other file formats, it uses the base filename without extension."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.PARAM_LAYERS,
                self.tr('Layers to restore name'),
                layerType=QgsProcessing.TypeMapLayer,
                defaultValue=None
            )
        )

        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_COUNT, self.tr('Restored layers count')))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_LOG, self.tr('Restore log')))

    def _derive_layer_name(self, layer):
        dp = layer.dataProvider()
        if not dp:
            return None
        uri = dp.dataSourceUri() or ''
        
        # 1. Try extracting exact layer name from GeoPackage or other multi-layer formats
        # Format often resembles: path/to/file.gpkg|layername=my_layer_abc
        match = re.search(r"layername=([^|]+)", uri, re.IGNORECASE)
        if match:
            # Drop any trailing things attached to the layer name option
            return match.group(1).split(' ')[0]

        # 2. Extract base filename (fallback)
        uri_main = uri.split('|', 1)[0]
        uri_main = uri_main.split('?', 1)[0]
        base = os.path.basename(uri_main)
        name_no_ext, _ = os.path.splitext(base)

        return name_no_ext or None

    def processAlgorithm(self, parameters, context, feedback):
        layers_param = self.parameterAsLayerList(parameters, self.PARAM_LAYERS, context)
        
        target_layers = []
        if layers_param:
            target_layers = [lyr for lyr in layers_param if isinstance(lyr, QgsMapLayer)]
        else:
            # Fallback to current selection in QGIS if no layer provided via parameter UI
            try:
                from qgis.utils import iface
                if iface is not None and iface.layerTreeView() is not None:
                    target_layers = iface.layerTreeView().selectedLayers()
            except Exception:
                target_layers = []

        if not target_layers:
            feedback.pushInfo(self.tr("No valid layers provided or selected to restore name."))
            return {
                self.OUTPUT_COUNT: 0,
                self.OUTPUT_LOG: 'No layers processed.'
            }

        restored_count = 0
        log_lines = []

        for layer in target_layers:
            old_name = layer.name()
            new_name = self._derive_layer_name(layer)

            if new_name and new_name != old_name:
                layer.setName(new_name)
                log_lines.append(f'"{old_name}" -> "{new_name}"')
                restored_count += 1
            elif not new_name:
                log_lines.append(f'"{old_name}" -> (Could not derive source name)')
            else:
                log_lines.append(f'"{old_name}" -> (Unchanged, already matches source)')

        log_text = '\n'.join(log_lines)
        feedback.pushInfo('Restore Results:\n' + log_text)

        return {
            self.OUTPUT_COUNT: restored_count,
            self.OUTPUT_LOG: log_text
        }
