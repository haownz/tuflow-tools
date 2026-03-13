# -*- coding: utf-8 -*-
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterVectorLayer, QgsProcessingParameterBoolean, QgsProcessingParameterString,
    QgsRasterLayer, QgsProject, QgsMapLayer
)
from qgis.utils import iface
from .po_common import (guess_selected_raster, derive_poline_path_from_raster, load_vector_with_fallback, clone_vector)
import os

class POFilterZoomAlgorithm(QgsProcessingAlgorithm):
    P_RASTER="RASTER"; P_VECTOR="VECTOR"; P_FILTERS="APPLY_FILTERS"; P_REMOVE="REMOVE_ORIG"
    P_POLINE_SUFFIX="POLINE_SUFFIX"

    ALG_ID = "po_filter_zoom"

    def name(self): return self.ALG_ID
    def displayName(self): return "Filter Zoom"
    def group(self): return "PO tools"
    def groupId(self): return "po_tools"


    def shortHelpString(self):
        return ("Creates two views '<raster> ov' and '<raster> zo'. If OV/ZO fields exist, "
                "applies filters 'OV'=1 and 'ZO'=1 respectively.")

    def initAlgorithm(self, config=None):
        # Prefer the Layers panel selection (more reliable than activeLayer)
        sel = None
        try:
            view = iface.layerTreeView()
            if hasattr(view, "selectedLayers"):
                sels = view.selectedLayers()
                if sels:
                    sel = sels[0]
        except Exception:
            sel = iface.activeLayer() if hasattr(iface, "activeLayer") else None

        # build default layer id strings
        r_default = None
        v_default = None

        # guess_selected_raster() may return a layer object or an id string
        try:
            guessed = guess_selected_raster()
            if guessed:
                if hasattr(guessed, "id"):
                    r_default = guessed.id()
                elif isinstance(guessed, str):
                    r_default = guessed
        except Exception:
            r_default = None

        if sel is not None:
            try:
                if sel.type() == QgsMapLayer.RasterLayer:
                    r_default = sel.id()
                elif sel.type() == QgsMapLayer.VectorLayer:
                    v_default = sel.id()
            except Exception:
                # duck-typing fallback
                if hasattr(sel, "bandCount"):
                    r_default = sel.id()
                elif hasattr(sel, "fields"):
                    v_default = sel.id()

        # ensure defaults are id strings (QgsProcessing expects ids)
        if hasattr(r_default, "id"):
            r_default = r_default.id()
        if hasattr(v_default, "id"):
            v_default = v_default.id()

        # make raster optional; if a vector is selected it will override raster
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.P_RASTER,
            "Raster (optional; used for naming; defaults to selected)",
            optional=True,
            defaultValue=r_default
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_VECTOR,
            "PO line vector (optional; derived from raster if empty)",
            optional=True,
            defaultValue=v_default
        ))
        # customizable suffix used to recognise/strip 'poline' style suffixes from vector names
        self.addParameter(QgsProcessingParameterString(
            self.P_POLINE_SUFFIX,
            "PO line name suffix to strip (default: 'poline')",
            defaultValue="poline"
        ))
        self.addParameter(QgsProcessingParameterBoolean(self.P_FILTERS, "Apply filters ('OV' = 1 / 'ZO' = 1)", defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.P_REMOVE, "Remove original PO line after duplicate", defaultValue=False))

    def processAlgorithm(self, parameters, context, feedback):
        # read provided parameter values (may be None)
        r = self.parameterAsRasterLayer(parameters, self.P_RASTER, context)
        v = self.parameterAsVectorLayer(parameters, self.P_VECTOR, context)

        # prefer the Layers panel selection (vector selection overrides raster)
        sel = None
        try:
            view = iface.layerTreeView()
            if hasattr(view, "selectedLayers"):
                sels = view.selectedLayers()
                if sels:
                    sel = sels[0]
        except Exception:
            sel = iface.activeLayer() if hasattr(iface, "activeLayer") else None

        if sel is not None:
            # if a vector is selected in the Layers panel, use it as PO line (overrides raster)
            if v is None and hasattr(sel, "fields"):
                v = sel
            # otherwise if a raster is selected and no raster param given, use it
            if r is None and hasattr(sel, "bandCount"):
                r = sel

        # if raster still not set, try the helper guess_selected_raster()
        if r is None:
            guessed = guess_selected_raster()
            if guessed:
                if hasattr(guessed, "id"):
                    r = guessed
                elif isinstance(guessed, str):
                    # guessed may be a layer id string
                    r = QgsProject.instance().mapLayer(guessed)

        # if no PO line vector supplied/selected, derive it from raster (r must exist)
        if v is None:
            if not r or not isinstance(r, QgsRasterLayer):
                raise QgsProcessingException("PO line vector is required or a raster must be available to derive it.")
            src = (r.source() or "").splitlines()[0]
            if not os.path.exists(src):
                raise QgsProcessingException("Selected raster is not a local file: {}".format(src))
            po = derive_poline_path_from_raster(src)
            if not os.path.exists(po):
                raise QgsProcessingException("PO line shapefile not found: {}".format(po))
            v = load_vector_with_fallback(po, "{} poline".format(r.name().strip()))
            if not v:
                raise QgsProcessingException("Failed to open PO line: {}".format(po))
            QgsProject.instance().addMapLayer(v)

        apply_filters = self.parameterAsBoolean(parameters, self.P_FILTERS, context)
        remove_orig = self.parameterAsBoolean(parameters, self.P_REMOVE, context)

        # --- WAIT: ensure the vector/provider is not being edited by another process ---
        from qgis.PyQt.QtCore import QCoreApplication
        import time

        # give the GUI a chance to finish any pending commits
        QCoreApplication.processEvents()
        timeout = 30.0
        deadline = time.time() + timeout
        while True:
            try:
                if not v.isEditable():
                    break
            except Exception:
                # if layer invalid or gone, stop waiting
                break
            QCoreApplication.processEvents()
            if time.time() > deadline:
                raise QgsProcessingException("Timeout waiting for layer edits to finish before duplicating.")
            time.sleep(0.05)

        # small extra pause to ensure provider flush
        time.sleep(0.05)
        try:
            prov = v.dataProvider()
            if hasattr(prov, "forceReload"):
                prov.forceReload()
        except Exception:
            pass
        try:
            if hasattr(v, "reload"):
                v.reload()
        except Exception:
            pass

        # determine base name: prefer raster name, else vector name; strip trailing " poline"
        base_name = None
        try:
            base_name = r.name().strip() if isinstance(r, QgsRasterLayer) else None
        except Exception:
            base_name = None
        if not base_name:
            try:
                base_name = v.name().strip()
            except Exception:
                base_name = None
        if base_name and base_name.lower().endswith(" poline"):
            base_name = base_name[:-7].rstrip()
        if not base_name:
            base_name = "layer"

        ov = clone_vector(v, "{} ov".format(base_name))
        zo = clone_vector(v, "{} zo".format(base_name))
        prj = QgsProject.instance()
        prj.addMapLayer(ov)
        prj.addMapLayer(zo)

        flds = [f.name() for f in v.fields()]
        if apply_filters:
            if "OV" in flds:
                ov.setSubsetString('"OV" = 1')
            else:
                feedback.reportError('Field "OV" not found; OV filter not applied.', fatal=False)
            if "ZO" in flds:
                zo.setSubsetString('"ZO" = 1')
            else:
                feedback.reportError('Field "ZO" not found; ZO filter not applied.', fatal=False)

        if remove_orig:
            prj.removeMapLayer(v.id())

        feedback.pushInfo("Created:\n - {} (filter: {})\n - {} (filter: {})".format(
            ov.name(), repr(ov.subsetString()), zo.name(), repr(zo.subsetString())))
        return {}

    def createInstance(self): return POFilterZoomAlgorithm()
