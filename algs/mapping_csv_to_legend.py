# -*- coding: utf-8 -*-

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
    QgsProcessingParameterField,
    QgsProcessingException,
    QgsVectorLayer,
    QgsProject,
    QgsSettings,
)

import csv
from qgis.core import QgsSettings
import os


class MappingCSVToLegendAlgorithm(QgsProcessingAlgorithm):
    """
    Maps CSV data to vector layer symbology legend.
    """

    P_LAYER = "VECTOR_LAYER"
    P_CSV_FILE = "CSV_FILE"
    P_SOURCE_INDEX = "SOURCE_INDEX"
    P_TARGET_INDEX = "TARGET_INDEX"
    P_LEGEND_COLUMN = "LEGEND_COLUMN"

    def createInstance(self):
        return MappingCSVToLegendAlgorithm()

    def name(self):
        return "mapping_csv_to_legend"

    def displayName(self):
        return "Mapping CSV to Legend"

    def group(self):
        return "3 - Utilities"

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return (
            "Maps data from a CSV file to vector layer symbology legend.\n\n"
            "Parameters:\n"
            "• Vector Layer: The vector layer to update\n"
            "• CSV File: Path to the CSV file containing mapping data\n"
            "• Source Index: Field from the vector layer (e.g., layer's attribute field)\n"
            "• Target Index: Column name from the CSV (e.g., CSV ID column)\n"
            "• Legend Column: CSV column that contains the display text for legend\n\n"
            "The algorithm matches values from the Source Index field with the Target Index column "
            "in the CSV, then updates the layer's symbology legend labels using the Legend Column values."
        )

    def initAlgorithm(self, config=None):
        # Vector layer parameter
        self.addParameter(
            QgsProcessingParameterVectorLayer(self.P_LAYER, "Vector Layer")
        )

        # CSV file parameter
        self.addParameter(
            QgsProcessingParameterFile(
                self.P_CSV_FILE,
                "CSV File",
                extension="csv",
            )
        )

        # Source index parameter — field from the vector layer
        p_source_index = QgsProcessingParameterField(
            self.P_SOURCE_INDEX,
            "Source Index (Vector Layer Field)",
            parentLayerParameterName=self.P_LAYER,
            type=QgsProcessingParameterField.Any,
            allowMultiple=False,
        )
        self.addParameter(p_source_index)

        # Load recent histories from QgsSettings
        settings = QgsSettings()
        recent_targets = settings.value("TUFLOWTools/MappingCSV/recent_targets", [], type=list)
        recent_legends = settings.value("TUFLOWTools/MappingCSV/recent_legends", [], type=list)

        # Target index parameter — string parameter for CSV column name
        # We'll use a ComboBox to allow selection from history or manual input
        p_target_index = QgsProcessingParameterString(
            self.P_TARGET_INDEX,
            "Target Index (CSV Column)",
            defaultValue=recent_targets[0] if recent_targets else "Landuse_ID",
        )
        p_target_index.setMetadata({
            'widget_wrapper': {
                'use_line_edit': True,
                'combo_box_values': recent_targets
            }
        })
        self.addParameter(p_target_index)

        # Legend column parameter — string parameter for CSV column name
        p_legend = QgsProcessingParameterString(
            self.P_LEGEND_COLUMN,
            "Legend Column (display text)",
            defaultValue=recent_legends[0] if recent_legends else "Landuse_Description",
        )
        p_legend.setMetadata({
            'widget_wrapper': {
                'use_line_edit': True,
                'combo_box_values': recent_legends
            }
        })
        self.addParameter(p_legend)

        # Try to pre-fill layer with active layer
        try:
            from qgis.utils import iface

            active = iface.activeLayer() if iface else None
            if isinstance(active, QgsVectorLayer):
                p_layer = self.parameterDefinition(self.P_LAYER)
                if p_layer:
                    p_layer.setDefaultValue(active.id())
        except Exception:
            pass

    def processAlgorithm(self, parameters, context, feedback):
        """
        Process the CSV mapping and update layer symbology.
        """
        vector_layer = self.parameterAsVectorLayer(parameters, self.P_LAYER, context)
        csv_file = self.parameterAsFile(parameters, self.P_CSV_FILE, context)
        source_index_field = self.parameterAsString(
            parameters, self.P_SOURCE_INDEX, context
        ).strip()
        target_index_column = self.parameterAsString(
            parameters, self.P_TARGET_INDEX, context
        ).strip()
        legend_column = self.parameterAsString(
            parameters, self.P_LEGEND_COLUMN, context
        ).strip()

        if not vector_layer or not vector_layer.isValid():
            raise QgsProcessingException("Invalid vector layer.")

        if not csv_file or not os.path.exists(csv_file):
            raise QgsProcessingException("CSV file not found.")

        if not source_index_field or not target_index_column or not legend_column:
            raise QgsProcessingException(
                "Source index field, target index column, and legend column must be specified."
            )

        feedback.pushInfo(f"Processing layer: {vector_layer.name()}")
        feedback.pushInfo(f"CSV file: {csv_file}")
        feedback.pushInfo(
            f"Source index field: '{source_index_field}', "
            f"Target index column: '{target_index_column}', "
            f"Legend column: '{legend_column}'"
        )

        # Save to history
        settings = QgsSettings()
        
        recent_targets = settings.value("TUFLOWTools/MappingCSV/recent_targets", [], type=list)
        if target_index_column in recent_targets:
            recent_targets.remove(target_index_column)
        recent_targets.insert(0, target_index_column)
        settings.setValue("TUFLOWTools/MappingCSV/recent_targets", recent_targets[:10])
        
        recent_legends = settings.value("TUFLOWTools/MappingCSV/recent_legends", [], type=list)
        if legend_column in recent_legends:
            recent_legends.remove(legend_column)
        recent_legends.insert(0, legend_column)
        settings.setValue("TUFLOWTools/MappingCSV/recent_legends", recent_legends[:10])

        # Read CSV and create mapping
        csv_mapping = {}
        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                # Get fieldnames and strip whitespace
                if not reader.fieldnames:
                    raise QgsProcessingException("CSV file appears to be empty.")

                # Create mapping of stripped names to original names, filtering out empty columns
                fieldnames_stripped = {
                    name.strip(): name
                    for name in reader.fieldnames
                    if name and name.strip()
                }

                if target_index_column not in fieldnames_stripped:
                    raise QgsProcessingException(
                        f"Column '{target_index_column}' not found in CSV. "
                        f"Available columns: {', '.join(fieldnames_stripped.keys())}"
                    )
                if legend_column not in fieldnames_stripped:
                    raise QgsProcessingException(
                        f"Column '{legend_column}' not found in CSV. "
                        f"Available columns: {', '.join(fieldnames_stripped.keys())}"
                    )

                # Get original column names (with original spacing)
                target_col_original = fieldnames_stripped[target_index_column]
                legend_col_original = fieldnames_stripped[legend_column]

                # Reset file pointer for actual reading
                f.seek(0)
                reader = csv.DictReader(f)

                for row in reader:
                    target_val = str(row[target_col_original]).strip()
                    legend_val = str(row[legend_col_original]).strip()
                    if target_val:  # Only add non-empty target index values
                        csv_mapping[target_val] = legend_val

        except csv.Error as e:
            raise QgsProcessingException(f"Error parsing CSV file: {e}")
        except QgsProcessingException:
            raise
        except Exception as e:
            raise QgsProcessingException(f"Error reading CSV: {e}")

        feedback.pushInfo(f"Loaded {len(csv_mapping)} mappings from CSV")

        # Update layer renderer legend
        try:
            renderer = vector_layer.renderer()
            if renderer is None:
                raise QgsProcessingException("Layer has no renderer/symbology defined.")

            # For categorized renderer
            from qgis.core import (
                QgsCategorizedSymbolRenderer,
                QgsRendererCategory,
            )

            if isinstance(renderer, QgsCategorizedSymbolRenderer):
                categories = renderer.categories()
                updated_count = 0

                # Create a new list of updated categories
                updated_categories = []
                for category in categories:
                    category_value = str(category.value()).strip()
                    # Match category value (source index) against CSV target index
                    if category_value in csv_mapping:
                        # Create a new category with updated label
                        mapped_label = csv_mapping[category_value].strip()
                        new_category = QgsRendererCategory(
                            category.value(),
                            category.symbol().clone() if category.symbol() else None,
                            mapped_label,
                            category.renderState(),
                        )
                        updated_categories.append(new_category)
                        updated_count += 1
                        feedback.pushInfo(f"  {category_value} -> {mapped_label}")
                    else:
                        updated_categories.append(category)

                # Clear and reset all categories
                renderer.deleteAllCategories()
                for cat in updated_categories:
                    renderer.addCategory(cat)

                feedback.pushInfo(f"Updated {updated_count} legend entries")

                # Set renderer back on layer
                vector_layer.setRenderer(renderer)
            else:
                feedback.pushWarning(
                    "Layer renderer is not categorized. "
                    "Only categorized symbology is supported. "
                    "Please ensure the layer has categorized symbology."
                )

            # Emit style changed and trigger repaint
            vector_layer.emitStyleChanged()
            vector_layer.triggerRepaint()

            # Refresh the layer tree legend panel
            try:
                from qgis.utils import iface

                if iface:
                    try:
                        layer_tree_model = iface.layerTreeView().model()
                        if layer_tree_model:
                            layer_tree_model.refreshLayerLegend(
                                QgsProject.instance()
                                .layerTreeRoot()
                                .findLayer(vector_layer.id())
                            )
                    except Exception:
                        try:
                            legend = iface.legendInterface()
                            if legend:
                                legend.refreshLayerSymbology(vector_layer)
                        except Exception:
                            pass
            except Exception:
                pass

            feedback.pushInfo("Layer symbology updated and legend panel refreshed")

        except QgsProcessingException:
            raise
        except Exception as e:
            import traceback

            feedback.pushWarning(f"Error updating symbology: {e}")
            feedback.pushWarning(traceback.format_exc())
            raise QgsProcessingException(f"Error updating symbology: {e}")

        # Save current settings for next time
        settings = QgsSettings()
        settings.setValue(
            "tuflow_tools/mapping_csv_to_legend/SOURCE_INDEX", source_index_field
        )
        settings.setValue(
            "tuflow_tools/mapping_csv_to_legend/TARGET_INDEX", target_index_column
        )
        settings.setValue(
            "tuflow_tools/mapping_csv_to_legend/LEGEND_COLUMN", legend_column
        )

        return {"MESSAGE": f"Successfully mapped {len(csv_mapping)} values to legend"}
