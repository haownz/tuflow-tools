import fnmatch
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                 QPushButton, QTableWidget, QTableWidgetItem,
                                 QMessageBox, QLineEdit, QHeaderView, QAbstractItemView, QCheckBox, QWidget)
from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.core import QgsProject, QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingFeedback

class ThemeManagerDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Theme Manager")
        self.resize(800, 640)
        
        self.theme_collection = QgsProject.instance().mapThemeCollection()
        self.theme_collection.mapThemesChanged.connect(self.refresh_table)
        
        self._setup_ui()
        self.refresh_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Table
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Select", "Theme Name"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)
        
        # Selection buttons
        btn_layout = QHBoxLayout()
        btn_sel_all = QPushButton("Select All")
        btn_sel_all.clicked.connect(self._select_all)
        btn_sel_none = QPushButton("Clear All")
        btn_sel_none.clicked.connect(self._clear_all)
        
        btn_check_sel = QPushButton("Check Selected")
        btn_check_sel.clicked.connect(self._check_selected)
        btn_uncheck_sel = QPushButton("Uncheck Selected")
        btn_uncheck_sel.clicked.connect(self._uncheck_selected)

        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)

        btn_layout.addWidget(btn_sel_all)
        btn_layout.addWidget(btn_sel_none)
        btn_layout.addWidget(btn_check_sel)
        btn_layout.addWidget(btn_uncheck_sel)
        btn_layout.addWidget(btn_remove)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # Replace section
        replace_layout = QHBoxLayout()
        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Find (e.g. *old*)")
        self.txt_replace = QLineEdit()
        self.txt_replace.setPlaceholderText("Replace with (e.g. *new*)")
        btn_replace = QPushButton("Replace All")
        btn_replace.clicked.connect(self._replace_names)
        
        replace_layout.addWidget(QLabel("Find:"))
        replace_layout.addWidget(self.txt_find)
        replace_layout.addWidget(QLabel("Replace:"))
        replace_layout.addWidget(self.txt_replace)
        replace_layout.addWidget(btn_replace)
        
        layout.addLayout(replace_layout)

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        themes = self.theme_collection.mapThemes()
        self.table.setRowCount(len(themes))
        
        for row, theme in enumerate(themes):
            # Checkbox item
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk_item)
            
            # Name item
            name_item = QTableWidgetItem(theme)
            # Store original name in UserRole
            name_item.setData(Qt.UserRole, theme)
            self.table.setItem(row, 1, name_item)
            
        self.table.blockSignals(False)

    def _on_cell_changed(self, row, col):
        if col == 1:
            item = self.table.item(row, col)
            new_name = item.text().strip()
            old_name = item.data(Qt.UserRole)
            
            if new_name and new_name != old_name:
                if self.theme_collection.hasMapTheme(new_name):
                    QMessageBox.warning(self, "Warning", f"Theme '{new_name}' already exists.")
                    self.refresh_table()
                    return
                
                state = self.theme_collection.mapThemeState(old_name)
                
                self.theme_collection.blockSignals(True)
                self.theme_collection.removeMapTheme(old_name)
                self.theme_collection.insert(new_name, state)
                self.theme_collection.blockSignals(False)
                self.theme_collection.mapThemesChanged.emit()
            elif not new_name:
                self.refresh_table() # revert to old name

    def _select_all(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked)

    def _clear_all(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)

    def _check_selected(self):
        for item in self.table.selectedItems():
            if item.column() == 0:
                item.setCheckState(Qt.Checked)
            else:
                # also allow checking if the row is selected but cell is col 1
                self.table.item(item.row(), 0).setCheckState(Qt.Checked)

    def _uncheck_selected(self):
        for item in self.table.selectedItems():
            if item.column() == 0:
                item.setCheckState(Qt.Unchecked)
            else:
                self.table.item(item.row(), 0).setCheckState(Qt.Unchecked)

    def _remove_selected(self):
        themes_to_remove = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                themes_to_remove.append(self.table.item(row, 1).data(Qt.UserRole))
                
        if not themes_to_remove:
            return
            
        reply = QMessageBox.question(self, "Confirm", f"Are you sure you want to remove {len(themes_to_remove)} themes?", 
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.theme_collection.blockSignals(True)
            for theme in themes_to_remove:
                self.theme_collection.removeMapTheme(theme)
            self.theme_collection.blockSignals(False)
            self.theme_collection.mapThemesChanged.emit()

    def _replace_names(self):
        find_str = self.txt_find.text()
        replace_str = self.txt_replace.text()
        
        if not find_str:
            return
            
        themes = self.theme_collection.mapThemes()
        
        self.theme_collection.blockSignals(True)
        count = 0
        for theme in themes:
            new_name = theme
            
            # Simple wildcard support
            if '*' in find_str and '*' in replace_str and find_str.count('*') == 1 and replace_str.count('*') == 1:
                prefix, suffix = find_str.split('*')
                if theme.startswith(prefix) and theme.endswith(suffix):
                    # extract the middle part
                    middle = theme[len(prefix):len(theme)-len(suffix) if suffix else len(theme)]
                    r_prefix, r_suffix = replace_str.split('*')
                    new_name = r_prefix + middle + r_suffix
            elif '*' in find_str and '*' not in replace_str:
                # If they just put * in find, they might be expecting fnmatch replacing the whole string,
                # but let's do standard wildcard match -> replace the whole thing.
                if fnmatch.fnmatch(theme, find_str):
                    new_name = replace_str
            else:
                # Standard substring replacement
                new_name = theme.replace(find_str, replace_str)
                
            if new_name != theme and new_name:
                if not self.theme_collection.hasMapTheme(new_name):
                    state = self.theme_collection.mapThemeState(theme)
                    self.theme_collection.removeMapTheme(theme)
                    self.theme_collection.insert(new_name, state)
                    count += 1
        
        self.theme_collection.blockSignals(False)
        self.theme_collection.mapThemesChanged.emit()
        self.refresh_table()
        
        if count > 0:
            QMessageBox.information(self, "Success", f"Replaced {count} theme names.")
        else:
            QMessageBox.information(self, "No Match", "No themes were matched or renamed.")

_theme_manager_dialog_instance = None

def show_theme_manager_dialog():
    global _theme_manager_dialog_instance
    if _theme_manager_dialog_instance is None:
        _theme_manager_dialog_instance = ThemeManagerDialog()
    else:
        _theme_manager_dialog_instance.refresh_table()
    
    _theme_manager_dialog_instance.show()
    _theme_manager_dialog_instance.raise_()
    _theme_manager_dialog_instance.activateWindow()

class ThemeManagerAlgorithm(QgsProcessingAlgorithm):
    ALG_ID = "theme_manager"

    def name(self):
        return self.ALG_ID

    def displayName(self):
        return self.tr("Layer Theme Manager")

    def group(self):
        return self.tr("3 - Utilities")

    def groupId(self):
        return "utilities"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return self.tr("Manage map themes: list, remove, edit, and wildcard replace theme names.")

    def tr(self, string):
        return QCoreApplication.translate("ThemeManagerAlgorithm", string)

    def createInstance(self):
        return ThemeManagerAlgorithm()

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        show_theme_manager_dialog()
        return {}