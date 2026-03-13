
# -*- coding: utf-8 -*-
"""
Sample raster Z values at vertices of input geometries (points, lines, polygons).

- Points: samples at the point.
- Lines/Polygons: samples at each vertex; for polygons, can restrict to exterior ring(s) only.
- Multiple rasters supported with "last valid wins" (later rasters override earlier valid samples).
- Creates a NEW PointZ layer with attributes:
  - Z (double): sampled elevation
  - z_src (optional): raster name that provided the final Z
  - src_fid (optional): source feature ID from "ID" attribute if available
  - v_idx (optional): index of vertex within that feature’s vertex stream
- Optional toggles:
  - BAND (global band index; default 1)
  - POLY_EXTERIOR_ONLY (exterior rings only; default True)
  - INCLUDE_CLOSURE (include polygon closing vertex; default True)
  - DENSIFY_DISTANCE (>=0; add intermediate vertices before sampling; default 0)
  - SKIP_NULL_SAMPLES (drop vertices that cannot be sampled; default False)
  - FALLBACK_Z (geometry Z when sample is NULL and SKIP_NULL_SAMPLES is False; default 0)
  - BATCH_SIZE (features per write; default 1000)

Progress dialog with Cancel; chunked writes for responsiveness.
Optional save to Shapefile/GeoPackage via OUTPUT (overwrites supported).

Author: Hao Wu
Version: 1.1.0
Tested with: QGIS 3.22+ (should work on most QGIS 3.x)
"""

import math
import csv
import os
from typing import Generator, Iterable, List, Optional, Tuple

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
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsFeatureRequest,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsApplication,
    QgsVectorLayer,
    QgsProject,
    QgsMapLayer,
)

