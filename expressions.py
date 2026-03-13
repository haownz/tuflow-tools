# -*- coding: utf-8 -*-
"""
Registers a custom QGIS expression function:
  visible_rasters_in_group(group_name) -> list of visible raster layer names

Usage in QGIS expressions:
  array_first( visible_rasters_in_group('Max WSE') )
"""

from qgis.core import (
    qgsfunction,
    QgsProject,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsMapLayerType,
)

def _collect_visible_rasters_in_group(group_node):
    """
    Recursively collect names of visible raster layers under the given group node.
    Only checks the layer-tree checkbox visibility (i.e., node.isVisible()).
    """
    names = []
    if group_node is None:
        return names

    for child in group_node.children():
        # Child group: recurse
        if isinstance(child, QgsLayerTreeGroup):
            # If the group is unchecked, its children are effectively invisible
            if child.isVisible():
                names.extend(_collect_visible_rasters_in_group(child))

        # Child layer node: check visibility + type
        elif isinstance(child, QgsLayerTreeLayer):
            lyr = child.layer()
            if (lyr is not None
                and child.isVisible()
                and lyr.type() == QgsMapLayerType.RasterLayer):
                names.append(lyr.name())

    return names


@qgsfunction(args='auto', group='Layer Tree')
def visible_rasters_in_group(group_name, feature, parent):
    """
    visible_rasters_in_group(group_name) -> array

    Returns an array (Python list) of names of visible raster layers
    under the layer-tree group named `group_name`.
    Searches recursively into sub-groups.

    Examples:
      array_first( visible_rasters_in_group('Max WSE') )
      array_length( visible_rasters_in_group('MyGroup') )
    """
    try:
        if not group_name:
            return []

        root = QgsProject.instance().layerTreeRoot()
        if root is None:
            return []

        group = root.findGroup(group_name)
        if group is None:
            return []

        return _collect_visible_rasters_in_group(group)

    except Exception:
        return []