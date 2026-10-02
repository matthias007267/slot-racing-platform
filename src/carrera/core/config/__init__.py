"""Application configuration."""

from carrera.core.config.models import AppConfig, ConfigError, load_config, save_config
from carrera.core.config.paths import app_data_dir, default_config_path, default_database_path

__all__ = [
    "AppConfig",
    "ConfigError",
    "app_data_dir",
    "default_config_path",
    "default_database_path",
    "load_config",
    "save_config",
]
