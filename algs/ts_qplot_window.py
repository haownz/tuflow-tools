
# -*- coding: utf-8 -*-
"""
TUFLOW tools — Flow (Q) Plot Viewer (Auto multi-selection across layers + peaks toggle + pan/zoom)

- Auto-detect selected features across ALL vector layers (no layer-selection panel).
- Multiple PO lines present = overlay in same plot.
- Peak flow annotation toggle ("Show Peaks") with same auto-logic as "Show Volume":
    * If TOTAL series == 1 -> both toggles enabled + ON
    * If TOTAL series > 1  -> both toggles disabled + OFF
- Legend rules:
    * If TOTAL series == 1 -> legend shows ID only
    * If TOTAL series > 1  -> legend shows "Layer Name • ID"
- Legend font smaller to avoid overlap.
- Bottom info shows TOTAL VOLUME for every series.
- Interactive pan/zoom via Matplotlib NavigationToolbar2QT.

Other retained features:
- Exact, full-string ID matching for 'Q <ID>' (NO numeric coercion, NO prefix/fuzzy matching).
- Robust CSV discovery for each layer (2D and optional 1D).
- Peak markers/annotations (conditionally), tight axes, stable lifetime via _LIVE_WINDOWS.

Author: Hao Wu
"""

from __future__ import annotations
import csv
import re
from pathlib import Path
from typing import Optional, List, Tuple, Dict

from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QWidget
from qgis.PyQt.QtCore import Qt

from qgis.core import QgsFeature, QgsMapLayer, QgsProject
from qgis.utils import iface

# Qt-agnostic backend (works for Qt5 and Qt6)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure

# Strong refs to keep windows alive
_LIVE_WINDOWS: List[QDialog] = []

# Matplotlib default color cycle (fallback)
_DEFAULT_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf"
]


