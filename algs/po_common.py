# -*- coding: utf-8 -*-
"""
Common helpers for PO tools (QGIS Processing)

Includes:
- Raster/vector selection and source path helpers
- Derivation of PO line path from raster name (<base><suffix> → <base>_PLOT_L.shp)
- CSV discovery and parsing for QP updates
- ID normalization and small layer utilities
"""

from __future__ import annotations

import os
import re
import csv
import math
from pathlib import Path
from typing import Dict, Tuple, Optional, Set

from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer, QgsField, edit
from qgis.utils import iface


# ---------- Selection helpers ----------

def guess_selected_raster() -> Optional[QgsRasterLayer]:
    """Return the first selected raster layer, else the active layer if it is a raster, else None."""
    try:
        for lyr in iface.layerTreeView().selectedLayers():
            if isinstance(lyr, QgsRasterLayer):
                return lyr
    except Exception:
        pass
    try:
        a = iface.activeLayer()
        if isinstance(a, QgsRasterLayer):
            return a
    except Exception:
        pass
    return None


# ---------- Path helpers ----------

def _strip_provider_options(uri: str) -> str:
    """Split off provider options (e.g., 'path|layerid=0') and any surrounding quotes."""
    if not uri:
        return ""
    first = uri.splitlines()[0]
    first = first.strip().strip('"').strip("'")

    # Handle file:/// prefix from some providers
    if first.lower().startswith("file:///"):
        # Keep the absolute path after file:///
        first = first[8:] if first.startswith("file:////") else first[7:]
        # On Windows a leading slash may remain before drive letter: /C:/...
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", first):
            first = first[1:]

    # Strip provider options after '|'
    if "|" in first:
        first = first.split("|", 1)[0]

    return first
def source_path_from_layer(layer) -> Path:
    """Best-effort local filesystem path for a layer source (may not exist)."""
    uri = ""
    try:
        uri = layer.dataProvider().dataSourceUri()
    except Exception:
        try:
            uri = layer.source()
        except Exception:
            uri = ""
    return Path(_strip_provider_options(uri))


def layer_base_dir(layer) -> Path:
    """Directory to anchor relative lookups for a layer (source dir, project home, project file dir, cwd)."""
    p = source_path_from_layer(layer)
    if p.exists():
        return p.parent

    prj = QgsProject.instance()
    # Project home path (user-set)
    try:
        hp = Path(prj.homePath() or "")
        if hp and hp.exists():
            return hp
    except Exception:
        pass

    # Project file location
    try:
        pf = Path(prj.fileName() or "")
        if pf and pf.exists():
            return pf.parent
    except Exception:
        pass

    return Path.cwd()


# ---------- PO line derivation ----------

def derive_poline_path_from_raster(src: str, suffix: str = "_d_*.tif") -> str:
    """
    Given a raster at .../results/<run>/grids/<base><suffix>, return:
        .../results/<run>/plot/gis/<base>_PLOT_L.shp

    - `suffix` may include '*' wildcards (e.g., '_d_*.tif', '_depth_*.tif', or no wildcard like '_r0.tif').
    - Matching is case-insensitive.
    - Does not check file existence; it only computes the expected path.

    Raises:
        ValueError: if `src` is empty or the filename does not match `suffix`.
    """
    if not src:
        raise ValueError("src is required")

    # Normalize and split
    src = os.path.abspath(src)
    fname = os.path.basename(src)

    # Build a regex from the suffix pattern: escape then convert '*' → '.*'
    suf_regex = re.escape(suffix).replace(r"\*", ".*")

    # Extract <base> from "<base><suffix>"
    m = re.match(rf"^(.+){suf_regex}$", fname, re.IGNORECASE)
    if not m:
        raise ValueError(f"Filename '{fname}' does not match suffix pattern '{suffix}'")

    base = m.group(1)

    # Locate the run directory.
    grids_dir = os.path.dirname(src)
    run_dir = os.path.dirname(grids_dir)
    if os.path.basename(grids_dir).lower() != "grids":
        # Try to find a 'grids' segment above; if found, run_dir = parent of that segment.
        head = grids_dir
        while True:
            head, tail = os.path.split(head)
            if not tail:
                break
            if tail.lower() == "grids":
                run_dir = head
                break

    # Compose the PO line shapefile path
    po = os.path.join(run_dir, "plot", "gis", f"{base}_PLOT_L.shp")
    return po


def load_vector_with_fallback(path: str, display_name: str) -> Optional[QgsVectorLayer]:
    """Try loading by raw path first; if it fails, try a file:/// URL form."""
    v = QgsVectorLayer(path, display_name, "ogr")
    if v.isValid():
        return v
    url = "file:///" + path.replace("\\", "/")
    v2 = QgsVectorLayer(url, display_name, "ogr")
    return v2 if v2.isValid() else None


# ---------- CSV / QP helpers ----------

def base_name_from_source(layer) -> str:
    """
    Base name for CSVs inferred from the PO line layer source:
    strips a trailing `_PLOT_L` if present.
    """
    p = source_path_from_layer(layer)
    stem = p.stem if p and p.name else (layer.name().strip() or "output")
    return re.sub(r"_PLOT_L$", "", stem, flags=re.IGNORECASE)


