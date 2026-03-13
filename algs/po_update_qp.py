# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QVariant, QSettings
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString, QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsVectorLayer, QgsRasterLayer, QgsProject, QgsField
)
from .po_common import (
    guess_selected_raster, derive_poline_path_from_raster, load_vector_with_fallback,
    resolve_csv_paths_from_layer, compute_max_map_for_csv, normalize_id
)
import os, math, re, csv

def guess_selected_vector():
    """Try to return the currently active vector layer (line geometry) from the QGIS interface."""
    try:
        from qgis.utils import iface
        layer = iface.activeLayer()
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.geometryType() == 1:
            return layer
    except Exception:
        pass
    return None

REL_DIR_DEFAULT = r"..\\csv"
SETTINGS_KEY_SUFFIX = "po/suffix_pattern"
SKIP_COLS_DEFAULT = 2
ID_FIELD_DEFAULT = "ID"
FLOW_FIELD_DEFAULT = "QP"
UPDATE_SCOPES = ["auto", "selected", "all"]

class POUpdateQPAlgorithm(QgsProcessingAlgorithm):
    P_RASTER = "RASTER"
    P_VECTOR = "VECTOR"
    P_SUFFIX = "SUFFIX"
    P_ID = "ID_FIELD"
    P_FLOW = "FLOW_FIELD"
    P_REL = "REL_DIR"
    P_SKIP = "SKIP_COLS"
    P_SCOPE = "UPDATE_SCOPE"
    P_PREF = "PREF_ORDER"
    P_SRC = "SOURCE_FIELD"
    P_QV = "VOLUME_FIELD"

    ALG_ID = "po_update_qp"

    def name(self): return self.ALG_ID
    def displayName(self): return "Update QP/QV"
    def group(self): return "PO tools"
    def groupId(self): return "po_tools"

    def shortHelpString(self):
        return (
            "Writes peak flow to a field (default 'QP') using <base>_1d_Q.csv / <base>_2d_Q.csv "
            "found in the relative CSV folder (default ..\\csv). Provide a PO line vector to override raster; "
            "if no vector is provided, a raster may be used to locate the PO line via <base><suffix> "
            "(suffix is sticky via QSettings; default '_d_*.tif').\n\n"
            "Optionally writes flow volume (QV) integrated from the same Q time series. "
            "QV is taken from a single dataset only, following your CSV preference order (no combining)."
        )

    def initAlgorithm(self, config=None):
        default_suffix = QSettings().value(SETTINGS_KEY_SUFFIX, "_d_*.tif", type=str)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.P_RASTER, "Raster (optional; used only if PO line vector is empty)",
            optional=True, defaultValue=guess_selected_raster()
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_VECTOR, "PO line vector (optional; overrides Raster if provided)",
            optional=True, defaultValue=guess_selected_vector()
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_SUFFIX, "Suffix pattern for raster-derived PO line",
            defaultValue=default_suffix
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_ID, "ID field", defaultValue=ID_FIELD_DEFAULT
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_FLOW, "Flow field (to write)", defaultValue=FLOW_FIELD_DEFAULT
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_REL, "Relative CSV folder", defaultValue=REL_DIR_DEFAULT
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_SKIP, "Non-data columns at start (SKIP_COLS)",
            QgsProcessingParameterNumber.Integer, defaultValue=SKIP_COLS_DEFAULT, minValue=0
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.P_SCOPE, "Update scope", options=UPDATE_SCOPES, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_PREF, "CSV preference order (comma separated)", defaultValue="1d,2d"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_SRC, "Optional field to store CSV source (blank to skip)",
            optional=True, defaultValue=""
        ))
        self.addParameter(QgsProcessingParameterString(
            self.P_QV, "Volume field (leave blank to skip writing QV)",
            optional=True, defaultValue="QV"
        ))

    def processAlgorithm(self, parameters, context, feedback):
        # Persist suffix
        suffix = self.parameterAsString(parameters, self.P_SUFFIX, context) or "_d_*.tif"
        QSettings().setValue(SETTINGS_KEY_SUFFIX, suffix)

        # Prefer explicitly supplied PO line vector
        v = self.parameterAsVectorLayer(parameters, self.P_VECTOR, context)
        if v is None:
            # Fall back to raster -> derive PO line path
            r = self.parameterAsRasterLayer(parameters, self.P_RASTER, context) or guess_selected_raster()
            if not isinstance(r, QgsRasterLayer):
                raise QgsProcessingException("Either PO line vector or raster is required.")
            src = (r.source() or "").splitlines()[0]
            if not os.path.exists(src):
                raise QgsProcessingException(f"Selected raster is not a local file: {src}")
            try:
                po = derive_poline_path_from_raster(src, suffix)  # type: ignore
            except TypeError:
                po = self._derive_poline_path_local(src, suffix)
            if not os.path.exists(po):
                raise QgsProcessingException(f"PO line shapefile not found: {po}")
            v = load_vector_with_fallback(po, f"{r.name().strip()} poline")
            if not v or not v.isValid():
                raise QgsProcessingException(f"Failed to open PO line: {po}")
            feedback.pushInfo("PO line resolved from raster; original shapefile will not be modified.")
        else:
            if not isinstance(v, QgsVectorLayer) or not v.isValid():
                raise QgsProcessingException("Provided PO line vector is invalid.")
            feedback.pushInfo("PO line vector provided; raster parameter will be ignored.")

        # Keep a reference to the original layer for CSV resolution
        original_layer = v

        # Use a temporary memory copy unless already memory layer
        if v.providerType().lower() != "memory":
            temp_v = self._make_temp_copy(v)
            QgsProject.instance().addMapLayer(temp_v)
            feedback.pushInfo(f"Created temporary layer '{temp_v.name()}' to write attributes (original not modified).")
            v = temp_v
        else:
            feedback.pushInfo(f"Using provided in-memory layer '{v.name()}' for updates.")

        # Parameters for update
        id_field = self.parameterAsString(parameters, self.P_ID, context)
        flow_field = self.parameterAsString(parameters, self.P_FLOW, context)
        rel_dir = self.parameterAsString(parameters, self.P_REL, context)
        skip_cols = self.parameterAsInt(parameters, self.P_SKIP, context)
        scope = UPDATE_SCOPES[self.parameterAsEnum(parameters, self.P_SCOPE, context)]
        pref = self.parameterAsString(parameters, self.P_PREF, context)
        pref_order = [s.strip() for s in pref.split(",") if s.strip()] or ["1d", "2d"]
        src_field = (self.parameterAsString(parameters, self.P_SRC, context) or "").strip() or None
        qv_field = (self.parameterAsString(parameters, self.P_QV, context) or "").strip()

        # Validate ID field
        if id_field not in v.fields().names():
            raise QgsProcessingException(f"Field '{id_field}' not found in layer '{v.name()}'.")

        # Resolve CSVs
        resolver_layer = original_layer if (original_layer and original_layer.providerType().lower() != "memory") else v
        csvs, tried, csv_dir, base_dir = resolve_csv_paths_from_layer(resolver_layer, rel_dir)

        # Announce CSV presence
        one_d = csvs.get("1d")
        two_d = csvs.get("2d")
        if one_d: feedback.pushInfo(f"[CSV] 1d found: {one_d}")
        else:     feedback.reportError(f"[CSV] 1d not found (expected at: {tried.get('1d')})")
        if two_d: feedback.pushInfo(f"[CSV] 2d found: {two_d}")
        else:     feedback.reportError(f"[CSV] 2d not found (expected at: {tried.get('2d')})")
        if not (one_d or two_d):
            msg = ["No CSV files found. Tried:"] + [f" - {k}: {p}" for k, p in tried.items()]
            msg.append(f"(Layer dir = {base_dir}) (Resolved csv dir = {csv_dir})")
            raise QgsProcessingException("\n".join(msg))

        # Build QP peak maps (unchanged)
        maps = {}
        if csvs.get("1d"):
            maps["1d"] = compute_max_map_for_csv(csvs["1d"], skip_cols)
        if csvs.get("2d"):
            maps["2d"] = compute_max_map_for_csv(csvs["2d"], skip_cols)

        # Build QV volume maps (strict parsing/matching for column-oriented TUFLOW)
        vol_maps = {}
        vol_keys_preview = {}
        if csvs.get("1d"):
            vm, note = compute_volume_map_for_csv(csvs["1d"], skip_cols)
            vol_maps["1d"] = vm
            vol_keys_preview["1d"] = list(vm.keys())[:8]
            feedback.pushInfo(f"[CSV] 1d keys (first 8): {vol_keys_preview['1d']}")
            feedback.pushInfo(f"[VOL] 1d parsing note: {note}")
        if csvs.get("2d"):
            vm, note = compute_volume_map_for_csv(csvs["2d"], skip_cols)
            vol_maps["2d"] = vm
            vol_keys_preview["2d"] = list(vm.keys())[:8]
            feedback.pushInfo(f"[CSV] 2d keys (first 8): {vol_keys_preview['2d']}")
            feedback.pushInfo(f"[VOL] 2d parsing note: {note}")

        # Ensure fields exist
        flow_idx = v.fields().indexOf(flow_field)
        if flow_idx == -1:
            v.dataProvider().addAttributes([QgsField(flow_field, QVariant.Double, 'Double', 15, 5)])
            v.updateFields()
            flow_idx = v.fields().indexOf(flow_field)

        src_idx = -1
        if src_field:
            src_idx = v.fields().indexOf(src_field)
            if src_idx == -1:
                v.dataProvider().addAttributes([QgsField(src_field, QVariant.String)])
                v.updateFields()
                src_idx = v.fields().indexOf(src_field)

        qv_idx = -1
        if qv_field:
            qv_idx = v.fields().indexOf(qv_field)
            if qv_idx == -1:
                # If you want Double(10,0) instead, change to (10, 0) below:
                v.dataProvider().addAttributes([QgsField(qv_field, QVariant.Double, 'Double', 15, 1)])
                v.updateFields()
                qv_idx = v.fields().indexOf(qv_field)

        # Select features by scope
        sel_ids = list(v.selectedFeatureIds())
        if scope == "selected":
            feats_iter = v.getSelectedFeatures()
        elif scope == "all":
            feats_iter = v.getFeatures()
        else:
            feats_iter = v.getSelectedFeatures() if sel_ids else v.getFeatures()
        feats = list(feats_iter)
        id_raw = [(f.id(), f.attribute(id_field)) for f in feats]

        # ---- QP lookup (unchanged behaviour; pref_order decides which dataset to use) ----
        def lookup(norm_id):
            if norm_id is None:
                return None, None
            variants = id_variants(norm_id)
            for tag in pref_order:
                mp = maps.get(tag)
                if not mp:
                    continue
                for k in variants:
                    val = mp.get(k)
                    if val is not None and val != -math.inf:
                        return val, tag
            return None, None

        # ---- QV lookup (NO COMBINE): always pick ONE dataset following pref_order ----
        def vol_lookup(raw_id_value):
            clean_id = _debracket(str(raw_id_value))
            exact_keys = station_variants(clean_id)
            for tag in pref_order:
                mp = vol_maps.get(tag)
                if not mp:
                    continue
                for k in exact_keys:
                    val = mp.get(k)
                    if val is not None:
                        return float(val), tag
            return None, None

        updated_qp = 0
        unmatched_qp = []
        updated_qv = 0
        unmatched_qv = []

        # Batched attribute updates
        changes = {}
        for fid, raw in id_raw:
            # QP
            qp_val, qp_tag = lookup(normalize_id(raw))
            if qp_val is None:
                unmatched_qp.append((fid, raw))
            else:
                entry = changes.get(fid, {})
                try:
                    entry[flow_idx] = round(float(qp_val), 5)
                    if src_field and src_idx != -1:
                        entry[src_idx] = qp_tag or ""
                    changes[fid] = entry
                    updated_qp += 1
                except Exception:
                    unmatched_qp.append((fid, raw))

            # QV (no combine)
            if qv_idx != -1:
                qv_val, qv_tag = vol_lookup(raw)
                if qv_val is None:
                    unmatched_qv.append((fid, raw))
                else:
                    entry = changes.get(fid, {})
                    try:
                        entry[qv_idx] = round(float(qv_val), 1)
                        changes[fid] = entry
                        updated_qv += 1
                    except Exception:
                        unmatched_qv.append((fid, raw))

        if changes:
            try:
                ok = v.dataProvider().changeAttributeValues(changes)
                if not ok:
                    feedback.reportError("Provider failed to apply attribute changes (changeAttributeValues returned False).")
            except Exception as e:
                feedback.reportError(f"Exception while applying attribute changes: {e}")
        else:
            feedback.pushInfo("No attribute updates to apply.")

        scope_label = ("selected features" if (scope == "selected" or (scope == "auto" and sel_ids)) else "all features")
        feedback.pushInfo("Updated {} {} with '{}' (order {}).".format(updated_qp, scope_label, flow_field, pref_order))
        if qv_idx != -1:
            feedback.pushInfo("Updated {} {} with '{}' (single-dataset per ID, order {}).".format(updated_qv, scope_label, qv_field, pref_order))
        for t in ("1d", "2d"):
            if csvs.get(t):
                feedback.pushInfo(" used {}: {}".format(t, csvs[t]))
        if unmatched_qp:
            feedback.pushInfo("{} features had no QP match (first up to 10):".format(len(unmatched_qp)))
            for fid, raw in unmatched_qp[:10]:
                feedback.pushInfo(" feature {}: {}={!r}".format(fid, id_field, raw))
        if qv_idx != -1 and unmatched_qv:
            feedback.pushInfo("{} features had no QV match (first up to 10):".format(len(unmatched_qv)))
            for fid, raw in unmatched_qv[:10]:
                feedback.pushInfo(" feature {}: {}={!r}".format(fid, id_field, raw))
        return {}

    def createInstance(self): return POUpdateQPAlgorithm()

    # --------------------------------------------------------------------------
    # Local fallback resolver
    # --------------------------------------------------------------------------
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
        po = os.path.join(run_dir, "plot", "gis", "{}_PLOT_L.shp".format(base))
        return po

    # --------------------------------------------------------------------------
    # Create in-memory copy
    # --------------------------------------------------------------------------
    def _make_temp_copy(self, src_layer):
        """
        Create a temporary in-memory copy of src_layer, preserving fields,
        geometries and CRS. Returns the new memory layer.
        """
        geom_map = {0: "Point", 1: "LineString", 2: "Polygon"}
        geom_name = geom_map.get(src_layer.geometryType(), "Unknown")
        uri = "{}?crs={}".format(geom_name, src_layer.crs().authid() or "")
        base_name = os.path.splitext(os.path.basename(src_layer.source()))[0] if src_layer.source().lower().endswith(".shp") else src_layer.name().strip()
        name = "{}_QP".format(base_name) if base_name else "PO line QP"
        mem = QgsVectorLayer(uri, name, "memory")
        if not mem.isValid():
            raise QgsProcessingException("Failed to create temporary memory layer.")
        mem_dp = mem.dataProvider()
        mem_dp.addAttributes(src_layer.fields())
        mem.updateFields()
        feats = [f for f in src_layer.getFeatures()]
        mem_dp.addFeatures(feats)
        mem.updateExtents()
        return mem

