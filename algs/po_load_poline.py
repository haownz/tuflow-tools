# -*- coding: utf-8 -*-
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterString,
    QgsRasterLayer, QgsProject
)
from qgis.PyQt.QtCore import QSettings
from .po_common import guess_selected_raster, derive_poline_path_from_raster, load_vector_with_fallback
import os
import re

SETTINGS_KEY_SUFFIX = "po/suffix_pattern"


class LoadPoLineAlgorithm(QgsProcessingAlgorithm):
    P_RASTER = "RASTER"
    P_SUFFIX = "SUFFIX"

    ALG_ID = "po_load_poline"

    def name(self): return self.ALG_ID
    def displayName(self): return "Load PO line"
    def group(self): return "PO tools"
    def groupId(self): return "po_tools"

    def shortHelpString(self):
        return ("Derives and loads results/<run>/plot/gis/<base>_PLOT_L.shp "
                "from raster results/<run>/grids/<base><suffix>. \n"
                "Suffix pattern is sticky via QSettings (default: '_d_*.tif').")

    def initAlgorithm(self, config=None):
        # Sticky default suffix
        default_suffix = QSettings().value(SETTINGS_KEY_SUFFIX, "_d_*.tif", type=str)

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.P_RASTER, "Raster (required; defaults to selected raster)",
                optional=False, defaultValue=guess_selected_raster()
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.P_SUFFIX, "Suffix pattern", defaultValue=default_suffix, multiLine=False
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        rlayer = self.parameterAsRasterLayer(parameters, self.P_RASTER, context) or guess_selected_raster()
        if not isinstance(rlayer, QgsRasterLayer):
            raise QgsProcessingException("Raster is required. Select a raster layer and retry.")

        src = (rlayer.source() or "").splitlines()[0]
        if not os.path.exists(src):
            raise QgsProcessingException("Selected raster is not a local file: {}".format(src))

        suffix = self.parameterAsString(parameters, self.P_SUFFIX, context) or "_d_*.tif"
        # Persist for next runs (sticky)
        QSettings().setValue(SETTINGS_KEY_SUFFIX, suffix)

        # Prefer shared helper; if it doesn't accept suffix (TypeError), use local fallback
        try:
            po = derive_poline_path_from_raster(src, suffix)  # type: ignore
        except TypeError:
            po = self._derive_poline_path_local(src, suffix)

        # If the helper returned a plain *_PLOT_L.shp, prefer *_PLOT_L_QP.shp when present
        try:
            po_dir = os.path.dirname(po)
            base_noext = os.path.splitext(os.path.basename(po))[0]
            qp_candidate = os.path.join(po_dir, f"{base_noext}_QP.shp")
            if os.path.exists(qp_candidate):
                po = qp_candidate
        except Exception:
            # Ignore any errors here; keep original po
            pass

        if not os.path.exists(po):
            raise QgsProcessingException("PO line shapefile not found at expected path: {}".format(po))

        # Use base name from raster for vector layer name
        # name = "{} poline".format(rlayer.name().strip())

        # Use base name from shapefile to avoid duplicates
        name = os.path.splitext(os.path.basename(po))[0]

        v = load_vector_with_fallback(po, name)
        if not v or not v.isValid():
            raise QgsProcessingException("Failed to load vector: {}".format(po))

        QgsProject.instance().addMapLayer(v)
        feedback.pushInfo("Loaded PO line: {} as {}".format(po, name))
        return {}

    def createInstance(self): return LoadPoLineAlgorithm()

    # ------------------------
    # Local fallback resolver
    # ------------------------
    def _derive_poline_path_local(self, src, suffix):
        """
        Construct results/<run>/plot/gis/<base>_PLOT_L.shp by extracting <base>
        from the raster filename using the provided suffix pattern.
        The suffix can contain one or more '*' wildcards (e.g., '_d_*.tif').
        """
        grids_dir = os.path.dirname(src)
        run_dir = os.path.dirname(grids_dir)
        fname = os.path.basename(src)

        if "*" in suffix:
            suf_regex = re.escape(suffix).replace("\\*", ".*")
            pattern = re.compile(r"^(.+)" + suf_regex + r"$", re.IGNORECASE)
            match = pattern.match(fname)
            if not match:
                raise QgsProcessingException(
                    "Filename '{}' does not match suffix pattern '{}'.".format(fname, suffix)
                )
            base = match.group(1)
        else:
            if not fname.lower().endswith(suffix.lower()):
                raise QgsProcessingException(
                    "Filename '{}' does not end with suffix '{}'.".format(fname, suffix)
                )
            base = fname[: -len(suffix)]

        plot_gis_dir = os.path.join(run_dir, "plot", "gis")
        po_qp = os.path.join(plot_gis_dir, "{}_PLOT_L_QP.shp".format(base))
        po_plain = os.path.join(plot_gis_dir, "{}_PLOT_L.shp".format(base))
        # Prefer QP variant when present
        if os.path.exists(po_qp):
            return po_qp
        else:
            return po_plain
