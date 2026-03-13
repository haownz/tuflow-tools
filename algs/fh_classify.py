# QGIS processing toolbox provider - Flood hazard classification from depth and velocity rasters
# Classifies into 4 hazard levels (0=Low, 1=Medium, 2=High, 3=Very High)
# Low: d < 0.3 m and d*v < 0.24 m2/s
# Medium: 0.3 ≤ d < 0.5 m or 0.24 ≤ d*v < 0.4 m2/s
# High: 0.5 ≤ d < 1.2 m or 0.4 ≤ d*v < 0.8 m2/s
# Very High: d ≥ 1.2 m or d*v ≥ 0.8 m2/s
# Author: Hao Wu

# -*- coding: utf-8 -*-
import os
import re
from typing import Optional, Tuple

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,  # QGIS 3.x: use Number for doubles
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsRasterLayer,
)
from osgeo import gdal
import numpy as np

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def _get_active_raster_layer() -> Optional[QgsRasterLayer]:
    """Return the currently active raster layer, if any."""
    try:
        from qgis.utils import iface  # type: ignore
        lyr = iface.activeLayer() if iface else None
        return lyr if isinstance(lyr, QgsRasterLayer) else None
    except Exception:
        return None


def _extent_to_bounds(ext):
    """Return (xmin, ymin, xmax, ymax) tuple."""
    return (ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum())


# -----------------------------------------------------------------------------
# Main Processing Algorithm
# -----------------------------------------------------------------------------

