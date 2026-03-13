# -*- coding: utf-8 -*-
import gc
from qgis.core import (QgsProcessingAlgorithm, QgsApplication, QgsProject,
                       QgsRasterLayer, QgsVectorLayer, Qgis)
from qgis.utils import iface
from qgis.PyQt.QtWidgets import QApplication
try:
    from osgeo import gdal
except ImportError:
    gdal = None

class ClearMemoryAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm to clear QGIS memory caches, refresh GDAL cache, and trigger Python GC.
    Helps in releasing file locks on Windows.
    """
    
    def createInstance(self):
        return ClearMemoryAlgorithm()

    def name(self):
        return 'clear_memory'

    def displayName(self):
        return 'Clear Memory'

    def group(self):
        return '0 - Configuration'

    def groupId(self):
        return 'configuration'

    def shortHelpString(self):
        return (
            "<h3>QGIS Memory & File Lock Cleanup</h3>"
            "<p>This tool performs the following actions to free up memory and release file locks:</p>"
            "<ul>"
            "<li><b>GDAL Cache:</b> Flushes the GDAL raster cache.</li>"
            "<li><b>Layer Connections:</b> Reloads data providers for raster and vector layers.</li>"
            "<li><b>Garbage Collection:</b> Triggers Python's garbage collection mechanism.</li>"
            "<li><b>QGIS Caches:</b> Clears internal QGIS image and SVG caches.</li>"
            "</ul>"
            "<p>Useful when files are locked by QGIS and cannot be modified or deleted externally.</p>"
        )

    def initAlgorithm(self, config=None):
        pass

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def processAlgorithm(self, parameters, context, feedback):
        # 1. Trigger Python Garbage Collection
        feedback.pushInfo("Step 1/6: Running Python Garbage Collection...")
        collected = gc.collect()
        feedback.pushInfo(f"  - Collected {collected} objects.")

        # 2. Clear QGIS Internal Caches
        feedback.pushInfo("Step 2/6: Clearing QGIS internal caches...")
        try:
            if QgsApplication.imageCache():
                QgsApplication.imageCache().clear()
        except AttributeError:
            pass

        try:
            if QgsApplication.svgCache():
                QgsApplication.svgCache().clear()
        except AttributeError:
            pass
        
        # 3. Refresh GDAL Cache
        feedback.pushInfo("Step 3/6: Flushing GDAL cache...")
        if gdal:
            # Flush GDAL cache by setting cache max to 0 and back
            old_max = gdal.GetCacheMax()
            gdal.SetCacheMax(0)
            gdal.SetCacheMax(old_max)
            feedback.pushInfo(f"  - GDAL cache flushed (Max size restored to {old_max}).")
        else:
            feedback.pushInfo("  - GDAL python bindings not available.")

        # 4. Reset Layer Connections (Raster & Vector)
        feedback.pushInfo("Step 4/6: Resetting layer connections...")
        project = QgsProject.instance()
        layers = project.mapLayers().values()
        for layer in layers:
            if isinstance(layer, (QgsRasterLayer, QgsVectorLayer)) and layer.isValid():
                layer.dataProvider().reloadData()
                layer.triggerRepaint()

        # 5. Clear Undo Stack and Clipboard
        feedback.pushInfo("Step 5/6: Clearing Undo Stack and Clipboard...")
        try:
            if project.undoStack():
                project.undoStack().clear()
                feedback.pushInfo("  - Undo stack cleared.")
        except Exception:
            pass
            
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.clear()
                feedback.pushInfo("  - Clipboard cleared.")
        except Exception:
            pass

        # 6. Refresh Browser Connections
        feedback.pushInfo("Step 6/6: Refreshing browser connections...")
        try:
            iface.reloadConnections()
        except Exception:
            pass

        # Final GC run
        gc.collect()
        
        feedback.pushInfo("Cleanup complete.")
        feedback.pushInfo("IMPORTANT: If file remains locked, collapse the folder in the QGIS Browser Panel.")
        iface.messageBar().pushMessage("Success", "Memory cleared. If lock persists, collapse folder in Browser Panel.", Qgis.Success, 5)
        return {}