# ------------------------------------------------------------------------------
# Helpers: robust CSV + ID variant expansion + volume integration
# ------------------------------------------------------------------------------

def _read_csv_rows(path):
    """
    Read CSV lines robustly:
    - UTF-8-SIG to strip BOM
    - Try delimiters: ',', ';', '\t'
    - Skip leading empty/comment lines
    Returns list[list[str]].
    """
    if not os.path.exists(path):
        return []
    encodings = ["utf-8-sig", "utf-8"]
    delims = [",", ";", "\t"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                text = f.read()
            lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))]
            for d in delims:
                try:
                    reader = csv.reader(lines, delimiter=d)
                    rows = [row for row in reader if row]
                    if any(len(r) > 1 for r in rows):
                        return rows
                except Exception:
                    continue
        except Exception:
            continue
    try:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            return [r for r in reader if r]
    except Exception:
        return []

def _digits_only(s):
    """Return the digits-only string from s, or None if no digits."""
    if s is None: return None
    m = re.findall(r"\d+", str(s))
    return "".join(m) if m else None

def _debracket(s):
    """Strip trailing run suffix like ' [M01_001]' and leading 'Q ' prefix."""
    if s is None: return ""
    s2 = re.sub(r"^\s*Q\s+", "", str(s).strip(), flags=re.IGNORECASE)  # remove leading 'Q '
    s2 = re.split(r"\s*\[", s2)[0].strip()  # keep part before ' [ ... ]'
    s2 = s2.replace("  ", " ").strip()
    return s2

