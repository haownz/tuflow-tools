# -*- coding: utf-8 -*-
import os
from qgis.PyQt.QtCore import QCoreApplication, QVariant, QSettings
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsProcessingException,
    QgsExpression,
    QgsFeatureRequest,
    QgsWkbTypes
)

class ExtractFieldAcrossLayersAlgorithm(QgsProcessingAlgorithm):
    """
    Extract field value across multiple vector layers by index field value.
    """

    INPUT_LAYERS = 'INPUT_LAYERS'
    INDEX_FIELD = 'INDEX_FIELD'
    INDEX_VALUES = 'INDEX_VALUES'
    EXTRACT_FIELDS = 'EXTRACT_FIELDS'
    OUTPUT = 'OUTPUT'

    def tr(self, message):
        return QCoreApplication.translate("ExtractFieldAcrossLayers", message)

    def createInstance(self):
        return ExtractFieldAcrossLayersAlgorithm()

    def name(self):
        return "extract_field_across_layers"

    def displayName(self):
        return self.tr("Extract Field Across Layers")

    def group(self):
        return self.tr("3 - Utilities")

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return self.tr(
            "Extracts specific fields' values across multiple vector layers based on multiple target index values. "
            "For example, extract 'Q' from multiple layers when 'ID' = '01' and '02'. "
            "The output table contains one row per layer, with target values as column headers."
        )

    def initAlgorithm(self, config=None):
        settings = QSettings()
        default_index_field = settings.value("tuflow_tools/extract_fields/index_field", "ID", type=str)
        default_index_values = settings.value("tuflow_tools/extract_fields/index_values", "01, 02", type=str)
        default_extract_fields = settings.value("tuflow_tools/extract_fields/extract_fields", "Q", type=str)

        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT_LAYERS,
                self.tr("Input Vector Layers"),
                layerType=QgsProcessing.TypeVectorAnyGeometry
            )
        )
        
        self.addParameter(
            QgsProcessingParameterString(
                self.INDEX_FIELD,
                self.tr("Index Field Name"),
                defaultValue=default_index_field
            )
        )
        
        self.addParameter(
            QgsProcessingParameterString(
                self.INDEX_VALUES,
                self.tr("Target Index Values (comma separated)"),
                defaultValue=default_index_values
            )
        )
        
        self.addParameter(
            QgsProcessingParameterString(
                self.EXTRACT_FIELDS,
                self.tr("Fields to Extract (comma separated)"),
                defaultValue=default_extract_fields
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Extracted Table")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        input_layers = self.parameterAsLayerList(parameters, self.INPUT_LAYERS, context)
        index_field = self.parameterAsString(parameters, self.INDEX_FIELD, context).strip()
        index_values_str = self.parameterAsString(parameters, self.INDEX_VALUES, context)
        extract_field_str = self.parameterAsString(parameters, self.EXTRACT_FIELDS, context)

        # Save to settings
        settings = QSettings()
        settings.setValue("tuflow_tools/extract_fields/index_field", index_field)
        settings.setValue("tuflow_tools/extract_fields/index_values", index_values_str)
        settings.setValue("tuflow_tools/extract_fields/extract_fields", extract_field_str)

        index_values = [v.strip() for v in index_values_str.split(',') if v.strip()]
        extract_fields = [f.strip() for f in extract_field_str.split(',') if f.strip()]

        if not input_layers:
            raise QgsProcessingException("No input layers selected.")
        if not index_field:
            raise QgsProcessingException("No index field provided.")
        if not index_values:
            raise QgsProcessingException("No target index values provided.")
        if not extract_fields:
            raise QgsProcessingException("No extract fields provided.")

        fields = QgsFields()
        fields.append(QgsField("Layer_Name", QVariant.String))
        
        for val in index_values:
            for field in extract_fields:
                if len(extract_fields) == 1:
                    col_name = str(val)
                else:
                    col_name = f"{val}_{field}"
                fields.append(QgsField(col_name, QVariant.String))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.NoGeometry,
            context.project().crs()
        )
        
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        total_layers = len(input_layers)
        step = 100.0 / total_layers if total_layers > 0 else 1

        total_extracted = 0

        for i, layer in enumerate(input_layers):
            if feedback.isCanceled():
                break
                
            layer_name = layer.name()
            
            out_feat = QgsFeature(fields)
            out_feat.setAttribute("Layer_Name", layer_name)
            
            # Initialize with empty strings
            for val in index_values:
                for field in extract_fields:
                    col_name = str(val) if len(extract_fields) == 1 else f"{val}_{field}"
                    out_feat.setAttribute(col_name, "")
            
            # Check if index field exist
            index_field_idx = layer.fields().lookupField(index_field)
            if index_field_idx == -1:
                feedback.pushInfo(f"Layer '{layer_name}' does not contain index field '{index_field}'. Adding empty row.")
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
                feedback.setProgress(int((i + 1) * step))
                continue
                
            actual_index_field = layer.fields().at(index_field_idx).name()
                
            # Filter available extract fields
            field_map = {}
            missing_fields = []
            for field in extract_fields:
                field_idx = layer.fields().lookupField(field)
                if field_idx != -1:
                    field_map[field] = layer.fields().at(field_idx).name()
                else:
                    missing_fields.append(field)
                    
            if missing_fields:
                feedback.pushInfo(f"Layer '{layer_name}' is missing fields: {', '.join(missing_fields)}.")
                
            if not field_map:
                feedback.pushInfo(f"Layer '{layer_name}' does not contain any of the requested extract fields. Adding empty row.")
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
                feedback.setProgress(int((i + 1) * step))
                continue
                
            # Filter expression
            vals_formatted = ", ".join([f"'{v.replace(chr(39), chr(39)+chr(39))}'" for v in index_values])
            expr_str = f"\"{actual_index_field}\" IN ({vals_formatted})"
            expr = QgsExpression(expr_str)
            
            if expr.hasParserError():
                feedback.reportError(f"Expression error on layer '{layer_name}': {expr.parserErrorString()}")
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
                feedback.setProgress(int((i + 1) * step))
                continue

            request = QgsFeatureRequest()
            request.setFilterExpression(expr_str)
            
            subset_fields = [actual_index_field] + list(field_map.values())
            request.setSubsetOfAttributes(subset_fields, layer.fields())
            
            count = 0
            for feature in layer.getFeatures(request):
                val_raw = feature.attribute(actual_index_field)
                if val_raw is None:
                    continue
                val_str = str(val_raw)
                
                # Check if this matched value is in our target values (case-insensitive for robustness)
                matched_val = None
                for v in index_values:
                    if str(v) == val_str:
                        matched_val = v
                        break
                
                # fallback for string representations
                if not matched_val:
                    for v in index_values:
                        if str(v).lower() == val_str.lower():
                            matched_val = v
                            break
                            
                if matched_val:
                    for field in extract_fields:
                        if field in field_map:
                            val = feature.attribute(field_map[field])
                            col_name = str(matched_val) if len(extract_fields) == 1 else f"{matched_val}_{field}"
                            out_feat.setAttribute(col_name, str(val) if val is not None else "")
                            
                count += 1
                total_extracted += 1
            
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
            
            if count == 0:
                feedback.pushInfo(f"No features found in '{layer_name}' matching condition.")
            else:
                feedback.pushInfo(f"Extracted {count} feature(s) from '{layer_name}'")

            feedback.setProgress(int((i + 1) * step))

        feedback.pushInfo(f"Total extracted features (across all layers): {total_extracted}")
        
        return {self.OUTPUT: dest_id}