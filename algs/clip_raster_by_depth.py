# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorLayer,
    QgsRasterLayer,
    QgsProject,
)
from osgeo import gdal
import numpy as np
import os
import shutil
import difflib

class ClipRasterByDepthAlgorithm(QgsProcessingAlgorithm):
    """
    Clips an input raster using a depth raster and a threshold.
    """

    P_INPUT_RASTER = "INPUT_RASTER"
    P_DEPTH_RASTER = "DEPTH_RASTER"
    P_DEPTH_THRESHOLD = "DEPTH_THRESHOLD"
    P_RESTRICT_POLY = "RESTRICT_POLY"
    P_OUTPUT = "OUTPUT"

    def tr(self, message):
        return QCoreApplication.translate("ClipRasterByDepth", message)

    def createInstance(self):
        return ClipRasterByDepthAlgorithm()

    def name(self):
        return "clip_raster_by_depth"

    def displayName(self):
        return self.tr("Clip Raster by Depth")

    def group(self):
        return self.tr("2 - Result Analysis")

    def groupId(self):
        return "result_analysis"

    def shortHelpString(self):
        return self.tr(
            "Clips an input raster by applying a depth threshold.\n\n"
            "Only pixels where the depth raster value is >= the depth threshold will be kept. "
            "Other pixels are set to NoData.\n"
            "If an optional polygon layer is provided, the threshold is only applied inside the polygons. "
            "Areas outside the polygons will remain unchanged from the input raster."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_INPUT_RASTER, self.tr("Input Raster (to be clipped)")
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_DEPTH_RASTER, self.tr("Depth Raster")
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_DEPTH_THRESHOLD,
                self.tr("Depth Threshold (m)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.05,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.P_RESTRICT_POLY,
                self.tr("Polygon Layer to Restrict Clipping"),
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.P_OUTPUT,
                self.tr("Clipped Output"),
                QgsProcessing.TEMPORARY_OUTPUT,
            )
        )

        try:
            from qgis.utils import iface

            active = iface.activeLayer() if iface else None
            if active and isinstance(active, QgsRasterLayer):
                layer_name = active.name().lower()
                if "_d_" in layer_name:
                    p_depth = self.parameterDefinition(self.P_DEPTH_RASTER)
                    if p_depth:
                        p_depth.setDefaultValue(active.id())
                else:
                    p_input = self.parameterDefinition(self.P_INPUT_RASTER)
                    if p_input:
                        p_input.setDefaultValue(active.id())
                        
                    # Try to identify the matched depth layer
                    project = QgsProject.instance()
                    best_match = None
                    best_ratio = 0.0
                    
                    for lyr in project.mapLayers().values():
                        if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                            candidate_name = lyr.name().lower()
                            if "_d_" in candidate_name:
                                ratio = difflib.SequenceMatcher(None, layer_name, candidate_name).ratio()
                                if ratio > best_ratio:
                                    best_ratio = ratio
                                    best_match = lyr
                                    
                    if best_match and best_ratio > 0.5:
                        p_depth = self.parameterDefinition(self.P_DEPTH_RASTER)
                        if p_depth:
                            p_depth.setDefaultValue(best_match.id())
        except Exception:
            pass

    def processAlgorithm(self, parameters, context, feedback):
        input_layer = self.parameterAsRasterLayer(
            parameters, self.P_INPUT_RASTER, context
        )
        depth_layer = self.parameterAsRasterLayer(
            parameters, self.P_DEPTH_RASTER, context
        )
        restrict_poly = self.parameterAsVectorLayer(
            parameters, self.P_RESTRICT_POLY, context
        )
        depth_threshold = self.parameterAsDouble(
            parameters, self.P_DEPTH_THRESHOLD, context
        )
        output_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)

        if input_layer is None or depth_layer is None:
            raise QgsProcessingException(self.tr("Invalid input or depth raster."))

        # Open inputs with GDAL
        input_ds = gdal.Open(input_layer.source())
        depth_ds = gdal.Open(depth_layer.source())

        if input_ds is None or depth_ds is None:
            raise QgsProcessingException(self.tr("Could not open one of the input rasters with GDAL."))

        input_band = input_ds.GetRasterBand(1)
        depth_band = depth_ds.GetRasterBand(1)

        input_nodata = input_band.GetNoDataValue()
        depth_nodata = depth_band.GetNoDataValue()

        # Check if they match in size and geotransform
        input_gt = input_ds.GetGeoTransform()
        depth_gt = depth_ds.GetGeoTransform()
        
        if (input_ds.RasterXSize != depth_ds.RasterXSize or 
            input_ds.RasterYSize != depth_ds.RasterYSize or
            input_gt != depth_gt or
            input_ds.GetProjection() != depth_ds.GetProjection()):
            
            feedback.pushInfo(self.tr("Dimensions, geotransform, or projection do not match. Warping Depth raster to match Input raster..."))
            
            min_x = input_gt[0]
            max_y = input_gt[3]
            max_x = min_x + input_gt[1] * input_ds.RasterXSize
            min_y = max_y + input_gt[5] * input_ds.RasterYSize
            
            warp_opts = gdal.WarpOptions(
                format="VRT",
                width=input_ds.RasterXSize,
                height=input_ds.RasterYSize,
                outputBounds=[min_x, min_y, max_x, max_y],
                dstSRS=input_ds.GetProjection(),
                resampleAlg=gdal.GRA_NearestNeighbour
            )
            
            # Using depth_ds directly to Warp
            depth_ds = gdal.Warp("", depth_ds, options=warp_opts)
            if depth_ds is None:
                raise QgsProcessingException(self.tr("Failed to warp Depth raster to Input raster dimensions."))
            
            depth_band = depth_ds.GetRasterBand(1)
            depth_nodata = depth_band.GetNoDataValue()

        # Create output raster
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            output_path,
            input_ds.RasterXSize,
            input_ds.RasterYSize,
            1,
            input_band.DataType,
            options=["COMPRESS=DEFLATE"]
        )
        
        out_ds.SetGeoTransform(input_gt)
        out_ds.SetProjection(input_ds.GetProjection())
        out_band = out_ds.GetRasterBand(1)
        if input_nodata is not None:
            out_band.SetNoDataValue(input_nodata)
        else:
            # default to -9999 if input has no nodata
            input_nodata = -9999.0
            out_band.SetNoDataValue(input_nodata)

        poly_ds = None
        poly_band = None
        if restrict_poly:
            feedback.pushInfo(self.tr(f"Using restrict polygon layer: {restrict_poly.name()}"))
            
            # Rasterize polygon to match input raster exactly
            mem_driver = gdal.GetDriverByName("MEM")
            poly_ds = mem_driver.Create(
                "",
                input_ds.RasterXSize,
                input_ds.RasterYSize,
                1,
                gdal.GDT_Byte
            )
            poly_ds.SetGeoTransform(input_gt)
            poly_ds.SetProjection(input_ds.GetProjection())
            poly_band = poly_ds.GetRasterBand(1)
            poly_band.Fill(0)
            
            # Need to create OGR dataset from QgsVectorLayer for GDAL rasterize
            from osgeo import ogr
            
            # Handle potential QGIS specific source strings (like delimited text or subset)
            source_parts = restrict_poly.source().split("|")
            v_source = source_parts[0]
            
            ogr_ds = ogr.Open(v_source)
            if ogr_ds:
                layer_name_ogr = None
                for part in source_parts[1:]:
                    if part.startswith("layername="):
                        layer_name_ogr = part.replace("layername=", "")
                        break
                        
                if layer_name_ogr:
                    ogr_layer = ogr_ds.GetLayerByName(layer_name_ogr)
                else:
                    ogr_layer = ogr_ds.GetLayer(0)
                    
                gdal.RasterizeLayer(poly_ds, [1], ogr_layer, burn_values=[1])
            else:
                feedback.reportError(self.tr("Could not open restrict polygon with GDAL/OGR. Restriction will not be applied."))
                poly_ds = None
                poly_band = None

        feedback.setProgressText(self.tr("Processing rasters..."))
        
        # Read in blocks to handle large rasters
        x_size = input_ds.RasterXSize
        y_size = input_ds.RasterYSize
        block_xsize, block_ysize = input_band.GetBlockSize()
        
        # Ensure block size isn't larger than image
        block_xsize = min(block_xsize, x_size)
        block_ysize = min(block_ysize, y_size)
        
        total_blocks = ((x_size + block_xsize - 1) // block_xsize) * ((y_size + block_ysize - 1) // block_ysize)
        blocks_processed = 0
        
        for y in range(0, y_size, block_ysize):
            if feedback.isCanceled():
                break
            
            ys = min(block_ysize, y_size - y)
            for x in range(0, x_size, block_xsize):
                xs = min(block_xsize, x_size - x)
                
                input_data = input_band.ReadAsArray(x, y, xs, ys)
                depth_data = depth_band.ReadAsArray(x, y, xs, ys)
                
                # Masks
                mask = (depth_data >= depth_threshold)
                if depth_nodata is not None:
                    mask &= (depth_data != depth_nodata)
                    
                if poly_band:
                    poly_data = poly_band.ReadAsArray(x, y, xs, ys)
                    # Inside polygon (poly_data == 1): apply depth mask
                    # Outside polygon (poly_data == 0): keep input_data (effectively mask is True)
                    mask = mask | (poly_data == 0)
                    
                # Apply mask: keep input_data where mask is True, else nodata
                out_data = np.where(mask, input_data, input_nodata)
                
                out_band.WriteArray(out_data, x, y)
                
                blocks_processed += 1
                feedback.setProgress(int(100 * blocks_processed / total_blocks))
                
        out_band.FlushCache()
        out_band.ComputeStatistics(False)
        
        out_ds = None
        input_ds = None
        depth_ds = None

        return {self.P_OUTPUT: output_path}
