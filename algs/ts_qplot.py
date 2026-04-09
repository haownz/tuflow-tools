# -*- coding: utf-8 -*-
import re
import csv
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
)
from qgis.utils import iface

from .ts_qplot_window import TimeSeriesPlotWindow


class TimeSeriesQPlotAlgorithm(QgsProcessingAlgorithm):
    # Algorithm id (renamed earlier)
    ALG_ID = "flow_q_plot"

    def name(self):
        return self.ALG_ID

    def displayName(self):
        return self.tr("Flow Plot Viewer")

    def group(self):
        return self.tr("2 - Result Analysis")

    def groupId(self):
        return "result_analysis"

    def tr(self, string):
        return QCoreApplication.translate("TimeSeriesQPlotAlgorithm", string)

    def createInstance(self):
        return TimeSeriesQPlotAlgorithm()

    def initAlgorithm(self, config=None):
        pass

    # ------------- main -------------
    def processAlgorithm(
        self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ):
        # Launch viewer with active layer (if available) for CSV discovery
        layer = iface.activeLayer()

        # Try to find CSV files from active layer or generic search
        csv_2d = None
        csv_1d = None
        time_2d = None
        q_headers_2d = []
        time_1d = None
        q_headers_1d = []

        if layer and layer.type() == layer.VectorLayer:
            # Guess CSVs from active layer
            csv_2d = self._guess_2d_csv_path(layer.source())
            csv_1d = self._guess_1d_csv_path(csv_2d)

        # Read headers if CSVs found
        if csv_2d:
            time_2d, q_headers_2d = self._peek_headers(csv_2d)

        if csv_1d:
            time_1d, q_headers_1d = self._peek_headers(csv_1d)

        # Launch viewer with any discovered CSVs (or None if not found)
        w = TimeSeriesPlotWindow(
            layer,
            csv_path_2d=str(csv_2d) if csv_2d else "",
            time_header_2d=time_2d or "",
            q_headers_2d=q_headers_2d or [],
            csv_path_1d=str(csv_1d) if csv_1d else None,
            time_header_1d=time_1d,
            q_headers_1d=q_headers_1d,
        )
        w.show()
        return {}

    # ------------- helpers -------------
    def _guess_2d_csv_path(self, vector_source: str) -> Path | None:
        """
        Try to locate a '<scenario>_2d_Q.csv' relative to the vector layer source.
        Handles common QGIS source strings with |layerid=... etc.
        """
        p = Path(vector_source.split("|")[0]).resolve()
        base_no_ext = p.stem
        scenario = re.sub(r"_PLOT_.*$", "", base_no_ext)
        csv_name = f"{scenario}_2d_Q.csv"

        candidates = [
            p.parent.parent / "csv" / csv_name,
            p.parent / "csv" / csv_name,
            p.parent / csv_name,
        ]
        for c in candidates:
            if c.is_file():
                return c

        # Walk up ancestors looking for a 'csv/<scenario>_2d_Q.csv'
        for ancestor in [p.parent, p.parent.parent, p.parent.parent.parent]:
            c = ancestor / "csv" / csv_name
            if c.is_file():
                return c
        return None

    def _guess_1d_csv_path(self, csv_2d: Path | None) -> Path | None:
        """
        Try to derive and locate '<scenario>_1d_Q.csv':
        - Sibling swap if *_2d_Q.csv exists
        - Otherwise look for '*_1d_Q.csv' beside and in ancestor 'csv' folders
        """
        if csv_2d and csv_2d.name.lower().endswith("_2d_q.csv"):
            # Direct sibling replacement
            csv_1d = csv_2d.with_name(
                csv_2d.name[:-8] + "1d_Q.csv"
            )  # replace '2d_Q.csv' with '1d_Q.csv'
            if csv_1d.is_file():
                return csv_1d
            # Also try same folder with scenario stem
            csv_1d = csv_2d.with_name(csv_2d.stem[:-3] + "_1d_Q.csv")  # robust fallback
            if csv_1d.is_file():
                return csv_1d

        # If no 2D given, or replacement not found, search nearby
        base_folder = csv_2d.parent if csv_2d else None
        search_roots = []
        if base_folder:
            search_roots += [base_folder, base_folder.parent, base_folder.parent.parent]
        else:
            search_roots += [Path.cwd()]  # last resort

        for root in search_roots:
            for cand in root.glob("*_1d_Q.csv"):
                if cand.is_file():
                    return cand.resolve()
        return None

    def _peek_headers(self, csv_path: Path):
        """
        Returns (time_header, q_headers_list)
        - time_header: first header that looks like 'Time (h)'
        - q_headers_list: headers starting with 'Q '
        """
        try:
            with open(
                csv_path, "r", newline="", encoding="utf-8", errors="ignore"
            ) as f:
                reader = csv.reader(f)
                headers = next(reader, [])
        except Exception as ex:
            raise QgsProcessingException(
                f"Error reading headers from {csv_path}:\n{ex}"
            )

        headers = [h.strip() for h in headers]
        time_header = next(
            (h for h in headers if h.lower().startswith("time") and "(h" in h.lower()),
            None,
        )
        q_headers = [h for h in headers if h.startswith("Q ")]
        return time_header, q_headers
