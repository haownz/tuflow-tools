# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, 
                                 QFileDialog, QHBoxLayout, QTabWidget, QTableWidget, 
                                 QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProcessingAlgorithm
from ..settings import PluginSettings

class PluginSettingsDialog(QDialog):
    """Dialog for setting plugin global variables."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TUFLOW Tools Settings")
        self.setMinimumSize(600, 400)
        self.init_ui()
        self.load_settings()
    
    def init_ui(self):
        layout = QVBoxLayout()
        
        # Create tab widget
        self.tab_widget = QTabWidget()
        
        # Path tab
        path_tab = self.create_path_tab()
        self.tab_widget.addTab(path_tab, "Path")
        
        # Style tab
        style_tab = self.create_style_tab()
        self.tab_widget.addTab(style_tab, "Style")
        
        layout.addWidget(self.tab_widget)
        
        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
    
    def create_path_tab(self):
        from qgis.PyQt.QtWidgets import QWidget
        widget = QWidget()
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Project Paths Configuration:"))
        
        # Create table
        self.path_table = QTableWidget()
        self.path_table.setColumnCount(3)
        self.path_table.setHorizontalHeaderLabels(["Name", "Path", "Browse"])
        
        # Set column widths
        header = self.path_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.path_table)
        
        # Buttons for table management
        table_btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Row")
        remove_btn = QPushButton("Remove Row")
        add_btn.clicked.connect(self.add_path_row)
        remove_btn.clicked.connect(self.remove_path_row)
        table_btn_layout.addWidget(add_btn)
        table_btn_layout.addWidget(remove_btn)
        table_btn_layout.addStretch()
        layout.addLayout(table_btn_layout)
        
        widget.setLayout(layout)
        return widget
    
    def add_path_row(self):
        row = self.path_table.rowCount()
        self.path_table.insertRow(row)
        
        # Add browse button
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self.browse_path(row))
        self.path_table.setCellWidget(row, 2, browse_btn)
        
        # Add empty items
        self.path_table.setItem(row, 0, QTableWidgetItem(""))
        self.path_table.setItem(row, 1, QTableWidgetItem(""))
    
    def remove_path_row(self):
        current_row = self.path_table.currentRow()
        if current_row >= 0:
            self.path_table.removeRow(current_row)
    
    def browse_path(self, row):
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            self.path_table.setItem(row, 1, QTableWidgetItem(path))
    
    def create_style_tab(self):
        from qgis.PyQt.QtWidgets import QWidget
        widget = QWidget()
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Style Mappings (top item has priority):"))
        
        # Create table
        self.style_table = QTableWidget()
        self.style_table.setColumnCount(3)
        self.style_table.setHorizontalHeaderLabels(["Pattern", "QML File", "Layer Type"])
        
        # Enable drag and drop for reordering
        self.style_table.setDragDropMode(QAbstractItemView.InternalMove)
        self.style_table.setDragDropOverwriteMode(False)
        self.style_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # Set column widths
        header = self.style_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.style_table)
        
        # Buttons for table management
        table_btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Row")
        remove_btn = QPushButton("Remove Row")
        move_up_btn = QPushButton("Move Up")
        move_down_btn = QPushButton("Move Down")
        add_btn.clicked.connect(self.add_style_row)
        remove_btn.clicked.connect(self.remove_style_row)
        move_up_btn.clicked.connect(self.move_row_up)
        move_down_btn.clicked.connect(self.move_row_down)
        table_btn_layout.addWidget(add_btn)
        table_btn_layout.addWidget(remove_btn)
        table_btn_layout.addWidget(move_up_btn)
        table_btn_layout.addWidget(move_down_btn)
        table_btn_layout.addStretch()
        layout.addLayout(table_btn_layout)
        
        widget.setLayout(layout)
        return widget
    
    def add_style_row(self):
        row = self.style_table.rowCount()
        self.style_table.insertRow(row)
        
        # Add combo box for layer type
        combo = QComboBox()
        combo.addItems(["vector", "raster", "both"])
        self.style_table.setCellWidget(row, 2, combo)
        
        # Add empty items for pattern and QML file
        self.style_table.setItem(row, 0, QTableWidgetItem(""))
        self.style_table.setItem(row, 1, QTableWidgetItem(""))
    
    def remove_style_row(self):
        current_row = self.style_table.currentRow()
        if current_row >= 0:
            self.style_table.removeRow(current_row)
    
    def move_row_up(self):
        current_row = self.style_table.currentRow()
        if current_row > 0:
            self._swap_rows(current_row, current_row - 1)
            self.style_table.setCurrentCell(current_row - 1, 0)
    
    def move_row_down(self):
        current_row = self.style_table.currentRow()
        if current_row >= 0 and current_row < self.style_table.rowCount() - 1:
            self._swap_rows(current_row, current_row + 1)
            self.style_table.setCurrentCell(current_row + 1, 0)
    
    def _swap_rows(self, row1, row2):
        # Swap text items
        for col in range(2):  # Only for pattern and QML file columns
            item1 = self.style_table.takeItem(row1, col)
            item2 = self.style_table.takeItem(row2, col)
            self.style_table.setItem(row1, col, item2)
            self.style_table.setItem(row2, col, item1)
        
        # Swap combo boxes
        combo1 = self.style_table.cellWidget(row1, 2)
        combo2 = self.style_table.cellWidget(row2, 2)
        text1 = combo1.currentText() if combo1 else "vector"
        text2 = combo2.currentText() if combo2 else "vector"
        
        new_combo1 = QComboBox()
        new_combo1.addItems(["vector", "raster", "both"])
        new_combo1.setCurrentText(text2)
        
        new_combo2 = QComboBox()
        new_combo2.addItems(["vector", "raster", "both"])
        new_combo2.setCurrentText(text1)
        
        self.style_table.setCellWidget(row1, 2, new_combo1)
        self.style_table.setCellWidget(row2, 2, new_combo2)
    
    def load_settings(self):
        # Load path mappings
        mappings = PluginSettings.get_path_mappings()
        self.path_table.setRowCount(len(mappings))
        
        for row, (name, value) in enumerate(mappings):
            self.path_table.setItem(row, 0, QTableWidgetItem(name))
            self.path_table.setItem(row, 1, QTableWidgetItem(value))
            
            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(lambda checked, r=row: self.browse_path(r))
            self.path_table.setCellWidget(row, 2, browse_btn)
        
        # Load style mappings
        mappings = PluginSettings.get_style_mappings()
        self.style_table.setRowCount(len(mappings))
        
        for row, (pattern, qml_file, layer_type) in enumerate(mappings):
            self.style_table.setItem(row, 0, QTableWidgetItem(pattern))
            self.style_table.setItem(row, 1, QTableWidgetItem(qml_file))
            
            combo = QComboBox()
            combo.addItems(["vector", "raster", "both"])
            combo.setCurrentText(layer_type)
            self.style_table.setCellWidget(row, 2, combo)
    
    def save_settings(self):
        # Save path mappings
        path_mappings = []
        for row in range(self.path_table.rowCount()):
            name_item = self.path_table.item(row, 0)
            value_item = self.path_table.item(row, 1)
            
            if name_item and value_item:
                name = name_item.text().strip()
                value = value_item.text().strip()
                
                if name and value:
                    path_mappings.append([name, value])
                    # Update legacy settings for backward compatibility
                    if name == "Model Path":
                        PluginSettings.set_model_path(value)
                    elif name == "Style Path":
                        PluginSettings.set_style_path(value)
        
        PluginSettings.set_path_mappings(path_mappings)
        
        # Save style mappings
        mappings = []
        for row in range(self.style_table.rowCount()):
            pattern_item = self.style_table.item(row, 0)
            qml_item = self.style_table.item(row, 1)
            combo = self.style_table.cellWidget(row, 2)
            
            if pattern_item and qml_item and combo:
                pattern = pattern_item.text().strip()
                qml_file = qml_item.text().strip()
                layer_type = combo.currentText()
                
                if pattern and qml_file:
                    mappings.append([pattern, qml_file, layer_type])
        
        PluginSettings.set_style_mappings(mappings)
        self.accept()


class PluginSettingsAlgorithm(QgsProcessingAlgorithm):
    """Processing algorithm to open plugin settings dialog."""
    
    def createInstance(self):
        return PluginSettingsAlgorithm()
    
    def name(self):
        return 'plugin_settings'
    
    def displayName(self):
        return 'Plugin Settings'
    
    def group(self):
        return '0 - Configuration'
    
    def groupId(self):
        return 'configuration'
    
    def shortHelpString(self):
        return "Configure TUFLOW Tools plugin global variables including model path and style path."
    
    def initAlgorithm(self, config=None):
        pass
    
    def processAlgorithm(self, parameters, context, feedback):
        dialog = PluginSettingsDialog()
        dialog.exec_()
        return {}
