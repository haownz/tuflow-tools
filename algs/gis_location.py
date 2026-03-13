# -*- coding: utf-8 -*-
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsGeometry,
    QgsFeature,
    QgsFields,
    QgsWkbTypes,
    QgsPointXY,
    QgsProject
)
from qgis.PyQt.QtCore import QVariant

class GISLocationAlgorithm(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    MODE = 'MODE'
    DISTANCE = 'DISTANCE'
    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return GISLocationAlgorithm()

    def name(self):
        return 'gis_location'

    def displayName(self):
        return 'GIS Location'

    def group(self):
        return '1 - Input Processing'

    def groupId(self):
        return 'input_processing'

    def shortHelpString(self):
        return (
            "Generates a model location extent (polygon) from a vector or raster layer.\n\n"
            "Modes:\n"
            "1. Layer Extent Box: Axis-aligned bounding box buffered by distance.\n"
            "2. Oriented Minimum Bounding Box: Minimum area rotated rectangle buffered by distance.\n\n"
            "Output follows TUFLOW rule: 4 sides digitized clockwise, with the 2nd vertex at the Top-Left corner."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT,
                "Input Layer (Vector or Raster)",
                types=[QgsProcessing.TypeVector, QgsProcessing.TypeRaster]
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                "Generation Mode",
                options=["Layer Extent Box", "Oriented Minimum Bounding Box"],
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DISTANCE,
                "Buffer Distance (meters)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=50.0
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "2d_loc GIS layer",
                type=QgsProcessing.TypeVectorPolygon
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsLayer(parameters, self.INPUT, context)
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        distance = self.parameterAsDouble(parameters, self.DISTANCE, context)

        if not layer:
            raise QgsProcessingException("Invalid input layer.")

        base_geom = None

        # 1. Get Base Geometry
        if isinstance(layer, QgsRasterLayer):
            # Raster extent is always axis-aligned in QGIS provider
            extent = layer.extent()
            base_geom = QgsGeometry.fromRect(extent)
        
        elif isinstance(layer, QgsVectorLayer):
            if mode == 0: # Layer Extent Box
                extent = layer.extent()
                base_geom = QgsGeometry.fromRect(extent)
            else: # Oriented Minimum Bounding Box
                # Calculate combined geometry efficiently using convex hulls
                geoms = []
                iterator = layer.getFeatures()
                for feat in iterator:
                    if feedback.isCanceled():
                        return {}
                    g = feat.geometry()
                    if g and not g.isEmpty():
                        geoms.append(g.convexHull())
                
                if not geoms:
                    raise QgsProcessingException("Input vector layer has no valid geometries.")
                
                combined = QgsGeometry.unaryUnion(geoms)
                if combined.isEmpty():
                    combined = QgsGeometry.fromRect(layer.extent())
                
                base_geom = combined.orientedMinimumBoundingBox()
                if isinstance(base_geom, tuple):
                    base_geom = base_geom[0]

        if not base_geom:
            raise QgsProcessingException("Failed to generate base geometry.")

        # 2. Buffer/Offset
        # Use Miter join (2) to preserve rectangle corners
        buffered_geom = base_geom.buffer(distance, 5, QgsGeometry.EndCapStyle.Square, QgsGeometry.JoinStyle.Miter, 2.0)
        
        if buffered_geom.isEmpty():
            raise QgsProcessingException("Buffering failed.")

        # 3. Process Vertices (Clockwise, Start at Bottom-Left)
        # Extract the single ring
        if buffered_geom.isMultipart():
            ring = buffered_geom.asMultiPolygon()[0][0]
        else:
            ring = buffered_geom.asPolygon()[0]

        # Remove closure for processing
        if ring[0] == ring[-1]:
            ring.pop()

        # Ensure Clockwise (Sum of edges (x2-x1)(y2+y1) > 0 for Clockwise)
        area = sum((ring[(i + 1) % len(ring)].x() - ring[i].x()) * (ring[(i + 1) % len(ring)].y() + ring[i].y()) for i in range(len(ring)))
        if area < 0:
            ring.reverse()

        # Find Bottom-Left (Min Y, then Min X) to set as Vertex 1
        # This ensures Vertex 2 is Top-Left (next clockwise)
        min_idx = min(range(len(ring)), key=lambda i: (ring[i].y(), ring[i].x()))
        
        # Rotate ring so min_idx is at 0
        new_ring = ring[min_idx:] + ring[:min_idx]
        
        # Add closure
        new_ring.append(new_ring[0])
        final_geom = QgsGeometry.fromPolygonXY([new_ring])

        # 4. Output
        (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, QgsFields(), QgsWkbTypes.Polygon, layer.crs())
        f = QgsFeature()
        f.setGeometry(final_geom)
        sink.addFeatures([f])
        
        return {self.OUTPUT: dest_id}