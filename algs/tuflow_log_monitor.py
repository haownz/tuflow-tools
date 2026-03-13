
# -*- coding: utf-8 -*-
"""
TUFLOW Status Monitor (auto-follow latest .tsf; tail matching .tlf)

- Follows the latest .tsf in a selected folder to parse status/percent/metadata.
- Tail text view reads the corresponding .tlf (same base name as the .tsf).
- Tail lines are customizable (default: 30) and remembered via QSettings.
- All keys are remembered via QSettings:
    * Log folder, Refresh interval, Auto-close, Follow latest, Tail lines.
- Auto-scrolls to the bottom of the tail view on each refresh.
- Colour-highlighted status and log lines; humanized times.

Tested on QGIS 3.44 (PyQGIS), using FlagNoThreading to safely create GUI from a Processing alg.
"""
from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.PyQt.QtCore import Qt, QTimer, QDateTime, QSettings, QRegExp
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QProgressBar, QPushButton, QPlainTextEdit,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QSizePolicy
)
from qgis.PyQt.QtGui import (
    QColor, QSyntaxHighlighter, QTextCharFormat, QBrush, QFont, QTextCursor
)
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingOutputBoolean,
    QgsMessageLog,
    Qgis,
    QgsProcessingException,
)
import os
import re

# Keep references so Python GC doesn't close windows
MONITOR_WINDOWS = []

# Settings keys
SETTINGS_KEY_LOG_DIR    = "TUFLOW/LogFolder"
SETTINGS_KEY_REFRESH    = "TUFLOW/RefreshSecs"
SETTINGS_KEY_AUTOCLOSE  = "TUFLOW/AutoCloseWhenFinished"
SETTINGS_KEY_FOLLOW     = "TUFLOW/FollowLatestTSF"
SETTINGS_KEY_TAIL_LINES = "TUFLOW/MonitorTailLines"

# -------------------------
# Helpers
# -------------------------
def _safe_tail_read(path, max_bytes=200 * 1024):
    """
    Read the last max_bytes from file (UTF-8), returning clean text.
    Helps keep UI responsive with large/remote logs.
    """
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        text = data.decode('utf-8', errors='ignore')
        # Drop partial first line so we return complete lines
        i = text.find('\n')
        return text[i + 1:] if i >= 0 else text
    except Exception as e:
        QgsMessageLog.logMessage(f"TUFLOW Monitor: read error: {e}",
                                 "TUFLOW Monitor", Qgis.Warning)
        return ""

def _parse_key_values(text):
    """
    Parse TUFLOW 'Key == Value' lines and keep last occurrence of each key.
    """
    kv = {}
    pat = re.compile(r'^\s*(.*?)\s*==\s*(.*)\s*$')
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            kv[m.group(1).strip()] = m.group(2).strip()
    return kv

def _num(value, default=None):
    """
    Extract numeric float from messy strings like '16.', '600.', '0.3825 h'.
    """
    if value is None:
        return default
    try:
        return float(re.sub(r'[^0-9.\-]', '', str(value)))
    except Exception:
        return default

def hours_to_hhmmss(value):
    """
    Convert decimal hours to 'hh:mm:ss'. Returns None if not parseable.
    """
    h = _num(value, default=None)
    if h is None:
        return None
    total = int(round(h * 3600))
    hhh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hhh:02d}:{mm:02d}:{ss:02d}"

def secs_to_hhmmss(value):
    """
    Convert seconds to 'hh:mm:ss'. Returns None if not parseable.
    """
    s = _num(value, default=None)
    if s is None:
        return None
    total = int(round(s))
    hhh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hhh:02d}:{mm:02d}:{ss:02d}"

def _find_latest_tsf(dir_path):
    """
    Return the path of the most recently modified *.tsf file in dir_path.
    None if none exist or dir not accessible.
    """
    try:
        if not os.path.isdir(dir_path):
            return None
        latest = None
        latest_mtime = -1
        # Non-recursive scan to keep it snappy on network shares
        for name in os.listdir(dir_path):
            if name.lower().endswith(".tsf"):
                full = os.path.join(dir_path, name)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest = full
                except Exception:
                    pass
        return latest
    except Exception as e:
        QgsMessageLog.logMessage(f"TUFLOW Monitor: dir scan error: {e}",
                                 "TUFLOW Monitor", Qgis.Warning)
        return None

