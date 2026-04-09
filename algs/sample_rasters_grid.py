# -*- coding: utf-8 -*-
"""
Sample raster Z values at evenly distributed points by space interval (dx = dy).

- Optionally set clip extent from current view extent, layer extent or polygon layer.
- If not set, falls back to the rasters' maximum envelope boundary.
- Supports multiple rasters with "last valid wins".
- Outputs NEW PointZ layer with attributes.
"""

import math
import csv
import os
from typing import Iterable, List, Optional, Tuple

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsApplication,
    QgsVectorLayer,
    QgsProject,
    QgsRectangle,
)


class SampleRastersGridAlgorithm(QgsProcessingAlgorithm):
    # Parameter and output keys
    RASTERS = "RASTERS"
    SPACING = "SPACING"
    EXTENT = "EXTENT"
    CLIP_POLY = "CLIP_POLY"
    BAND = "BAND"
    SEPARATE_FIELDS = "SEPARATE_FIELDS"
    INCLUDE_SRC_NAME = "INCLUDE_SRC_NAME"
    FILTER_RANGE = "FILTER_RANGE"
    FILTER_MIN = "FILTER_MIN"
    FILTER_MAX = "FILTER_MAX"
    BATCH_SIZE = "BATCH_SIZE"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.RASTERS,
                "Raster layer(s) to sample — order matters: last valid wins",
                layerType=QgsProcessing.TypeRaster,
                optional=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SPACING,
                "Point Spacing (x = y)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=50.0,
                minValue=0.1,
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT,
                "Processing extent (optional, fallback to rasters' bounding box)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.CLIP_POLY,
                "Clip to polygon layer (optional)",
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BAND,
                "Raster band (applies to all rasters)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SEPARATE_FIELDS,
                "Create separate fields for each raster (otherwise merge with last valid wins)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_SRC_NAME,
                "Include 'z_src' (raster name)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.FILTER_RANGE,
                "Drop points within a value range (e.g. wet/dry thresholds)",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FILTER_MIN,
                "Drop minimum value",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=-0.005,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FILTER_MAX,
                "Drop maximum value",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.005,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BATCH_SIZE,
                "Batch size (features per write)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1000,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Sampled grid points (PointZ)",
                type=QgsProcessing.TypeVectorPoint,
            )
        )

    def name(self):
        return "sample_rasters_on_grid"

    def displayName(self):
        return "Sample Rasters on Grid"

    def shortHelpString(self):
        return (
            "Samples raster Z values at evenly distributed points based on a given spacing (dx = dy).\n\n"
            "- Extent: can be set from current view extent, layer extent. Falls back to rasters' envelope.\n"
            "- Clip Polygon: optionally provide a polygon layer to only sample inside it.\n"
            "- Multiple rasters: choose merge pattern ('last valid wins') or separate pattern (individual fields)\n"
            "- Global band selector.\n"
            "- Outputs NEW PointZ layer with attributes based on sampling pattern\n"
        )

    def group(self):
        return "2 - Result Analysis"

    def groupId(self):
        return "result_analysis"

    def createInstance(self):
        return SampleRastersGridAlgorithm()

    def _build_transforms(
        self,
        source_crs,
        rasters: Iterable[QgsRasterLayer],
        context: QgsProcessingContext,
    ) -> List[Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]]:
        xforms = []
        tctx = context.transformContext()
        for r in rasters:
            if not isinstance(r, QgsRasterLayer) or not r.isValid():
                xforms.append((None, None))
                continue
            r_crs = r.crs()
            if not r_crs.isValid():
                xforms.append((None, None))
                continue
            if r_crs == source_crs:
                xforms.append((r, None))
            else:
                xforms.append((r, QgsCoordinateTransform(source_crs, r_crs, tctx)))
        return xforms

    def _is_nodata(self, provider, band: int, v: float) -> bool:
        try:
            if provider and provider.sourceHasNoDataValue(band):
                nd = provider.sourceNoDataValue(band)
                if nd is not None:
                    try:
                        if float(v) == float(nd):
                            return True
                    except Exception:
                        pass
            get_user_nd = getattr(provider, "userNoDataValues", None)
            if callable(get_user_nd):
                ranges = get_user_nd(band)
                for rr in ranges or []:
                    mn = getattr(rr, "min", None)
                    mx = getattr(rr, "max", None)
                    if mn is not None and mx is not None and mn <= v <= mx:
                        return True
        except Exception:
            pass
        return False

    def _is_valid_value(
        self,
        val,
        provider,
        band: int,
        filter_range: bool = False,
        filter_min: float = 0.0,
        filter_max: float = 0.0,
    ) -> bool:
        try:
            if val is None:
                return False
            v = float(val)
            if math.isnan(v):
                return False
            if v >= 9999 or v <= -9999:
                return False
            if self._is_nodata(provider, band, v):
                return False
            if filter_range and filter_min <= v <= filter_max:
                return False
            return True
        except Exception:
            return False

    def _sample_z_last_valid_wins(
        self,
        pt_xy: QgsPointXY,
        rasters_xforms: List[
            Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]
        ],
        band: int,
        filter_range: bool = False,
        filter_min: float = 0.0,
        filter_max: float = 0.0,
    ) -> Tuple[Optional[float], Optional[str]]:
        z = None
        z_src = None
        for r, xform in rasters_xforms:
            if r is None:
                continue
            provider = r.dataProvider()
            try:
                pt_r = pt_xy if xform is None else xform.transform(pt_xy)
                res = provider.sample(pt_r, band)
                if isinstance(res, tuple):
                    val, ok = res
                    if not ok:
                        continue
                else:
                    val = res

                if self._is_valid_value(
                    val,
                    provider,
                    band=band,
                    filter_range=filter_range,
                    filter_min=filter_min,
                    filter_max=filter_max,
                ):
                    z = float(val)
                    z_src = r.name()
            except Exception:
                continue
        return z, z_src

    def _sample_z_separate_fields(
        self,
        pt_xy: QgsPointXY,
        rasters_xforms: List[
            Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]
        ],
        band: int,
        filter_range: bool = False,
        filter_min: float = 0.0,
        filter_max: float = 0.0,
    ) -> List[Optional[float]]:
        results = []
        for r, xform in rasters_xforms:
            if r is None:
                results.append(None)
                continue
            provider = r.dataProvider()
            try:
                pt_r = pt_xy if xform is None else xform.transform(pt_xy)
                res = provider.sample(pt_r, band)
                if isinstance(res, tuple):
                    val, ok = res
                    if not ok:
                        results.append(None)
                        continue
                else:
                    val = res

                if self._is_valid_value(
                    val,
                    provider,
                    band=band,
                    filter_range=filter_range,
                    filter_min=filter_min,
                    filter_max=filter_max,
                ):
                    results.append(float(val))
                else:
                    results.append(None)
            except Exception:
                results.append(None)
        return results

    def processAlgorithm(
        self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ):
        raster_layers = self.parameterAsLayerList(parameters, self.RASTERS, context)
        if not raster_layers:
            raise QgsProcessingException(
                "Please provide at least one raster layer to sample."
            )

        spacing = float(self.parameterAsDouble(parameters, self.SPACING, context))
        band = int(self.parameterAsInt(parameters, self.BAND, context))
        separate_fields = self.parameterAsBool(
            parameters, self.SEPARATE_FIELDS, context
        )
        include_src_name = self.parameterAsBool(
            parameters, self.INCLUDE_SRC_NAME, context
        )
        filter_range = self.parameterAsBool(parameters, self.FILTER_RANGE, context)
        filter_min = float(self.parameterAsDouble(parameters, self.FILTER_MIN, context))
        filter_max = float(self.parameterAsDouble(parameters, self.FILTER_MAX, context))
        batch_size = max(
            1, int(self.parameterAsInt(parameters, self.BATCH_SIZE, context))
        )
        clip_source = self.parameterAsSource(parameters, self.CLIP_POLY, context)

        # Determine processing CRS and Extent
        crs = None
        extent = None

        if clip_source:
            crs = clip_source.sourceCrs()

        # Try to get extent from parameters
        if parameters.get(self.EXTENT):
            # Evaluate extent
            extent_geom = self.parameterAsExtentGeometry(
                parameters, self.EXTENT, context
            )
            if extent_geom and not extent_geom.isEmpty():
                # Extent geometry contains the CRS
                if crs is None:
                    crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
                # Need to get rectangle in processing crs
                if (
                    crs
                    and crs.isValid()
                    and crs
                    != self.parameterAsExtentCrs(parameters, self.EXTENT, context)
                ):
                    xform = QgsCoordinateTransform(
                        self.parameterAsExtentCrs(parameters, self.EXTENT, context),
                        crs,
                        context.transformContext(),
                    )
                    extent_geom.transform(xform)
                extent = extent_geom.boundingBox()

        # Fallback to raster extents
        if not extent or extent.isEmpty():
            if not crs or not crs.isValid():
                for r in raster_layers:
                    if r.crs().isValid():
                        crs = r.crs()
                        break

            if not crs or not crs.isValid():
                raise QgsProcessingException(
                    "Could not determine a valid CRS from inputs."
                )

            # Calculate combined extent of all rasters in target crs
            xform_ctx = context.transformContext()
            combined_extent = QgsRectangle()
            combined_extent.setMinimal()

            for r in raster_layers:
                r_crs = r.crs()
                r_ext = r.extent()
                if not r_crs.isValid() or r_ext.isEmpty():
                    continue
                if r_crs != crs:
                    xform = QgsCoordinateTransform(r_crs, crs, xform_ctx)
                    try:
                        r_ext = xform.transformBoundingBox(r_ext)
                    except Exception:
                        continue
                combined_extent.combineExtentWith(r_ext)

            if combined_extent.isEmpty():
                raise QgsProcessingException(
                    "Could not calculate fallback bounding box from rasters."
                )
            extent = combined_extent

        # If crs is still none, get it from first valid raster
        if not crs or not crs.isValid():
            for r in raster_layers:
                if r.crs().isValid():
                    crs = r.crs()
                    break

        # Collect clip geometries if poly layer is given
        clip_geometries = []
        if clip_source:
            for feat in clip_source.getFeatures():
                g = feat.geometry()
                if g and not g.isEmpty():
                    clip_geometries.append(g)

        # Precompute transforms
        rasters_xforms = self._build_transforms(crs, raster_layers, context)

        # Calculate grid dimension
        nx = int(math.ceil(extent.width() / spacing)) + 1
        ny = int(math.ceil(extent.height() / spacing)) + 1
        total_points = nx * ny

        if total_points <= 0:
            raise QgsProcessingException(
                "Invalid extent or spacing resulted in 0 points."
            )

        feedback.pushInfo(f"Generating grid: {nx} x {ny} = {total_points} points.")

        if separate_fields:
            import tempfile

            feedback.pushInfo("Generating temporary CSV file in memory...")
            csv_headers = ["ID"]
            valid_rasters = []
            for r in raster_layers:
                if isinstance(r, QgsRasterLayer) and r.isValid():
                    csv_headers.append(r.name())
                    valid_rasters.append(r)

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False, encoding="utf-8"
            ) as temp_file:
                csv_path = temp_file.name
                writer = csv.writer(temp_file)
                writer.writerow(csv_headers)

                processed = 0
                written = 0
                pt_id = 1

                for i in range(nx):
                    if feedback.isCanceled():
                        break
                    x = extent.xMinimum() + i * spacing
                    for j in range(ny):
                        y = extent.yMinimum() + j * spacing

                        pt_xy = QgsPointXY(x, y)

                        if clip_geometries:
                            geom_pt = QgsGeometry.fromPointXY(pt_xy)
                            if not any(
                                geom_pt.intersects(cg) for cg in clip_geometries
                            ):
                                processed += 1
                                continue

                        z_values = self._sample_z_separate_fields(
                            pt_xy,
                            rasters_xforms,
                            band=band,
                            filter_range=filter_range,
                            filter_min=filter_min,
                            filter_max=filter_max,
                        )

                        if all(z is None for z in z_values):
                            processed += 1
                            continue

                        row = [pt_id] + z_values
                        writer.writerow(row)
                        written += 1
                        pt_id += 1
                        processed += 1

                        if processed % batch_size == 0:
                            feedback.setProgress(int(100.0 * processed / total_points))

            csv_uri = f"file:///{csv_path.replace(os.sep, '/')}?delimiter=,"
            csv_layer = QgsVectorLayer(csv_uri, "Sampled_Values", "delimitedtext")
            if csv_layer.isValid():
                QgsProject.instance().addMapLayer(csv_layer)
                feedback.pushInfo(
                    "Temporary CSV loaded into QGIS as 'Sampled_Values' layer"
                )
            else:
                feedback.pushWarning(
                    f"Could not load temporary CSV into QGIS. URI: {csv_uri}"
                )

            feedback.pushInfo(
                f"Done. Evaluated {processed} points; loaded {written} records from temporary CSV."
            )
            return {self.OUTPUT: csv_path}

        # Non-separate fields mode
        fields = QgsFields()
        fields.append(QgsField("ID", QVariant.LongLong))
        fields.append(QgsField("Z", QVariant.Double))
        if include_src_name:
            fields.append(QgsField("z_src", QVariant.String))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.PointZ, crs
        )
        if sink is None:
            raise QgsProcessingException("Could not create output sink.")

        processed = 0
        written = 0
        pt_id = 1
        batch = []

        for i in range(nx):
            if feedback.isCanceled():
                break
            x = extent.xMinimum() + i * spacing
            for j in range(ny):
                y = extent.yMinimum() + j * spacing

                pt_xy = QgsPointXY(x, y)

                if clip_geometries:
                    geom_pt = QgsGeometry.fromPointXY(pt_xy)
                    if not any(geom_pt.intersects(cg) for cg in clip_geometries):
                        processed += 1
                        continue

                z_val, z_src = self._sample_z_last_valid_wins(
                    pt_xy,
                    rasters_xforms,
                    band=band,
                    filter_range=filter_range,
                    filter_min=filter_min,
                    filter_max=filter_max,
                )

                if z_val is None:
                    processed += 1
                    if processed % batch_size == 0:
                        feedback.setProgress(int(100.0 * processed / total_points))
                    continue

                z_for_geom = float(z_val)

                attrs = [pt_id, float(z_val)]
                if include_src_name:
                    attrs.append(z_src if z_src is not None else None)

                ptz = QgsPoint(pt_xy.x(), pt_xy.y(), z_for_geom)
                out_geom = QgsGeometry.fromPoint(ptz)

                out_f = QgsFeature(fields)
                out_f.setGeometry(out_geom)
                out_f.setAttributes(attrs)
                batch.append(out_f)

                pt_id += 1
                processed += 1

                if len(batch) >= batch_size:
                    sink.addFeatures(batch)
                    written += len(batch)
                    batch.clear()
                    QgsApplication.processEvents()

                if processed % batch_size == 0:
                    feedback.setProgress(int(100.0 * processed / total_points))

        if batch:
            sink.addFeatures(batch)
            written += len(batch)
            batch.clear()

        feedback.pushInfo(
            f"Done. Evaluated {processed} points; wrote {written} features to PointZ layer."
        )
        return {self.OUTPUT: dest_id}