def station_variants(strict_id):
    """
    Build variants for column-oriented TUFLOW station IDs WITHOUT adding digits-only forms.
    Ensures 'PL001' does not match 'CVT_001' via '001'.
    """
    tokens = {
        strict_id,
        strict_id.upper(),
        strict_id.replace(" ", ""),
        strict_id.replace("_", ""),
        strict_id.replace(" ", "_"),
    }
    variants = set(t for t in tokens if t)
    # accept 'Q' prefixed forms in case layer ID carries them
    base = list(variants)
    for b in base:
        variants.add("Q" + b)
        variants.add("Q " + b)
    return variants

def id_variants(id_value, pad_widths=(2, 3, 4)):
    """
    (Used for QP and row-oriented fallback)
    Build a set of robust ID variants:
    - original as str and upper
    - digits-only (no leading zeros) and zero-padded forms
    - with 'Q' and 'Q ' prefixes for each variant
    """
    variants = set()
    if id_value is None:
        return variants
    s_raw = str(id_value).strip()
    if s_raw:
        variants.add(s_raw)
        variants.add(s_raw.upper())
    digits = _digits_only(s_raw)
    if digits:
        nonpad = str(int(digits))
        variants.add(nonpad)
        for w in pad_widths:
            variants.add(nonpad.zfill(w))
        variants.add(digits)
    base_list = list(variants)
    for b in base_list:
        variants.add("Q" + b)
        variants.add("Q " + b)
    return variants