def _corresponding_tlf(tsf_path):
    """
    Return the .tlf path that shares the base name with tsf_path.
    Example: foo.tsf -> foo.tlf (same folder). None if tsf_path is None.
    """
    if not tsf_path:
        return None
    base, _ = os.path.splitext(tsf_path)
    return base + ".tlf"

# -------------------------
# Syntax highlighter for log tail
# -------------------------
class LogHighlighter(QSyntaxHighlighter):
    """
    Colours important tokens in the tail log view (QPlainTextEdit).
    WARNING, CHECK, ERROR/FATAL/FAILED, Percentage Complete, FINISHED, etc.
    """
    def __init__(self, parent_document):
        super().__init__(parent_document)
        self.rules = []
        def fmt(color_hex, bold=False):
            f = QTextCharFormat()
            f.setForeground(QBrush(QColor(color_hex)))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        # Patterns (case-insensitive)
        self.rules += [
            (QRegExp(r"\bERROR\b|\bFATAL\b|\bFAILED\b", Qt.CaseInsensitive), fmt("#c62828", True)),  # red
            (QRegExp(r"\bWARNING[s]?\b", Qt.CaseInsensitive), fmt("#ef6c00", True)),                  # orange
            (QRegExp(r"\bCHECK[s]?\b", Qt.CaseInsensitive), fmt("#6a1b9a", True)),                    # purple
            (QRegExp(r"Percentage Complete\s*\(\%\)\s*\=\=\s*\d+(\.\d+)?", Qt.CaseInsensitive), fmt("#1565c0")),  # blue
            (QRegExp(r"\bSimulation Status\s*\=\=\s*FINISHED\b", Qt.CaseInsensitive), fmt("#2e7d32", True)),      # green
            # Additional keywords for TUFLOW log lines
            (QRegExp(r"SIM:"), fmt("#1565c0")),  # blue for SIM
            (QRegExp(r"-d"), fmt("#1565c0")),    # blue for -d
            (QRegExp(r"\b0D\b"), fmt("#2e7d32")),  # green for 0D
            (QRegExp(r"\b1D\b"), fmt("#2e7d32")),  # green for 1D
            (QRegExp(r"\b2D\b"), fmt("#2e7d32")),  # green for 2D
            (QRegExp(r"\bCE\b"), fmt("#6a1b9a")),  # purple for CE
            (QRegExp(r"\bVi\b"), fmt("#6a1b9a")),  # purple for Vi
            (QRegExp(r"\bVo\b"), fmt("#6a1b9a")),  # purple for Vo
            (QRegExp(r"\bdV\b"), fmt("#6a1b9a")),  # purple for dV
            (QRegExp(r"\bQuadtree\b"), fmt("#ef6c00")),  # orange for Quadtree
            (QRegExp(r"\bOutput\b"), fmt("#ef6c00")),    # orange for Output
            (QRegExp(r"\bClock\b"), fmt("#ef6c66")),     # orange for Clock
            (QRegExp(r"\bCPU\b"), fmt("#ef6cff")),       # orange for CPU
        ]

    def highlightBlock(self, text):
        for rx, fmt in self.rules:
            i = rx.indexIn(text, 0)
            while i >= 0:
                length = rx.matchedLength()
                self.setFormat(i, length, fmt)
                i = rx.indexIn(text, i + length)

