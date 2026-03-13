# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsFeature,
    QgsGeometry
)

class AppendFeaturesAlgorithm(QgsProcessingAlgorithm):
    """
    Appends features from a source vector layer to a target vector layer.
    Automatically creates missing fields in the target layer and preserves all attribute values.
    """
    PARAM_INPUT = 'INPUT'
    PARAM_TARGET = 'TARGET'
    
    OUTPUT_COUNT = 'APPENDED_COUNT'
    OUTPUT_NEW_FIELDS = 'ADDED_FIELDS_COUNT'
    OUTPUT_LOG = 'APPEND_LOG'

    def tr(self, message):
        return QCoreApplication.translate('AppendFeatures', message)

    def createInstance(self):
        return AppendFeaturesAlgorithm()

    def name(self):
        return 'append_features'

    def displayName(self):
        return self.tr('Append Features')

    def group(self):
        return self.tr('General Tools')

    def groupId(self):
        return 'general_tools'

    def shortHelpString(self):
        return self.tr(
            "Copies all features from an input source layer and appends them to a target vector layer. "
            "It will automatically add any fields that exist in the source layer but are missing from the target layer, "
            "and all attribute values will be retained."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PARAM_INPUT,
                self.tr('Input layer (source features)'),
                [QgsProcessing.TypeVectorAnyGeometry]
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAM_TARGET,
                self.tr('Target layer (to append into)'),
                [QgsProcessing.TypeVectorAnyGeometry],
                defaultValue=None
            )
        )

        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_COUNT, self.tr('Appended features count')))
        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_NEW_FIELDS, self.tr('New fields added to target')))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_LOG, self.tr('Append log')))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.PARAM_INPUT, context)
        if source is None:
            feedback.reportError("Invalid input source layer.", fatalError=True)
            return {}

        target_layer = self.parameterAsVectorLayer(parameters, self.PARAM_TARGET, context)
        if target_layer is None:
            feedback.reportError("Invalid target layer.", fatalError=True)
            return {}

        if not target_layer.isEditable() and not target_layer.dataProvider().capabilities() & target_layer.dataProvider().AddFeatures:
            feedback.reportError("Target layer does not support adding features or is not editable.", fatalError=True)
            return {}

        dp_target = target_layer.dataProvider()
        
        source_fields = source.fields()
        target_fields = target_layer.fields()

        # 1. Identify and add missing fields to the target layer
        missing_fields = []
        added_field_names = []
        for field in source_fields:
            idx = target_fields.indexOf(field.name())
            if idx == -1:
                missing_fields.append(field)
                added_field_names.append(field.name())

        if missing_fields:
            # We have to start an edit command if the layer is in edit mode otherwise we use dataprovider directly
            is_editing = target_layer.isEditable()
            if is_editing:
                target_layer.addAttributes(missing_fields)
                target_layer.updateFields()
            else:
                if dp_target.capabilities() & dp_target.AddAttributes:
                    dp_target.addAttributes(missing_fields)
                    target_layer.updateFields()
                else:
                    feedback.reportError("Target layer does not support adding missing attributes.", fatalError=True)
                    return {}
            
            # refresh target_fields since it changed
            target_fields = target_layer.fields()

        # 2. Build mapping between source field indices and target field indices
        field_mapping = {}  # source_idx: target_idx
        for s_idx, field in enumerate(source_fields):
            t_idx = target_fields.indexOf(field.name())
            if t_idx != -1:
                field_mapping[s_idx] = t_idx

        # 3. Read source features and append
        features_to_add = []
        total_features = source.featureCount()
        step = 100.0 / total_features if total_features > 0 else 1

        is_target_editing = target_layer.isEditable()
        appended_count = 0

        for current, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break

            new_feat = QgsFeature(target_fields)
            
            # Geometry
            if feat.hasGeometry():
                new_feat.setGeometry(QgsGeometry(feat.geometry()))

            # Attributes
            for s_idx, t_idx in field_mapping.items():
                val = feat.attribute(s_idx)
                new_feat.setAttribute(t_idx, val)

            features_to_add.append(new_feat)
            appended_count += 1
            
            feedback.setProgress(int(current * step))

        if features_to_add:
            if is_target_editing:
                target_layer.addFeatures(features_to_add)
            else:
                dp_target.addFeatures(features_to_add)
            
            target_layer.triggerRepaint()

        log_lines = []
        log_lines.append(f"Successfully appended {appended_count} features.")
        if added_field_names:
            log_lines.append(f"Added {len(added_field_names)} missing fields: {', '.join(added_field_names)}")
        else:
            log_lines.append("No new fields needed to be added.")

        log_text = '\n'.join(log_lines)
        feedback.pushInfo(log_text)

        return {
            self.OUTPUT_COUNT: appended_count,
            self.OUTPUT_NEW_FIELDS: len(added_field_names),
            self.OUTPUT_LOG: log_text
        }
