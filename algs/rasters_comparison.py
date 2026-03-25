# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterCrs,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsRasterLayer,
    QgsCoordinateTransform,
    QgsRectangle,
)
from osgeo import gdal
import numpy as np
import os
import shutil
import fnmatch
from ..settings import PluginSettings
from ..style_manager import StyleManager


class RastersComparisonAlgorithm(QgsProcessingAlgorithm):
    """
    Compares two rasters and calculates the difference (Raster 1 - Raster 2).
    Supports optional clipping by polygon or extent.
    """

    P_RASTER1 = "RASTER1"
    P_RASTER2 = "RASTER2"
    P_CLIP_POLY = "CLIP_POLY"
    P_EXTENT = "EXTENT"
    P_TARGET_CRS = "TARGET_CRS"
    P_TARGET_RES = "TARGET_RESOLUTION"
    P_OUTPUT = "OUTPUT"

    def tr(self, message):
        return QCoreApplication.translate("RastersComparison", message)

    def createInstance(self):
        return RastersComparisonAlgorithm()

    def name(self):
        return "rasters_comparison"

    def displayName(self):
        return self.tr("Rasters Comparison")

    def group(self):
        return self.tr("General Tools")

    def groupId(self):
        return "general_tools"

    def shortHelpString(self):
        return self.tr(
            "Calculates the difference between two raster layers (Raster 1 - Raster 2).\n\n"
            "Options include clipping the output to a polygon layer, a specific extent, or the current canvas view. "
            "If no clipping is specified, the extent will be the intersection (smaller area) of the two input rasters."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_RASTER1, self.tr("Raster 1 (Base/Current)")
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_RASTER2, self.tr("Raster 2 (Comparison)")
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.P_CLIP_POLY,
                self.tr("Clip by polygon layer"),
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.P_EXTENT,
                self.tr("Clip by extent"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.P_TARGET_CRS, self.tr("Target CRS"), defaultValue="ProjectCrs"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_TARGET_RES,
                self.tr("Target Resolution (leave empty for min of inputs)"),
                type=QgsProcessingParameterNumber.Double,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.P_OUTPUT, self.tr("Raster Difference Output"), QgsProcessing.TEMPORARY_OUTPUT
            )
        )

    def _warp_raster(
        self,
        layer,
        target_crs_wkt,
        target_res,
        feedback,
        output_bounds=None,
        cutline_ds=None,
        cutline_layer=None,
    ):
        try:
            opts_dict = {
                "format": "VRT",
                "xRes": target_res,
                "yRes": target_res,
                "resampleAlg": gdal.GRA_Bilinear,
                "dstSRS": target_crs_wkt,
                "multithread": True,
                "warpOptions": ["INIT_DEST=NO_DATA"],
            }
            if output_bounds:
                opts_dict["outputBounds"] = output_bounds

            if cutline_ds:
                opts_dict["cutlineDSName"] = cutline_ds
                if cutline_layer:
                    opts_dict["cutlineLayer"] = cutline_layer
                opts_dict["cropToCutline"] = True

            opts = gdal.WarpOptions(**opts_dict)
            ds = gdal.Warp("", layer.source(), options=opts)
            if ds is None:
                raise QgsProcessingException(f"GDAL Warp failed for {layer.name()}.")
            return ds
        except Exception as e:
            raise QgsProcessingException(
                f"Resampling failed for {layer.name()}. Error: {e}"
            )

    def processAlgorithm(self, parameters, context, feedback):
        raster1_layer = self.parameterAsRasterLayer(parameters, self.P_RASTER1, context)
        raster2_layer = self.parameterAsRasterLayer(parameters, self.P_RASTER2, context)
        clip_poly_layer = self.parameterAsVectorLayer(parameters, self.P_CLIP_POLY, context)
        extent_param = self.parameterAsExtent(parameters, self.P_EXTENT, context)
        target_crs_param = self.parameterAsCrs(parameters, self.P_TARGET_CRS, context)
        target_res = self.parameterAsDouble(parameters, self.P_TARGET_RES, context)
        out_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)

        if not raster1_layer or not raster1_layer.isValid():
            raise QgsProcessingException("Invalid Raster 1 layer.")
        if not raster2_layer or not raster2_layer.isValid():
            raise QgsProcessingException("Invalid Raster 2 layer.")

        # Determine target CRS
        if not target_crs_param.isValid():
            target_crs_param = context.project().crs()

        # Determine target resolution
        r1_res = min(
            raster1_layer.rasterUnitsPerPixelX(), abs(raster1_layer.rasterUnitsPerPixelY())
        )
        r2_res = min(
            raster2_layer.rasterUnitsPerPixelX(), abs(raster2_layer.rasterUnitsPerPixelY())
        )

        if target_res <= 0:
            target_res = min(r1_res, r2_res)
            feedback.pushInfo(f"Target resolution auto-calculated as: {target_res}")
        else:
            feedback.pushInfo(f"Using target resolution: {target_res}")

        # Determine output extent
        output_bounds = None
        cutline_ds = None
        cutline_layer = None

        if not extent_param.isEmpty():
            # Use user-specified extent
            ext = extent_param
            output_bounds = [
                ext.xMinimum(),
                ext.yMinimum(),
                ext.xMaximum(),
                ext.yMaximum(),
            ]
            feedback.pushInfo("Using user-specified extent.")
        elif clip_poly_layer:
            # Use polygon layer extent
            transform = QgsCoordinateTransform(
                clip_poly_layer.crs(), target_crs_param, context.transformContext()
            )
            ext = transform.transformBoundingBox(clip_poly_layer.extent())
            output_bounds = [
                ext.xMinimum(),
                ext.yMinimum(),
                ext.xMaximum(),
                ext.yMaximum(),
            ]

            # Parse source for GDAL cutline
            source = clip_poly_layer.source()
            if "|" in source:
                parts = source.split("|")
                cutline_ds = parts[0]
                for p in parts[1:]:
                    if p.startswith("layername="):
                        cutline_layer = p.replace("layername=", "")
                        break
            else:
                cutline_ds = source

            feedback.pushInfo(f"Clipping by polygon layer: {clip_poly_layer.name()}")
        else:
            # Default: Intersection of input rasters
            try:
                transform1 = QgsCoordinateTransform(
                    raster1_layer.crs(), target_crs_param, context.transformContext()
                )
                transform2 = QgsCoordinateTransform(
                    raster2_layer.crs(), target_crs_param, context.transformContext()
                )
                ext1 = transform1.transformBoundingBox(raster1_layer.extent())
                ext2 = transform2.transformBoundingBox(raster2_layer.extent())
                ext_intersect = ext1.intersect(ext2)

                if ext_intersect.isEmpty():
                    raise QgsProcessingException("Input rasters do not intersect.")

                output_bounds = [
                    ext_intersect.xMinimum(),
                    ext_intersect.yMinimum(),
                    ext_intersect.xMaximum(),
                    ext_intersect.yMaximum(),
                ]
                feedback.pushInfo("Using intersection of input rasters.")
            except Exception as e:
                feedback.reportError(f"Error calculating intersection extent: {e}")
                ext1 = raster1_layer.extent()
                output_bounds = [
                    ext1.xMinimum(),
                    ext1.yMinimum(),
                    ext1.xMaximum(),
                    ext1.yMaximum(),
                ]

        # Warp both to target CRS, res, and bounds
        target_crs_wkt = target_crs_param.toWkt()
        feedback.pushInfo("Warping Raster 1...")
        ds1 = self._warp_raster(
            raster1_layer,
            target_crs_wkt,
            target_res,
            feedback,
            output_bounds,
            cutline_ds,
            cutline_layer,
        )
        feedback.pushInfo("Warping Raster 2...")
        ds2 = self._warp_raster(
            raster2_layer,
            target_crs_wkt,
            target_res,
            feedback,
            output_bounds,
            cutline_ds,
            cutline_layer,
        )

        # Read arrays
        band1 = ds1.GetRasterBand(1)
        nd1 = band1.GetNoDataValue()
        arr1 = band1.ReadAsArray().astype(np.float32)

        band2 = ds2.GetRasterBand(1)
        nd2 = band2.GetNoDataValue()
        arr2 = band2.ReadAsArray().astype(np.float32)

        if arr1.shape != arr2.shape:
            raise QgsProcessingException(
                f"Resampling produced different grid sizes: R1 {arr1.shape} vs R2 {arr2.shape}"
            )

        feedback.pushInfo("Calculating difference...")

        # Masks for nodata
        valid1 = (arr1 != nd1) if nd1 is not None else ~np.isnan(arr1)
        valid2 = (arr2 != nd2) if nd2 is not None else ~np.isnan(arr2)
        both_valid = valid1 & valid2

        # Calculate diff
        out_arr = np.full_like(arr1, -99999.0)  # Background nodata
        out_arr[both_valid] = arr1[both_valid] - arr2[both_valid]

        # Save result
        if not out_path or out_path == QgsProcessing.TEMPORARY_OUTPUT:
            tmp_dest = QgsProcessingParameterRasterDestination(self.P_OUTPUT, "")
            out_path = tmp_dest.generateTemporaryDestination()

        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            out_path,
            ds1.RasterXSize,
            ds1.RasterYSize,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=LZW", "TILED=YES"],
        )
        out_ds.SetGeoTransform(ds1.GetGeoTransform())
        out_ds.SetProjection(ds1.GetProjection())

        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-99999.0)
        out_band.WriteArray(out_arr)
        out_band.FlushCache()
        out_ds.FlushCache()
        out_ds = None
        ds1 = None
        ds2 = None

        # Add to project
        try:
            layer_name = "Raster_Diff"
            project = context.project() or QgsProject.instance()
            details = QgsProcessingContext.LayerDetails(layer_name, project)
            context.addLayerToLoadOnCompletion(out_path, details)

            # Apply style via style_manager if available
            try:
                style_path = PluginSettings.get_style_path()
                if style_path and os.path.isdir(style_path):
                    for (
                        pattern_str,
                        qml_file_str,
                        layer_type,
                    ) in StyleManager.get_style_mappings():
                        patterns = [p.strip() for p in pattern_str.split(",")]
                        if layer_type == "raster" and any(
                            fnmatch.fnmatch(layer_name, p) for p in patterns if p
                        ):
                            qml_files = [q.strip() for q in qml_file_str.split(",")]
                            style_applied = False
                            for qml_file in qml_files:
                                if not qml_file:
                                    continue
                                src_qml = os.path.join(style_path, qml_file)
                                if os.path.exists(src_qml):
                                    dst_qml = os.path.splitext(out_path)[0] + ".qml"
                                    shutil.copy2(src_qml, dst_qml)
                                    feedback.pushInfo(f"Applied style: {qml_file}")
                                    style_applied = True
                                    break
                            
                            if style_applied:
                                break
            except Exception as se:
                feedback.reportError(f"Could not apply style: {se}")

        except Exception as e:
            feedback.reportError(f"Could not register layer for loading: {e}")

        feedback.pushInfo("Calculation complete.")
        return {self.P_OUTPUT: out_path}
