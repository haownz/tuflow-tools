# -*- coding: utf-8 -*-
"""
QGIS Processing algorithm for TUFLOW Consistency Check, Integrity Verification & Dependency Audit (Preview).

Features:
- Persist defaults with QgsSettings.
- Preview-only block for 'Write Check Files' and helper commands (no .tcf edits).
- Optional TUFLOW test mode (-t) to validate inputs without running hydraulics.
- Optional 1D integrity scan (empty geometry, duplicate IDs).
- NEW: Dependency audit using TUFLOW -cL (this run) or -pmL (all events/scenarios).
       Produces CSV with existence/size/date/CRS checks and a summary.

References:
- Write Check Files Include/Exclude options and path/prefix behaviour: TUFLOW Wiki "TUFLOW Check Files".  # cite in user docs
- Test mode (-t) and copy/package switches (-c, -cL, -pm, -pmL): TUFLOW Wiki "Run TUFLOW From a Batch-file".  # cite in user docs
- Events/Scenarios (TEF) logic and control file structure: TUFLOW Manual Sections 13 & 4.  # cite in user docs
"""

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsSettings,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsProcessingOutputString,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsWkbTypes,
    QgsGeometry,
    QgsProject,
    QgsProcessingException,
    QgsCoordinateReferenceSystem
)

import os
    # os.path functions used throughout
import re
import csv
import datetime
import subprocess
import shlex
import stat


