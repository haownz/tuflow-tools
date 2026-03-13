# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString, QgsProcessingParameterFile, QgsProcessingParameterNumber,
    QgsRasterLayer, QgsProject, QgsField, edit, QgsMapLayer
)
from qgis.utils import iface
from .po_common import (
    guess_selected_raster, derive_poline_path_from_raster, load_vector_with_fallback,
    locate_ov_zo_csvs_for_layer, read_row_from_csv
)
import os

class POFlagZoomAlgorithm(QgsProcessingAlgorithm):
    P_RASTER="RASTER"; P_VECTOR="VECTOR"
    P_ID="ID_FIELD"; P_RESULTS="RESULTS_DIR"; P_ZO="ZO_ROW"

    ALG_ID = "po_flag_zoom"

    def name(self): return self.ALG_ID
    def displayName(self): return "Flag Zoom"
    def group(self): return "PO tools"
    def groupId(self): return "po_tools"

    def shortHelpString(self):
        return ("Sets OV/ZO integer flags using PO_Line_OV.csv (row 1) and PO_Line_ZO.csv "
                "(choose ZO row). Raster is required to locate PO line if not provided.")

    def initAlgorithm(self, config=None):
        # determine currently selected layer (dialog is constructed when user opens the tool)
        sel = iface.activeLayer() if hasattr(iface, "activeLayer") else None

        # try to build sensible default layer ids
        r_default = None
        v_default = None

        # guess_selected_raster() may return a layer or an id; prefer that if available
        try:
            guessed = guess_selected_raster()
            if guessed:
                if hasattr(guessed, "id"):
                    r_default = guessed.id()
                else:
                    r_default = guessed
        except Exception:
            r_default = None

        if sel is not None:
            try:
                # preferred enum-based checks
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

        # make absolutely sure defaults are layer id strings (QgsProcessing expects ids)
        if hasattr(r_default, "id"):
            r_default = r_default.id()
        if hasattr(v_default, "id"):
            v_default = v_default.id()

        # raster is optional; default to current raster selection when available
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.P_RASTER,
            "Raster (optional; defaults to selected raster when needed)",
            optional=True,
            defaultValue=r_default
        ))

        # PO line vector defaults to the selected vector layer if not provided;
        # if still not found the PO line will be derived from the raster.
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_VECTOR,
            "PO line vector (optional; defaults to selected vector layer)",
            optional=True,
            defaultValue=v_default
        ))

        self.addParameter(QgsProcessingParameterString(self.P_ID, "ID field", defaultValue="ID"))
        self.addParameter(QgsProcessingParameterFile(self.P_RESULTS, "Results folder (where PO-Line_OV/ZO.csv live)", behavior=QgsProcessingParameterFile.Folder, optional=True))
        self.addParameter(QgsProcessingParameterNumber(self.P_ZO, "ZO row number (1-based)", QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1))

    def processAlgorithm(self, parameters, context, feedback):
        # Try to read provided inputs
        r = self.parameterAsRasterLayer(parameters, self.P_RASTER, context)
        v = self.parameterAsVectorLayer(parameters, self.P_VECTOR, context)

        # If no vector provided, prefer the currently selected layer in QGIS:
        # - if the selected layer is vector -> use it as PO line
        # - if selected layer is raster and no raster supplied -> use it as raster
        sel = iface.activeLayer() if hasattr(iface, "activeLayer") else None
        if v is None and sel is not None:
            try:
                if sel.type() == QgsMapLayer.VectorLayer:
                    v = sel
                elif sel.type() == QgsMapLayer.RasterLayer and r is None:
                    r = sel
            except Exception:
                # Fallback: use duck-typing check for fields() to detect vector
                if hasattr(sel, "fields") and v is None:
                    v = sel
                elif hasattr(sel, "bandCount") and r is None:
                    r = sel

        # If still no PO line vector, we must have a raster to derive it from
        if v is None:
            if not r:
                r = guess_selected_raster()
            if not isinstance(r, QgsRasterLayer):
                raise QgsProcessingException("Raster is required when no PO line vector is provided.")
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

        # If a PO line vector was provided (or selected), it overrides any raster usage
        id_field = self.parameterAsString(parameters, self.P_ID, context)
        if id_field not in v.fields().names():
            raise QgsProcessingException("Field '{}' not found in layer '{}'.".format(id_field, v.name()))

        results_dir = self.parameterAsFile(parameters, self.P_RESULTS, context) or None
        zo_row = self.parameterAsInt(parameters, self.P_ZO, context)

        ov_path, zo_path = locate_ov_zo_csvs_for_layer(v, results_dir)
        if not ov_path and not zo_path:
            raise QgsProcessingException("Could not find PO_Line_OV.csv / PO_Line_ZO.csv.")
        if ov_path and not ov_path.exists(): feedback.reportError("OV CSV not found: {}".format(ov_path), fatal=False)
        if zo_path and not zo_path.exists(): feedback.reportError("ZO CSV not found: {}".format(zo_path), fatal=False)

        ov_ids = read_row_from_csv(ov_path, 0) if ov_path and ov_path.exists() else []
        zo_ids = read_row_from_csv(zo_path, zo_row - 1) if zo_path and zo_path.exists() else []

        # add OV/ZO fields via provider (no edit session) and update attributes via provider changeAttributeValues
        fields = [f.name() for f in v.fields()]
        prov = v.dataProvider()
        attrs_to_add = []
        if "OV" not in fields:
            attrs_to_add.append(QgsField("OV", QVariant.Int))
        if "ZO" not in fields:
            attrs_to_add.append(QgsField("ZO", QVariant.Int))
        if attrs_to_add:
            prov.addAttributes(attrs_to_add)
            v.updateFields()

        ov_idx = v.fields().indexOf("OV")
        zo_idx = v.fields().indexOf("ZO")

        # build mapping { feature_id: {attr_index: value, ...}, ... } and apply in one provider call
        changes = {}
        for feat in v.getFeatures():
            val = str(feat[id_field]).strip() if feat[id_field] is not None else ""
            ch = {}
            if ov_idx != -1:
                ch[ov_idx] = 1 if val in ov_ids else 0
            if zo_idx != -1:
                ch[zo_idx] = 1 if val in zo_ids else 0
            if ch:
                changes[feat.id()] = ch

        updated = 0
        if changes:
            prov.changeAttributeValues(changes)
            updated = len(changes)

        feedback.pushInfo("Updated OV/ZO flags on {} features (ZO row={}).".format(updated, zo_row))
        return {}

    def createInstance(self): return POFlagZoomAlgorithm()
