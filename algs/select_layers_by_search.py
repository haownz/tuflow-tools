# -*- coding: utf-8 -*-
"""
Select Layers by Search
-----------------------
A small dialog that lets the user type a search pattern and highlights / selects
all matching layers in the QGIS Layers panel.

Matching rules (applied in order, first match wins):
  1. Wildcard  – pattern contains * or ?  → fnmatch
  2. Fuzzy     – no wildcards            → every character in pattern must appear
                                            in the layer name in order (subsequence)

Matching is always case-insensitive.
"""

import fnmatch
from qgis.PyQt.QtCore import Qt, QSortFilterProxyModel, QStringListModel
from qgis.PyQt.QtGui import QColor, QFont, QStandardItemModel, QStandardItem
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QCheckBox,
    QFrame,
    QSizePolicy,
    QApplication,
)
from qgis.core import QgsProject, QgsLayerTree, QgsLayerTreeLayer


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _is_wildcard(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern


def _wildcard_match(pattern: str, name: str) -> bool:
    return fnmatch.fnmatch(name.lower(), pattern.lower())


def _fuzzy_match(pattern: str, name: str) -> tuple[bool, int]:
    """
    Returns (matched, score).
    Score = number of consecutive characters matched (higher = better).
    """
    p = pattern.lower()
    n = name.lower()
    pi = 0
    consecutive = 0
    max_consecutive = 0
    for ni, ch in enumerate(n):
        if pi < len(p) and ch == p[pi]:
            pi += 1
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
    matched = pi == len(p)
    return matched, max_consecutive


def collect_all_layers(root=None):
    """Yield (QgsLayerTreeLayer node, QgsMapLayer) for every layer in the tree."""
    if root is None:
        root = QgsProject.instance().layerTreeRoot()
    for node in root.findLayers():
        layer = node.layer()
        if layer:
            yield node, layer


def match_layers(pattern: str):
    """
    Return a list of (score, node, layer) sorted by best score first.
    """
    pattern = pattern.strip()
    if not pattern:
        return []

    results = []
    if _is_wildcard(pattern):
        for node, layer in collect_all_layers():
            if _wildcard_match(pattern, layer.name()):
                results.append((1, node, layer))
    else:
        for node, layer in collect_all_layers():
            matched, score = _fuzzy_match(pattern, layer.name())
            if matched:
                results.append((score, node, layer))
        results.sort(key=lambda x: -x[0])

    return results


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class SelectLayersBySearchDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("Select Layers by Search")
        self.setMinimumWidth(480)
        self.setMinimumHeight(400)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self._matched_layers = []  # (node, layer) pairs currently shown
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # ── Search row ────────────────────────────────────────────────
        search_label = QLabel(
            "Search pattern  <small style='color:#888'>"
            "(wildcard: <b>*</b> <b>?</b> &nbsp;|&nbsp; fuzzy: partial text)</small>"
        )
        search_label.setTextFormat(Qt.RichText)
        layout.addWidget(search_label)

        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("e.g.  2d_bc*  or  boun  or  *result*")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        row.addWidget(self.search_edit)
        layout.addLayout(row)

        # ── Options ───────────────────────────────────────────────────
        opt_row = QHBoxLayout()
        self.chk_add = QCheckBox("Add to current selection")
        self.chk_add.setChecked(False)
        opt_row.addWidget(self.chk_add)
        opt_row.addStretch()
        self.lbl_count = QLabel("0 layers matched")
        self.lbl_count.setStyleSheet("color: #888; font-style: italic;")
        opt_row.addWidget(self.lbl_count)
        layout.addLayout(opt_row)

        # ── Separator ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # ── Results list ──────────────────────────────────────────────
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        self.list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.list_widget)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self.btn_select = btn_box.addButton("Select Layers", QDialogButtonBox.AcceptRole)
        self.btn_select.setEnabled(False)
        btn_box.addButton(QDialogButtonBox.Close)
        btn_box.accepted.connect(self._apply_selection)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.setLayout(layout)
        self.search_edit.setFocus()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_search_changed(self, text):
        results = match_layers(text)
        self._matched_layers = [(node, layer) for _, node, layer in results]
        self._populate_list(results)
        n = len(self._matched_layers)
        self.lbl_count.setText(f"{n} layer{'s' if n != 1 else ''} matched")
        self.btn_select.setEnabled(n > 0)

    def _populate_list(self, results):
        self.list_widget.clear()
        for _, node, layer in results:
            item = QListWidgetItem(layer.name())
            # colour icon to layer type
            geom_type = getattr(layer, "geometryType", None)
            item.setToolTip(layer.source())
            self.list_widget.addItem(item)

    def _apply_selection(self):
        if not self._matched_layers:
            return

        view = self.iface.layerTreeView()
        model = view.model()
        selection_model = view.selectionModel()

        from qgis.PyQt.QtCore import QItemSelection, QItemSelectionModel

        if not self.chk_add.isChecked():
            selection_model.clearSelection()

        for node, layer in self._matched_layers:
            idx = view.node2index(node)
            if idx.isValid():
                selection_model.select(
                    idx,
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )

        # Close the dialog after applying
        self.accept()


# ---------------------------------------------------------------------------
# Entry point called from plugin.py
# ---------------------------------------------------------------------------

def show_select_layers_dialog(iface):
    dlg = SelectLayersBySearchDialog(iface)
    dlg.exec_()
