"""Loading the optional TOML configuration file.

Configuration is read with the standard-library :mod:`tomllib` (Python 3.11+),
so there is no third-party dependency just to parse TOML. The tool ships a
documented ``backup-config.example.toml``; the real file is optional and every
value has a sensible default.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from adbk.categories import DEFAULT_CATEGORIES
from adbk.errors import ConfigError
from adbk.models import Category


@dataclass(frozen=True)
class AppConfig:
    """Values read from ``backup-config.toml`` (all optional)."""

    backup_dir: Path | None = None
    adb_path: Path | None = None
    adb_server_host: str | None = None
    adb_server_port: int | None = None
    auto_install_adb: bool | None = None  # None means "ask when interactive"
    categories: tuple[Category, ...] = field(default_factory=tuple)
    ignore_dirs: tuple[str, ...] = field(default_factory=tuple)
    ignore_files: tuple[str, ...] = field(default_factory=tuple)
    # Packages whose APK is always kept, even if they came from the store (use
    # this for delisted apps that a store check would otherwise mark available).
    force_apk: tuple[str, ...] = field(default_factory=tuple)


def _as_path(value: object, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Config value '{field}' must be a string path.")
    return Path(value).expanduser() if value else None


def _as_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Config value '{field}' must be an integer.")
    return value


def _as_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"Config value '{field}' must be true or false.")
    return value


def load_config(path: Path | None) -> AppConfig:
    """Load configuration from ``path``; return defaults if it does not exist."""

    if path is None or not path.is_file():
        return AppConfig()

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    backup = raw.get("backup", {})
    adb = raw.get("adb", {})
    filters_table = raw.get("filters", {})
    apps_table = raw.get("apps", {})
    if not isinstance(apps_table, dict):
        raise ConfigError("Config section [apps] must be a table.")
    if not isinstance(backup, dict) or not isinstance(adb, dict):
        raise ConfigError("Config sections [backup] and [adb] must be tables.")
    if not isinstance(filters_table, dict):
        raise ConfigError("Config section [filters] must be a table.")

    return AppConfig(
        backup_dir=_as_path(backup.get("destination"), "backup.destination"),
        adb_path=_as_path(adb.get("path"), "adb.path"),
        adb_server_host=_as_str(adb.get("server_host"), "adb.server_host"),
        adb_server_port=_as_int(adb.get("server_port"), "adb.server_port"),
        auto_install_adb=_as_bool(adb.get("auto_install"), "adb.auto_install"),
        categories=_parse_categories(raw.get("category", [])),
        ignore_dirs=_as_str_list(filters_table.get("ignore_dirs"), "filters.ignore_dirs"),
        ignore_files=_as_str_list(filters_table.get("ignore_files"), "filters.ignore_files"),
        force_apk=_as_str_list(apps_table.get("force_apk"), "apps.force_apk"),
    )


def _as_str_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"Config '{field}' must be a list of strings.")
    return tuple(value)


def _parse_categories(raw: object) -> tuple[Category, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("Config '[[category]]' entries must be an array of tables.")
    categories: list[Category] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"Category #{index + 1} must be a table.")
        name = item.get("name")
        raw_paths = item.get("paths")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"Category #{index + 1} needs a non-empty string 'name'.")
        if not isinstance(raw_paths, list) or not all(isinstance(p, str) for p in raw_paths):
            raise ConfigError(f"Category '{name}' needs 'paths' as a list of strings.")
        description = item.get("description", "")
        default_selected = item.get("default", True)
        if not isinstance(description, str):
            raise ConfigError(f"Category '{name}' description must be a string.")
        if not isinstance(default_selected, bool):
            raise ConfigError(f"Category '{name}' 'default' must be true or false.")
        categories.append(
            Category(name, tuple(raw_paths), description, default_selected)
        )
    return tuple(categories)


def effective_categories(config: AppConfig) -> tuple[Category, ...]:
    """Return configured categories, or the built-in defaults if none are set."""

    return config.categories or DEFAULT_CATEGORIES


def _as_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Config value '{field}' must be a string.")
    return value or None