class TimeSeriesPlotWindow(QDialog):
    """
    TUFLOW tools — Flow (Q) Plot Viewer.
    Auto-scans selected features across all vector layers and overlays their Q time series.
    """

    def __init__(
        self,
        layer,
        # Explicit 2D (required by algorithm for initial load)
        csv_path_2d: str,
        time_header_2d: Optional[str],
        q_headers_2d: Optional[List[str]],
        parent=None,
        # Explicit 1D (optional; if absent we try discovery)
        csv_path_1d: Optional[str] = None,
        time_header_1d: Optional[str] = None,
        q_headers_1d: Optional[List[str]] = None
    ):
        super().__init__(parent)
        self.setWindowTitle('TUFLOW tools — Flow (Q) Plot Viewer')
        self.resize(1200, 640)

        # Inputs (initial)
        self.layer: Optional[QgsMapLayer] = layer
        self.csv_path_2d = Path(csv_path_2d).resolve() if csv_path_2d else None
        self.time_header_2d = time_header_2d or ''
        self.q_headers_2d = q_headers_2d or []

        self._explicit_1d: Dict[str, object] = {
            'path': Path(csv_path_1d).resolve() if csv_path_1d else None,
            'time': time_header_1d,
            'headers': q_headers_1d
        }

        # ----- Matplotlib figure/canvas -----
        self._fig = Figure(constrained_layout=True)
        self._ax = self._fig.add_subplot(111)  # left y-axis: Flow (Q)
        self._ax2 = None  # right y-axis (created when needed)
        self._canvas = FigureCanvas(self._fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)  # pan/zoom/home/save

        # ----- Top controls -----
        self._chk_volume: QCheckBox = QCheckBox('Show Volume')
        self._chk_volume.setChecked(True)
        self._chk_volume.stateChanged.connect(self._on_toggle_changed)

        self._chk_peak: QCheckBox = QCheckBox('Show Peaks')
        self._chk_peak.setChecked(True)
        self._chk_peak.stateChanged.connect(self._on_toggle_changed)

        # When enabled, use a single selected feature ID (from any layer) to search across
        # all currently selected vector layers and plot matching IDs from each layer.
        self._chk_match_single: QCheckBox = QCheckBox('Match single ID')
        self._chk_match_single.setChecked(True)
        self._chk_match_single.stateChanged.connect(self._on_toggle_changed)

        # ----- Bottom info label -----
        self._hint = QLabel('Select features (with exact ID strings) in one or more vector layers.')
        self._hint.setWordWrap(True)

        # Layout (no layer panel)
        right = QWidget()
        right_lay = QVBoxLayout()

        ctrl = QHBoxLayout()
        ctrl.addWidget(self._chk_volume)
        ctrl.addWidget(self._chk_peak)
        ctrl.addWidget(self._chk_match_single)
        ctrl.addStretch(1)

        right_lay.addLayout(ctrl)
        right_lay.addWidget(self._toolbar)   # interactive toolbar
        right_lay.addWidget(self._canvas)
        right_lay.addWidget(self._hint)
        right.setLayout(right_lay)

        main_lay = QVBoxLayout()
        main_lay.addWidget(right)
        self.setLayout(main_lay)

        # Keep a strong ref to avoid garbage collection after Processing returns
        _LIVE_WINDOWS.append(self)

        # Debounce timer for refreshes to avoid heavy repeated IO blocking the UI
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)  # ms
        self._refresh_timer.timeout.connect(self._refresh_plot)

        # Guard flags to prevent re-entrant refreshes
        self._refresh_in_progress = False
        self._pending_refresh = False

        # Sources cache per layer: {layer_id: [ {path, time, headers}, ... ] }
        self._layer_sources: Dict[str, List[Dict[str, object]]] = {}

        # Last plot payloads (one dict per series) for redraw
        self._last_plot_payloads: List[Dict[str, object]] = []

        # Prepare initial sources
        self._prepare_sources_initial()

        # Bind selectionChanged for ALL vector layers in the project
        self._bind_selection_listeners_for_all_layers()

        # Listen for ACTIVE layer changes (refresh sources & redraw)
        try:
            iface.currentLayerChanged.connect(self._on_current_layer_changed)
        except Exception:
            pass

        # Initial refresh (debounced)
        self._refresh_timer.start()

    # --------------------- SOURCES (initial active layer) ---------------------
    def _prepare_sources_initial(self):
        """
        Build sources for the initial active layer:
        - 2D from explicit inputs
        - 1D from explicit inputs (if provided)
        - Otherwise, try *_1d_Q.csv discovery
        """
        if self.layer and self.layer.type() == self.layer.VectorLayer:
            layer_id = self.layer.id()
            self._layer_sources[layer_id] = []

            # 2D source (provided)
            if self.csv_path_2d and self.csv_path_2d.is_file():
                time_hdr = self.time_header_2d or self._detect_time_header(self.csv_path_2d)
                headers = self.q_headers_2d or self._read_headers(self.csv_path_2d)
                self._layer_sources[layer_id].append({'path': self.csv_path_2d, 'time': time_hdr, 'headers': headers})

            # 1D — prefer explicit inputs, else fallback to discovery near the 2D path
            explicit = self._explicit_1d
            if explicit['path'] and isinstance(explicit['path'], Path) and explicit['path'].is_file():
                time_hdr_1d = explicit['time'] or self._detect_time_header(explicit['path'])
                headers_1d = explicit['headers'] or self._read_headers(explicit['path'])
                self._layer_sources[layer_id].append({'path': explicit['path'], 'time': time_hdr_1d, 'headers': headers_1d})
                return  # explicit found; skip discovery

            if self.csv_path_2d:
                name = self.csv_path_2d.name
                if name.lower().endswith('_2d_q.csv'):
                    csv_path_1d = self.csv_path_2d.with_name(name[:-8] + '1d_Q.csv')
                else:
                    csv_path_1d = self.csv_path_2d.with_name(self.csv_path_2d.stem + '_1d_Q.csv')

                if csv_path_1d and csv_path_1d.is_file():
                    t1d = self._detect_time_header(csv_path_1d)
                    h1d = self._read_headers(csv_path_1d)
                    self._layer_sources[layer_id].append({'path': csv_path_1d.resolve(), 'time': t1d, 'headers': h1d})
                    return

                # Ancestor search for *_1d_Q.csv
                for ancestor in [self.csv_path_2d.parent,
                                 self.csv_path_2d.parent.parent,
                                 self.csv_path_2d.parent.parent.parent]:
                    for cand in ancestor.glob('*_1d_Q.csv'):
                        if cand.is_file():
                            self._layer_sources[layer_id].append({
                                'path': cand.resolve(),
                                'time': self._detect_time_header(cand),
                                'headers': self._read_headers(cand)
                            })
                            return

    # --------------------- LISTENER BINDING ---------------------
    def _bind_selection_listeners_for_all_layers(self):
        """
        Bind selectionChanged for ALL vector layers so any selection update triggers a redraw.
        """
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() != lyr.VectorLayer:
                continue
            try:
                lyr.selectionChanged.connect(self._on_any_selection_changed, Qt.QueuedConnection)
            except Exception:
                pass

    def _on_any_selection_changed(self, *args):
        """
        Any layer's selection change triggers a redraw.
        """
        # Debounced refresh to avoid heavy IO on every selectionChanged signal
        try:
            self._refresh_timer.start()
        except Exception:
            QtCore.QTimer.singleShot(0, self._refresh_plot)

    # --------------------- ACTIVE LAYER CHANGE ---------------------
    def _on_current_layer_changed(self, new_layer: Optional[QgsMapLayer]):
        """
        When the user switches active layer in QGIS:
        - Update self.layer
        - Prepare sources for the new layer
        - (Re)bind selection listeners for all layers
        - Redraw
        """
        self.layer = new_layer

        # Ensure sources for the new active layer
        if self.layer and self.layer.type() == self.layer.VectorLayer:
            self._prepare_sources_for_layer(self.layer)

        # (Re)bind listeners (defensive)
        self._bind_selection_listeners_for_all_layers()

        # Redraw (debounced)
        try:
            self._refresh_timer.start()
        except Exception:
            QtCore.QTimer.singleShot(0, self._refresh_plot)

    # --------------------- SOURCES FOR A GIVEN LAYER ---------------------
    def _prepare_sources_for_layer(self, layer: Optional[QgsMapLayer]):
        """
        Prepare sources for a given layer:
        - Guess <scenario>_2d_Q.csv relative to layer.source()
        - Derive/guess <scenario>_1d_Q.csv
        """
        if not layer or layer.type() != layer.VectorLayer:
            return

        layer_id = layer.id()
        self._layer_sources[layer_id] = []

        # Guess CSVs
        csv_2d = self._guess_2d_csv_from_layer(layer)
        csv_1d = self._guess_1d_csv_from_2d(csv_2d)

        # 2D headers/time
        if csv_2d and csv_2d.is_file():
            t2d, h2d = self._peek_headers(csv_2d)
            if t2d:
                self._layer_sources[layer_id].append({'path': csv_2d, 'time': t2d, 'headers': h2d})

        # 1D headers/time (optional)
        if csv_1d and csv_1d.is_file():
            t1d, h1d = self._peek_headers(csv_1d)
            if t1d:
                self._layer_sources[layer_id].append({'path': csv_1d, 'time': t1d, 'headers': h1d})

        # Fallback: search ancestors for *_1d_Q.csv if not already added
        has_1d = any(p['path'].name.lower().endswith('_1d_q.csv') for p in self._layer_sources[layer_id])
        if not has_1d:
            roots = []
            if csv_2d:
                roots = [csv_2d.parent, csv_2d.parent.parent, csv_2d.parent.parent.parent]
            else:
                p = Path(layer.source().split('\n')[0]).resolve()
                roots = [p.parent, p.parent.parent, p.parent.parent.parent]
            for root in roots:
                for cand in root.glob('*_1d_Q.csv'):
                    if cand.is_file():
                        t, h = self._peek_headers(cand)
                        if t:
                            self._layer_sources[layer_id].append({'path': cand.resolve(), 'time': t, 'headers': h})
                            return

    # --------------------- REFRESH / DRAW ---------------------
    def _refresh_plot(self):
        """
        Scan ALL vector layers, collect all selected features, and plot their Q time series.
        - Each selected feature must have an 'ID' field (exact full-string match to the CSV 'Q <ID>' token).
        """
        # Re-entrancy guard: if a refresh is already running, mark pending and return.
        if getattr(self, '_refresh_in_progress', False):
            self._pending_refresh = True
            return

        self._refresh_in_progress = True
        payloads: List[Dict[str, object]] = []
        info_lines: List[str] = []

        # If match-single is enabled, choose one selected feature ID (first encountered)
        # and then search for that ID across each vector layer (regardless of whether
        # the feature is selected in that layer).
        match_single = getattr(self, '_chk_match_single', None) and self._chk_match_single.isChecked()
        search_id: Optional[str] = None
        if match_single:
            # collect all selected features across all vector layers
            all_sel = []
            for l in QgsProject.instance().mapLayers().values():
                if l.type() != l.VectorLayer:
                    continue
                try:
                    all_sel.extend([f for f in l.selectedFeatures()])
                except Exception:
                    pass

            if not all_sel:
                info_lines.append('Match-single enabled but no selected feature found')
                match_single = False
            else:
                # pick first selected feature's ID
                first = all_sel[0]
                search_id = str(first['ID']).strip() if 'ID' in first.fields().names() else ''
                if not search_id:
                    info_lines.append('Match-single: selected feature has empty or missing ID')
                    match_single = False

        # Determine user-selected layers in the Layers panel (if available).
        selected_layer_ids = set()
        try:
            if iface and hasattr(iface, 'layerTreeView'):
                sel_layers = iface.layerTreeView().selectedLayers()
                selected_layer_ids = {l.id() for l in sel_layers}
        except Exception:
            selected_layer_ids = set()

        # Iterate all vector layers
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() != lyr.VectorLayer:
                continue

            # Determine which features to process for this layer:
            # - If match_single: find the feature with ID == search_id (if present)
            # - Otherwise: use the layer's selected features
            sel = []
            try:
                if match_single and search_id:
                    # If the user has selected layers in the Layers panel, only consider
                    # those layers. Otherwise, fall back to requiring that the layer
                    # has feature selection (selectedFeatureIds / selectedFeatures).
                    if selected_layer_ids:
                        if lyr.id() not in selected_layer_ids:
                            continue
                    else:
                        try:
                            if not lyr.selectedFeatureIds():
                                continue
                        except Exception:
                            # If selectedFeatureIds is not available, fall back to selectedFeatures check
                            try:
                                if not list(lyr.selectedFeatures()):
                                    continue
                            except Exception:
                                continue

                    # find first feature with matching ID in this layer
                    found = None
                    for f in lyr.getFeatures():
                        try:
                            if 'ID' in lyr.fields().names() and str(f['ID']).strip() == search_id:
                                found = f
                                break
                        except Exception:
                            continue
                    if found:
                        sel = [found]
                    else:
                        # no match in this layer — skip
                        continue
                else:
                    sel = lyr.selectedFeatures()
                    if not sel:
                        continue
            except Exception:
                continue

            # Ensure sources ready for this layer
            self._prepare_sources_for_layer(lyr)
            sources = self._layer_sources.get(lyr.id(), [])
            if not sources:
                info_lines.append(f"{lyr.name()}: no Q CSV found")
                continue

            # Validate ID field once
            has_id = 'ID' in lyr.fields().names()
            if not has_id:
                info_lines.append(f"{lyr.name()}: missing 'ID' field")
                continue

            # For each selected feature in this layer, build a series
            for f in sel:
                id_val = str(f['ID']).strip()
                if not id_val:
                    info_lines.append(f"{lyr.name()}: empty ID (skipped)")
                    continue

                # Resolve 'Q <ID>' header using EXACT token match
                src, q_header = self._find_q_column_exact(sources, id_val)
                if src is None or q_header is None:
                    info_lines.append(f"{lyr.name()}: no 'Q {id_val}' column (skipped)")
                    continue

                # Read time + Q
                try:
                    t_vals, q_vals = self._read_two_columns(src['path'], src['time'], q_header)
                except Exception as ex:
                    info_lines.append(f"{lyr.name()}: CSV read error ({ex})")
                    continue

                if not t_vals:
                    info_lines.append(f"{lyr.name()}: no data for ID={id_val}")
                    continue

                # Sort by time
                pairs = sorted(zip(t_vals, q_vals), key=lambda x: x[0])
                t_vals, q_vals = zip(*pairs)

                # Metrics
                peak_q = max(q_vals, key=abs)
                i_peak = q_vals.index(peak_q)
                t_peak = t_vals[i_peak]
                cum_vol_m3, total_vol_m3 = self._compute_cumulative_volume(t_vals, q_vals)

                # Assign color based on global series index
                color = _DEFAULT_COLORS[len(payloads) % len(_DEFAULT_COLORS)]

                payloads.append({
                    'layer': lyr,
                    't_vals': t_vals,
                    'q_vals': q_vals,
                    'cum_vol_m3': cum_vol_m3,
                    'total_vol_m3': total_vol_m3,
                    'id_val': id_val,
                    'peak_q': peak_q,
                    't_peak': t_peak,
                    'color': color
                })

                # Bottom info: include total volume for every series
                info_lines.append(
                    f"{lyr.name()}: ID={id_val}  Qp={peak_q:.3f} m³/s  V={total_vol_m3:,.0f} m³"
                )

        # Auto toggles based on TOTAL series count
        n_series = len(payloads)
        if n_series > 1:
            # Disable & turn OFF both toggles
            for chk in (self._chk_volume, self._chk_peak):
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.setEnabled(False)
                chk.blockSignals(False)
        elif n_series == 1:
            # Enable & turn ON both toggles
            for chk in (self._chk_volume, self._chk_peak):
                chk.blockSignals(True)
                chk.setEnabled(True)
                chk.setChecked(True)
                chk.blockSignals(False)
        else:
            # No series → allow toggles but keep state; show empty later
            for chk in (self._chk_volume, self._chk_peak):
                chk.setEnabled(True)

        # Cache & draw
        try:
            self._last_plot_payloads = payloads
            self._draw_plot(info_lines)
        finally:
            # Clear in-progress flag and handle pending refresh
            self._refresh_in_progress = False
            if getattr(self, '_pending_refresh', False):
                self._pending_refresh = False
                # schedule another refresh shortly
                try:
                    self._refresh_timer.start()
                except Exception:
                    QtCore.QTimer.singleShot(50, self._refresh_plot)

    def _on_toggle_changed(self, state: int):
        """
        Redraw when the user toggles volume or peaks.
        In multi-series mode, toggles are disabled+OFF via _refresh_plot.
        """
        if not self._last_plot_payloads:
            self._show_empty("Select features with 'ID' in vector layers")
            return
        self._draw_plot()  # Keep current payloads; just redraw

    # --------------------- PLOTTING ---------------------

    def _draw_plot(self, info_lines: Optional[List[str]] = None):
        # Guard
        if not self._last_plot_payloads:
            self._show_empty("Select features with 'ID' in vector layers")
            return

        payloads = self._last_plot_payloads
        n_series = len(payloads)

        # --- NEW: determine number of UNIQUE layers involved ---
        unique_layers = {p['layer'].id() for p in payloads}
        n_layers_involved = len(unique_layers)
        # -------------------------------------------------------

        # Clear axes
        self._ax.clear()
        self._remove_secondary_axis()

        handles, labels = [], []

        for p in payloads:
            t_vals = p['t_vals']
            q_vals = p['q_vals']
            color  = p['color']
            lyr    = p['layer']
            id_val = p['id_val']
            peak_q = p['peak_q']
            t_peak = p['t_peak']

            # --- NEW LEGEND RULES ---
            # If features are all from a single layer -> legend shows ID only.
            # If features span multiple layers -> legend shows "Layer • ID".
            legend_label = id_val if n_layers_involved == 1 else f"{lyr.name()} • {id_val}"
            # ------------------------

            line = self._ax.plot(t_vals, q_vals, color=color, linewidth=1.8,
                                label=legend_label)[0]

            # Peak markers/annotations ONLY if peaks toggle is ON
            if self._chk_peak.isChecked():
                self._ax.scatter([t_peak], [peak_q],
                                facecolors='white', edgecolors=color,
                                linewidths=1.5, zorder=3)
                self._ax.annotate(
                    f'{peak_q:.3f} m³/s',
                    xy=(t_peak, peak_q),
                    xytext=(5, 6),
                    textcoords='offset points',
                    color=color,
                    fontsize=9,
                    bbox=dict(boxstyle='round,pad=0.2', fc='white', ec=color, alpha=0.7)
                )

            handles.append(line)
            labels.append(legend_label)

        # Axes labels/title/grid
        self._ax.set_xlabel('Time (h)')
        self._ax.set_ylabel('Flow (m³/s)')
        self._ax.set_title('Flow (Q) time series')
        self._ax.grid(True, alpha=0.4)

        # Tight axes margins
        self._ax.margins(x=0, y=0.15)

        # Common x-limits across series
        all_t = [t for p in payloads for t in p['t_vals']]
        if all_t:
            self._ax.set_xlim(min(all_t), max(all_t))

        # Left axis baseline at 0
        top_q = self._ax.get_ylim()[1]
        self._ax.set_ylim(0.0, top_q)
        self._ax.axhline(0.0, color='#888', linewidth=0.8, alpha=0.6, zorder=0)

        # Right axis (Volume) — only if SINGLE series AND toggle ON
        if n_series == 1 and self._chk_volume.isChecked():
            p = payloads[0]
            cum_vol_km3 = [v / 1000.0 for v in p['cum_vol_m3']]  # thousands (10^3 m³)
            self._ax2 = self._ax.twinx()
            self._ax2.plot(p['t_vals'], cum_vol_km3, color='#ff7f0e', linewidth=1.4, alpha=0.9,
                           label='Accumulated Volume (10³ m³)')
            self._ax2.set_ylabel('Accumulated Volume (10³ m³)')
            self._ax2.grid(False)
            self._ax2.set_xlim(self._ax.get_xlim())
            # Align baseline at 0
            top_v = self._ax2.get_ylim()[1]
            self._ax2.set_ylim(0.0, top_v)

        # Legend (top-left) with smaller font size
        self._ax.legend(handles, labels, loc='upper left',
                        frameon=True, framealpha=0.85, borderpad=0.4,
                        fontsize=8)

        # Draw
        self._canvas.draw_idle()

        # Bottom info
        if info_lines:
            self._hint.setText("\n".join(info_lines))
        else:
            s = []
            for p in payloads:
                lyr = p['layer']
                id_val = p['id_val']
                peak_q = p['peak_q']
                total_v = p['total_vol_m3']
                s.append(f"{lyr.name()}: ID={id_val}  Qp={peak_q:.3f} m³/s  V={total_v:,.0f} m³")
            self._hint.setText("\n".join(s))

    def _show_empty(self, title: str):
        self._ax.clear()
        self._remove_secondary_axis()
        self._ax.set_title(title)
        self._ax.set_xlabel('Time (h)')
        self._ax.set_ylabel('Flow (m³/s)')
        self._ax.grid(True, alpha=0.4)
        self._canvas.draw_idle()

    # --------------------- EXACT ID MATCHING ---------------------
    def _candidate_id_variants(self, id_val: str) -> List[str]:
        """
        ID is a string and can be any characters.
        NO numeric-only coercion; NO case folding; ONLY trimmed full string.
        """
        s = id_val.strip()
        return [s] if s else []

    def _find_q_column_exact(
        self,
        sources: List[Dict[str, object]],
        id_val: str
    ) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
        """
        Exact token match:
        - Extract the token immediately after 'Q ' (up to first space or '[').
        - Compare EXACTLY (case-sensitive) to the selected ID (trimmed).
        """
        variants = self._candidate_id_variants(id_val)  # just [exact_id]
        if not variants:
            return None, None
        exact_id = variants[0]

        for src in sources:
            headers: List[str] = src.get('headers', [])
            time_hdr: Optional[str] = src.get('time')
            if not headers or not time_hdr:
                continue

            # Build token map: token after 'Q ' -> full header
            token_map = {}
            for h in headers:
                m = re.match(r'^Q\s+([^\s\[]+)', h)  # token can include hyphens/underscores/digits/letters
                if m:
                    token_map[m.group(1)] = h

            q_header = token_map.get(exact_id)
            if q_header:
                return src, q_header

        return None, None

    # --------------------- CSV IO / HEADERS ---------------------
    def _read_headers(self, path: Path) -> List[str]:
        try:
            with open(path, 'r', newline='', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                return [h.strip() for h in headers]
        except Exception:
            return []

    def _detect_time_header(self, path: Path) -> Optional[str]:
        for h in self._read_headers(path):
            hl = h.lower()
            if hl.startswith('time') and '(h' in hl:
                return h
        return None

    def _peek_headers(self, csv_path: Path) -> Tuple[Optional[str], List[str]]:
        """
        Returns (time_header, q_headers_list)
        - time_header: header that looks like 'Time (h)'
        - q_headers_list: headers starting with 'Q '
        """
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f)
                headers = next(reader, [])
        except Exception:
            return None, []
        headers = [h.strip() for h in headers]
        time_header = next((h for h in headers if h.lower().startswith('time') and '(h' in h.lower()), None)
        q_headers = [h for h in headers if h.startswith('Q ')]
        return time_header, q_headers

    def _read_two_columns(self, path: Path, time_header: Optional[str], q_header: str) -> Tuple[List[float], List[float]]:
        if not time_header:
            raise ValueError(f'Could not detect a time column in: {path}')
        t_vals: List[float] = []
        q_vals: List[float] = []
        with open(path, 'r', newline='', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = float(row[time_header])
                    qstr = row.get(q_header, '').strip()
                    if qstr == '' or qstr.lower() == 'nan':
                        continue
                    q = float(qstr)
                    t_vals.append(t)
                    q_vals.append(q)
                except Exception:
                    # Skip malformed rows silently
                    continue
        return t_vals, q_vals

    # --------------------- INTEGRATION ---------------------
    def _compute_cumulative_volume(self, t_vals: Tuple[float, ...], q_vals: Tuple[float, ...]) -> Tuple[List[float], float]:
        """
        Compute accumulated volume (m³) via trapezoidal rule.
        Time in hours (per 'Time (h)'), Q in m³/s.
        V_i = 0.5*(Q[i-1]+Q[i]) * Δt_hours * 3600
        Returns (cum_vol_series, total_volume).
        """
        if len(t_vals) < 2:
            return [0.0], 0.0
        cum = [0.0]
        total = 0.0
        for i in range(1, len(t_vals)):
            dt_h = t_vals[i] - t_vals[i - 1]
            if dt_h < 0:
                continue
            step = 0.5 * (q_vals[i - 1] + q_vals[i]) * dt_h * 3600.0  # hours→seconds
            total += step
            cum.append(total)
        return cum, total

    # --------------------- AXES HOUSEKEEPING ---------------------
    def _remove_secondary_axis(self):
        if hasattr(self, '_ax2') and self._ax2 is not None:
            try:
                self._fig.delaxes(self._ax2)
            except Exception:
                pass
        self._ax2 = None

    # --------------------- CSV path guessing ---------------------
    def _guess_2d_csv_from_layer(self, layer: QgsMapLayer) -> Optional[Path]:
        """
        Try to locate '<scenario>_2d_Q.csv' relative to layer.source().
        """
        try:
            p = Path(layer.source().split('\n')[0]).resolve()
        except Exception:
            return None
        base_no_ext = p.stem
        scenario = re.sub(r'_PLOT_.*$', '', base_no_ext)
        csv_name = f'{scenario}_2d_Q.csv'
        candidates = [
            p.parent.parent / 'csv' / csv_name,
            p.parent / 'csv' / csv_name,
            p.parent / csv_name
        ]
        for c in candidates:
            if c.is_file():
                return c.resolve()
        # Walk up ancestors looking for 'csv/<scenario>_2d_Q.csv'
        for ancestor in [p.parent, p.parent.parent, p.parent.parent.parent]:
            c = ancestor / 'csv' / csv_name
            if c.is_file():
                return c.resolve()
        return None

    def _guess_1d_csv_from_2d(self, csv_2d: Optional[Path]) -> Optional[Path]:
        """
        Derive/locate '<scenario>_1d_Q.csv':
        - Sibling swap if *_2d_Q.csv exists
        - Otherwise search for '*_1d_Q.csv' in nearby folders
        """
        if csv_2d and csv_2d.name.lower().endswith('_2d_q.csv'):
            csv_1d = csv_2d.with_name(csv_2d.name[:-8] + '1d_Q.csv')  # replace '2d_Q.csv' with '1d_Q.csv'
            if csv_1d.is_file():
                return csv_1d.resolve()
            csv_1d = csv_2d.with_name(csv_2d.stem[:-3] + '_1d_Q.csv')
            if csv_1d.is_file():
                return csv_1d.resolve()

        # Nearby search
        search_roots: List[Path] = []
        if csv_2d:
            base_folder = csv_2d.parent
            search_roots += [base_folder, base_folder.parent, base_folder.parent.parent]
        for root in search_roots:
            for cand in root.glob('*_1d_Q.csv'):
                if cand.is_file():
                    return cand.resolve()
        return None

    # --------------------- CLOSE ---------------------
    def closeEvent(self, e):
        # Disconnect safely
        try:
            iface.currentLayerChanged.disconnect(self._on_current_layer_changed)
        except Exception:
            pass

        # Disconnect selectionChanged for all vector layers
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() != lyr.VectorLayer:
                continue
            try:
                lyr.selectionChanged.disconnect(self._on_any_selection_changed)
            except Exception:
                pass

        # Remove strong ref
        try:
            _LIVE_WINDOWS.remove(self)
        except ValueError:
            pass

        super().closeEvent(e)
# EOF