# -------------------------
# Monitor Dialog
# -------------------------
class TuflowMonitorWidget(QDialog):
    def __init__(self, dir_path, refresh_secs=10, auto_close=False, follow_latest=True,
                 tail_lines=30, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TUFLOW Running Status Monitor")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(860, 620)

        self.dir_path = dir_path
        self.log_path = _find_latest_tsf(dir_path)
        self.tlf_path = _corresponding_tlf(self.log_path)
        self.refresh_ms = max(1, int(refresh_secs)) * 1000
        self.auto_close = bool(auto_close)
        self.follow_latest = bool(follow_latest)
        self.tail_lines = max(1, int(tail_lines))
        self._updating = False

        # --- UI ---
        main = QVBoxLayout(self)

        # Folder + scenario info
        info = QFormLayout()
        self.lbl_dir = QLabel(self.dir_path or "—")
        scenario_name = os.path.splitext(os.path.basename(self.log_path))[0] if self.log_path else "—"
        self.lbl_scenario = QLabel(scenario_name)
        self.lbl_scenario.setStyleSheet("color: #455a64; font-weight: 600;")  # blue-grey
        info.addRow("Current Scenario:", self.lbl_scenario)
        main.addLayout(info)

        # Top row: core status
        top = QFormLayout()
        self.lbl_status = QLabel("—")
        self.lbl_pct = QLabel("—")
        self.lbl_steps = QLabel("—")
        top.addRow("Simulation Status:", self.lbl_status)
        top.addRow("Percentage Complete (%):", self.lbl_pct)
        top.addRow("Completed Steps:", self.lbl_steps)
        main.addLayout(top)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        main.addWidget(self.progress)

        # Times (humanized only)
        times = QFormLayout()
        self.lbl_clock = QLabel("—")
        self.lbl_remaining = QLabel("—")
        self.lbl_simstart = QLabel("—")
        self.lbl_simend = QLabel("—")
        self.lbl_simtime = QLabel("—")
        times.addRow("Clock Time:", self.lbl_clock)
        times.addRow("Time Remaining:", self.lbl_remaining)
        times.addRow("Simulation Start:", self.lbl_simstart)
        times.addRow("Simulation End:", self.lbl_simend)
        times.addRow("Simulation Time:", self.lbl_simtime)
        main.addLayout(times)

        # Intervals in seconds (humanized only)
        intervals = QFormLayout()
        self.lbl_summary = QLabel("—")
        self.lbl_shortest = QLabel("—")
        intervals.addRow("Summary Output Interval:", self.lbl_summary)
        intervals.addRow("Shortest Map Output Interval:", self.lbl_shortest)
        main.addLayout(intervals)

        # Additional info
        more = QFormLayout()
        self.lbl_build = QLabel("—")
        self.lbl_scheme = QLabel("—")
        self.lbl_hw = QLabel("—")
        self.lbl_gpu = QLabel("—")
        self.lbl_comp = QLabel("—")
        self.lbl_domain = QLabel("—")
        self.lbl_cells = QLabel("—")
        self.lbl_warns = QLabel("—")
        self.lbl_checks = QLabel("—")
        self.lbl_updated = QLabel("—")
        more.addRow("Build:", self.lbl_build)
        more.addRow("Solution Scheme:", self.lbl_scheme)
        more.addRow("Hardware:", self.lbl_hw)
        more.addRow("GPU Device IDs:", self.lbl_gpu)
        more.addRow("Computer:", self.lbl_comp)
        more.addRow("2D Domain(s):", self.lbl_domain)
        more.addRow("Active / Total 2D Cells:", self.lbl_cells)
        more.addRow("Warnings (pre/during):", self.lbl_warns)
        more.addRow("CHECKs (pre/during):", self.lbl_checks)
        more.addRow("Last Updated:", self.lbl_updated)
        main.addLayout(more)

        # Tail view (.tlf)
        self.tail = QPlainTextEdit()
        self.tail.setReadOnly(True)
        self.tail.setMinimumHeight(240)
        main.addWidget(self.tail)

        # Attach syntax highlighter
        self._highlighter = LogHighlighter(self.tail.document())

        # Buttons
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh Now")
        self.btn_open_tsf = QPushButton("Open .tsf")
        self.btn_open_tlf = QPushButton("Open .tlf")
        self.btn_open_dir = QPushButton("Open Folder")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_refresh)
        btns.addStretch()
        btns.addWidget(self.btn_open_tsf)
        btns.addWidget(self.btn_open_tlf)
        btns.addWidget(self.btn_open_dir)
        btns.addWidget(self.btn_close)
        main.addLayout(btns)

        self.btn_refresh.clicked.connect(self.update_once)
        self.btn_open_tsf.clicked.connect(lambda: self._open_path(self.log_path))
        self.btn_open_tlf.clicked.connect(lambda: self._open_path(self.tlf_path))
        self.btn_open_dir.clicked.connect(lambda: self._open_path(self.dir_path))
        self.btn_close.clicked.connect(self.close)

        # Timer
        self.timer = QTimer(self)
        self.timer.setInterval(self.refresh_ms)
        self.timer.timeout.connect(self.update_once)
        self.timer.start()

        # Initial update
        self.update_once()

    def _open_path(self, path):
        if path and os.path.exists(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _set(self, label, text):
        label.setText(text if text else "—")

    def _set_coloured_status(self, status: str):
        if not status:
            self.lbl_status.setStyleSheet("")
            self.lbl_status.setText("—")
            return
        s = status.strip().upper()
        colour = None
        if "FINISHED" in s or "COMPLETE" in s:
            colour = "#2e7d32"  # green 700
        elif "RUN" in s or "PROGRESS" in s:
            colour = "#1565c0"  # blue 800
        elif "PAUSED" in s or "WAIT" in s:
            colour = "#ef6c00"  # orange 800
        elif "ERROR" in s or "FAILED" in s or "FATAL" in s:
            colour = "#c62828"  # red 800
        if colour:
            self.lbl_status.setStyleSheet(f"color: {colour}; font-weight: 600;")
        else:
            self.lbl_status.setStyleSheet("")  # reset
        self.lbl_status.setText(status)

    def _set_progress_style(self, p: float):
        if p >= 99.9:
            style = ("QProgressBar { text-align: center; } "
                     "QProgressBar::chunk { background-color: #2e7d32; }")
        elif p >= 75:
            style = ("QProgressBar { text-align: center; } "
                     "QProgressBar::chunk { background-color: #43a047; }")
        elif p >= 50:
            style = ("QProgressBar { text-align: center; } "
                     "QProgressBar::chunk { background-color: #1e88e5; }")
        else:
            style = ("QProgressBar { text-align: center; } "
                     "QProgressBar::chunk { background-color: #90caf9; }")
        self.progress.setStyleSheet(style)

    def _maybe_switch_latest(self):
        if not self.follow_latest or not self.dir_path or not os.path.isdir(self.dir_path):
            return
        latest = _find_latest_tsf(self.dir_path)
        if latest and latest != self.log_path:
            self.log_path = latest
            self.tlf_path = _corresponding_tlf(self.log_path)
            scenario_name = os.path.splitext(os.path.basename(self.log_path))[0]
            self._set(self.lbl_scenario, scenario_name)
            self.tail.clear()
            QgsMessageLog.logMessage(f"TUFLOW Monitor: switched to latest {self.log_path}",
                                     "TUFLOW Monitor", Qgis.Info)

    def _scroll_tail_to_bottom(self):
        """Ensure the tail view is scrolled to the bottom (latest content visible)."""
        self.tail.moveCursor(QTextCursor.End)
        self.tail.ensureCursorVisible()
        sb = self.tail.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def update_once(self):
        if self._updating:
            return
        self._updating = True
        try:
            # Switch to the latest file if needed
            self._maybe_switch_latest()

            # If still no file, show unreadable
            if not self.log_path or not os.path.exists(self.log_path):
                self._set_coloured_status("No .tsf file found")
                self._set(self.lbl_pct, None)
                self._set(self.lbl_steps, None)
                self.progress.setValue(0)
                self.progress.setFormat("0%")
                self._set_progress_style(0.0)
                self.tail.setPlainText("")
                self._set(self.lbl_updated, QDateTime.currentDateTime().toString("ddd, dd MMM yyyy • HH:mm:ss"))
                self._scroll_tail_to_bottom()
                return

            # Read TSF for status parsing
            text_tsf = _safe_tail_read(self.log_path)
            if not text_tsf:
                self._set_coloured_status("Unreadable .tsf")
                self._set(self.lbl_updated, QDateTime.currentDateTime().toString("ddd, dd MMM yyyy • HH:mm:ss"))
                self._scroll_tail_to_bottom()
                return

            kv = _parse_key_values(text_tsf)

            # Core status
            status = kv.get("Simulation Status")
            pct = kv.get("Percentage Complete (%)")
            steps = kv.get("Completed Computational Steps")
            self._set_coloured_status(status)
            self._set(self.lbl_pct, pct)
            self._set(self.lbl_steps, steps)

            # Progress
            try:
                p = _num(pct, default=0.0)
                p = max(0.0, min(100.0, p))
            except Exception:
                p = 0.0
            self.progress.setValue(int(round(p)))
            self.progress.setFormat(f"{p:.1f}%")
            self._set_progress_style(p)

            # Humanized hours-only fields
            clock = hours_to_hhmmss(kv.get("Clock Time (h)"))
            remain = hours_to_hhmmss(kv.get("Approximate Clock Time Remaining (h)"))
            sim_start = hours_to_hhmmss(kv.get("Simulation Start Time (h)"))
            sim_end = hours_to_hhmmss(kv.get("Simulation End Time (h)"))
            sim_time = hours_to_hhmmss(kv.get("Simulation Time (h)"))
            self._set(self.lbl_clock, clock)
            self._set(self.lbl_remaining, remain)
            self._set(self.lbl_simstart, sim_start)
            self._set(self.lbl_simend, sim_end)
            self._set(self.lbl_simtime, sim_time)

            # Humanized seconds-only fields
            summary_int = secs_to_hhmmss(kv.get("Summary Output Interval (s)"))
            shortest_map = secs_to_hhmmss(kv.get("Shortest Map Output Interval (s)"))
            self._set(self.lbl_summary, summary_int)
            self._set(self.lbl_shortest, shortest_map)

            # Additional info
            build = kv.get("TUFLOW Build") or kv.get("Build")
            scheme = kv.get("Solution Scheme")
            hw = kv.get("Hardware")
            gpu = kv.get("GPU Device IDs")
            comp = kv.get("Computer Name")
            domains = kv.get("Number 2D Domains")
            active = kv.get("Active 2D Cells")
            total = kv.get("Total 2D Cells")
            warns_pre = kv.get("WARNINGs Prior to Simulation")
            warns_dur = kv.get("WARNINGs During Simulation")
            checks_pre = kv.get("CHECKs Prior to Simulation")
            checks_dur = kv.get("CHECKs During Simulation")
            self._set(self.lbl_build, build)
            self._set(self.lbl_scheme, scheme)
            self._set(self.lbl_hw, hw)
            self._set(self.lbl_gpu, gpu)
            self._set(self.lbl_comp, comp)
            self._set(self.lbl_domain, domains)
            self._set(self.lbl_cells, f"{active or '—'} / {total or '—'}")
            self._set(self.lbl_warns, f"{warns_pre or '—'} / {warns_dur or '—'}")
            self._set(self.lbl_checks, f"{checks_pre or '—'} / {checks_dur or '—'}")

            # Tail of text: corresponding .tlf (last N lines)
            tlf_text = ""
            if self.tlf_path and os.path.exists(self.tlf_path):
                tlf_text = _safe_tail_read(self.tlf_path)
            else:
                tlf_text = f"(No matching .tlf found for {os.path.basename(self.log_path)})"

            lines = tlf_text.splitlines()
            self.tail.setPlainText("\n".join(lines[-self.tail_lines:]))

            # Friendly timestamp
            self._set(self.lbl_updated, QDateTime.currentDateTime().toString("ddd, dd MMM yyyy • HH:mm:ss"))

            # Auto-scroll to bottom after content update
            self._scroll_tail_to_bottom()

            # Optional auto-close when finished
            if self.auto_close and status and status.upper().strip() == "FINISHED":
                self.timer.stop()
                QtCore.QTimer.singleShot(1500, self.close)
        finally:
            self._updating = False

    def closeEvent(self, ev):
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            MONITOR_WINDOWS.remove(self)
        except ValueError:
            pass
        super().closeEvent(ev)

# -------------------------
# Input Dialog with History
# -------------------------
class TuflowLogMonitorInputDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TUFLOW Log Monitor")
        self.resize(550, 220)
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 15)
        layout.setSpacing(8)

        # Log Folder
        folder_layout = QHBoxLayout()
        self.combo_folder = QComboBox()
        self.combo_folder.setEditable(True)
        self.combo_folder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo_folder.setInsertPolicy(QComboBox.NoInsert)
        self.btn_browse = QPushButton("Browse...")
        folder_layout.addWidget(self.combo_folder)
        folder_layout.addWidget(self.btn_browse)
        
        vbox_folder = QVBoxLayout()
        vbox_folder.setSpacing(2)
        vbox_folder.addWidget(QLabel("TUFLOW Log Folder:"))
        vbox_folder.addLayout(folder_layout)
        layout.addLayout(vbox_folder)

        # Other settings
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(5)
        self.spin_refresh = QSpinBox()
        self.spin_refresh.setRange(1, 86400)
        self.spin_refresh.setSuffix(" s")
        
        self.chk_autoclose = QCheckBox("Auto-close when FINISHED")
        self.chk_follow = QCheckBox("Follow latest .tsf")
        
        self.spin_tail = QSpinBox()
        self.spin_tail.setRange(1, 2000)
        
        form.addRow("Refresh Interval:", self.spin_refresh)
        form.addRow("", self.chk_autoclose)
        form.addRow("", self.chk_follow)
        form.addRow("Tail Lines:", self.spin_tail)
        
        layout.addLayout(form)
        
        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_ok = QPushButton("Start Monitor")
        self.btn_cancel = QPushButton("Cancel")
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        # Connections
        self.btn_browse.clicked.connect(self.browse_folder)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def browse_folder(self):
        current = self.combo_folder.currentText()
        d = QFileDialog.getExistingDirectory(self, "Select TUFLOW Log Folder", current)
        if d:
            self.combo_folder.setEditText(d)

    def load_settings(self):
        s = QSettings()
        # History
        history = s.value("TUFLOW/LogFolderHistory", [], type=list)
        if not isinstance(history, list): history = []
        
        # Legacy single value check
        last_dir = s.value(SETTINGS_KEY_LOG_DIR, "", type=str)
        if last_dir and last_dir not in history:
            history.insert(0, last_dir)
            
        self.combo_folder.clear()
        self.combo_folder.addItems([str(x) for x in history])
        if history:
            self.combo_folder.setEditText(history[0])
        elif last_dir:
            self.combo_folder.setEditText(last_dir)

        # Others
        try: ref = int(s.value(SETTINGS_KEY_REFRESH, 10))
        except: ref = 10
        self.spin_refresh.setValue(ref)
        
        self.chk_autoclose.setChecked(s.value(SETTINGS_KEY_AUTOCLOSE, False, type=bool))
        self.chk_follow.setChecked(s.value(SETTINGS_KEY_FOLLOW, True, type=bool))
        
        try: tl = int(s.value(SETTINGS_KEY_TAIL_LINES, 30))
        except: tl = 30
        self.spin_tail.setValue(tl)

    def save_settings(self):
        s = QSettings()
        current_path = self.combo_folder.currentText().strip()
        
        # Update history
        history = s.value("TUFLOW/LogFolderHistory", [], type=list)
        if not isinstance(history, list): history = []
        
        # Remove if exists to move to top
        if current_path in history:
            history.remove(current_path)
        history.insert(0, current_path)
        history = history[:20] # Keep max 20
        
        s.setValue("TUFLOW/LogFolderHistory", history)
        s.setValue(SETTINGS_KEY_LOG_DIR, current_path)
        
        s.setValue(SETTINGS_KEY_REFRESH, self.spin_refresh.value())
        s.setValue(SETTINGS_KEY_AUTOCLOSE, self.chk_autoclose.isChecked())
        s.setValue(SETTINGS_KEY_FOLLOW, self.chk_follow.isChecked())
        s.setValue(SETTINGS_KEY_TAIL_LINES, self.spin_tail.value())
        
        return {
            'dir': current_path,
            'refresh': self.spin_refresh.value(),
            'autoclose': self.chk_autoclose.isChecked(),
            'follow': self.chk_follow.isChecked(),
            'tail': self.spin_tail.value()
        }

