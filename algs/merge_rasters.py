# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsMapLayerType,
)
from osgeo import gdal


class MergeRastersAlgorithm(QgsProcessingAlgorithm):
    """
    Simplifies gdal.BuildVRT.
    Explicitly indicates that multiple raster layers override with the last one winning.
    """

    P_INPUTS = "INPUTS"
    P_RESOLUTION_STRATEGY = "RESOLUTION_STRATEGY"
    P_CUSTOM_RES = "CUSTOM_RESOLUTION"
    P_RESAMPLING = "RESAMPLING"
    P_OUTPUT = "OUTPUT"

    RESOLUTION_OPTIONS = [
        "Highest",
        "Lowest",
        "Average",
        "Custom"
    ]

    RESAMPLING_OPTIONS = [
        "Nearest Neighbour",
        "Bilinear",
        "Cubic",
        "Cubic Spline",
        "Lanczos",
        "Average",
        "Mode"
    ]

    RESAMPLING_MAP = [
        gdal.GRA_NearestNeighbour,
        gdal.GRA_Bilinear,
        gdal.GRA_Cubic,
        gdal.GRA_CubicSpline,
        gdal.GRA_Lanczos,
        gdal.GRA_Average,
        gdal.GRA_Mode
    ]

    def tr(self, message):
        return QCoreApplication.translate("MergeRasters", message)

    def createInstance(self):
        return MergeRastersAlgorithm()

    def name(self):
        return "merge_rasters"

    def displayName(self):
        return self.tr("Merge Rasters")

    def group(self):
        return "General Tools"

    def groupId(self):
        return "general_tools"

    def shortHelpString(self):
        return self.tr(
            "Simplifies the GDAL 'Build virtual raster' tool to quickly merge multiple rasters.<br><br>"
            "<b>IMPORTANT: Overlapping rule</b><br>"
            "When multiple raster layers overlap, <b>the last layer in the list wins</b> and overrides the previous ones.<br><br>"
            "You can choose the resolution strategy (default is Highest) and the resampling algorithm."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.P_INPUTS,
                self.tr("Input Rasters (Override with Last Wins)"),
                layerType=QgsProcessing.TypeRaster
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_RESOLUTION_STRATEGY,
                self.tr("Resolution Strategy"),
                options=self.RESOLUTION_OPTIONS,
                defaultValue=0  # Highest
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_CUSTOM_RES,
                self.tr("Custom Resolution (used if strategy is Custom)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=None,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_RESAMPLING,
                self.tr("Resampling Algorithm"),
                options=self.RESAMPLING_OPTIONS,
                defaultValue=0  # Nearest Neighbour
            )
        )

        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.P_OUTPUT,
                self.tr("Merged Virtual Raster (*.vrt)"),
                fileFilter="Virtual Raster (*.vrt)",
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layers = self.parameterAsLayerList(parameters, self.P_INPUTS, context)
        res_idx = self.parameterAsEnum(parameters, self.P_RESOLUTION_STRATEGY, context)
        custom_res = self.parameterAsDouble(parameters, self.P_CUSTOM_RES, context)
        resamp_idx = self.parameterAsEnum(parameters, self.P_RESAMPLING, context)
        out_path = self.parameterAsFileOutput(parameters, self.P_OUTPUT, context)

        if not layers:
            raise QgsProcessingException(self.tr("No input layers selected."))

        if not out_path:
            raise QgsProcessingException(self.tr("No output path specified."))

        if not out_path.lower().endswith('.vrt'):
            out_path += '.vrt'

        input_paths = []
        for layer in layers:
            if not layer.isValid():
                continue
            
            source = layer.source()
            # Handle standard file-based rasters and others (like WMS/WCS if applicable, though usually just file paths are used)
            # Some providers like 'gdal' prefix files or use tricky paths, but typically source() is the path or VRT.
            # We strip any provider modifiers if necessary, but usually source() works for GDAL.
            input_paths.append(source)

        if not input_paths:
            raise QgsProcessingException(self.tr("No valid input paths found from the selected layers."))

        feedback.pushInfo(f"Merging {len(input_paths)} rasters...")
        for idx, path in enumerate(input_paths):
            feedback.pushInfo(f"[{idx+1}] {path}")

        res_str = 'highest'
        if res_idx == 1:
            res_str = 'lowest'
        elif res_idx == 2:
            res_str = 'average'
        elif res_idx == 3:
            res_str = 'user'
            # QGIS might return 0.0 if the optional field is left blank.
            if not custom_res or custom_res <= 0:
                raise QgsProcessingException(self.tr("Custom resolution must be > 0 when Custom strategy is selected."))

        resamp_alg = self.RESAMPLING_MAP[resamp_idx]

        build_vrt_kwargs = {
            'resampleAlg': resamp_alg,
            'resolution': res_str
        }

        if res_str == 'user':
            build_vrt_kwargs['xRes'] = custom_res
            build_vrt_kwargs['yRes'] = custom_res

        # In gdal.BuildVRT, the *last* dataset in the list overrides earlier ones in overlapping areas.
        try:
            vrt_options = gdal.BuildVRTOptions(**build_vrt_kwargs)
            ds = gdal.BuildVRT(out_path, input_paths, options=vrt_options)
            if ds is None:
                raise Exception("GDAL returned None when building VRT.")
            
            ds.FlushCache()
            ds = None
        except Exception as e:
            raise QgsProcessingException(f"Failed to build virtual raster: {e}")

        # Add to project
        try:
            project = context.project() or QgsProject.instance()
            details = QgsProcessingContext.LayerDetails("Merged_Rasters", project)
            context.addLayerToLoadOnCompletion(out_path, details)
        except Exception as e:
            feedback.reportError(f"Could not register layer for loading: {e}")

        feedback.pushInfo("Merge complete.")
        return {self.P_OUTPUT: out_path}
