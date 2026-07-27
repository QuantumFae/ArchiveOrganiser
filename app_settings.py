"""Remember preferences and last-scan path between app launches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


APP_DIR_NAME = "ArchiveOrganiser"
_PROJECT_ROOT = Path(__file__).resolve().parent


def _ensure_dir(preferred: Path, fallback: Path) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        # Confirm we can write
        probe = preferred / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except OSError:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def config_dir() -> Path:
    preferred = Path.home() / ".config" / APP_DIR_NAME
    fallback = _PROJECT_ROOT / ".archive_organiser_config"
    return _ensure_dir(preferred, fallback)


def data_dir() -> Path:
    preferred = Path.home() / ".local" / "share" / APP_DIR_NAME
    fallback = _PROJECT_ROOT / ".archive_organiser_data"
    return _ensure_dir(preferred, fallback)


def settings_path() -> Path:
    return config_dir() / "settings.json"


def last_scan_db_path() -> Path:
    return data_dir() / "last_scan.sqlite"


DEFAULT_SETTINGS: dict[str, Any] = {
    "source_paths": [],
    "destination": "",
    "layout_ids": [],
    "window_geometry": "1280x800",
    "appearance": "System",
    "include_junk": False,
    "scan_zips": False,
    "copy_instead_of_move": True,
    "dry_run": True,
    "last_quarantine_session": "",
    "last_scan_db": "",
}


def load_settings() -> dict[str, Any]:
    """Load saved settings, filling in any missing defaults."""
    path = settings_path()
    data = dict(DEFAULT_SETTINGS)
    if not path.exists():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in DEFAULT_SETTINGS:
                    data[key] = value
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return data


def save_settings(data: dict[str, Any]) -> None:
    """Write settings to disk (only known keys)."""
    out = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in data:
            out[key] = data[key]
    path = settings_path()
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