class FloodHazardClassifyAlgorithm(QgsProcessingAlgorithm):
    """
    Hazard classes: 0=Low, 1=Medium, 2=High, 3=Very High
    Depth (m) + Velocity (m/s); auto-derives inputs (supports _HR_Max),
    auto-resamples velocity if misaligned; default gray single band output.
    """

    # Parameter keys
    P_DEPTH = "DEPTH"
    P_VEL = "VELOCITY"
    P_AUTODERIVE = "AUTODERIVE"
    P_OUTPUT = "OUTPUT"

    P_RESAMPLE = "RESAMPLE"
    P_RESAMPLE_ALG = "RESAMPLE_ALG"

    P_INCLUSIVE = "INCLUSIVE"
    P_EPS = "EPSILON"

    P_NAME_REGEX = "NAME_SUFFIX_REGEX"
    P_DEPTH_TOKEN = "DEPTH_TOKEN"
    P_VEL_TOKEN = "VELOCITY_TOKEN"
    P_TAIL_SUFFIX = "TAIL_SUFFIX"

    P_APPLY_STYLE = "APPLY_STYLE"  # kept to avoid breaking models; no-op in this revision

    RESAMPLE_ENUM = ["Nearest", "Bilinear", "Cubic"]

    ALG_ID = "classify_flood_hazard"

    # Required for Processing to instantiate by ID
    def createInstance(self):
        return FloodHazardClassifyAlgorithm()

    def name(self):
        return self.ALG_ID

    def displayName(self):
        return "Classify Flood Hazard"

    def group(self):
        return "2 - Result Analysis"

    def groupId(self):
        return "result_analysis"

    def shortHelpString(self):
        return (
            "Outputs UInt8 GeoTIFF (gray single band) with values 0=Low,1=Medium,2=High,3=Very High.\n"
            "Very High: d ≥ 1.2 or d×v ≥ 0.8; High: 0.5–1.2 or 0.4–0.8; "
            "Medium: 0.3–0.5 or 0.24–0.4; Low: otherwise."
        )

    def initAlgorithm(self, config=None):
        # Inputs
        self.addParameter(QgsProcessingParameterRasterLayer(self.P_DEPTH, "Depth raster (m)", optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(self.P_VEL, "Velocity raster (m/s)", optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.P_AUTODERIVE, "Auto derive from active layer", defaultValue=True))

        # Naming pattern flexibility (recognizes optional HR in tail)
        self.addParameter(QgsProcessingParameterString(
            self.P_NAME_REGEX,
            "Active-layer suffix regex to strip to base",
            defaultValue=r'_(?P<token>h|d|V)_(?P<hr>HR_)?Max$'
        ))
        self.addParameter(QgsProcessingParameterString(self.P_DEPTH_TOKEN, "Depth token", defaultValue="d"))
        self.addParameter(QgsProcessingParameterString(self.P_VEL_TOKEN, "Velocity token", defaultValue="V"))
        # AUTO tail uses matched _HR_Max or _Max with rules below
        self.addParameter(QgsProcessingParameterString(self.P_TAIL_SUFFIX, "Tail suffix", defaultValue="AUTO"))

        # Alignment / resample
        self.addParameter(QgsProcessingParameterBoolean(self.P_RESAMPLE, "Auto-resample velocity to depth grid", defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum(self.P_RESAMPLE_ALG, "Resample algorithm", options=self.RESAMPLE_ENUM, defaultValue=1))  # Bilinear

        # Strictness / tolerance
        self.addParameter(QgsProcessingParameterBoolean(self.P_INCLUSIVE, "Inclusive boundaries (≥)", defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(self.P_EPS, "Epsilon tolerance", type=QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))

        # Output (no styling applied in this revision)
        self.addParameter(QgsProcessingParameterBoolean(self.P_APPLY_STYLE, "Apply palette + labels (ignored)", defaultValue=False))
        self.addParameter(QgsProcessingParameterRasterDestination(self.P_OUTPUT, "Flood Hazard Classification", QgsProcessing.TEMPORARY_OUTPUT))

        # ----- Prefill defaults for DEPTH / VELOCITY from active layer -----
        try:
            from qgis.utils import iface  # UI context
            active = iface.activeLayer() if iface else None

            def _layer_or_sibling_default(active_layer: QgsRasterLayer, target_name: str) -> Optional[str]:
                # 1) project lookup by name -> return layer ID (preferred)
                lyr = next(
                    (l for l in QgsProject.instance().mapLayers().values()
                     if isinstance(l, QgsRasterLayer) and l.name() == target_name and l.isValid()),
                    None
                )
                if lyr:
                    return lyr.id()
                # 2) sibling file next to active layer's source -> return data source path
                directory = os.path.dirname(active_layer.source())
                for ext in (".tif", ".tiff", ".img", ".vrt"):
                    p = os.path.join(directory, f"{target_name}{ext}")
                    if os.path.exists(p):
                        return p
                return None

            if isinstance(active, QgsRasterLayer):
                base, token, matched_tail = self._base_and_tail_from_name(
                    active.name(), r'_(?P<token>h|d|V)_(?P<hr>HR_)?Max$'
                )
                if base:
                    # Apply rule: d/h_HR_Max -> use _Max for defaults
                    tail = self._choose_tail_for_defaults(token, matched_tail)

                    depth_name = f"{base}_d{tail}"
                    vel_name   = f"{base}_V{tail}"

                    depth_def = _layer_or_sibling_default(active, depth_name)
                    vel_def   = _layer_or_sibling_default(active, vel_name)

                    if depth_def:
                        p = self.parameterDefinition(self.P_DEPTH)
                        if p: p.setDefaultValue(depth_def)
                    if vel_def:
                        p = self.parameterDefinition(self.P_VEL)
                        if p: p.setDefaultValue(vel_def)
        except Exception:
            # Silent: best-effort defaults
            pass

    # ---------- helpers ----------
    def _compile_base_regex(self, regex_text: str):
        try:
            return re.compile(regex_text, re.IGNORECASE)
        except Exception:
            # Fallback to simple pattern if user-supplied regex is invalid
            return re.compile(r'_(h|d|V)_Max$', re.IGNORECASE)

    def _base_and_tail_from_name(self, name: str, regex_text: str):
        """
        Parses active layer name with regex r'_(?P<token>h|d|V)_(?P<hr>HR_)?Max$'
        Returns (base, token, tail), where tail is '_HR_Max' or '_Max'.
        """
        rx = self._compile_base_regex(regex_text)
        m = rx.search(name)
        if not m:
            return None, None, None
        base = name[:m.start()]
        token = m.groupdict().get('token')
        hr = m.groupdict().get('hr') or ''
        tail = f"_{hr}Max"
        return base, token, tail

    def _choose_tail_for_defaults(self, token: Optional[str], matched_tail: Optional[str]) -> str:
        """
        Rule:
        - If the active token is 'd' or 'h' and we matched '_HR_Max', prefer '_Max' (drop HR).
        - Otherwise use the matched tail if present, else fallback to '_Max'.
        """
        mt = (matched_tail or "_Max")
        if token and token.lower() in ("d", "h") and mt == "_HR_Max":
            return "_Max"
        return mt

    @staticmethod
    def _find_project_layer(name: str) -> Optional[QgsRasterLayer]:
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer) and lyr.name() == name and lyr.isValid():
                return lyr
        return None

    @staticmethod
    def _try_load_sibling(selected_layer: QgsRasterLayer, sibling_name: str) -> Optional[QgsRasterLayer]:
        src = selected_layer.source()
        directory = os.path.dirname(src)
        for ext in (".tif", ".tiff", ".img", ".vrt"):
            candidate = os.path.join(directory, f"{sibling_name}{ext}")
            if os.path.exists(candidate):
                rl = QgsRasterLayer(candidate, sibling_name)
                if rl.isValid():
                    return rl
        return None

    def _autoderive(self, context: QgsProcessingContext,
                    depth_token: str, vel_token: str,
                    tail_suffix: str, name_regex: str) -> Tuple[QgsRasterLayer, QgsRasterLayer, str]:
        active = _get_active_raster_layer()
        if active is None:
            raise QgsProcessingException(
                "Select a raster named like '<base>_(h|d|V)_(HR_)?Max' to auto-derive <base>_d_* and <base>_V_*."
            )

        base, token, matched_tail = self._base_and_tail_from_name(active.name(), name_regex)
        if not base:
            raise QgsProcessingException(
                f"Cannot parse base scenario from '{active.name()}'. Regex: '{name_regex}'."
            )

        # Tail: AUTO uses drop-HR rule for d/h; otherwise respect user-provided suffix
        use_tail = (tail_suffix or "").strip()
        if not use_tail or use_tail.upper() == "AUTO":
            use_tail = self._choose_tail_for_defaults(token, matched_tail)

        depth_name = f"{base}_{depth_token}{use_tail}"
        vel_name   = f"{base}_{vel_token}{use_tail}"

        depth = self._find_project_layer(depth_name) or self._try_load_sibling(active, depth_name)
        vel   = self._find_project_layer(vel_name)   or self._try_load_sibling(active, vel_name)

        if not depth or not depth.isValid():
            raise QgsProcessingException(f"Depth raster not found: '{depth_name}' (project or sibling file).")
        if not vel or not vel.isValid():
            raise QgsProcessingException(f"Velocity raster not found: '{vel_name}' (project or sibling file).")

        return depth, vel, base

    @staticmethod
    def _np_from_raster(path: str):
        ds = gdal.Open(path, gdal.GA_ReadOnly)
        if ds is None:
            raise QgsProcessingException(f"Cannot open raster: {path}")
        band = ds.GetRasterBand(1)
        nd = band.GetNoDataValue()
        arr = band.ReadAsArray().astype(np.float32)
        return ds, arr, nd

    @staticmethod
    def _warp_velocity_to_depth(vel_path: str, depth_layer: QgsRasterLayer, resample_alg: str, feedback) -> gdal.Dataset:
        try:
            bounds = _extent_to_bounds(depth_layer.extent())
            xres = depth_layer.rasterUnitsPerPixelX()
            yres = abs(depth_layer.rasterUnitsPerPixelY())
            dst_wkt = depth_layer.crs().toWkt()
            alg_map = {"Nearest": gdal.GRA_NearestNeighbour, "Bilinear": gdal.GRA_Bilinear, "Cubic": gdal.GRA_Cubic}
            alg = alg_map.get(resample_alg, gdal.GRA_Bilinear)

            dst_name = "/vsimem/vel_resampled.tif"
            opts = gdal.WarpOptions(
                format="GTiff",
                outputBounds=bounds,
                xRes=xres,
                yRes=yres,
                resampleAlg=alg,
                dstSRS=dst_wkt,
                multithread=True,
                warpOptions=["INIT_DEST=NO_DATA"],
                creationOptions=["COMPRESS=LZW", "TILED=YES"],
            )
            ds = gdal.Warp(dst_name, vel_path, options=opts)
            if ds is None:
                raise QgsProcessingException("GDAL Warp failed to resample velocity raster.")
            return ds
        except Exception as e:
            feedback.reportError(f"Resampling failed; proceeding without resample. Error: {e}")
            ds = gdal.Open(vel_path, gdal.GA_ReadOnly)
            if ds is None:
                raise QgsProcessingException("Fallback open velocity raster failed.")
            return ds

    @staticmethod
    def _write_gtiff_byte(out_path: str, template_ds, array: np.ndarray, nodata: int = 255):
        """
        Write a standard Byte GeoTIFF (no palette, default MINISBLACK photometric),
        so QGIS loads as a gray single band. No styling is applied here.
        """
        driver = gdal.GetDriverByName("GTiff")
        out = driver.Create(
            out_path,
            template_ds.RasterXSize,
            template_ds.RasterYSize,
            1,
            gdal.GDT_Byte,
            options=["COMPRESS=LZW", "TILED=YES"],
        )
        out.SetGeoTransform(template_ds.GetGeoTransform())
        out.SetProjection(template_ds.GetProjection())

        band = out.GetRasterBand(1)
        band.SetNoDataValue(float(nodata))
        band.WriteArray(array.astype(np.uint8))
        band.FlushCache()
        out.FlushCache()
        out = None

    # ---------- main ----------
    def processAlgorithm(self, parameters, context, feedback):
        # Read parameters
        autoderive = self.parameterAsBool(parameters, self.P_AUTODERIVE, context)
        name_regex = self.parameterAsString(parameters, self.P_NAME_REGEX, context)
        depth_token = self.parameterAsString(parameters, self.P_DEPTH_TOKEN, context) or "d"
        vel_token = self.parameterAsString(parameters, self.P_VEL_TOKEN, context) or "V"
        tail_suffix = self.parameterAsString(parameters, self.P_TAIL_SUFFIX, context) or "AUTO"

        resample = self.parameterAsBool(parameters, self.P_RESAMPLE, context)
        resample_idx = self.parameterAsEnum(parameters, self.P_RESAMPLE_ALG, context)
        resample_alg = self.RESAMPLE_ENUM[resample_idx] if resample_idx is not None else "Bilinear"

        inclusive = self.parameterAsBool(parameters, self.P_INCLUSIVE, context)
        eps = float(self.parameterAsDouble(parameters, self.P_EPS, context))

        # apply_style parameter is ignored in this revision (no symbology)
        _ = self.parameterAsBool(parameters, self.P_APPLY_STYLE, context)

        depth_layer = self.parameterAsRasterLayer(parameters, self.P_DEPTH, context)
        vel_layer = self.parameterAsRasterLayer(parameters, self.P_VEL, context)
        base_name = None

        if autoderive or depth_layer is None or vel_layer is None:
            depth_layer, vel_layer, base_name = self._autoderive(context, depth_token, vel_token, tail_suffix, name_regex)

        # Alignment check
        aligned = (
            depth_layer.width() == vel_layer.width()
            and depth_layer.height() == vel_layer.height()
            and depth_layer.extent() == vel_layer.extent()
            and depth_layer.crs() == vel_layer.crs()
        )

        # Read depth
        ds_d, d, nd_d = self._np_from_raster(depth_layer.source())

        # Velocity: resample if necessary
        if aligned or not resample:
            ds_v, v, nd_v = self._np_from_raster(vel_layer.source())
        else:
            feedback.pushInfo("Velocity misaligned — resampling to depth grid via GDAL Warp …")
            ds_v = self._warp_velocity_to_depth(vel_layer.source(), depth_layer, resample_alg, feedback)
            band_v = ds_v.GetRasterBand(1)
            nd_v = band_v.GetNoDataValue()
            v = band_v.ReadAsArray().astype(np.float32)

        # NoData mask
        nod_mask = np.zeros_like(d, dtype=bool)
        if nd_d is not None:
            nod_mask |= (d == nd_d)
        if nd_v is not None:
            nod_mask |= (v == nd_v)

        # Threshold helpers
        def ge(val, bound):
            # >= (inclusive) or > (exclusive), with epsilon
            return (val >= (bound - eps)) if inclusive else (val > (bound + eps))

        def lt(val, bound):
            # < upper bound; keep a small tolerance to avoid overlaps at boundaries
            return val < (bound - eps)

        dv = d * v

        very_high = ge(d, 1.2) | ge(dv, 0.8)
        high = ((ge(d, 0.5) & lt(d, 1.2)) | (ge(dv, 0.4) & lt(dv, 0.8)))
        medium = ((ge(d, 0.3) & lt(d, 0.5)) | (ge(dv, 0.24) & lt(dv, 0.4)))
        # Low is the remainder

        out = np.zeros_like(d, dtype=np.uint8)
        out[very_high] = 3
        out[~very_high & high] = 2
        out[~very_high & ~high & medium] = 1
        
        nod_mask |= (d <= 0.005) | (v <= 0.005) # treat very small values as NoData
        nodata_val = 255
        out[nod_mask] = nodata_val

        # Resolve TEMPORARY_OUTPUT to a real path (so we always return a concrete filename)
        out_path = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)
        if not out_path or out_path == QgsProcessing.TEMPORARY_OUTPUT:
            tmp_dest = QgsProcessingParameterRasterDestination(self.P_OUTPUT, "")
            out_path = tmp_dest.generateTemporaryDestination()

        # Write result (no palette, default gray band)
        self._write_gtiff_byte(out_path, ds_d, out, nodata=nodata_val)

        # Name the output layer "Flood Hazard" when loading, but do not style it
        try:
            project = context.project() or QgsProject.instance()
            details = QgsProcessingContext.LayerDetails("Flood Hazard", project)
            context.addLayerToLoadOnCompletion(out_path, details)
        except Exception as e:
            feedback.reportError(f"Could not register layer for loading: {e}")

        feedback.pushInfo("Hazard classification complete (0=Low, 1=Medium, 2=High, 3=Very High).")
        return {self.P_OUTPUT: out_path}