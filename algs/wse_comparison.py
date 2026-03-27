# -*- coding: utf-8 -*-

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterCrs,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsRasterLayer,
    QgsCoordinateTransform,
)
from osgeo import gdal
import numpy as np
import os
import shutil
import fnmatch
from ..settings import PluginSettings
from ..style_manager import StyleManager


class WSEComparisonAlgorithm(QgsProcessingAlgorithm):
    """
    Compares two WSE rasters and calculates the difference.
    Handles Was Wet Now Dry (-9999) and Was Dry Now Wet (9999).
    """

    P_WSE1 = "WSE1"
    P_WSE2 = "WSE2"
    P_DEPTH = "DEPTH"
    P_DEPTH_THRESHOLD = "DEPTH_THRESHOLD"
    P_TARGET_CRS = "TARGET_CRS"
    P_TARGET_RES = "TARGET_RESOLUTION"
    P_OUTPUT = "OUTPUT"

    def createInstance(self):
        return WSEComparisonAlgorithm()

    def name(self):
        return "wse_comparison"

    def displayName(self):
        return "WSE Comparison"

    def group(self):
        return "2 - Result Analysis"

    def groupId(self):
        return "result_analysis"

    def shortHelpString(self):
        return (
            "Compares two WSE (Water Surface Elevation) raster layers.\n\n"
            "Calculates WSE1 - WSE2.\n"
            "Outputs special values -9999.0 for 'Was Wet Now Dry' and 9999.0 for 'Was Dry Now Wet'.\n"
            "Projects and resamples inputs to the target CRS and resolution if they differ."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.P_WSE1, "WSE1 (Base/Current Raster)")
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.P_WSE2, "WSE2 (Comparison Raster)")
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_DEPTH, "Depth Layer (Optional)", optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_DEPTH_THRESHOLD,
                "Depth Threshold (m) - applied only if depth layer is provided/detected",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.05,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.P_TARGET_CRS, "Target CRS", defaultValue="ProjectCrs"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_TARGET_RES,
                "Target Resolution (leave empty for min of inputs)",
                type=QgsProcessingParameterNumber.Double,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.P_OUTPUT, "WSE Difference Output", QgsProcessing.TEMPORARY_OUTPUT
            )
        )

        # Try to pre-fill WSE1 and Depth with the active raster layer
        try:
            from qgis.utils import iface

            active = iface.activeLayer() if iface else None
            if isinstance(active, QgsRasterLayer):
                p_wse1 = self.parameterDefinition(self.P_WSE1)
                if p_wse1:
                    p_wse1.setDefaultValue(active.id())

                # Auto-detect depth layer based on active layer
                wse1_name = active.name()
                import re

                if '_h_' in wse1_name.lower():
                    depth_name = re.sub(r'(_h_)', '_d_', wse1_name, flags=re.IGNORECASE)

                    project = QgsProject.instance()
                    depth_layer_found = None

                    # Exact match
                    layers = project.mapLayersByName(depth_name)
                    for l in layers:
                        if isinstance(l, QgsRasterLayer) and l.isValid():
                            depth_layer_found = l
                            break

                    # Case-insensitive match
                    if not depth_layer_found:
                        for l in project.mapLayers().values():
                            if isinstance(l, QgsRasterLayer) and l.isValid():
                                if l.name().lower() == depth_name.lower():
                                    depth_layer_found = l
                                    break

                    if depth_layer_found:
                        p_depth = self.parameterDefinition(self.P_DEPTH)
                        if p_depth:
                            p_depth.setDefaultValue(depth_layer_found.id())
        except Exception:
            pass

    def _warp_raster(
        self, layer, target_crs_wkt, target_res, feedback, output_bounds=None
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

            opts = gdal.WarpOptions(**opts_dict)
            ds = gdal.Warp("", layer.source(), options=opts)
            if ds is None:
                raise QgsProcessingException(f"GDAL Warp failed for {layer.name()}.")
            return ds
        except Exception as e:
            raise QgsProcessingException(
                f"Resampling failed for {layer.name()}. Error: {e}"
            )

    def checkParameterValues(self, parameters, context):
        """
        Dynamically update the Depth Layer parameter based on WSE1 selection
        if Depth Layer is currently blank.
        """
        wse1_layer = self.parameterAsRasterLayer(parameters, self.P_WSE1, context)
        depth_layer = self.parameterAsRasterLayer(parameters, self.P_DEPTH, context)
        
        # If user hasn't explicitly set a depth layer, try to find one
        if not depth_layer and wse1_layer:
            wse1_name = wse1_layer.name()
            depth_name = None
            if '_h_' in wse1_name.lower():
                import re
                depth_name = re.sub(r'(_h_)', '_d_', wse1_name, flags=re.IGNORECASE)
            
            if depth_name:
                project = context.project() or QgsProject.instance()
                
                # First try exact match by name
                found_layer = None
                layers = project.mapLayersByName(depth_name)
                for l in layers:
                    if isinstance(l, QgsRasterLayer) and l.isValid():
                        found_layer = l
                        break
                
                # If exact match fails, try a case-insensitive search
                if not found_layer:
                    for l in project.mapLayers().values():
                        if isinstance(l, QgsRasterLayer) and l.isValid():
                            if l.name().lower() == depth_name.lower():
                                found_layer = l
                                break
                                
                if found_layer:
                    # Modify the parameters dictionary so the UI updates
                    parameters[self.P_DEPTH] = found_layer.id()

        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        wse1_layer = self.parameterAsRasterLayer(parameters, self.P_WSE1, context)
        wse2_layer = self.parameterAsRasterLayer(parameters, self.P_WSE2, context)
        depth_layer = self.parameterAsRasterLayer(parameters, self.P_DEPTH, context)
        depth_threshold = self.parameterAsDouble(parameters, self.P_DEPTH_THRESHOLD, context)
        target_crs_param = self.parameterAsCrs(parameters, self.P_TARGET_CRS, context)
        target_res = self.parameterAsDouble(parameters, self.P_TARGET_RES, context)
        out_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)

        if not wse1_layer or not wse1_layer.isValid():
            raise QgsProcessingException("Invalid WSE1 layer.")
        if not wse2_layer or not wse2_layer.isValid():
            raise QgsProcessingException("Invalid WSE2 layer.")

        if depth_layer:
            feedback.pushInfo(f"Using Depth Layer: {depth_layer.name()} with threshold {depth_threshold}m")
        else:
            feedback.pushInfo("No Depth Layer provided or detected. Depth threshold will not be applied.")

        # Determine target CRS
        if not target_crs_param.isValid():
            target_crs_param = context.project().crs()

        if wse1_layer.crs() != target_crs_param or wse2_layer.crs() != target_crs_param:
            feedback.pushInfo(
                f"Target CRS is {target_crs_param.authid()}. Rasters will be projected."
            )

        # Determine target resolution
        wse1_res = min(
            wse1_layer.rasterUnitsPerPixelX(), abs(wse1_layer.rasterUnitsPerPixelY())
        )
        wse2_res = min(
            wse2_layer.rasterUnitsPerPixelX(), abs(wse2_layer.rasterUnitsPerPixelY())
        )

        if target_res <= 0:
            target_res = min(wse1_res, wse2_res)
            feedback.pushInfo(f"Target resolution auto-calculated as: {target_res}")
        else:
            feedback.pushInfo(f"Using target resolution: {target_res}")

        # Compute combined bounds in target CRS
        try:
            transform1 = QgsCoordinateTransform(
                wse1_layer.crs(), target_crs_param, context.transformContext()
            )
            transform2 = QgsCoordinateTransform(
                wse2_layer.crs(), target_crs_param, context.transformContext()
            )
            ext1 = transform1.transformBoundingBox(wse1_layer.extent())
            ext2 = transform2.transformBoundingBox(wse2_layer.extent())
            ext1.combineExtentWith(ext2)
            combined_bounds = [
                ext1.xMinimum(),
                ext1.yMinimum(),
                ext1.xMaximum(),
                ext1.yMaximum(),
            ]
        except Exception:
            ext1 = wse1_layer.extent()
            ext2 = wse2_layer.extent()
            ext1.combineExtentWith(ext2)
            combined_bounds = [
                ext1.xMinimum(),
                ext1.yMinimum(),
                ext1.xMaximum(),
                ext1.yMaximum(),
            ]

        # Warp both to target CRS, res, and bounds
        target_crs_wkt = target_crs_param.toWkt()
        feedback.pushInfo("Warping WSE1...")
        ds1 = self._warp_raster(
            wse1_layer, target_crs_wkt, target_res, feedback, combined_bounds
        )
        feedback.pushInfo("Warping WSE2...")
        ds2 = self._warp_raster(
            wse2_layer, target_crs_wkt, target_res, feedback, combined_bounds
        )

        ds_depth = None
        if depth_layer:
            feedback.pushInfo("Warping Depth Layer...")
            ds_depth = self._warp_raster(
                depth_layer, target_crs_wkt, target_res, feedback, combined_bounds
            )

        # Read arrays
        band1 = ds1.GetRasterBand(1)
        nd1 = band1.GetNoDataValue()
        arr1 = band1.ReadAsArray().astype(np.float32)

        band2 = ds2.GetRasterBand(1)
        nd2 = band2.GetNoDataValue()
        arr2 = band2.ReadAsArray().astype(np.float32)

        arr_depth = None
        nd_depth = None
        if ds_depth:
            band_depth = ds_depth.GetRasterBand(1)
            nd_depth = band_depth.GetNoDataValue()
            arr_depth = band_depth.ReadAsArray().astype(np.float32)
            if arr_depth.shape != arr1.shape:
                raise QgsProcessingException(
                    f"Resampling produced different grid sizes for Depth: {arr_depth.shape} vs WSE1 {arr1.shape}"
                )

        if arr1.shape != arr2.shape:
            raise QgsProcessingException(
                f"Resampling produced different grid sizes: WSE1 {arr1.shape} vs WSE2 {arr2.shape}"
            )

        feedback.pushInfo("Calculating differences...")

        # Masks
        valid1 = (arr1 != nd1) if nd1 is not None else ~np.isnan(arr1)
        valid2 = (arr2 != nd2) if nd2 is not None else ~np.isnan(arr2)

        both_wet = valid1 & valid2
        was_wet_now_dry = ~valid1 & valid2
        was_dry_now_wet = valid1 & ~valid2

        # Calculate diff
        out_arr = np.full_like(arr1, -99999.0)  # Background nodata

        out_arr[both_wet] = arr1[both_wet] - arr2[both_wet]
        out_arr[was_wet_now_dry] = -9999.0
        out_arr[was_dry_now_wet] = 9999.0

        if arr_depth is not None:
            # Mask out cells where depth < threshold
            valid_depth_pixels = (arr_depth != nd_depth) if nd_depth is not None else ~np.isnan(arr_depth)
            shallow_pixels = valid_depth_pixels & (arr_depth < depth_threshold)
            out_arr[shallow_pixels] = -99999.0

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

        # Add to project with specific name to trigger style
        try:
            layer_name = "WSE_DIFF"
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
                        if (layer_type == "raster" or layer_type == "both") and any(
                            fnmatch.fnmatch(layer_name.upper(), p.strip().upper())
                            for p in patterns
                            if p
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
