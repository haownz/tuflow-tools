# -*- coding: utf-8 -*-
"""
Land Cover Processing Algorithm

Clips Land Cover and Impervious layers by an Input Polygon,
removes overlapping areas from Land Cover, and merges the two results.
"""

import processing
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsSettings,
    QgsFeatureRequest,
    QgsProcessingContext,
    QgsProcessingUtils
)


class ProcessLandcoverAlgorithm(QgsProcessingAlgorithm):
    """
    Clips Land Cover and Impervious layers by an input polygon, performs a difference, 
    and merges the remainders into a single layer.
    """
    
    PARAM_LC_LAYER = 'LC_LAYER'
    PARAM_IMP_LAYER = 'IMP_LAYER'
    OUTPUT = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ProcessLandcoverAlgorithm()

    def name(self):
        return 'process_landcover'

    def displayName(self):
        return self.tr('Land Cover Processing')

    def group(self):
        return self.tr('Input Processing')

    def groupId(self):
        return 'input_processing'

    def shortHelpString(self):
        return self.tr(
            "1. Fixes Invalid Geometries of the Land Cover and Impervious Layers.\n"
            "2. Computes the geometric difference to remove Impervious areas from the Land Cover features.\n"
            "3. Merges the remaining Land Cover with the Impervious Layer to generate a single combined dataset."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PARAM_LC_LAYER,
                self.tr('Land Cover Layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PARAM_IMP_LAYER,
                self.tr('Impervious Layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Combined Land Cover Layer')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        lc_layer = self.parameterAsSource(parameters, self.PARAM_LC_LAYER, context)
        if lc_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.PARAM_LC_LAYER))

        imp_layer = self.parameterAsSource(parameters, self.PARAM_IMP_LAYER, context)
        if imp_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.PARAM_IMP_LAYER))
            
        # Get parameter values for processing algs
        lc_param = parameters[self.PARAM_LC_LAYER]
        imp_param = parameters[self.PARAM_IMP_LAYER]

        # Geometry errors are common in datasets, instruct the context to ignore invalid features
        context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)

        # Optimization: Use 'TEMPORARY_OUTPUT' instead of 'memory:' to leverage GeoPackage spatial
        # indexing and avoid out-of-memory errors for huge amounts of polygons.
        
        # Step 1: Fix Geometries
        feedback.pushInfo("Fixing geometries for input layers...")
        res_fix_lc = processing.run('native:fixgeometries', {'INPUT': lc_param, 'OUTPUT': 'TEMPORARY_OUTPUT'}, context=context, feedback=feedback, is_child_algorithm=True)
        res_fix_imp = processing.run('native:fixgeometries', {'INPUT': imp_param, 'OUTPUT': 'TEMPORARY_OUTPUT'}, context=context, feedback=feedback, is_child_algorithm=True)
        fixed_lc = res_fix_lc['OUTPUT']
        fixed_imp = res_fix_imp['OUTPUT']

        # Step 2: Subdivide complex polygons to drastically speed up spatial intersection
        feedback.pushInfo("Subdividing complex geometries...")
        res_sub_lc = processing.run('native:subdivide', {'INPUT': fixed_lc, 'MAX_NODES': 256, 'OUTPUT': 'TEMPORARY_OUTPUT'}, context=context, feedback=feedback, is_child_algorithm=True)
        sub_lc = res_sub_lc['OUTPUT']

        res_sub_imp = processing.run('native:subdivide', {'INPUT': fixed_imp, 'MAX_NODES': 256, 'OUTPUT': 'TEMPORARY_OUTPUT'}, context=context, feedback=feedback, is_child_algorithm=True)
        sub_imp = res_sub_imp['OUTPUT']

        # Build explicit spatial index on the subdivided layers for fast Geoprocessing
        feedback.pushInfo("Creating explicit spatial index on overlays...")
        processing.run('native:createspatialindex', {'INPUT': sub_lc}, context=context, feedback=feedback, is_child_algorithm=True)
        processing.run('native:createspatialindex', {'INPUT': sub_imp}, context=context, feedback=feedback, is_child_algorithm=True)

        # Step 3: Difference (LC - Impervious)
        feedback.pushInfo("Performing geometric difference under standard execution...")
        diff_params = {
            'INPUT': sub_lc,
            'OVERLAY': sub_imp,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        res_diff = processing.run('native:difference', diff_params, context=context, feedback=feedback, is_child_algorithm=True)
        diff_lc = res_diff['OUTPUT']

        # Step 4: Merge remaining Land Cover with original Impervious
        feedback.pushInfo("Merging difference results with Impervious layer...")
        merge_params = {
            'LAYERS': [diff_lc, fixed_imp],
            'CRS': lc_layer.sourceCrs(),
            'OUTPUT': parameters[self.OUTPUT]
        }
        res_merge = processing.run('native:mergevectorlayers', merge_params, context=context, feedback=feedback, is_child_algorithm=True)

        return {self.OUTPUT: res_merge['OUTPUT']}
