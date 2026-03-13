# -*- coding: utf-8 -*-
import os
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsFeature,
    QgsProcessingUtils
)
import processing

class InundationBoundaryAlgorithm(QgsProcessingAlgorithm):
    """
    Takes a TUFLOW flood depth raster, filters for pixels > cutoff, and 
    generates a closed 2D polygon vector layer representing the inundation boundary.
    """
    
    INPUT = 'INPUT'
    CUTOFF = 'CUTOFF'
    SIMPLIFY = 'SIMPLIFY'
    SMOOTHING = 'SMOOTHING'
    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return InundationBoundaryAlgorithm()

    def name(self):
        return 'inundation_boundary'

    def displayName(self):
        return 'Inundation Boundary'

    def group(self):
        return '2 - Result Analysis'

    def groupId(self):
        return 'result_analysis'

    def shortHelpString(self):
        return (
            "Generates a closed polygon inundation boundary from a flood depth raster.\n\n"
            "By default, traces areas where depth is strictly greater than 0.05m.\n"
            "Can simplify the resulting geometry by an interval (tolerance) and optionally smooth it."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT,
                "Flood Depth Raster Layer",
                [QgsProcessing.TypeRaster]
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CUTOFF,
                "Depth Cutoff (m)",
                QgsProcessingParameterNumber.Double,
                defaultValue=0.05
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SIMPLIFY,
                "Simplify Tolerance (m, 0 = Disabled)",
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                minValue=0.0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTHING,
                "Smoothing Iterations (0 = Disabled)",
                QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
                maxValue=10
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Inundation Boundary",
                QgsProcessing.TypeVectorPolygon
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        input_layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        cutoff = self.parameterAsDouble(parameters, self.CUTOFF, context)
        simplify = self.parameterAsDouble(parameters, self.SIMPLIFY, context)
        smoothing = self.parameterAsInt(parameters, self.SMOOTHING, context)

        if not input_layer:
            raise QgsProcessingException("Invalid input raster layer.")

        # Step 1: Raster Calculator to extract mask where Depth > Cutoff
        # Using gdal:rastercalculator for maximum compatibility across QGIS versions
        # Output is 1 where condition is true, 0 (or nodata depending on implementation) where false.
        
        formula = f'A > {cutoff}'
        
        feedback.pushInfo(f"Step 1: Calculating raster mask (Depth > {cutoff}m)...")
        mask_result = processing.run(
            "gdal:rastercalculator",
            {
                'INPUT_A': input_layer,
                'BAND_A': 1,
                'FORMULA': formula,
                'RTYPE': 5, # Float32
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        
        mask_raster = mask_result['OUTPUT']
        
        # Step 2: Polygonize the mask raster
        feedback.pushInfo("Step 2: Polygonizing flood mask...")
        poly_result = processing.run(
            "gdal:polygonize",
            {
                'INPUT': mask_raster,
                'BAND': 1,
                'FIELD': 'DN', # Default field for gdal:polygonize containing the pixel value
                'EIGHT_CONNECTEDNESS': False,
                'EXTRA': '',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        
        poly_layer = poly_result['OUTPUT']
        
        # Step 3: Filter the polygons to keep only the flooded areas (DN = 1)
        # Because the raster calculator formula output True=1, False=0.
        feedback.pushInfo("Step 3: Filtering and smoothing boundary...")
        extracted_result = processing.run(
            "native:extractbyexpression",
            {
                'INPUT': poly_layer,
                'EXPRESSION': '"DN" = 1',
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        
        flooded_polys = extracted_result['OUTPUT']
        
        # Step 4: Dissolve the polygons into a single seamless boundary per contiguous area
        dissolve_result = processing.run(
            "native:dissolve",
            {
                'INPUT': flooded_polys,
                'FIELD': [], # Dissolve all
                'OUTPUT': 'TEMPORARY_OUTPUT'
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )
        
        dissolved_id = dissolve_result['OUTPUT']
        
        # Step 5: Simplify polygons (if requested)
        if simplify > 0:
            feedback.pushInfo(f"Step 5: Simplifying polygons ({simplify}m tolerance)...")
            simplify_result = processing.run(
                "native:simplifygeometries",
                {
                    'INPUT': dissolved_id,
                    'METHOD': 0, # Distance (Douglas-Peucker)
                    'TOLERANCE': simplify,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            simplified_id = simplify_result['OUTPUT']
        else:
            simplified_id = dissolved_id
        
        # Step 6: Smooth polygons (if requested)
        if smoothing > 0:
            feedback.pushInfo(f"Step 6: Smoothing polygons ({smoothing} iterations)...")
            smooth_result = processing.run(
                "native:smoothgeometry",
                {
                    'INPUT': simplified_id,
                    'ITERATIONS': smoothing,
                    'OFFSET': 0.25,
                    'MAX_ANGLE': 180,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            final_id = smooth_result['OUTPUT']
        else:
            final_id = simplified_id
            
        final_layer = QgsProcessingUtils.mapLayerFromString(final_id, context)
        
        # Step 7: Save to final output sink
        feedback.pushInfo("Step 7: Writing output features...")
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            final_layer.fields(),
            final_layer.wkbType(),
            final_layer.crs()
        )
        
        for feature in final_layer.getFeatures():
            sink.addFeature(feature, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}