def _parse_time_token(token, unit_hint=None):
    """Parse a time token into seconds, using optional unit_hint ('h' or 'min')."""
    s = (token or "").strip()
    if not s:
        return None
    try:
        val = float(s)
        if unit_hint == 'h':     return val * 3600.0
        if unit_hint == 'min':   return val * 60.0
        return val               # assume seconds
    except Exception:
        pass
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|second|seconds|min|minutes|hr|hour|hours)?\s*$", s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit.startswith("min"):            mult = 60.0
        elif unit.startswith("hr") or unit.startswith("hour"): mult = 3600.0
        else:                                 mult = 1.0
        return val * mult
    m2 = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m2.group(1)) if m2 else None

def _parse_time_header(header, skip_cols):
    """
    (legacy fallback) Parse time labels from CSV header after SKIP_COLS.
    Returns (times_in_seconds:list[float], note:str).
    """
    times = []
    parsed_all = True
    for name in header[skip_cols:]:
        s = (name or "").strip()
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|second|seconds|min|minutes|hr|hour|hours)?\s*$", s, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = (m.group(2) or "").lower()
            if unit.startswith("min"):      mult = 60.0
            elif unit.startswith("hr") or unit.startswith("hour"): mult = 3600.0
            else:                           mult = 1.0
            times.append(val * mult)
            continue
        m2 = re.search(r"(\d+(?:\.\d+)?)", s)
        if m2:
            times.append(float(m2.group(1)))  # assume seconds
        else:
            parsed_all = False
            break
    if not parsed_all or len(times) == 0:
        n = max(0, len(header) - skip_cols)
        times = [float(i) for i in range(n)]
        note = "uniform dt=1 (time labels not parseable)"
    else:
        note = "assuming Q in m³/s and time in seconds parsed from header"
    return times, note

