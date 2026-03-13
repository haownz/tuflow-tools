# -*- coding: utf-8 -*-
import os
import glob
import re
import json

from qgis.PyQt.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QHBoxLayout, QCheckBox, QHeaderView, QSplitter, QFileDialog,
    QApplication, QWidget, QSizePolicy, QMessageBox
)
from qgis.PyQt.QtCore import Qt, QDateTime
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsRasterLayer,
    QgsProject,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsExpressionContextUtils,
    QgsProcessingParameterBoolean
)
from qgis.utils import iface

from ..settings import PluginSettings
from ..style_manager import StyleManager

# --- Debug Configuration ---
DEBUG_MODE = True  # Set to False in production

def _write_debug_log(content):
    """Write debug content to a text file in Documents/tuflow_tools_debug."""
    if not DEBUG_MODE:
        return
    try:
        debug_dir = os.path.join(os.path.expanduser("~"), "Documents", "tuflow_tools_debug")
        os.makedirs(debug_dir, exist_ok=True)
        log_file = os.path.join(debug_dir, "debug_log.txt")
        
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]\n{content}\n{'-'*60}\n")
    except Exception:
        pass

def _find_tif_files(grids_folder: str):
    """Find all TIF/TIFF files in grids folder(s) (case-insensitive)."""
    files = []
    if not grids_folder:
        return files
    
    folders = grids_folder if isinstance(grids_folder, list) else [grids_folder]
    for folder in folders:
        if folder and os.path.exists(folder):
            for ext in ("*.tif", "*.TIF", "*.tiff", "*.TIFF"):
                files.extend(glob.glob(os.path.join(folder, ext)))
    return files

class TCFSelectionWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load TUFLOW Grid Output")
        self.setMinimumSize(950, 750)
        
        self.all_discovered_events = set()
        self.grids_folder_cache = []

        self.tcf_page = TCFSelectionPage()
        self.scenario_page = ScenarioSelectionPage()
        self.output_page = OutputDataTypePage()
        self.preview_page = PreviewPage()

        self.addPage(self.tcf_page)
        self.addPage(self.scenario_page)
        self.addPage(self.output_page)
        self.addPage(self.preview_page)

def create_table_controls(table_widget, parent_layout):
    """Add 'Select All' and 'Clear All' buttons to control table checkboxes."""
    btn_layout = QHBoxLayout()
    all_btn = QPushButton("Select All")
    none_btn = QPushButton("Clear All")
    
    def set_all(state):
        for r in range(table_widget.rowCount()):
            cb = table_widget.cellWidget(r, 0)
            if cb: cb.setChecked(state)

    all_btn.clicked.connect(lambda: set_all(True))
    none_btn.clicked.connect(lambda: set_all(False))
    btn_layout.addWidget(all_btn)
    btn_layout.addWidget(none_btn)
    btn_layout.addStretch()
    parent_layout.addLayout(btn_layout)

class TCFSelectionPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 1: Select TUFLOW Control Files")
        self.run_path = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        
        top_layout = QHBoxLayout()
        self.lbl_folder = QLabel("Folder: (Not selected)")
        btn_browse = QPushButton("Browse Folder...")
        btn_browse.clicked.connect(self.browse_folder)
        top_layout.addWidget(self.lbl_folder)
        top_layout.addWidget(btn_browse)
        layout.addLayout(top_layout)
        
        self.tcf_table = QTableWidget(0, 2)
        self.tcf_table.setHorizontalHeaderLabels(["Select", "TCF File"])
        self.tcf_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.tcf_table.setColumnWidth(0, 60)
        self.tcf_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tcf_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        layout.addWidget(self.tcf_table)
        layout.setStretchFactor(self.tcf_table, 1)
        create_table_controls(self.tcf_table, layout)
        self.setLayout(layout)

    def initializePage(self):
        if not self.run_path:
            model_path = PluginSettings.get_model_path()
            if model_path and os.path.exists(model_path):
                run_path = os.path.join(model_path, "runs")
                if not os.path.exists(run_path):
                    run_path = os.path.join(model_path, "run")
                
                if os.path.exists(run_path):
                    self.run_path = run_path
                elif os.path.exists(model_path):
                    self.run_path = model_path
            
            if not self.run_path:
                prj_path = QgsProject.instance().homePath()
                if prj_path and os.path.exists(prj_path):
                    candidates = [
                        os.path.join(prj_path, "runs"),
                        os.path.join(prj_path, "run"),
                        prj_path
                    ]
                    for c in candidates:
                        if os.path.exists(c) and glob.glob(os.path.join(c, "*.tcf")):
                            self.run_path = c
                            break
        
        self.refresh_file_list()

    def browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select TCF Folder", self.run_path or "")
        if d:
            self.run_path = d
            self.refresh_file_list()

    def refresh_file_list(self):
        self.tcf_table.setRowCount(0)
        if not self.run_path or not os.path.exists(self.run_path):
            self.lbl_folder.setText("Folder: (Invalid or Not Found)")
            return
        
        self.lbl_folder.setText(f"Folder: {self.run_path}")
        tcf_files = glob.glob(os.path.join(self.run_path, "*.tcf"))
        self.tcf_table.setRowCount(len(tcf_files))
        for row, f in enumerate(tcf_files):
            cb = QCheckBox(); cb.stateChanged.connect(self.completeChanged.emit)
            self.tcf_table.setCellWidget(row, 0, cb)
            item = QTableWidgetItem(os.path.basename(f))
            item.setData(Qt.UserRole, f)
            self.tcf_table.setItem(row, 1, item)

    def get_selected_files(self):
        selected = []
        for r in range(self.tcf_table.rowCount()):
            cb = self.tcf_table.cellWidget(r, 0)
            if cb and cb.isChecked():
                item = self.tcf_table.item(r, 1)
                if item: selected.append(item.data(Qt.UserRole))
        return selected

    def isComplete(self):
        return len(self.get_selected_files()) > 0

class ScenarioSelectionPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 2: Select Scenarios and Events")
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()
        splitter = QSplitter(Qt.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Scenarios
        scen_container = QWidget()
        scen_layout = QVBoxLayout(scen_container)
        scen_layout.setContentsMargins(0, 0, 0, 0)
        self.scenarios_table = QTableWidget(0, 2)
        self.scenarios_table.setHorizontalHeaderLabels(["Select", "Scenario"])
        self.scenarios_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        scen_layout.addWidget(self.scenarios_table)
        create_table_controls(self.scenarios_table, scen_layout)
        
        # Events
        ev_container = QWidget()
        ev_layout = QVBoxLayout(ev_container)
        ev_layout.setContentsMargins(0, 0, 0, 0)
        self.events_table = QTableWidget(0, 2)
        self.events_table.setHorizontalHeaderLabels(["Select", "Event"])
        self.events_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        ev_layout.addWidget(self.events_table)
        create_table_controls(self.events_table, ev_layout)
        
        splitter.addWidget(scen_container)
        splitter.addWidget(ev_container)
        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

    def initializePage(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            wizard = self.wizard()
            selected_tcfs = wizard.tcf_page.get_selected_files()
            all_scenarios, all_events = self._extract_scenarios_and_events(selected_tcfs, wizard)
            
            if DEBUG_MODE:
                msg = "=== TUFLOW Tools Debug: Scenarios & Events ===\n"
                msg += f"TCFs Selected: {len(selected_tcfs)}\n"
                msg += "--- Scenarios ---\n" + "\n".join(sorted(all_scenarios)) + "\n"
                msg += "--- Events ---\n" + "\n".join(sorted(all_events))
                _write_debug_log(msg)

            wizard.all_discovered_events = all_events
            self.populate_table(self.scenarios_table, sorted(all_scenarios))
            self.populate_table(self.events_table, sorted(all_events))
        finally:
            QApplication.restoreOverrideCursor()
    
    def _extract_scenarios_and_events(self, selected_tcfs, wizard):
        """Extract using strict structure where possible, fallback to heuristic."""
        all_scenarios, all_events = set(), set()
        wizard.grids_folder_cache = []
        tcf_names = []

        # 1. Parse TCFs for Events, Structure and Output Folder
        for tcf in selected_tcfs:
            tcf_names.append(os.path.basename(tcf))
            try:
                with open(tcf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                cleaned_lines = self._clean_tcf_content(content)
                if DEBUG_MODE:
                    _write_debug_log(f"--- Tidied TCF: {os.path.basename(tcf)} ---\n" + "\n".join(cleaned_lines))

                variables = self._extract_variables_from_tcf(cleaned_lines)
                cleaned_lines_with_vars = [self._substitute_variables(line, variables) for line in cleaned_lines]
                
                folders, scenarios = self.extract_output_folders_and_scenarios(tcf)
                if folders:
                    for f in folders:
                        if f not in wizard.grids_folder_cache:
                            wizard.grids_folder_cache.append(f)
                all_scenarios.update(scenarios)

                # Extract Events from referenced Event File
                tcf_dir = os.path.dirname(tcf)
                for line in cleaned_lines_with_vars:
                    if 'event file' in line.lower() and '==' in line:
                        ep = os.path.join(tcf_dir, line.split('==')[1].strip().strip('"\''))
                        if os.path.exists(ep):
                            try:
                                with open(ep, 'r', encoding='utf-8', errors='ignore') as ef:
                                    ef_lines = self._clean_tcf_content(ef.read())
                                    if DEBUG_MODE:
                                        _write_debug_log(f"--- Tidied Event File: {os.path.basename(ep)} ---\n" + "\n".join(ef_lines))
                                    for el in ef_lines:
                                        if el.lower().startswith('define event') and '==' in el:
                                            all_events.add(el.split('==')[1].strip())
                            except Exception: continue
            except Exception: continue

        # Fallback for grids folder if empty or contains no TIFs
        needs_fallback = not wizard.grids_folder_cache
        if not needs_fallback:
            # Check if the currently cached folders actually contain any TIFs
            found_any = False
            for folder in wizard.grids_folder_cache:
                if folder and os.path.exists(folder) and _find_tif_files(folder):
                    found_any = True
                    break
            if not found_any:
                needs_fallback = True

        if needs_fallback and selected_tcfs:
            base_dir = os.path.dirname(selected_tcfs[0])
            candidates = [
                os.path.join(base_dir, "grids"),
                os.path.join(os.path.dirname(base_dir), "results", "grids"),
                os.path.join(base_dir, "..", "results", "grids"),
                base_dir
            ]
            
            # Additional fallback: look for 'grids' in any subdirectories of 'results'
            results_dir = os.path.join(os.path.dirname(base_dir), "results")
            if not os.path.exists(results_dir):
                results_dir = os.path.join(base_dir, "..", "results")
            
            if os.path.exists(results_dir):
                try:
                    for root_dir, dirs, files in os.walk(results_dir):
                        if os.path.basename(root_dir).lower() == 'grids':
                            candidates.append(root_dir)
                except Exception: pass

            for c in candidates:
                if os.path.exists(c) and _find_tif_files(c):
                    if c not in wizard.grids_folder_cache:
                        wizard.grids_folder_cache.append(c)

        # 2. Scan grids
        if wizard.grids_folder_cache:
            all_tif_files = _find_tif_files(wizard.grids_folder_cache)
            valid_tifs = all_tif_files
            
            # Pre-calc structures
            structures = []
            for name in tcf_names:
                st = self.extract_tcf_structure(name)
                if st: structures.append(st)

            for tif in valid_tifs:
                # Try strict structure extraction first
                s, e = self.extract_logic(os.path.basename(tif), all_events, structures)
                all_scenarios.update(s)
                all_events.update(e)

        return all_scenarios, all_events

    def extract_tcf_structure(self, tcf_name):
        """Parse TCF filename to determine slot types: Scenario, Event, or Literal (constant)."""
        clean_name = os.path.splitext(tcf_name)[0]
        parts = clean_name.split('_')
        structure = []
        
        valid_struct = False
        for part in parts:
            if re.match(r'~s\d+~', part):
                structure.append('S')
                valid_struct = True
            elif re.match(r'~e\d+~', part):
                structure.append('E')
                valid_struct = True
            else:
                # Store the literal value (e.g. '135041' or '001')
                structure.append(('L', part))
        
        return structure if valid_struct else None

    def extract_logic(self, filename, event_list, tcf_structures):
        """
        Determines Scenarios and Events for a file.
        Attempts strict structure matching first. If no structure matches, falls back to heuristic.
        """
        base_name = os.path.splitext(filename)[0]
        
        for struct in tcf_structures:
            res = self.parse_with_structure(base_name, struct, event_list)
            if res:
                return res

        # Fallback Heuristic
        return self.extract_heuristic(filename, event_list)

    def parse_with_structure(self, filename_str, structure, event_list):
        """
        Strictly parses filename_str using the structure list.
        Validates that 'Literal' parts of the filename match the TCF structure exactly.
        """
        scenarios = set()
        events = set()
        ignored_keywords = {'h', 'd', 'v', 'q', 'hr', 'max', 'tmax', 'min', 'avg', 'dem', 'grid', 'znz2', 'tmax_h'}
        
        parts = filename_str.split('_')
        p_idx = 0
        
        for slot in structure:
            if p_idx >= len(parts):
                break

            if slot == 'S':
                if parts[p_idx] and parts[p_idx].lower() not in ignored_keywords:
                    scenarios.add(parts[p_idx])
                p_idx += 1

            elif slot == 'E':
                match_found = None
                best_len = 0
                for lookahead in range(1, 4):
                    if p_idx + lookahead > len(parts): break
                    candidate = "_".join(parts[p_idx : p_idx + lookahead])
                    for ev in event_list:
                        if candidate.lower() == ev.lower():
                            match_found = ev
                            best_len = lookahead; break
                    if match_found: break
                
                if match_found:
                    events.add(match_found)
                    p_idx += best_len
                else:
                    # If expected event not found, treat token as orphan scenario and move on
                    if parts[p_idx] and parts[p_idx].lower() not in ignored_keywords:
                        scenarios.add(parts[p_idx])
                    p_idx += 1

            elif isinstance(slot, tuple) and slot[0] == 'L':
                expected = slot[1]
                # Look ahead for this constant; any skipped tokens are scenarios
                found_at = -1
                for lookahead in range(p_idx, len(parts)):
                    if parts[lookahead].lower() == expected.lower():
                        found_at = lookahead; break
                
                if found_at != -1:
                    for i in range(p_idx, found_at):
                        if parts[i] and parts[i] not in ignored_keywords:
                            scenarios.add(parts[i])
                    p_idx = found_at + 1
                else:
                    # Constant not found, treat current token as scenario
                    if parts[p_idx] and parts[p_idx].lower() not in ignored_keywords:
                        scenarios.add(parts[p_idx])
                    p_idx += 1
        
        # Add remaining tokens as scenarios
        while p_idx < len(parts):
            if parts[p_idx] and parts[p_idx].lower() not in ignored_keywords:
                scenarios.add(parts[p_idx])
            p_idx += 1

        return scenarios, events

    def extract_heuristic(self, filename, event_list):
        """Legacy fallback extraction."""
        scenarios, events = set(), set()
        working_string = os.path.splitext(filename)[0]
        
        sorted_ev = sorted(event_list, key=len, reverse=True)
        for ev in sorted_ev:
            pattern = rf"(^|[_|]){re.escape(ev)}([_|]|$)"
            if re.search(pattern, working_string, re.IGNORECASE):
                events.add(ev)
                working_string = re.sub(pattern, '|', working_string, count=1, flags=re.IGNORECASE)
        
        ignored_keywords = {'h', 'd', 'v', 'q', 'hr', 'max', 'tmax', 'min', 'avg', 'dem', 'grid', 'znz2', 'tmax_h'}
        parts = re.split(r'[_|]', working_string)
        for part in parts:
            if not part: continue
            if part.lower() in ignored_keywords: continue
            if any(part.lower() == ev.lower() for ev in event_list): continue
            scenarios.add(part)
            
        return scenarios, events

    def _extract_variables_from_tcf(self, cleaned_lines):
        variables = {}
        for line in cleaned_lines:
            if 'set variable' in line.lower() and '==' in line:
                parts = line.split('==')
                if len(parts) == 2:
                    # Use regex to strip 'set variable' case-insensitively
                    var_name = re.sub(r'(?i)^\s*set\s+variable\s+', '', parts[0]).strip()
                    variables[var_name.lower()] = parts[1].strip().strip('"\'')
        return variables

    def _substitute_variables(self, text, variables):
        for var_name, var_value in variables.items():
            pattern = r'<<' + re.escape(var_name) + r'>>'
            text = re.sub(pattern, var_value, text, flags=re.IGNORECASE)
        return text

    def _clean_tcf_content(self, content):
        lines = content.split('\n')
        cleaned = []
        in_define_block = False
        for line in lines:
            line = line.split('!')[0].strip()
            if not line: continue
            if 'define output zone' in line.lower() and '==' in line.lower():
                in_define_block = True
                continue
            if in_define_block and 'end define' in line.lower():
                in_define_block = False
                continue
            if in_define_block: continue
            cleaned.append(line)
        return cleaned

    def extract_output_folders_and_scenarios(self, tcf_file):
        found_folders = []
        found_scenarios = set()
        try:
            with open(tcf_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                cleaned_lines = self._clean_tcf_content(content)
                variables = self._extract_variables_from_tcf(cleaned_lines)
                for line in cleaned_lines:
                    if line.lower().startswith('output folder') and '==' in line:
                        raw_folder = line.split('==')[1].strip().strip('"\'')
                        folder = self._substitute_variables(raw_folder, variables)
                        
                        if not os.path.isabs(folder):
                            folder = os.path.join(os.path.dirname(tcf_file), folder)
                        folder = os.path.normpath(folder)
                        
                        # Handle variable placeholders like <<~s2~>> or <<Version>>
                        if '<<' in folder and '>>' in folder:
                            head = folder
                            tail_parts = []
                            # Walk up until no variables in path
                            while '<<' in head and '>>' in head:
                                head, tail = os.path.split(head)
                                tail_parts.insert(0, tail)
                            
                            if os.path.exists(head):
                                try:
                                    # List subdirectories in the base path
                                    subdirs = [d for d in os.listdir(head) if os.path.isdir(os.path.join(head, d))]
                                    for sd in subdirs:
                                        # Treat subdir name as scenario keyword
                                        found_scenarios.add(sd)
                                        # Construct full path: head + subdir + rest of path
                                        full_path = os.path.join(head, sd, *tail_parts[1:])
                                        
                                        grids_sub = os.path.join(full_path, "grids")
                                        if os.path.exists(grids_sub):
                                            found_folders.append(grids_sub)
                                        elif os.path.exists(full_path):
                                            found_folders.append(full_path)
                                except Exception: pass
                        else:
                            grids_path = os.path.join(folder, "grids")
                            if os.path.exists(grids_path):
                                found_folders.append(grids_path)
                            elif os.path.exists(folder):
                                found_folders.append(folder)
        except Exception: pass
        return found_folders, found_scenarios

    def validate_grid_files_location(self, tif_files, tcf_output_folder):
        if not tcf_output_folder: return tif_files, []
        valid_files, invalid_files = [], []
        tcf_output_folder = os.path.normpath(tcf_output_folder)
        for tif in tif_files:
            if os.path.normpath(os.path.dirname(tif)).startswith(tcf_output_folder):
                valid_files.append(tif)
            else: invalid_files.append(tif)
        return valid_files, invalid_files

    def extract_map_output_formats(self, tcf_file):
        try:
            with open(tcf_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            cleaned_lines = self._clean_tcf_content(content)
            for line in cleaned_lines:
                if 'map output format' in line.lower() and '==' in line:
                    formats = []
                    for fmt in line.split('==')[1].strip().split():
                        fmt_upper = fmt.upper()
                        if fmt_upper in ['TIF', 'FLT', 'ASC', 'NC', 'TGO', 'WRR', 'GPKG',
                                        'HRTIF', 'HRFLT', 'HRASC', 'HRNC', 'HRTGO', 'HRWRR', 'HRGPKG']:
                            formats.append(fmt_upper)
                    if formats: return formats
        except Exception: pass
        return ['TIF']

    def populate_table(self, table, items):
        table.setRowCount(len(items))
        for i, text in enumerate(items):
            cb = QCheckBox(); cb.setChecked(True)
            table.setCellWidget(i, 0, cb); table.setItem(i, 1, QTableWidgetItem(text))

class OutputDataTypePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 3: Select Map Output Data Types")
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        self.output_table = QTableWidget(0, 3)
        self.output_table.setHorizontalHeaderLabels(["Select", "Data Type", "Grid Type"])
        self.output_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.output_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.output_table)
        create_table_controls(self.output_table, layout)
        self.setLayout(layout)

    def initializePage(self):
        wizard = self.wizard()
        all_types = set()
        
        grid_formats = set()
        for tcf in wizard.tcf_page.get_selected_files():
            formats = wizard.scenario_page.extract_map_output_formats(tcf)
            grid_formats.update(formats)
        
        for tcf in wizard.tcf_page.get_selected_files():
            with open(tcf, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().split('\n')
            
            spec_found = False
            temp = set()
            for fmt in grid_formats:
                for line in content:
                    line = line.split('!')[0].strip()
                    if (fmt.lower() + ' map output data types' in line.lower()) and '==' in line:
                        for dt in line.split('==')[1].strip().split():
                            temp.add((dt, fmt))
                        spec_found = True
            
            if not spec_found or not temp:
                for line in content:
                    line = line.split('!')[0].strip()
                    if 'map output data types' in line.lower() and '==' in line:
                        for dt in line.split('==')[1].strip().split():
                            for fmt in grid_formats:
                                all_types.add((dt, fmt))
                        break
            else:
                all_types.update(temp)

        self.output_table.setRowCount(len(all_types))
        sorted_types = sorted(all_types, key=lambda x: (x[1], x[0]))
        
        for i, (dt, gt) in enumerate(sorted_types):
            cb = QCheckBox(); cb.setChecked(True)
            self.output_table.setCellWidget(i, 0, cb)
            self.output_table.setItem(i, 1, QTableWidgetItem(dt))
            self.output_table.setItem(i, 2, QTableWidgetItem(gt))

class PreviewPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Step 4: Preview and Load")
        self.init_ui()
        self.debug_count = 0

    def init_ui(self):
        layout = QVBoxLayout()
        self.preview_table = QTableWidget(0, 2)
        self.preview_table.setHorizontalHeaderLabels(["Select", "Grid File"])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.preview_table.setColumnWidth(0, 60)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.preview_table)

        self.empty_warning_label = QLabel("⚠️ Warning: No matching grid files were found! The simulation may not have results yet, or the model path in the TCF might be incorrect.")
        self.empty_warning_label.setStyleSheet("color: #d9534f; font-weight: bold; padding: 5px;")
        self.empty_warning_label.setWordWrap(True)
        self.empty_warning_label.setVisible(False)
        layout.addWidget(self.empty_warning_label)

        create_table_controls(self.preview_table, layout)
        self.setLayout(layout)

    def initializePage(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.preview_table.setUpdatesEnabled(False)
        self.debug_count = 0
        try:
            wizard = self.wizard()
            selected_scenarios = [
                wizard.scenario_page.scenarios_table.item(r, 1).text()
                for r in range(wizard.scenario_page.scenarios_table.rowCount())
                if wizard.scenario_page.scenarios_table.cellWidget(r, 0).isChecked()
            ]
            selected_events = [
                wizard.scenario_page.events_table.item(r, 1).text()
                for r in range(wizard.scenario_page.events_table.rowCount())
                if wizard.scenario_page.events_table.cellWidget(r, 0).isChecked()
            ]
            selected_datatypes = [
                (wizard.output_page.output_table.item(r, 1).text(), wizard.output_page.output_table.item(r, 2).text())
                for r in range(wizard.output_page.output_table.rowCount())
                if wizard.output_page.output_table.cellWidget(r, 0).isChecked()
            ]

            self.grid_files_to_load = []
            if wizard.grids_folder_cache:
                unique_paths = set()
                
                # Pre-calc structures for filtering
                tcf_structures = []
                for tcf in wizard.tcf_page.get_selected_files():
                    st = wizard.scenario_page.extract_tcf_structure(os.path.basename(tcf))
                    if st: tcf_structures.append(st)

                all_tifs = _find_tif_files(wizard.grids_folder_cache)
                debug_lines = []
                if DEBUG_MODE:
                    debug_lines.append(f"=== TUFLOW Tools Debug: Grid Files ===\nFolders: {wizard.grids_folder_cache}\nTotal TIFs: {len(all_tifs)}")

                for fpath in all_tifs:
                    is_match = self.matches(os.path.basename(fpath), selected_scenarios, selected_events, selected_datatypes, tcf_structures)
                    if is_match:
                        unique_paths.add(fpath)
                    if DEBUG_MODE:
                        debug_lines.append(f"{'[MATCH]' if is_match else '[SKIP ]'} {os.path.basename(fpath)}")

                if DEBUG_MODE:
                    _write_debug_log("\n".join(debug_lines))

                self.grid_files_to_load = sorted(unique_paths)
                self.preview_table.setRowCount(len(self.grid_files_to_load))

                for i, fpath in enumerate(self.grid_files_to_load):
                    cb = QCheckBox(); cb.setChecked(True)
                    self.preview_table.setCellWidget(i, 0, cb)
                    item = QTableWidgetItem(os.path.basename(fpath))
                    item.setData(Qt.UserRole, fpath)
                    self.preview_table.setItem(i, 1, item)

                if len(self.grid_files_to_load) == 0:
                    self.empty_warning_label.setVisible(True)
                else:
                    self.empty_warning_label.setVisible(False)

        finally:
            self.preview_table.setUpdatesEnabled(True)
            QApplication.restoreOverrideCursor()

    def matches(self, filename, selected_scenarios, selected_events, selected_datatypes, tcf_structures):
        """
        Check if filename matches selections using Structure-Based Logic.
        """
        wizard = self.wizard()
        
        # Extract using the same structure logic used in Step 2
        res = wizard.scenario_page.extract_logic(
            filename, wizard.all_discovered_events, tcf_structures
        )
        
        if not res:
            return False # Failed structure validation (e.g. wrong literal ID)
            
        file_scenarios, file_events = res
        
        file_scen_lc = {s.lower() for s in file_scenarios}
        file_ev_lc = {e.lower() for e in file_events}
        
        sel_scen_lc = {s.lower() for s in selected_scenarios}
        sel_ev_lc = {e.lower() for e in selected_events}
        
        if sel_scen_lc:
            if not file_scen_lc.intersection(sel_scen_lc):
                return False
        
        if sel_ev_lc:
            if not file_ev_lc.intersection(sel_ev_lc):
                return False

        if selected_datatypes:
            dt_match = False
            for dt, gt in selected_datatypes:
                base_dt = dt.split()[0]
                hr_prefix = "HR_" if gt == "HRTIF" else ""
                pattern = rf"_{re.escape(base_dt)}_{hr_prefix}(Max|TMax|Avg|Min)|_{hr_prefix}(TMax|Max)_{re.escape(base_dt)}"
                if re.search(pattern, filename, flags=re.IGNORECASE):
                    dt_match = True; break
            if not dt_match: return False
            
        return True

class LoadGridOutputAlgorithm(QgsProcessingAlgorithm):
    def initAlgorithm(self, config=None):
        pass

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self): return 'load_grid_output'
    def displayName(self): return '1 - Load Grid Output'
    def group(self): return '2 - Result Analysis'
    def groupId(self): return 'result_analysis'
    def createInstance(self): return LoadGridOutputAlgorithm()

    def processAlgorithm(self, parameters, context, feedback):
        create_group = False
        wizard = TCFSelectionWizard(iface.mainWindow())
        if wizard.exec_() == QWizard.Accepted:
            feedback.pushInfo("Loading TUFLOW grid outputs...")
            root = QgsProject.instance().layerTreeRoot()
            view = iface.layerTreeView()
            current_node = view.currentNode()
            target_parent = root
            insert_index = 0
            if current_node:
                if isinstance(current_node, QgsLayerTreeLayer):
                    target_parent = current_node.parent()
                    insert_index = target_parent.children().index(current_node)
                elif isinstance(current_node, QgsLayerTreeGroup):
                    target_parent = current_node
            
            if create_group:
                group_name = "TUFLOW Results"
                if wizard.grids_folder_cache:
                    # Use the first folder to derive a group name if possible
                    first_folder = wizard.grids_folder_cache[0]
                    group_name = os.path.basename(os.path.dirname(first_folder))
                target_container = target_parent.insertGroup(insert_index, group_name)
                insertion_idx = 0
            else:
                target_container = target_parent
                insertion_idx = insert_index

            selected_paths = []
            for row in range(wizard.preview_page.preview_table.rowCount()):
                cb = wizard.preview_page.preview_table.cellWidget(row, 0)
                if cb and cb.isChecked():
                    item = wizard.preview_page.preview_table.item(row, 1)
                    if item: selected_paths.append(item.data(Qt.UserRole))

            wizard.preview_page.grid_files_to_load = selected_paths
            loaded_names = []
            sorted_files = sorted(wizard.preview_page.grid_files_to_load, key=lambda x: os.path.basename(x).lower())
            total = len(sorted_files)

            for idx, fpath in enumerate(reversed(sorted_files)):
                feedback.setProgress(int((idx / total * 100)) if total > 0 else 0)
                feedback.pushInfo(f"Loading: {os.path.basename(fpath)}")
                try:
                    lyr = QgsRasterLayer(fpath, os.path.splitext(os.path.basename(fpath))[0])
                    if lyr.isValid():
                        QgsProject.instance().addMapLayer(lyr, False)
                        target_container.insertLayer(insertion_idx, lyr)
                        loaded_names.append(lyr.name())
                        try: StyleManager.apply_style_to_layer(lyr)
                        except Exception: pass
                    else: feedback.reportError(f"Failed to load: {fpath}")
                except Exception as e: feedback.reportError(f"Error: {e}")

            QgsExpressionContextUtils.setGlobalVariable('tuflow_latest_scenario_layers', json.dumps(loaded_names))
            QgsExpressionContextUtils.setGlobalVariable('tuflow_latest_raster_files', json.dumps(wizard.preview_page.grid_files_to_load))
            QgsExpressionContextUtils.setGlobalVariable('tuflow_latest_load_time', QDateTime.currentDateTime().toString())
            feedback.pushInfo(f"Loaded {len(loaded_names)} of {total} raster layers")
        return {}