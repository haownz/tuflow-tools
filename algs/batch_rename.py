# -*- coding: utf-8 -*-
"""
Rename Selected Layers by Wildcard / Regex + Prefix/Suffix

- Renames the *currently selected* layers in the Layers panel (if available),
  otherwise renames layers chosen via the parameter input.
- Supports wildcard patterns (* and ?) with backreferences, or pure regex.
- Replacement supports Python regex groups (\1, \2, or \g<1>).
- Adds optional prefix and suffix to the final name (applied after pattern replacement).
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsMapLayer,
    QgsProject,
)

import re

class RenameLayersByPattern(QgsProcessingAlgorithm):
    # Parameters
    PARAM_USE_SELECTED = 'USE_SELECTED'
    PARAM_LAYERS = 'LAYERS'
    PARAM_MODE = 'MODE'
    PARAM_PATTERN = 'PATTERN'
    PARAM_REPLACEMENT = 'REPLACEMENT'
    PARAM_CASE = 'CASE_SENSITIVE'
    PARAM_ANCHOR = 'ANCHOR_WHOLE_NAME'
    PARAM_PREVIEW = 'PREVIEW_ONLY'
    PARAM_UNIQUE = 'ENSURE_UNIQUE'
    PARAM_PREFIX = 'PREFIX'
    PARAM_SUFFIX = 'SUFFIX'

    # Outputs
    OUTPUT_COUNT = 'RENAMED_COUNT'
    OUTPUT_LOG = 'RENAME_LOG'

    MODES = ['Wildcard', 'Regex']

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return RenameLayersByPattern()

    def name(self):
        return 'rename_layers_by_pattern'

    def displayName(self):
        return self.tr('Batch Rename Layers')

    def group(self):
        return 'General Tools'

    def groupId(self):
        return 'general_tools'

    def shortHelpString(self):
        return self.tr(
            "Renames selected (or chosen) layers using either a wildcard (* and ?) or a regular expression. "
            "Replacement supports regex backreferences (e.g., \\1, \\g<1>). You can also add a prefix and/or suffix "
            "to the final name. Options include case sensitivity, anchoring to the whole name, preview mode, and "
            "ensuring unique names."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_USE_SELECTED,
                self.tr('Use layers selected in the Layers panel (if available)'),
                defaultValue=True
            )
        )

        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.PARAM_LAYERS,
                self.tr('Layers to rename (used if no panel selection or Use selected is off)'),
                layerType=QgsProcessing.TypeMapLayer,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_MODE,
                self.tr('Pattern mode'),
                options=self.MODES,
                defaultValue=0  # Wildcard
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_PATTERN,
                self.tr('Pattern (Wildcard: * and ? | Regex) — leave empty to skip'),
                defaultValue='',
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_REPLACEMENT,
                self.tr('Replacement (supports regex backreferences, e.g., \\1, \\g<1>)'),
                defaultValue='',
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_CASE,
                self.tr('Case sensitive'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_ANCHOR,
                self.tr('Anchor to whole layer name (^...$)'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_PREFIX,
                self.tr('Prefix to add (optional)'),
                defaultValue='',
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_SUFFIX,
                self.tr('Suffix to add (optional)'),
                defaultValue='',
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_PREVIEW,
                self.tr('Preview only (don’t apply changes)'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_UNIQUE,
                self.tr('Ensure unique names (append _1, _2 if needed)'),
                defaultValue=True
            )
        )

        # Outputs
        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_COUNT, self.tr('Renamed layers count')))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_LOG, self.tr('Rename log')))

    # --- Helpers ---

    @staticmethod
    def wildcard_to_regex_with_groups(pattern: str, anchor: bool) -> str:
        """
        Convert a wildcard pattern to a regex string with capture groups:
          *  -> (.*)
          ?  -> (.)
        Other characters are escaped.
        If anchor is True, wrap with ^...$ to force full-name match.
        """
        res = []
        for ch in pattern:
            if ch == '*':
                res.append('(.*)')
            elif ch == '?':
                res.append('(.)')
            else:
                res.append(re.escape(ch))
        expr = ''.join(res)
        if anchor:
            expr = '^' + expr + '$'
        return expr

    @staticmethod
    def ensure_unique_name(proposed: str, used: set) -> str:
        """
        Ensure name uniqueness by appending _1, _2, ... if needed.
        """
        if proposed not in used:
            used.add(proposed)
            return proposed
        base = proposed
        i = 1
        while True:
            candidate = f"{base}_{i}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            i += 1

    def processAlgorithm(self, parameters, context, feedback):
        use_selected = self.parameterAsBool(parameters, self.PARAM_USE_SELECTED, context)
        layers_param = self.parameterAsLayerList(parameters, self.PARAM_LAYERS, context)
        mode_index = self.parameterAsInt(parameters, self.PARAM_MODE, context)
        pattern = self.parameterAsString(parameters, self.PARAM_PATTERN, context) or ''
        replacement = self.parameterAsString(parameters, self.PARAM_REPLACEMENT, context) or ''
        case_sensitive = self.parameterAsBool(parameters, self.PARAM_CASE, context)
        anchor_whole = self.parameterAsBool(parameters, self.PARAM_ANCHOR, context)
        prefix = self.parameterAsString(parameters, self.PARAM_PREFIX, context) or ''
        suffix = self.parameterAsString(parameters, self.PARAM_SUFFIX, context) or ''
        preview_only = self.parameterAsBool(parameters, self.PARAM_PREVIEW, context)
        ensure_unique = self.parameterAsBool(parameters, self.PARAM_UNIQUE, context)

        # Gather target layers
        target_layers = []
        iface_layers = []

        if use_selected:
            try:
                from qgis.utils import iface
                if iface is not None and iface.layerTreeView() is not None:
                    iface_layers = iface.layerTreeView().selectedLayers()
            except Exception:
                iface_layers = []

        if iface_layers:
            target_layers = iface_layers
        else:
            # Fallback to provided list
            if not layers_param:
                raise QgsProcessingException(
                    'No layers selected in the Layers panel and no layers were provided in the parameter.'
                )
            target_layers = [lyr for lyr in layers_param if isinstance(lyr, QgsMapLayer)]

        # Compile regex if pattern provided
        regex = None
        regex_str = None
        if pattern:
            flags = 0 if case_sensitive else re.IGNORECASE
            mode = self.MODES[mode_index] if 0 <= mode_index < len(self.MODES) else 'Wildcard'

            if mode == 'Wildcard':
                regex_str = self.wildcard_to_regex_with_groups(pattern, anchor=anchor_whole)
            else:  # Regex
                regex_str = pattern
                if anchor_whole:
                    # Enforce full-name match regardless of user anchors
                    if not regex_str.startswith('^'):
                        regex_str = '^' + regex_str
                    if not regex_str.endswith('$'):
                        regex_str = regex_str + '$'

            try:
                regex = re.compile(regex_str, flags)
            except re.error as err:
                raise QgsProcessingException(f'Invalid regular expression: {err}')

        # Prepare name set for uniqueness (all existing layer names at start)
        all_layers = list(QgsProject.instance().mapLayers().values())
        used_names = {lyr.name() for lyr in all_layers}

        rename_log_lines = []
        renamed_count = 0

        for lyr in target_layers:
            old_name = lyr.name()

            # Apply pattern replacement if a pattern was provided
            if regex is not None:
                name_after_pattern = regex.sub(replacement, old_name)
            else:
                name_after_pattern = old_name

            # Apply prefix and suffix after pattern replacement
            proposed_name = f"{prefix}{name_after_pattern}{suffix}"

            if proposed_name != old_name:
                final_name = proposed_name
                if ensure_unique:
                    # Allow the layer to reuse its own current name if needed
                    used_names.discard(old_name)
                    final_name = self.ensure_unique_name(final_name, used_names)

                if not preview_only:
                    lyr.setName(final_name)

                rename_log_lines.append(f'"{old_name}"  →  "{final_name}"')
                renamed_count += 1
            else:
                rename_log_lines.append(f'"{old_name}"  (unchanged)')

        log_text = '\n'.join(rename_log_lines)
        feedback.pushInfo('Rename results:\n' + log_text)

        return {
            self.OUTPUT_COUNT: renamed_count,
            self.OUTPUT_LOG: log_text
        }