class TuflowConsistencyIntegrity(QgsProcessingAlgorithm):
    """
    TUFLOW – Consistency Check, Integrity Verification & Dependency Audit (Preview)
    """

    # Parameters
    PARAM_TCF = 'TCF'
    PARAM_TUFLOW_EXE = 'TUFLOW_EXE'
    PARAM_CHECKFOLDER = 'CHECK_FOLDER'
    PARAM_MODE = 'CHECK_MODE'
    PARAM_INCLUDE = 'INCLUDE_LIST'
    PARAM_EXCLUDE = 'EXCLUDE_LIST'
    PARAM_PREFIX = 'CHECK_PREFIX'

    PARAM_SET_GIS_PROJ_CHECK = 'SET_GIS_PROJECTION_CHECK'
    PARAM_SET_MI_SAVE_DATE = 'SET_MI_SAVE_DATE'
    PARAM_SET_MI_SAVE_EXT = 'SET_MI_SAVE_EXT'

    PARAM_PREVIEW_ONLY = 'PREVIEW_ONLY'
    PARAM_REPORT_FILE = 'REPORT_FILE'

    PARAM_RUN_TEST = 'RUN_TEST_MODE'
    PARAM_RUN_1D_SCAN = 'RUN_1D_SCAN'
    PARAM_1D_SINK = 'ONE_D_ISSUES'

    PARAM_DEP_MODE = 'DEP_MODE'
    PARAM_DEP_CSV = 'DEP_CSV'

    PARAM_SAVE_DEFAULTS = 'SAVE_DEFAULTS'

    MODES = ['ALL', 'INCLUDE', 'EXCLUDE']
    DEP_MODES = [
        'OFF',
        'List this run only (-cL)',
        'List ALL events/scenarios (-pmL)'
    ]

    # Settings scope
    SETTINGS_ORG = 'tuflow_tools'
    SETTINGS_APP = 'qa'

    def name(self):
        return 'tuflow_consistency_integrity'

    def displayName(self):
        return 'Consistency, Integrity & Dependencies (Preview)'

    def group(self):
        return 'TUFLOW QA'

    def groupId(self):
        return 'tuflow_qa'

    def shortHelpString(self):
        return (
            "Generates a preview of TUFLOW 'Write Check Files' and helper commands (no TCF edits). "
            "Optionally runs 1D integrity scans, TUFLOW test mode (-t), and audits ALL input dependencies "
            "via TUFLOW -cL (this run) or -pmL (all events/scenarios). Defaults are persisted via QgsSettings."
        )

    # --- Defaults from settings
    def _read_defaults(self):
        s = QgsSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        def _b(key, default):
            return bool(s.value(key, default, type=bool))
        def _i(key, default):
            return int(s.value(key, default))
        def _s(key, default):
            return str(s.value(key, default))
        return {
            'mode': _i('mode', 2),  # EXCLUDE
            'include': _s('include', ''),
            'exclude': _s('exclude', 'zpt uvpt grd'),
            'prefix': _s('prefix', ''),
            'check_folder': _s('check_folder', ''),

            'proj_check': _b('proj_check', False),
            'mi_save_date': _i('mi_save_date', 0),
            'mi_save_ext': _i('mi_save_ext', 0),

            'preview_only': _b('preview_only', True),
            'run_test': _b('run_test', False),
            'run_1d': _b('run_1d', False),

            'dep_mode': _i('dep_mode', 0),  # OFF
            'dep_csv': _s('dep_csv', '')
        }

    def _write_defaults(self, values):
        s = QgsSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        for k, v in values.items():
            s.setValue(k, v)

    def initAlgorithm(self, config=None):
        d = self._read_defaults()

        # Core inputs
        self.addParameter(QgsProcessingParameterFile(
            self.PARAM_TCF, 'TUFLOW Control File (.tcf)',
            behavior=QgsProcessingParameterFile.File, extension='tcf'))

        self.addParameter(QgsProcessingParameterFile(
            self.PARAM_TUFLOW_EXE, 'Path to TUFLOW executable (e.g., TUFLOW_iSP_w64.exe)',
            behavior=QgsProcessingParameterFile.File, optional=True))

        self.addParameter(QgsProcessingParameterFolderDestination(
            self.PARAM_CHECKFOLDER, 'Check files folder (used in preview block)',
            defaultValue=d['check_folder'], optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.PARAM_MODE, 'Write Check Files mode',
            options=self.MODES, defaultValue=d['mode']))

        self.addParameter(QgsProcessingParameterString(
            self.PARAM_INCLUDE, 'Include list (space-separated)',
            defaultValue=d['include'], optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.PARAM_EXCLUDE, 'Exclude list (space-separated)',
            defaultValue=d['exclude'], optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.PARAM_PREFIX, 'Prefix for check files (optional)',
            defaultValue=d['prefix'], optional=True))

        # Helper checks
        self.addParameter(QgsProcessingParameterBoolean(
            self.PARAM_SET_GIS_PROJ_CHECK, 'Set GIS Projection Check == ON',
            defaultValue=d['proj_check']))

        self.addParameter(QgsProcessingParameterEnum(
            self.PARAM_SET_MI_SAVE_DATE, 'Check MI Save Date (LEAVE/OFF/WARNING)',
            options=['LEAVE', 'OFF', 'WARNING'], defaultValue=d['mi_save_date']))

        self.addParameter(QgsProcessingParameterEnum(
            self.PARAM_SET_MI_SAVE_EXT, 'Check MI Save Ext (LEAVE/OFF/WARNING)',
            options=['LEAVE', 'OFF', 'WARNING'], defaultValue=d['mi_save_ext']))

        # Preview & report
        self.addParameter(QgsProcessingParameterBoolean(
            self.PARAM_PREVIEW_ONLY, 'Preview only (do NOT edit TCF)',
            defaultValue=d['preview_only']))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.PARAM_REPORT_FILE, 'Optional preview report (*.txt)',
            fileFilter='Text files (*.txt);;All files (*.*)', optional=True))

        # Integrity/test
        self.addParameter(QgsProcessingParameterBoolean(
            self.PARAM_RUN_TEST, 'Run TUFLOW test mode (-t) [no TCF edits]',
            defaultValue=d['run_test']))

        self.addParameter(QgsProcessingParameterBoolean(
            self.PARAM_RUN_1D_SCAN, 'Scan loaded 1D layers for basic integrity issues',
            defaultValue=d['run_1d']))

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                name=self.PARAM_1D_SINK,
                description='1D integrity issues (points)',
                type=QgsProcessing.TypeVectorPoint,
                optional=True
            )
        )

        # NEW: Dependencies
        self.addParameter(QgsProcessingParameterEnum(
            self.PARAM_DEP_MODE, 'Dependencies mode',
            options=self.DEP_MODES, defaultValue=d['dep_mode']))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.PARAM_DEP_CSV, 'Dependencies CSV report',
            fileFilter='CSV files (*.csv);;All files (*.*)', defaultValue=d['dep_csv'], optional=True))

        # Save defaults toggle
        self.addParameter(QgsProcessingParameterBoolean(
            self.PARAM_SAVE_DEFAULTS, 'Save current values as default', defaultValue=False))

        # Output string for preview
        self.addOutput(QgsProcessingOutputString('preview_block', 'Preview TCF block'))
        # Output string for dep summary
        self.addOutput(QgsProcessingOutputString('dependencies_summary', 'Dependencies summary'))

    # ===== Utilities =====
    def _compose_preview_block(self, tcf_path, check_folder, mode, incl, excl, prefix,
                               set_proj_check, mi_save_date, mi_save_ext):
        def _norm_folder(path):
            if not path:
                return None
            if path.endswith('\\') or path.endswith('/'):
                return path
            return path + '\\'

        check_folder = _norm_folder(check_folder)

        block = []
        # Build Write Check Files section
        if mode == 0:  # ALL
            if check_folder and prefix:
                block.append(f'Write Check Files == {check_folder}{prefix}')
            elif check_folder:
                block.append(f'Write Check Files == {check_folder}')
            else:
                block.append('Write Check Files All !')
        elif mode == 1:  # INCLUDE
            target = incl.strip() if incl else ''
            if not target:
                raise ValueError('Include list is empty while mode=INCLUDE')
            block.append(f'Write Check Files Include == {target}')
            if check_folder:
                block.append(f'Write Check Files == {check_folder}{prefix or ""}')
        elif mode == 2:  # EXCLUDE
            target = excl.strip() if excl else ''
            if not target:
                raise ValueError('Exclude list is empty while mode=EXCLUDE')
            block.append(f'Write Check Files Exclude == {target}')
            if check_folder:
                block.append(f'Write Check Files == {check_folder}{prefix or ""}')

        # Optional helper checks
        if set_proj_check:
            block.append('GIS Projection Check == ON')
        if mi_save_date == 1:
            block.append('Check MI Save Date == OFF')
        elif mi_save_date == 2:
            block.append('Check MI Save Date == WARNING')
        if mi_save_ext == 1:
            block.append('Check MI Save Ext == OFF')
        elif mi_save_ext == 2:
            block.append('Check MI Save Ext == WARNING')

        header = f'# Preview for {os.path.basename(tcf_path)} (no edits performed)\n# Insert near top of .tcf (before Event File if present)\n'
        return header + '\n'.join(block) + '\n'

    def _run_tuflow(self, exe_path, args, feedback):
        """Run TUFLOW with given args (list), capture stdout/stderr, return (code, stdout, stderr)."""
        if not exe_path or not os.path.isfile(exe_path):
            feedback.pushInfo('No TUFLOW executable provided; skipping.')
            return None, b'', b''
        cmd = [exe_path] + args
        feedback.pushInfo('Running: ' + ' '.join(f'"{a}"' if ' ' in a else a for a in cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = proc.communicate()
            return proc.returncode, out, err
        except Exception as e:
            feedback.reportError(f'Error running TUFLOW: {e}')
            return None, b'', b''

    def _run_tuflow_test(self, exe_path, tcf_path, feedback):
        code, out, err = self._run_tuflow(exe_path, ['-t', '-b', '-nc', tcf_path], feedback)
        if code is None:
            return None
        # Save console text into .../TUFLOW/runs/log
        runs_dir = os.path.dirname(tcf_path)
        tuflow_root = os.path.dirname(runs_dir)
        log_dir = os.path.join(tuflow_root, 'runs', 'log')
        os.makedirs(log_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(tcf_path))[0]
        with open(os.path.join(log_dir, f'{base}_test_stdout.txt'), 'wb') as f:
            f.write(out or b'')
        with open(os.path.join(log_dir, f'{base}_test_stderr.txt'), 'wb') as f:
            f.write(err or b'')
        feedback.pushInfo(f'TUFLOW test completed (exit code={code}). Logs saved to: {log_dir}')
        return code

    # --- Dependency audit
    def _find_list_file_after_cL(self, tcf_path):
        # According to TUFLOW wiki, -cL writes a .tcl list in same dir as the .tcf, named after the simulation.
        base = os.path.splitext(os.path.basename(tcf_path))[0]
        folder = os.path.dirname(tcf_path)
        candidate = os.path.join(folder, f'{base}.tcl')
        return candidate if os.path.isfile(candidate) else None

    def _find_list_file_after_pmL(self, tcf_path):
        # For -pmL, TUFLOW writes an output "list" file as well. Typical naming mimics -cL but may vary.
        # Try common candidates in the runs folder.
        base = os.path.splitext(os.path.basename(tcf_path))[0]
        folder = os.path.dirname(tcf_path)
        candidates = [
            os.path.join(folder, f'{base}.tcl'),
            os.path.join(folder, f'pm_{base}.tcl')
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        # Fallback: scan for a recent .tcl created in this folder
        tcls = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith('.tcl')]
        tcls.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return tcls[0] if tcls else None

    def _classify_dep(self, path_lower):
        # Crude classification by extension
        ext = os.path.splitext(path_lower)[1]
        if ext in ('.tcf', '.tgc', '.tbc', '.ecf', '.tef', '.qcf', '.toc', '.trfc', '.tesf', '.adcf', '.tscf'):
            return 'Control'
        if ext in ('.shp', '.gpkg', '.mif', '.mid', '.tab', '.tif', '.tiff', '.asc', '.flt'):
            return 'GIS'
        if ext in ('.csv', '.txt'):
            return 'Table'
        if ext in ('.xf',):
            return 'XF'
        return 'Other'

    def _probe_gis(self, path):
        # Attempt to open as a vector/raster via OGR/GDAL to retrieve CRS quickly
        # We keep it lightweight—no heavy reading
        if not os.path.isfile(path):
            return '', ''
        # Try vector layer
        vl = QgsVectorLayer(path, 'probe', 'ogr')
        if vl and vl.isValid():
            crs = vl.crs().authid() if vl.crs().isValid() else ''
            return 'Vector', crs
        # Try raster via GDAL (not creating a layer here; keep simple)
        # If needed, you can extend with QgsRasterLayer and check crs()
        return '', ''

    def _read_list_file(self, list_path):
        with open(list_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.read().splitlines()
        # each line should be a file path; strip quotes if any
        files = []
        for ln in lines:
            ln = ln.strip().strip('"').strip("'")
            if ln:
                files.append(ln)
        return files

    def _audit_dependencies(self, exe_path, tcf_path, dep_mode, csv_path, feedback):
        """
        dep_mode: 0=OFF, 1=-cL this run, 2=-pmL all events/scenarios
        Returns (summary_text)
        """
        if dep_mode == 0:
            return 'Dependencies: OFF'

        # Call TUFLOW to produce file list only
        if dep_mode == 1:  # -cL (this run only)
            code, out, err = self._run_tuflow(exe_path, ['-cL', '-b', '-nc', tcf_path], feedback)
            if code is None:
                return 'Dependencies: TUFLOW not executed.'
            list_file = self._find_list_file_after_cL(tcf_path)
        else:  # -pmL (all events/scenarios)
            code, out, err = self._run_tuflow(exe_path, ['-pmL', '-b', '-nc', tcf_path], feedback)
            if code is None:
                return 'Dependencies: TUFLOW not executed.'
            list_file = self._find_list_file_after_pmL(tcf_path)

        if not list_file or not os.path.isfile(list_file):
            return 'Dependencies: list file not found. (Check permissions/paths.)'

        files = self._read_list_file(list_file)
        if not files:
            return 'Dependencies: empty list.'

        # Prepare CSV
        rows = []
        missing = 0
        has_gis = 0
        vector_ok = 0
        crs_missing = 0

        for p in files:
            p_norm = os.path.normpath(p)
            exists = os.path.isfile(p_norm)
            size = mtime = ''
            ftype = self._classify_dep(p_norm.lower())
            crs = ''
            gtype = ''

            if exists:
                try:
                    st = os.stat(p_norm)
                    size = st.st_size
                    mtime = datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')
                except Exception:
                    pass

                if ftype == 'GIS':
                    has_gis += 1
                    gtype, crs = self._probe_gis(p_norm)
                    if gtype == 'Vector':
                        vector_ok += 1
                        if not crs:
                            crs_missing += 1
                    # shapefile sidecars
                    root, ext = os.path.splitext(p_norm)
                    if ext.lower() == '.shp':
                        for side in ('.dbf', '.shx'):
                            if not os.path.isfile(root + side):
                                rows.append([p_norm + side, 'MISSING_SIDECAR', '', '', ftype, gtype, crs])
                                missing += 1

            else:
                missing += 1

            rows.append([p_norm, 'OK' if exists else 'MISSING', size, mtime, ftype, gtype, crs])

        # Write CSV if requested
        if csv_path:
            try:
                os.makedirs(os.path.dirname(csv_path) or '.', exist_ok=True)
                with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f)
                    w.writerow(['path', 'status', 'size_bytes', 'modified_iso', 'type', 'gis_kind', 'crs'])
                    w.writerows(rows)
                feedback.pushInfo(f'Dependencies CSV written: {csv_path}')
            except Exception as e:
                feedback.reportError(f'Failed to write dependencies CSV: {e}')

        # Summary text
        total = len([r for r in rows if not r[0].lower().endswith('.dbf') and not r[0].lower().endswith('.shx')])
        summary = (
            f'Dependencies summary:\n'
            f'  Total listed: {total}\n'
            f'  Missing: {missing}\n'
            f'  GIS items: {has_gis}\n'
            f'  Vector readable: {vector_ok}\n'
            f'  Vector with missing CRS: {crs_missing}\n'
            f'  List file: {list_file}\n'
            f'  Mode: {self.DEP_MODES[dep_mode]}\n'
        )
        feedback.pushInfo(summary)
        return summary

    def _scan_1d_layers(self, parameters, context, feedback):
        fields = QgsFields()
        fields.append(QgsField('layer', QVariant.String))
        fields.append(QgsField('issue', QVariant.String))
        fields.append(QgsField('feature_id', QVariant.String))
        fields.append(QgsField('attr_id', QVariant.String))

        project_crs = QgsProject.instance().crs() or QgsCoordinateReferenceSystem()

        sink, dest_id = self.parameterAsSink(
            parameters=parameters,
            name=self.PARAM_1D_SINK,
            context=context,
            fields=fields,
            geometryType=QgsWkbTypes.Point,
            crs=project_crs
        )

        if sink is None:
            feedback.pushInfo('No sink created for 1D issues; skipping.')
            return None

        pattern_names = ('1d_nwk', '1d_ta', '1d_cs', '1d_xs', '1d_hw', '1d_bc')
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
                  if isinstance(lyr, QgsVectorLayer)
                  and any(n in lyr.name().lower() for n in pattern_names)]

        for lyr in layers:
            id_field = None
            for candidate in ('ID', 'id', 'name', 'Name'):
                if lyr.fields().indexOf(candidate) >= 0:
                    id_field = candidate
                    break

            seen = set()
            for f in lyr.getFeatures():
                geom = f.geometry()
                if geom is None or geom.isEmpty():
                    feat = QgsFeature()
                    feat.setFields(fields)
                    feat.setAttributes([lyr.name(), 'EMPTY_GEOMETRY', str(f.id()), None])
                    pt = lyr.extent().center()
                    feat.setGeometry(QgsGeometry.fromWkt(f'POINT({pt.x()} {pt.y()})'))
                    sink.addFeature(feat)
                if id_field:
                    val = f[id_field]
                    if val in seen:
                        feat = QgsFeature()
                        feat.setFields(fields)
                        feat.setAttributes([lyr.name(), 'DUPLICATE_ID', str(f.id()), str(val)])
                        try:
                            pt = geom.centroid().asPoint() if geom and not geom.isEmpty() else lyr.extent().center()
                        except Exception:
                            pt = lyr.extent().center()
                        feat.setGeometry(QgsGeometry.fromWkt(f'POINT({pt.x()} {pt.y()})'))
                        sink.addFeature(feat)
                    else:
                        seen.add(val)
        return dest_id

    # ===== Main =====
    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        tcf_path = self.parameterAsFile(parameters, self.PARAM_TCF, context)
        exe_path = self.parameterAsFile(parameters, self.PARAM_TUFLOW_EXE, context)
        check_folder = self.parameterAsString(parameters, self.PARAM_CHECKFOLDER, context)
        mode_idx = self.parameterAsEnum(parameters, self.PARAM_MODE, context)
        incl = self.parameterAsString(parameters, self.PARAM_INCLUDE, context)
        excl = self.parameterAsString(parameters, self.PARAM_EXCLUDE, context)
        prefix = self.parameterAsString(parameters, self.PARAM_PREFIX, context)
        set_proj_check = self.parameterAsBoolean(parameters, self.PARAM_SET_GIS_PROJ_CHECK, context)
        mi_save_date = self.parameterAsEnum(parameters, self.PARAM_SET_MI_SAVE_DATE, context)
        mi_save_ext = self.parameterAsEnum(parameters, self.PARAM_SET_MI_SAVE_EXT, context)
        preview_only = self.parameterAsBoolean(parameters, self.PARAM_PREVIEW_ONLY, context)
        report_path = self.parameterAsFileOutput(parameters, self.PARAM_REPORT_FILE, context)
        run_test = self.parameterAsBoolean(parameters, self.PARAM_RUN_TEST, context)
        run_1d = self.parameterAsBoolean(parameters, self.PARAM_RUN_1D_SCAN, context)
        dep_mode = self.parameterAsEnum(parameters, self.PARAM_DEP_MODE, context)
        dep_csv = self.parameterAsFileOutput(parameters, self.PARAM_DEP_CSV, context)
        save_defaults = self.parameterAsBoolean(parameters, self.PARAM_SAVE_DEFAULTS, context)

        if not os.path.isfile(tcf_path):
            raise QgsProcessingException('TCF file not found.')

        # Compose preview block (no edits performed)
        preview_block = self._compose_preview_block(
            tcf_path, check_folder, mode_idx, incl, excl, prefix,
            set_proj_check, mi_save_date, mi_save_ext
        )
        feedback.pushInfo('Preview block (no TCF edits):\n' + preview_block)

        # Optional: write preview report
        if report_path:
            try:
                os.makedirs(os.path.dirname(report_path) or '.', exist_ok=True)
                with open(report_path, 'w', encoding='utf-8') as rf:
                    rf.write(preview_block)
                    rf.write('\n# Summary\n')
                    rf.write(f'Mode: {self.MODES[mode_idx]}\n')
                    rf.write(f'Include: {incl}\n')
                    rf.write(f'Exclude: {excl}\n')
                    rf.write(f'Check folder: {check_folder}\n')
                    rf.write(f'Prefix: {prefix}\n')
                    rf.write(f'GIS Projection Check: {"ON" if set_proj_check else "LEAVE"}\n')
                    rf.write(f'Check MI Save Date: {["LEAVE","OFF","WARNING"][mi_save_date]}\n')
                    rf.write(f'Check MI Save Ext: {["LEAVE","OFF","WARNING"][mi_save_ext]}\n')
            except Exception as e:
                feedback.reportError(f'Failed to write preview report: {e}')

        # Optional: test mode (no edits)
        test_code = None
        if run_test:
            test_code = self._run_tuflow_test(exe_path, tcf_path, feedback)

        # Optional: dependencies
        dep_summary = ''
        if dep_mode != 0:
            dep_summary = self._audit_dependencies(exe_path, tcf_path, dep_mode, dep_csv, feedback)

        # 1D scan
        dest_id = None
        if run_1d:
            dest_id = self._scan_1d_layers(parameters, context, feedback)

        # Save defaults
        if save_defaults:
            self._write_defaults({
                'mode': mode_idx,
                'include': incl,
                'exclude': excl,
                'prefix': prefix,
                'check_folder': check_folder,
                'proj_check': set_proj_check,
                'mi_save_date': mi_save_date,
                'mi_save_ext': mi_save_ext,
                'preview_only': preview_only,
                'run_test': run_test,
                'run_1d': run_1d,
                'dep_mode': dep_mode,
                'dep_csv': dep_csv or ''
            })
            feedback.pushInfo('Defaults saved (QgsSettings).')

        return {
            'preview_block': preview_block,
            'dependencies_summary': dep_summary,
            'tcf_path': tcf_path,
            'preview_report': report_path,
            'dependencies_csv': dep_csv,
            'test_exit_code': test_code,
            'one_d_issues': dest_id
        }

    def createInstance(self):
        return TuflowConsistencyIntegrity()