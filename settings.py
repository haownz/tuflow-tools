# -*- coding: utf-8 -*-
from typing import List, Tuple, Any, Optional, Dict
from qgis.PyQt.QtCore import QSettings
import json
import logging

logger = logging.getLogger(__name__)


class PluginSettings:
    """Global settings for TUFLOW Tools plugin with type-safe accessors."""
    
    _PREFIX = "tuflow_tools/"
    
    # Default style mappings
    _DEFAULT_STYLE_MAPPINGS = [
        ["*PLOT_L_QP", "plot_l_qp_02.qml", "vector"],
        ["1d_nwk_*L*", "1d_nwk_L_01.qml", "vector"],
        ["WSE_DIFF*", "wse_diff_01.qml", "raster"],
        ["*d_*Max*", "dmax_01.qml", "raster"],
        ["*h_*Max*", "hmax_01.qml", "raster"],
        ["DEM_*", "dem_hillshade.qml", "raster"],
    ]
    
    # Cache for frequently accessed settings
    _cache: Dict[str, Any] = {}
    
    @staticmethod
    def _get_setting(key: str, default: Any = None, use_cache: bool = True) -> Any:
        """Internal method to get setting with optional caching."""
        full_key = f"{PluginSettings._PREFIX}{key}"
        
        if use_cache and full_key in PluginSettings._cache:
            return PluginSettings._cache[full_key]
        
        value = QSettings().value(full_key, default)
        
        if use_cache:
            PluginSettings._cache[full_key] = value
        
        return value
    
    @staticmethod
    def _set_setting(key: str, value: Any, update_cache: bool = True) -> None:
        """Internal method to set setting with optional cache update."""
        full_key = f"{PluginSettings._PREFIX}{key}"
        QSettings().setValue(full_key, value)
        
        if update_cache:
            PluginSettings._cache[full_key] = value
    
    @staticmethod
    def clear_cache() -> None:
        """Clear the settings cache."""
        PluginSettings._cache.clear()
    
    @staticmethod
    def get_model_path() -> str:
        """Get the model path setting."""
        return PluginSettings._get_setting("model_path", "")
    
    @staticmethod
    def set_model_path(path: str) -> None:
        """Set the model path setting."""
        PluginSettings._set_setting("model_path", path)
    
    @staticmethod
    def get_style_path() -> str:
        """Get the style path setting."""
        return PluginSettings._get_setting("style_path", "")
    
    @staticmethod
    def set_style_path(path: str) -> None:
        """Set the style path setting."""
        PluginSettings._set_setting("style_path", path)
    
    @staticmethod
    def get_style_mappings() -> List[List[str]]:
        """Get style mappings from settings with robust error handling."""
        mappings_str = PluginSettings._get_setting("style_mappings", "")
        
        if not mappings_str:
            logger.debug("No style mappings found in settings, returning defaults")
            return PluginSettings._DEFAULT_STYLE_MAPPINGS.copy()
        
        try:
            mappings = json.loads(mappings_str)
            
            # Validate the structure
            if not isinstance(mappings, list):
                logger.warning("Style mappings is not a list, returning defaults")
                return PluginSettings._DEFAULT_STYLE_MAPPINGS.copy()
            
            # Validate each mapping entry
            valid_mappings = []
            for i, mapping in enumerate(mappings):
                if (isinstance(mapping, list) and len(mapping) == 3 and 
                    all(isinstance(item, str) for item in mapping)):
                    valid_mappings.append(mapping)
                else:
                    logger.warning(f"Invalid style mapping at index {i}: {mapping}")
            
            if not valid_mappings:
                logger.warning("No valid style mappings found, returning defaults")
                return PluginSettings._DEFAULT_STYLE_MAPPINGS.copy()
            
            return valid_mappings
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse style mappings JSON: {e}")
            return PluginSettings._DEFAULT_STYLE_MAPPINGS.copy()
        except Exception as e:
            logger.error(f"Unexpected error loading style mappings: {e}")
            return PluginSettings._DEFAULT_STYLE_MAPPINGS.copy()
    
    @staticmethod
    def set_style_mappings(mappings: List[List[str]]) -> None:
        """Save style mappings to settings with validation."""
        if not isinstance(mappings, list):
            raise ValueError("Style mappings must be a list")
        
        # Validate each mapping
        for i, mapping in enumerate(mappings):
            if not (isinstance(mapping, list) and len(mapping) == 3):
                raise ValueError(f"Mapping at index {i} must be a list of 3 strings")
            if not all(isinstance(item, str) for item in mapping):
                raise ValueError(f"Mapping at index {i} must contain only strings")
        
        try:
            mappings_str = json.dumps(mappings)
            PluginSettings._set_setting("style_mappings", mappings_str)
            logger.debug(f"Saved {len(mappings)} style mappings to settings")
        except Exception as e:
            logger.error(f"Failed to save style mappings: {e}")
            raise
    
    @staticmethod
    def get_path_mappings() -> List[Tuple[str, str]]:
        """Get path mappings from settings."""
        mappings_str = PluginSettings._get_setting("path_mappings", "")
        
        if not mappings_str:
            logger.debug("No path mappings found, returning defaults")
            return PluginSettings._get_default_path_mappings()
        
        try:
            mappings = json.loads(mappings_str)
            
            if not isinstance(mappings, list):
                logger.warning("Path mappings is not a list, returning defaults")
                return PluginSettings._get_default_path_mappings()
            
            # Convert to list of tuples for consistency
            valid_mappings = []
            for i, mapping in enumerate(mappings):
                if (isinstance(mapping, list) and len(mapping) == 2 and 
                    all(isinstance(item, str) for item in mapping)):
                    valid_mappings.append((mapping[0], mapping[1]))
                else:
                    logger.warning(f"Invalid path mapping at index {i}: {mapping}")
            
            if not valid_mappings:
                return PluginSettings._get_default_path_mappings()
            
            return valid_mappings
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse path mappings JSON: {e}")
            return PluginSettings._get_default_path_mappings()
    
    @staticmethod
    def _get_default_path_mappings() -> List[Tuple[str, str]]:
        """Get default path mappings."""
        return [
            ("Model Path", PluginSettings.get_model_path()),
            ("Style Path", PluginSettings.get_style_path()),
        ]
    
    @staticmethod
    def set_path_mappings(mappings: List[Tuple[str, str]]) -> None:
        """Save path mappings to settings."""
        if not isinstance(mappings, list):
            raise ValueError("Path mappings must be a list")
        
        # Convert tuples to lists for JSON serialization
        mappings_list = []
        for i, mapping in enumerate(mappings):
            if not (isinstance(mapping, (list, tuple)) and len(mapping) == 2):
                raise ValueError(f"Mapping at index {i} must be a tuple/list of 2 strings")
            if not all(isinstance(item, str) for item in mapping):
                raise ValueError(f"Mapping at index {i} must contain only strings")
            mappings_list.append(list(mapping))
        
        try:
            mappings_str = json.dumps(mappings_list)
            PluginSettings._set_setting("path_mappings", mappings_str)
            logger.debug(f"Saved {len(mappings)} path mappings to settings")
        except Exception as e:
            logger.error(f"Failed to save path mappings: {e}")
            raise
    
    @staticmethod
    def get_all_settings() -> Dict[str, Any]:
        """Get all plugin settings as a dictionary."""
        settings = QSettings()
        all_keys = settings.allKeys()
        
        plugin_settings = {}
        prefix = PluginSettings._PREFIX
        
        for key in all_keys:
            if key.startswith(prefix):
                setting_name = key[len(prefix):]
                plugin_settings[setting_name] = settings.value(key)
        
        return plugin_settings
    
    @staticmethod
    def reset_to_defaults() -> None:
        """Reset all plugin settings to their default values."""
        # Clear specific settings
        PluginSettings.set_model_path("")
        PluginSettings.set_style_path("")
        PluginSettings.set_style_mappings(PluginSettings._DEFAULT_STYLE_MAPPINGS)
        
        # Clear cache
        PluginSettings.clear_cache()
        
        logger.info("Plugin settings reset to defaults")
    
    @staticmethod
    def migrate_old_settings() -> bool:
        """Migrate settings from older versions if needed.
        
        Returns:
            bool: True if migration was performed, False otherwise
        """
        # Check for old setting format
        old_model_path = QSettings().value("tuflow_tools/model_path_old")
        if old_model_path:
            # Migrate to new format
            PluginSettings.set_model_path(old_model_path)
            QSettings().remove("tuflow_tools/model_path_old")
            logger.info("Migrated old model path setting")
            return True
        
        return False