class SampleRastersAlgorithm(QgsProcessingAlgorithm):
    """
    Processing algorithm to sample Z values from raster(s) at vertices of input features
    and output a PointZ layer with attributes describing the sample.
    
    Version 1.1.0: Uses actual "ID" attribute from input layer instead of internal feature ID.
    """

    # Parameter and output keys
    INPUT = "INPUT"
    RASTERS = "RASTERS"
    LIST_SAME_GROUP = "LIST_SAME_GROUP"
    BAND = "BAND"
    SEPARATE_FIELDS = "SEPARATE_FIELDS"
    POLY_EXTERIOR_ONLY = "POLY_EXTERIOR_ONLY"
    INCLUDE_CLOSURE = "INCLUDE_CLOSURE"
    DENSIFY_DISTANCE = "DENSIFY_DISTANCE"
    INCLUDE_SRC_NAME = "INCLUDE_SRC_NAME"
    INCLUDE_SRC_FID = "INCLUDE_SRC_FID"
    INCLUDE_V_IDX = "INCLUDE_V_IDX"
    SKIP_NULL_SAMPLES = "SKIP_NULL_SAMPLES"
    FALLBACK_Z = "FALLBACK_Z"
    BATCH_SIZE = "BATCH_SIZE"
    OUTPUT = "OUTPUT"

    # ---- Algorithm definition ----
    def initAlgorithm(self, config=None):
        # Input vector layer (points, lines, polygons)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                "Input vector layer (points/lines/polygons)",
                [QgsProcessing.TypeVectorAnyGeometry],
            )
        )

        # Multiple raster layers; order matters ("last valid wins")
        raster_param = QgsProcessingParameterMultipleLayers(
            self.RASTERS,
            "Raster layer(s) to sample — order matters: last valid wins (leave empty to auto-select from same group)",
            layerType=QgsProcessing.TypeRaster,
            optional=True
        )
        self.addParameter(raster_param)

        # Toggle to list raster layers in same group as input
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LIST_SAME_GROUP,
                "Select rasters in the same group with input vector layer",
                defaultValue=False,
            )
        )

        # Global band selector
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BAND,
                "Raster band (applies to all rasters)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1,
                minValue=1,
            )
        )

        # Sampling pattern toggle
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SEPARATE_FIELDS,
                "Create separate fields for each raster (otherwise merge with last valid wins)",
                defaultValue=False,
            )
        )

        # Polygon exterior-only toggle
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.POLY_EXTERIOR_ONLY,
                "For polygons, sample exterior ring(s) only",
                defaultValue=True,
            )
        )

        # Include the closing vertex of polygon rings (otherwise skip to avoid duplication)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_CLOSURE,
                "For polygon rings, include the closing vertex (duplicates first)",
                defaultValue=True,  # keep original behavior
            )
        )

        # Densify distance (<=0 disables)
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DENSIFY_DISTANCE,
                "Densify by distance (map units); 0 = disabled",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                minValue=0.0,
            )
        )

        # Optional attribute toggles
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_SRC_NAME,
                "Include 'z_src' (raster name)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_SRC_FID,
                "Include 'src_fid' (source feature ID)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_V_IDX,
                "Include 'v_idx' (vertex index within feature)",
                defaultValue=False,
            )
        )

        # Null sample behavior
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SKIP_NULL_SAMPLES,
                "Skip vertices that cannot be sampled (NoData/None/NaN)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.FALLBACK_Z,
                "Fallback Z for geometry when sample is NULL (if not skipping)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        # Batch size
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BATCH_SIZE,
                "Batch size (features per write)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1000,
                minValue=1,
            )
        )

        # Output: FeatureSink (use 'Create Temporary Layer' for in-memory; choose file to save)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Sampled vertices (PointZ)",
                type=QgsProcessing.TypeVectorPoint,
            )
        )

    # ---- Metadata ----
    def name(self):
        # Algorithm ID (unique within provider)
        return "sample_rasters_at_vertices"

    def displayName(self):
        return "Sample Rasters at Vertices"

    def shortHelpString(self):
        return (
            "Samples raster Z values at vertices of input geometries (points/lines/polygons).\n\n"
            "- Points: sample at the point\n"
            "- Lines/Polygons: sample at each vertex (exterior-only optional; ring closure optional)\n"
            "- Multiple rasters: choose merge pattern ('last valid wins') or separate pattern (individual fields)\n"
            "- Global band selector; optional densify-by-distance\n"
            "- Outputs NEW PointZ layer with attributes based on sampling pattern\n"
            "- Progress with cancel; chunked writes for responsiveness\n"
            "- Optional save to Shapefile or GeoPackage by choosing a file for OUTPUT (overwrites supported)\n"
        )

    def group(self) -> str:
        return "1 - Input Processing"

    def groupId(self) -> str:
        return "input_processing"

    def createInstance(self):
        return SampleRastersAlgorithm()

    # ---- Utilities ----
    def _iter_vertices(
        self,
        geom: QgsGeometry,
        exterior_only: bool,
        include_closure: bool,
    ) -> Generator[QgsPointXY, None, None]:
        """
        Yield QgsPointXY for each vertex of the input geometry.
        - Points: the point(s)
        - Lines: all vertices
        - Polygons: outer rings if exterior_only else all rings
        If include_closure is False, skip the closing vertex of polygon rings to avoid duplication.
        """
        if geom is None or geom.isEmpty():
            return

        gtype = QgsWkbTypes.geometryType(geom.wkbType())

        def to_xy(pt) -> QgsPointXY:
            if isinstance(pt, QgsPoint):
                return QgsPointXY(pt.x(), pt.y())
            return QgsPointXY(pt.x(), pt.y())

        if gtype == QgsWkbTypes.PointGeometry:
            if geom.isMultipart():
                for pt in geom.asMultiPoint():
                    yield to_xy(pt)
            else:
                yield to_xy(geom.asPoint())

        elif gtype == QgsWkbTypes.LineGeometry:
            if geom.isMultipart():
                for pl in geom.asMultiPolyline():
                    for pt in pl:
                        yield to_xy(pt)
            else:
                for pt in geom.asPolyline():
                    yield to_xy(pt)

        elif gtype == QgsWkbTypes.PolygonGeometry:
            def iter_ring(ring: List[QgsPoint]):
                rng = ring if include_closure or len(ring) < 2 else ring[:-1]
                for pt in rng:
                    yield to_xy(pt)

            if geom.isMultipart():
                mpoly = geom.asMultiPolygon()
                for poly in mpoly:
                    # poly: list of rings; [0] = exterior, others interior
                    if exterior_only and len(poly) > 0:
                        yield from iter_ring(poly[0])
                    else:
                        for ring in poly:
                            yield from iter_ring(ring)
            else:
                poly = geom.asPolygon()
                if exterior_only and len(poly) > 0:
                    yield from iter_ring(poly[0])
                else:
                    for ring in poly:
                        yield from iter_ring(ring)

        else:
            # Fallback: iterate vertices for unknown/curved types
            for v in geom.vertices():
                yield to_xy(v)

    def _build_transforms(
        self,
        source_crs,
        rasters: Iterable[QgsRasterLayer],
        context: QgsProcessingContext,
    ) -> List[Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]]:
        """
        Precompute coordinate transforms from source CRS to each raster's CRS.
        Returns list of tuples: (rasterLayer or None, transform_or_None)
        """
        xforms: List[Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]] = []
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
        """Return True if v should be treated as NoData for this provider/band."""
        try:
            # GDAL-based providers often use exact sentinels for nodata.
            if provider and provider.sourceHasNoDataValue(band):
                nd = provider.sourceNoDataValue(band)
                if nd is not None:
                    try:
                        if float(v) == float(nd):
                            return True
                    except Exception:
                        pass
            # Try user-defined NoData ranges if available
            get_user_nd = getattr(provider, "userNoDataValues", None)
            if callable(get_user_nd):
                ranges = get_user_nd(band)  # QgsRasterRangeList
                # Each range likely has .min and .max; be defensive
                for rr in ranges or []:
                    mn = getattr(rr, "min", None)
                    mx = getattr(rr, "max", None)
                    if mn is not None and mx is not None and mn <= v <= mx:
                        return True
        except Exception:
            pass
        return False

    def _is_valid_value(self, val, provider, band: int) -> bool:
        """Check whether a sampled value is valid (not None, not NaN, and not NoData)."""
        try:
            if val is None:
                return False
            v = float(val)
            if math.isnan(v):
                return False
            if self._is_nodata(provider, band, v):
                return False
            return True
        except Exception:
            return False

    def _sample_z_last_valid_wins(
        self,
        pt_xy: QgsPointXY,
        rasters_xforms: List[Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]],
        band: int,
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Sample `band` across rasters in order; the last valid value overrides earlier ones.
        Returns (z_value_or_None, source_raster_name_or_None).
        """
        z: Optional[float] = None
        z_src: Optional[str] = None

        for r, xform in rasters_xforms:
            if r is None:
                continue

            provider = r.dataProvider()
            try:
                # transform point to raster CRS if needed
                pt_r = pt_xy if xform is None else xform.transform(pt_xy)
                # sample band
                res = provider.sample(pt_r, band)
                # API may return (value, ok) or value
                if isinstance(res, tuple):
                    val, ok = res
                    if not ok:
                        continue
                else:
                    val = res

                if self._is_valid_value(val, provider, band=band):
                    z = float(val)
                    z_src = r.name()  # last valid wins
            except Exception:
                # Ignore sampling errors and continue to next raster
                continue

        return z, z_src

    def _sample_z_separate_fields(
        self,
        pt_xy: QgsPointXY,
        rasters_xforms: List[Tuple[Optional[QgsRasterLayer], Optional[QgsCoordinateTransform]]],
        band: int,
    ) -> List[Optional[float]]:
        """
        Sample `band` from each raster separately.
        Returns list of z_values (one per raster, None if invalid).
        """
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

                if self._is_valid_value(val, provider, band=band):
                    results.append(float(val))
                else:
                    results.append(None)
            except Exception:
                results.append(None)

        return results

    # ---- Main processing ----
    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("Invalid input vector source.")

        list_same_group = self.parameterAsBool(parameters, self.LIST_SAME_GROUP, context)
        raster_layers = self.parameterAsLayerList(parameters, self.RASTERS, context)
        
        if list_same_group and not raster_layers:
            input_layer = self.parameterAsLayer(parameters, self.INPUT, context)
            if input_layer:
                root = QgsProject.instance().layerTreeRoot()
                input_node = root.findLayer(input_layer.id())
                if input_node:
                    input_group = input_node.parent()
                    if input_group and input_group != root:
                        for child in input_group.children():
                            if hasattr(child, 'layer'):
                                layer = child.layer()
                                if isinstance(layer, QgsRasterLayer) and layer.isValid():
                                    raster_layers.append(layer)
        if not raster_layers:
            raise QgsProcessingException("Please provide at least one raster layer to sample.")

        band = int(self.parameterAsInt(parameters, self.BAND, context))
        exterior_only = self.parameterAsBool(parameters, self.POLY_EXTERIOR_ONLY, context)
        include_closure = self.parameterAsBool(parameters, self.INCLUDE_CLOSURE, context)
        densify_distance = float(self.parameterAsDouble(parameters, self.DENSIFY_DISTANCE, context))
        separate_fields = self.parameterAsBool(parameters, self.SEPARATE_FIELDS, context)
        include_src_name = self.parameterAsBool(parameters, self.INCLUDE_SRC_NAME, context)
        include_src_fid = self.parameterAsBool(parameters, self.INCLUDE_SRC_FID, context)
        include_v_idx = self.parameterAsBool(parameters, self.INCLUDE_V_IDX, context)
        skip_nulls = self.parameterAsBool(parameters, self.SKIP_NULL_SAMPLES, context)
        fallback_z = float(self.parameterAsDouble(parameters, self.FALLBACK_Z, context))
        batch_size = max(1, int(self.parameterAsInt(parameters, self.BATCH_SIZE, context)))

        # Precompute transforms from source CRS to rasters' CRS
        rasters_xforms = self._build_transforms(source.sourceCrs(), raster_layers, context)

        # Optionally densify features on-the-fly.
        def maybe_densify(g: QgsGeometry) -> QgsGeometry:
            if densify_distance > 0:
                try:
                    return g.densifyByDistance(densify_distance)
                except Exception:
                    return g
            return g

        if separate_fields:
            # For separate fields mode, generate temporary CSV in memory and load it
            try:
                import tempfile
                import io
                
                feedback.pushInfo("Generating temporary CSV file in memory...")
                
                # Prepare CSV headers using layer names
                csv_headers = ["ID"]
                valid_rasters = []
                for r in raster_layers:
                    if isinstance(r, QgsRasterLayer) and r.isValid():
                        csv_headers.append(r.name())
                        valid_rasters.append(r)
                
                if include_v_idx:
                    csv_headers.append("v_idx")
                
                feedback.pushInfo(f"CSV headers: {csv_headers}")
                
                # Create temporary file
                with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as temp_file:
                    csv_path = temp_file.name
                    feedback.pushInfo(f"Created temporary CSV file: {csv_path}")
                    
                    writer = csv.writer(temp_file)
                    writer.writerow(csv_headers)
                    
                    processed = 0
                    written = 0
                    
                    for feat in source.getFeatures():
                        if feedback.isCanceled():
                            break
                        
                        geom = feat.geometry()
                        if geom is None or geom.isEmpty():
                            continue
                        
                        geom = maybe_densify(geom)
                        src_fid_val = feat.attribute("ID") if feat.fieldNameIndex("ID") != -1 else feat.id()
                        v_idx = 0
                        
                        for pt_xy in self._iter_vertices(geom, exterior_only, include_closure):
                            if feedback.isCanceled():
                                break
                            
                            # Sample each raster separately
                            z_values = self._sample_z_separate_fields(pt_xy, rasters_xforms, band=band)
                            
                            # Check if all values are None and skip if requested
                            if skip_nulls and all(z is None for z in z_values):
                                processed += 1
                                continue
                            
                            # Build CSV row
                            row = [src_fid_val] + z_values
                            
                            if include_v_idx:
                                row.append(v_idx)
                            
                            writer.writerow(row)
                            written += 1
                            v_idx += 1
                            processed += 1
                
                feedback.pushInfo(f"Temporary CSV written with {written} records")
                
                # Load CSV into QGIS
                csv_uri = f"file:///{csv_path.replace(os.sep, '/')}?delimiter=,"
                csv_layer = QgsVectorLayer(csv_uri, "Sampled_Values", "delimitedtext")
                if csv_layer.isValid():
                    QgsProject.instance().addMapLayer(csv_layer)
                    feedback.pushInfo(f"Temporary CSV loaded into QGIS as 'Sampled_Values' layer")
                else:
                    feedback.pushWarning(f"Could not load temporary CSV into QGIS. URI: {csv_uri}")
                
                feedback.pushInfo(f"Done. Processed {processed} vertex samples; loaded {written} records from temporary CSV.")
                return {self.OUTPUT: csv_path}
                
            except Exception as e:
                raise QgsProcessingException(f"Error saving to CSV: {e}")
        
        # For non-separate fields mode, continue with original point layer creation
        fields = QgsFields()
        fields.append(QgsField("Z", QVariant.Double))
        if include_src_name:
            fields.append(QgsField("z_src", QVariant.String))
        
        if include_src_fid:
            fields.append(QgsField("src_fid", QVariant.LongLong))
        if include_v_idx:
            fields.append(QgsField("v_idx", QVariant.Int))

        # Create sink as PointZ with same CRS as input
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.PointZ,
            source.sourceCrs(),
        )
        if sink is None:
            raise QgsProcessingException("Could not create output sink.")

        # Precompute transforms from source CRS to rasters' CRS
        rasters_xforms = self._build_transforms(source.sourceCrs(), raster_layers, context)

        # Optionally densify features on-the-fly.
        def maybe_densify(g: QgsGeometry) -> QgsGeometry:
            if densify_distance > 0:
                try:
                    return g.densifyByDistance(densify_distance)
                except Exception:
                    return g
            return g

        # ---- First pass: count total vertices for progress ----
        total_vertices = 0
        for feat in source.getFeatures(QgsFeatureRequest().setNoAttributes()):
            if feedback.isCanceled():
                break
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            dgeom = maybe_densify(geom)
            for _ in self._iter_vertices(dgeom, exterior_only, include_closure):
                total_vertices += 1

        if total_vertices == 0:
            feedback.pushInfo("No vertices found in the input according to the current settings.")
            return {self.OUTPUT: dest_id}

        # ---- Second pass: sample and create point features (non-separate fields mode only) ----
        processed = 0
        written = 0
        batch: List[QgsFeature] = []

        for feat in source.getFeatures():
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue

            geom = maybe_densify(geom)
            src_fid_val = feat.attribute("ID") if feat.fieldNameIndex("ID") != -1 else feat.id()
            v_idx = 0

            for pt_xy in self._iter_vertices(geom, exterior_only, include_closure):
                if feedback.isCanceled():
                    break

                # Merge pattern ("last valid wins")
                z_val, z_src = self._sample_z_last_valid_wins(pt_xy, rasters_xforms, band=band)
                
                if z_val is None and skip_nulls:
                    processed += 1
                    feedback.setProgress(int(100.0 * processed / total_vertices))
                    continue
                
                z_for_geom = float(z_val) if (z_val is not None and not math.isnan(float(z_val))) else float(fallback_z)
                
                attrs = [float(z_val) if z_val is not None else None]
                if include_src_name:
                    attrs.append(z_src if z_src is not None else None)

                # Build PointZ geometry
                ptz = QgsPoint(pt_xy.x(), pt_xy.y(), z_for_geom)
                out_geom = QgsGeometry.fromPoint(ptz)

                out_f = QgsFeature(fields)
                out_f.setGeometry(out_geom)

                # Add common attributes
                if include_src_fid:
                    attrs.append(src_fid_val)
                if include_v_idx:
                    attrs.append(v_idx)

                out_f.setAttributes(attrs)
                batch.append(out_f)
                v_idx += 1
                processed += 1

                # Flush in chunks to keep responsive
                if len(batch) >= batch_size:
                    sink.addFeatures(batch)
                    written += len(batch)
                    batch.clear()
                    QgsApplication.processEvents()  # keep UI responsive

                # Update progress
                feedback.setProgress(int(100.0 * processed / total_vertices))

        # Flush any remaining features
        if batch:
            sink.addFeatures(batch)
            written += len(batch)
            batch.clear()

        feedback.pushInfo(f"Done. Processed {processed} vertex samples; wrote {written} features to PointZ layer.")
        if processed < total_vertices:
            feedback.pushWarning(
                f"Processing was canceled early. Output contains {written} of {total_vertices} vertices."
            )

        return {self.OUTPUT: dest_id}
