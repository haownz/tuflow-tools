import os
import tempfile
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                 QComboBox, QListWidget, QListWidgetItem, 
                                 QSpinBox, QPushButton, QFileDialog, QMessageBox, QProgressBar,
                                 QLineEdit, QCheckBox)
from qgis.PyQt.QtCore import Qt, QUrl, QSettings
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import (QgsProject, QgsLayoutExporter, QgsLayoutItemMap)

class BatchThemeExportDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Batch Export Layout by Map Themes")
        self.resize(450, 500)
        
        self.layout_manager = QgsProject.instance().layoutManager()
        self.theme_collection = QgsProject.instance().mapThemeCollection()
        
        self._setup_ui()
        self._populate_data()
        self._restore_settings()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Layout Selector
        main_layout.addWidget(QLabel("1. Select Active Layout:"))
        self.combo_layouts = QComboBox()
        main_layout.addWidget(self.combo_layouts)

        # 2. Theme List Table (ListWidget with Checkboxes)
        main_layout.addWidget(QLabel("2. Select Map Themes to Export (Drag to reorder):"))
        self.list_themes = QListWidget()
        self.list_themes.setDragDropMode(QListWidget.InternalMove)
        main_layout.addWidget(self.list_themes)

        theme_btn_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self._select_all_themes)
        self.btn_clear_all = QPushButton("Clear All")
        self.btn_clear_all.clicked.connect(self._clear_all_themes)
        self.btn_invert_selection = QPushButton("Invert Select")
        self.btn_invert_selection.clicked.connect(self._invert_selection)
        theme_btn_layout.addWidget(self.btn_select_all)
        theme_btn_layout.addWidget(self.btn_clear_all)
        theme_btn_layout.addWidget(self.btn_invert_selection)
        
        self.btn_move_top = QPushButton("Top")
        self.btn_move_top.clicked.connect(self._move_item_top)
        self.btn_move_bottom = QPushButton("Bottom")
        self.btn_move_bottom.clicked.connect(self._move_item_bottom)
        theme_btn_layout.addWidget(self.btn_move_top)
        theme_btn_layout.addWidget(self.btn_move_bottom)
        
        theme_btn_layout.addStretch()
        main_layout.addLayout(theme_btn_layout)

        # 3. DPI Setting
        dpi_layout = QHBoxLayout()
        dpi_layout.addWidget(QLabel("3. Output DPI:"))
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(72, 1200)
        self.spin_dpi.setValue(300)
        dpi_layout.addWidget(self.spin_dpi)
        dpi_layout.addStretch()
        main_layout.addLayout(dpi_layout)

        # 4. Output File
        file_layout = QHBoxLayout()
        self.txt_filepath = QLineEdit()
        self.txt_filepath.setPlaceholderText("Select output PDF file...")
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_file)
        file_layout.addWidget(self.txt_filepath)
        file_layout.addWidget(btn_browse)
        main_layout.addWidget(QLabel("4. Output Combined PDF File:"))
        main_layout.addLayout(file_layout)
        
        # Auto Open Checkbox
        self.chk_auto_open = QCheckBox("Automatically open PDF after export")
        self.chk_auto_open.setChecked(True)
        main_layout.addWidget(self.chk_auto_open)

        # Progress Bar
        self.progress = QProgressBar()
        self.progress.setValue(0)
        main_layout.addWidget(self.progress)

        # Run Button
        self.btn_run = QPushButton("Export to PDF")
        self.btn_run.clicked.connect(self._run_export)
        main_layout.addWidget(self.btn_run)

    def _populate_data(self):
        # Populate Layouts
        layouts = self.layout_manager.printLayouts()
        for layout in layouts:
            self.combo_layouts.addItem(layout.name(), layout)
            
        # Populate Themes with checkboxes
        themes = self.theme_collection.mapThemes()
        for theme in themes:
            item = QListWidgetItem(theme)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list_themes.addItem(item)

    def refresh_data(self):
        # 1. Update Layouts
        current_layout = self.combo_layouts.currentText()
        self.combo_layouts.clear()
        layouts = self.layout_manager.printLayouts()
        for layout in layouts:
            self.combo_layouts.addItem(layout.name(), layout)
        
        idx = self.combo_layouts.findText(current_layout)
        if idx >= 0:
            self.combo_layouts.setCurrentIndex(idx)

        # 2. Update Themes
        current_themes = self.theme_collection.mapThemes()
        
        # Remember current state in the list widget
        existing_items = {}
        for i in range(self.list_themes.count()):
            item = self.list_themes.item(i)
            existing_items[item.text()] = item.checkState()
            
        self.list_themes.clear()
        
        # Preserve the order of the existing items that are still in current_themes
        for text in existing_items.keys():
            if text in current_themes:
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(existing_items[text])
                self.list_themes.addItem(item)
                
        # Add new themes that were not in the list (to the bottom)
        for theme in current_themes:
            if theme not in existing_items:
                item = QListWidgetItem(theme)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.list_themes.addItem(item)

    def _browse_file(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save Combined PDF", "", "PDF Files (*.pdf)")
        if filename:
            self.txt_filepath.setText(filename)

    def _select_all_themes(self):
        for i in range(self.list_themes.count()):
            self.list_themes.item(i).setCheckState(Qt.Checked)

    def _clear_all_themes(self):
        for i in range(self.list_themes.count()):
            self.list_themes.item(i).setCheckState(Qt.Unchecked)

    def _invert_selection(self):
        for i in range(self.list_themes.count()):
            item = self.list_themes.item(i)
            if item.checkState() == Qt.Checked:
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(Qt.Checked)

    def _move_item_top(self):
        current_row = self.list_themes.currentRow()
        if current_row > 0:
            item = self.list_themes.takeItem(current_row)
            self.list_themes.insertItem(0, item)
            self.list_themes.setCurrentRow(0)

    def _move_item_bottom(self):
        current_row = self.list_themes.currentRow()
        if current_row >= 0 and current_row < self.list_themes.count() - 1:
            item = self.list_themes.takeItem(current_row)
            self.list_themes.addItem(item)
            self.list_themes.setCurrentRow(self.list_themes.count() - 1)

    def _save_settings(self):
        settings = QSettings()
        settings.setValue("tuflow_tools/batch_export/layout", self.combo_layouts.currentText())
        settings.setValue("tuflow_tools/batch_export/dpi", self.spin_dpi.value())
        settings.setValue("tuflow_tools/batch_export/output_path", self.txt_filepath.text())
        settings.setValue("tuflow_tools/batch_export/auto_open", self.chk_auto_open.isChecked())

        themes_state = []
        for i in range(self.list_themes.count()):
            item = self.list_themes.item(i)
            state = "1" if item.checkState() == Qt.Checked else "0"
            themes_state.append(f"{item.text()}|{state}")
        settings.setValue("tuflow_tools/batch_export/themes_state", themes_state)

    def _restore_settings(self):
        settings = QSettings()
        
        saved_layout = settings.value("tuflow_tools/batch_export/layout", "")
        if saved_layout:
            idx = self.combo_layouts.findText(str(saved_layout))
            if idx >= 0:
                self.combo_layouts.setCurrentIndex(idx)
                
        saved_dpi = settings.value("tuflow_tools/batch_export/dpi", 300, type=int)
        self.spin_dpi.setValue(saved_dpi)
        
        saved_path = settings.value("tuflow_tools/batch_export/output_path", "")
        self.txt_filepath.setText(str(saved_path))
        
        saved_auto_open = settings.value("tuflow_tools/batch_export/auto_open", True, type=bool)
        self.chk_auto_open.setChecked(saved_auto_open)
        
        themes_state = settings.value("tuflow_tools/batch_export/themes_state", [])
        if themes_state:
            saved_themes_dict = {}
            for idx, t_str in enumerate(themes_state):
                parts = str(t_str).rsplit("|", 1)
                if len(parts) == 2:
                    saved_themes_dict[parts[0]] = {"order": idx, "checked": parts[1] == "1"}

            items_in_settings = []
            items_not_in_settings = []
            
            for i in range(self.list_themes.count() - 1, -1, -1):
                item = self.list_themes.takeItem(i)
                if item.text() in saved_themes_dict:
                    items_in_settings.append(item)
                else:
                    items_not_in_settings.append(item)
                    
            items_in_settings.sort(key=lambda x: saved_themes_dict[x.text()]["order"])
            
            for item in items_in_settings:
                is_checked = saved_themes_dict[item.text()]["checked"]
                item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
                self.list_themes.addItem(item)
                
            # items not in settings go to bottom
            for item in items_not_in_settings:
                item.setCheckState(Qt.Unchecked)
                self.list_themes.addItem(item)

    def _run_export(self):
        # Validation
        layout_name = self.combo_layouts.currentText()
        qgs_layout = self.layout_manager.layoutByName(layout_name)
        
        if not qgs_layout:
            QMessageBox.warning(self, "Error", "No layout selected.")
            return

        selected_themes = []
        for i in range(self.list_themes.count()):
            item = self.list_themes.item(i)
            if item.checkState() == Qt.Checked:
                selected_themes.append(item.text())

        if not selected_themes:
            QMessageBox.warning(self, "Error", "Please select at least one map theme.")
            return

        output_path = self.txt_filepath.text().strip()
        if not output_path:
            QMessageBox.warning(self, "Error", "Please select or enter an output file path.")
            return

        self._save_settings()

        # Find Map Items set to follow theme
        theme_map_items = []
        for item in qgs_layout.items():
            if isinstance(item, QgsLayoutItemMap) and item.followVisibilityPreset():
                theme_map_items.append(item)

        if not theme_map_items:
            QMessageBox.warning(self, "Error", "The selected layout does not have any Map Item set to 'Follow map theme'.")
            return

        # Backup original theme to restore later
        original_themes = {map_item.uuid(): map_item.followVisibilityPresetName() for map_item in theme_map_items}

        # Setup Exporter
        exporter = QgsLayoutExporter(qgs_layout)
        pdf_settings = QgsLayoutExporter.PdfExportSettings()
        pdf_settings.dpi = self.spin_dpi.value()

        temp_pdfs = []
        self.progress.setMaximum(len(selected_themes))

        # Looping through selected themes
        for index, theme in enumerate(selected_themes):
            # Apply theme to targeted map items
            for map_item in theme_map_items:
                map_item.setFollowVisibilityPresetName(theme)
                map_item.refresh()
            
            qgs_layout.refresh()

            # Export to temporary file
            temp_file = os.path.join(tempfile.gettempdir(), f"temp_export_{index}.pdf")
            result = exporter.exportToPdf(temp_file, pdf_settings)
            
            if result == QgsLayoutExporter.Success:
                temp_pdfs.append((theme, temp_file))
            else:
                from qgis.utils import iface
                from qgis.core import Qgis
                iface.messageBar().pushMessage("Export Error", f"Failed to export theme: {theme}", level=Qgis.Warning, duration=5)
            
            self.progress.setValue(index + 1)

        # Restore original layout state
        for map_item in theme_map_items:
            map_item.setFollowVisibilityPresetName(original_themes[map_item.uuid()])
            map_item.refresh()
        qgs_layout.refresh()

        # Combine PDFs
        self._combine_pdfs(temp_pdfs, selected_themes, output_path)

    def _combine_pdfs(self, temp_pdfs, selected_themes, output_path):
        try:
            # Try to import PyPDF2 to merge files
            try:
                from PyPDF2 import PdfMerger
            except ImportError:
                # Fallback for older PyPDF2 versions
                from PyPDF2 import PdfFileMerger as PdfMerger 
                
            merger = PdfMerger()
            file_handles = []
            try:
                for theme, pdf_path in temp_pdfs:
                    f = open(pdf_path, "rb")
                    file_handles.append(f)
                    merger.append(f)
                
                with open(output_path, "wb") as out_file:
                    merger.write(out_file)
            finally:
                merger.close()
                for f in file_handles:
                    try:
                        f.close()
                    except Exception:
                        pass
            
            # Clean up temp files
            for _, pdf_path in temp_pdfs:
                if os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass
                    
            if self.chk_auto_open.isChecked():
                QDesktopServices.openUrl(QUrl.fromLocalFile(output_path))
                from qgis.utils import iface
                from qgis.core import Qgis
                iface.messageBar().pushMessage("Success", "Layouts successfully exported and combined into one PDF.", level=Qgis.Success, duration=5)
            else:
                from qgis.utils import iface
                from qgis.core import Qgis
                iface.messageBar().pushMessage("Success", "Layouts successfully exported and combined into one PDF.", level=Qgis.Success, duration=5)
                
            self.accept()

        except ImportError:
            # Fallback if PyPDF2 is not installed: Save as separate files
            base_dir = os.path.dirname(output_path)
            base_name = os.path.splitext(os.path.basename(output_path))[0]
            
            for theme, pdf_path in temp_pdfs:
                safe_theme_name = "".join([c for c in theme if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                new_path = os.path.join(base_dir, f"{base_name}_{safe_theme_name}.pdf")
                os.rename(pdf_path, new_path)
                
            from qgis.utils import iface
            from qgis.core import Qgis
            iface.messageBar().pushMessage("Missing Library", 
                                "The 'PyPDF2' Python library is not installed. "
                                "The script exported your themes, but saved them as separate PDF files instead of a combined one.",
                                level=Qgis.Warning, duration=10)
            self.accept()

from qgis.core import QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingFeedback
from qgis.PyQt.QtCore import QCoreApplication

_batch_theme_export_dialog_instance = None

class BatchThemeExportAlgorithm(QgsProcessingAlgorithm):
    ALG_ID = "batch_theme_export"

    def name(self):
        return self.ALG_ID

    def displayName(self):
        return self.tr("Batch Theme PDF Export")

    def group(self):
        return self.tr("3 - Utilities")

    def groupId(self):
        return "utilities"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return self.tr("Export a layout to a combined PDF looping with a series of selected map themes.")

    def tr(self, string):
        return QCoreApplication.translate("BatchThemeExportAlgorithm", string)

    def createInstance(self):
        return BatchThemeExportAlgorithm()

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        global _batch_theme_export_dialog_instance
        if _batch_theme_export_dialog_instance is None:
            _batch_theme_export_dialog_instance = BatchThemeExportDialog()
        else:
            _batch_theme_export_dialog_instance.refresh_data()
        
        _batch_theme_export_dialog_instance.show()
        _batch_theme_export_dialog_instance.raise_()
        _batch_theme_export_dialog_instance.activateWindow()
        return {}