# -------------------------
# Processing Algorithm
# -------------------------
class TuflowLogMonitorAlgorithm(QgsProcessingAlgorithm):
    TITLE = "Running Status"
    GROUP = "0 - Configuration"
    PARAM_DIR = "LOG_FOLDER"
    PARAM_REFRESH = "REFRESH_SECS"
    PARAM_AUTOCLOSE = "AUTO_CLOSE"
    PARAM_FOLLOW = "FOLLOW_LATEST"
    PARAM_TAIL = "TAIL_LINES"
    OUT_OPENED = "MONITOR_OPENED"

    def name(self): return ""
    def displayName(self): return self.TITLE
    def group(self): return self.GROUP
    def groupId(self): return "configuration"
    def shortHelpString(self):
        return ("Select a TUFLOW log folder. The monitor follows the latest *.tsf "
                "and shows the tail of its matching *.tlf. Updates on a set interval.\n"
                "Times shown as hh:mm:ss only. Status and log lines are colour-highlighted.")

    # IMPORTANT: run on main GUI thread (safe to create/update Qt widgets)
    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        from qgis.utils import iface
        dlg = TuflowLogMonitorInputDialog(iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            return {}
        
        vals = dlg.save_settings()
        
        dir_path = vals['dir']
        refresh = vals['refresh']
        auto_close = vals['autoclose']
        follow_latest = vals['follow']
        tail_lines = vals['tail']

        if not dir_path or not os.path.isdir(dir_path):
            raise QgsProcessingException("Log folder not found.")

        # Create the monitor window (non-modal) and return immediately
        w = TuflowMonitorWidget(dir_path, refresh_secs=refresh,
                                auto_close=auto_close, follow_latest=follow_latest,
                                tail_lines=tail_lines)
        w.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        w.show()
        MONITOR_WINDOWS.append(w)

        feedback.pushInfo(
            f"Monitoring folder: {dir_path} (every {refresh}s) — "
            f"follow_latest={follow_latest}, tail_lines={tail_lines}, auto_close={auto_close}"
        )
        return {self.OUT_OPENED: True}

    def createInstance(self): return TuflowLogMonitorAlgorithm()