def _integrate_trapezoid(times, q_series):
    """Trapezoidal integration of discharge (m³/s) over time (s) -> volume (m³)."""
    if not times or len(times) < 2:
        return 0.0
    n = min(len(times), len(q_series))
    vol = 0.0
    for i in range(n - 1):
        dt = times[i + 1] - times[i]
        qi = q_series[i] if i < len(q_series) else 0.0
        qj = q_series[i + 1] if i + 1 < len(q_series) else qi
        vol += 0.5 * (qi + qj) * max(0.0, dt)
    return vol

def compute_volume_map_for_csv(path, skip_cols):
    """
    Read a TUFLOW-like Q.csv and return ({key_variant -> volume_m3}, note).
    Supports:
      Column-oriented: [RunName, Time (h), Q PL001 [...], Q PL002 [...], ...]
      Row-oriented fallback: [ID, <t1>, <t2>, ...]
    """
    rows = _read_csv_rows(path)
    if not rows:
        return {}, "empty or unreadable CSV"

    header = rows[0]
    col_oriented = (len(header) > 1 and header[1].lower().startswith("time"))
    if col_oriented:
        unit_hint = 'h' if 'h' in header[1].lower() else None
        times = []
        for r in rows[1:]:
            tok = r[1] if len(r) > 1 else None
            t = _parse_time_token(tok, unit_hint=unit_hint)
            if t is None:
                t = (times[-1] + 1.0) if times else 0.0
            times.append(t)

        vol_map = {}
        for j in range(skip_cols, len(header)):
            q_series = []
            for r in rows[1:]:
                try:    q_series.append(float(r[j]))
                except: q_series.append(0.0)
            vol = _integrate_trapezoid(times, q_series)

            # Use a clean station id extracted from header column
            raw = header[j]
            clean = _debracket(raw)                  # "Q PL001 [M01_001]" -> "PL001"
            for k in station_variants(clean):        # strict variants, NO digits-only
                vol_map.setdefault(k, vol)

        note = f"column-oriented; time from '{header[1]}' (unit {'h' if unit_hint=='h' else 's'})"
        return vol_map, note

    # row-oriented fallback (legacy)
    times, note_hdr = _parse_time_header(header, skip_cols)
    vol_map = {}
    for r in rows[1:]:
        if not r:
            continue
        key_raw = (r[0] if len(r) > 0 else "").strip()
        data = []
        for s in r[skip_cols:]:
            try:    data.append(float(s))
            except: data.append(0.0)
        vol = _integrate_trapezoid(times, data)
        for k in id_variants(key_raw):
            vol_map.setdefault(k, vol)
    return vol_map, f"row-oriented; {note_hdr}"

def compute_total_volume_across_poline(csvs, skip_cols, pref_order, combine=False):
    """
    Compute the total volume across the PO line.
    Always uses 'prefer' semantics: the first dataset in pref_order that exists.
    Returns (total_volume_m3, parts:dict[tag->volume], note:str).
    """
    parts = {}
    notes = []
    total = 0.0
    if csvs.get("1d"):
        vm, note = compute_volume_map_for_csv(csvs["1d"], skip_cols)
        parts["1d"] = sum({k: v for k, v in vm.items() if not k.startswith("Q")}.values())  # avoid double-counting
        notes.append(f"1d: {note}")
    if csvs.get("2d"):
        vm, note = compute_volume_map_for_csv(csvs["2d"], skip_cols)
        parts["2d"] = sum({k: v for k, v in vm.items() if not k.startswith("Q")}.values())
        notes.append(f"2d: {note}")

    # prefer mode only
    for tag in (pref_order or ["1d", "2d"]):
        if tag in parts:
            total = parts[tag]
            break
    note = "preferred dataset only; " + ("; ".join(notes) if notes else "no CSVs available")
    return total, parts, note