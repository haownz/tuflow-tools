# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsFeatureRequest
)

import os

class LandCoverAddFieldsAlgorithm(QgsProcessingAlgorithm):
    """
    Adds ClassName, Material, and SoilID fields to a Land Cover layer based on a ClassID field.
    Modifies the input layer directly.
    """

    INPUT = 'INPUT'
    GEN_2D_MAT = 'GEN_2D_MAT'
    GEN_2D_SOIL = 'GEN_2D_SOIL'
    OUT_MAT = 'OUT_MAT'
    OUT_SOIL = 'OUT_SOIL'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return LandCoverAddFieldsAlgorithm()

    def name(self):
        return 'lc_add_fields'

    def displayName(self):
        return self.tr('Land Cover Add Fields')

    def group(self):
        return self.tr('1 - Input Processing')

    def groupId(self):
        return 'input_processing'

    def shortHelpString(self):
        return self.tr(
            "Adds attributes to a Land Cover layer based on an existing 'ClassID' field.\n"
            "Modifies the input layer directly instead of creating a new one.\n\n"
            "Added/Updated fields:\n"
            "- ClassName: String mapped from ClassID (0: Buildings, 1: Grass, etc.)\n"
            "- Material: Integer (ClassID + 1)\n"
            "- SoilID: Integer (ClassID + 1)\n"
            "Can optionally generate 2d_mat and 2d_soil GeoPackages if checked respectively."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                self.tr('Input Land Cover Layer (will be modified directly)'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_2D_MAT,
                self.tr('Generate 2d_mat input file (GeoPackage)'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_2D_SOIL,
                self.tr('Generate 2d_soil input file (GeoPackage)'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUT_MAT,
                self.tr('2d_mat Land Cover GeoPackage (Optional)'),
                fileFilter="GeoPackage (*.gpkg)",
                defaultValue="2d_mat_land_cover_R.gpkg"
            )
        )

        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUT_SOIL,
                self.tr('2d_soil Land Cover GeoPackage (Optional)'),
                fileFilter="GeoPackage (*.gpkg)",
                defaultValue="2d_soil_land_cover_R.gpkg"
            )
        )

        # Try to pre-fill INPUT with the active vector layer
        try:
            from qgis.utils import iface
            active = iface.activeLayer() if iface else None
            if active and active.type() == 0: # 0 denotes VectorLayer
                p_input = self.parameterDefinition(self.INPUT)
                if p_input:
                    p_input.setDefaultValue(active.id())
        except Exception:
            pass

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        gen_2d_mat = self.parameterAsBool(parameters, self.GEN_2D_MAT, context)
        gen_2d_soil = self.parameterAsBool(parameters, self.GEN_2D_SOIL, context)
        
        # Override output paths if model path is set
        from tuflow_tools.settings import PluginSettings
        model_path = PluginSettings.get_model_path()
        
        if (gen_2d_mat or gen_2d_soil) and model_path:
            # Clean up the model path in case it has trailing slashes or is just a drive letter
            gis_dir = os.path.join(model_path.strip('\\/'), 'model', 'gis')
            os.makedirs(gis_dir, exist_ok=True)
            if gen_2d_mat:
                out_mat_path = os.path.join(gis_dir, "2d_mat_land_cover_R.gpkg")
            if gen_2d_soil:
                out_soil_path = os.path.join(gis_dir, "2d_soil_land_cover_R.gpkg")
        else:
            if gen_2d_mat:
                out_mat_path = self.parameterAsFileOutput(parameters, self.OUT_MAT, context)
            if gen_2d_soil:
                out_soil_path = self.parameterAsFileOutput(parameters, self.OUT_SOIL, context)

        # Check if ClassID exists
        fields = layer.fields()
        class_id_idx = fields.lookupField('ClassID')
        if class_id_idx == -1:
            raise QgsProcessingException(self.tr("Input layer does not contain a 'ClassID' field."))

        layer.startEditing()
        try:
            # Add missing fields if they do not exist
            new_fields = []
            if fields.lookupField('ClassName') == -1:
                new_fields.append(QgsField('ClassName', QVariant.String, len=50))
            if fields.lookupField('Material') == -1:
                new_fields.append(QgsField('Material', QVariant.Int))
            if fields.lookupField('SoilID') == -1:
                new_fields.append(QgsField('SoilID', QVariant.Int))

            if new_fields:
                layer.dataProvider().addAttributes(new_fields)
                layer.updateFields()
            
            # Re-fetch fields and indices after potential add
            fields = layer.fields()
            class_name_idx = fields.lookupField('ClassName')
            material_idx = fields.lookupField('Material')
            soil_id_idx = fields.lookupField('SoilID')
            
            # We must use standard provider iteration (skipping geometry checks which can throw valid geometry errors)
            request = QgsFeatureRequest()
            request.setSubsetOfAttributes([class_id_idx])
            # We don't need geometry for the main layer update, saving time and skipping invalid geo checks
            request.setFlags(QgsFeatureRequest.NoGeometry)
            
            # If generating 2D files, we WILL need the geometry
            if gen_2d_mat or gen_2d_soil:
                request.setFlags(QgsFeatureRequest.NoFlags)

            # Setup optional 2d output sinks
            sink_mat, sink_soil = None, None
            if gen_2d_mat:
                # Setup Mat geopackage
                mat_fields = QgsFields()
                mat_fields.append(QgsField('Material', QVariant.Int))
                
                res_mat = QgsVectorFileWriter.create(
                    out_mat_path,
                    mat_fields,
                    layer.wkbType(),
                    layer.sourceCrs(),
                    context.transformContext(),
                    QgsVectorFileWriter.SaveVectorOptions()
                )
                sink_mat = res_mat[0] if isinstance(res_mat, tuple) else res_mat

            if gen_2d_soil:
                # Setup Soil geopackage
                soil_fields = QgsFields()
                soil_fields.append(QgsField('SoilID', QVariant.Int))

                res_soil = QgsVectorFileWriter.create(
                    out_soil_path,
                    soil_fields,
                    layer.wkbType(),
                    layer.sourceCrs(),
                    context.transformContext(),
                    QgsVectorFileWriter.SaveVectorOptions()
                )
                sink_soil = res_soil[0] if isinstance(res_soil, tuple) else res_soil

            class_map = {
                0: 'Buildings', 1: 'Grass', 2: 'Impervious paving',
                3: 'Roads', 4: 'Scrub', 5: 'Sand Gravel',
                6: 'High vegetation', 7: 'Water', 8: 'Parcels'
            }

            total = 100.0 / layer.featureCount() if layer.featureCount() > 0 else 0
            
            attr_map = {}
            batch_mat, batch_soil = [], []
            batch_size = 1000

            for current, feature in enumerate(layer.getFeatures(request)):
                if feedback.isCanceled():
                    break

                class_id_val = feature['ClassID']
                class_name = 'Unknown'
                material = None
                soil_id = None

                try:
                    if class_id_val is not None:
                        cid = int(class_id_val)
                        class_name = class_map.get(cid, 'Unknown')
                        material = cid + 1
                        soil_id = cid + 1
                except ValueError:
                    pass

                # Store attribute edits
                attr_map[feature.id()] = {
                    class_name_idx: class_name,
                    material_idx: material,
                    soil_id_idx: soil_id
                }

                if gen_2d_mat and sink_mat:
                    out_mat = QgsFeature(mat_fields)
                    out_mat.setGeometry(feature.geometry())
                    out_mat.setAttributes([material])
                    batch_mat.append(out_mat)

                if gen_2d_soil and sink_soil:
                    out_soil = QgsFeature(soil_fields)
                    out_soil.setGeometry(feature.geometry())
                    out_soil.setAttributes([soil_id])
                    batch_soil.append(out_soil)

                # Batch write 2D geometries
                if len(attr_map) >= batch_size:
                    layer.dataProvider().changeAttributeValues(attr_map)
                    attr_map.clear()
                    
                    if gen_2d_mat:
                        sink_mat.addFeatures(batch_mat)
                        batch_mat.clear()
                    if gen_2d_soil:
                        sink_soil.addFeatures(batch_soil)
                        batch_soil.clear()

                feedback.setProgress(int(current * total))

            # Write remaining features
            if attr_map:
                layer.dataProvider().changeAttributeValues(attr_map)
            if gen_2d_mat and batch_mat:
                sink_mat.addFeatures(batch_mat)
            if gen_2d_soil and batch_soil:
                sink_soil.addFeatures(batch_soil)

            layer.commitChanges()

        except Exception as e:
            layer.rollBack()
            raise QgsProcessingException(f"Error updating fields: {str(e)}")
            
        finally:
            # Explicitly delete writer objects to close files and remove file locks
            if 'sink_mat' in locals() and sink_mat is not None:
                del sink_mat
            if 'sink_soil' in locals() and sink_soil is not None:
                del sink_soil

        res = {}
        if gen_2d_mat:
            res[self.OUT_MAT] = out_mat_path

        if gen_2d_soil:
            res[self.OUT_SOIL] = out_soil_path

        return res