def resolve_csv_paths_from_layer(layer, rel_dir: str) -> Tuple[Dict[str, Path], Dict[str, Path], Path, Path]:
    """
    Return (found, tried, csv_dir, base_dir) where:
      - found = {'1d': Path|None, '2d': Path|None}
      - tried = {'1d': Path, '2d': Path}
    """
    base_dir = layer_base_dir(layer)
    csv_dir = (base_dir / Path(rel_dir.replace("\\", "/"))).resolve()
    base = base_name_from_source(layer)

    paths = {
        "1d": csv_dir / f"{base}_1d_Q.csv",
        "2d": csv_dir / f"{base}_2d_Q.csv",
    }
    found = {k: p for k, p in paths.items() if p.exists()}
    return {"1d": found.get("1d"), "2d": found.get("2d")}, paths, csv_dir, base_dir


def keys_from_column_header(col_name: str) -> Set[str]:
    """
    Generate a set of reasonable lookup keys from a CSV column header.
    Examples:
      "Q 101" -> {"101","Q101","Q 101",...}
      "Discharge A" -> {"DISCHARGE A","A","QA","Q A",...}
    """
    full = (col_name or "").strip()
    if not full:
        return set()

    full_u = full.upper()

    # Remove bracketed suffix like "[m^3/s]" if present
    pre = full.split("[", 1)[0].strip()
    pre_u = pre.upper()

    # Remove common leading tokens (Q, QP, DISCHARGE, Q_FLOW) followed by space(s)
    token = re.compile(r"^(Q|QP|DISCHARGE|Q_FLOW)\s+", re.IGNORECASE).sub("", pre, 1).strip()
    token_u = token.upper()

    keys: Set[str] = {token_u, f"Q{token_u}", f"Q {token_u}", pre_u, full_u}

    # Add numeric-only variant (e.g., "101.0" -> "101")
    if re.fullmatch(r"\d+", token_u):
        try:
            keys.add(str(int(token_u)))
        except Exception:
            pass

    # Add no-space variants
    nospace = token_u.replace(" ", "")
    if nospace != token_u:
        keys.update({nospace, f"Q{nospace}", f"Q {nospace}"})

    return keys


def compute_max_map_for_csv(csv_path: Path, skip_cols: int = 2) -> Dict[str, float]:
    """
    Read a CSV and compute the maximum value per data column (ignoring the first `skip_cols` columns).
    Returns a dict mapping multiple possible keys for each column (see keys_from_column_header) to the max.
    """
    header = None
    max_vals = None

    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue

            if header is None:
                header = [h.strip() for h in row]
                if len(header) <= skip_cols:
                    raise ValueError(f"SKIP_COLS={skip_cols} leaves no data columns in {csv_path}.")
                max_vals = [None] * (len(header) - skip_cols)
                continue

            for j, cell in enumerate(row[skip_cols:], start=0):
                try:
                    x = float(cell)
                except (ValueError, TypeError):
                    continue
                if max_vals[j] is None or abs(x) > abs(max_vals[j]):
                    max_vals[j] = x

    max_map: Dict[str, float] = {}
    if max_vals:
        for j, mx in enumerate(max_vals, start=skip_cols):
            if mx is not None:
                for k in keys_from_column_header(header[j].strip()):
                    max_map[k] = mx
    return max_map


def normalize_id(val) -> Optional[str]:
    """
    Normalize an ID value used to match CSV columns:
      - None/blank -> None
      - numeric (e.g., "101" or "101.0") -> "101"
      - strings like "Q 12" -> "12"
      - otherwise uppercase string
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None

    # If numeric possibly with .0+ -> int string
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s)))

    # Accept optional leading 'Q'/'q' followed by an alnum token
    m = re.match(r"^[Qq]\s*([A-Za-z0-9_\-\.]+)$", s)
    if m:
        tok = m.group(1)
        return str(int(tok)) if re.fullmatch(r"\d+", tok) else tok.upper()

    return s.upper()


# ---------- Misc helpers sometimes used elsewhere ----------

def find_parent_results_dir(path: Path) -> Optional[Path]:
    """Return the nearest ancestor named 'results', else None."""
    try:
        for parent in [path] + list(path.parents):
            if parent.name.lower() == "results":
                return parent
    except Exception:
        pass
    return None


def locate_ov_zo_csvs_for_layer(layer, results_dir: Optional[str] = None) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Try to locate PO_Line_OV.csv and PO_Line_ZO.csv near the given layer, optionally within a supplied results dir.
    Returns (ov_path_or_None, zo_path_or_None).
    """
    candidates = []
    if results_dir:
        candidates.append(Path(results_dir))

    src = source_path_from_layer(layer)
    res_dir = find_parent_results_dir(src.parent) if src and src.exists() else None
    if res_dir:
        candidates.append(res_dir)

    # If both files exist in any candidate root, return immediately
    for root in candidates:
        ov = root / "PO_Line_OV.csv"
        zo = root / "PO_Line_ZO.csv"
        if ov.exists() and zo.exists():
            return ov, zo

    # Else return whichever exist
    for root in candidates:
        ov = root / "PO_Line_OV.csv"
        zo = root / "PO_Line_ZO.csv"
        if ov.exists() or zo.exists():
            return (ov if ov.exists() else None), (zo if zo.exists() else None)

    return None, None


def read_row_from_csv(file_path: Path, row_index: int) -> list:
    """Return a trimmed list of non-empty items from the specified zero-based row; empty list if out of range."""
    with open(file_path, mode="r", newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        for i, row in enumerate(r):
            if i == row_index:
                return [item.strip() for item in row if item and item.strip()]
    return []


def clone_vector(layer: QgsVectorLayer, name: str) -> QgsVectorLayer:
    """Clone a layer with a new display name, falling back to a new instance if clone() fails."""
    try:
        c = layer.clone()
        c.setName(name)
        return c
    except Exception:
        src = (layer.source() or "").splitlines()[0]
        prov = layer.providerType() or "ogr"
        return QgsVectorLayer(src, name, prov)
