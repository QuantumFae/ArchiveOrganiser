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
    "extract_zips": False,
    "delete_zip_after_extract": False,
    "delete_zip_if_low_space": False,
    "copy_instead_of_move": True,
    "dry_run": True,
    "media_date_depth": "year_month",
    "documents_by_ext": True,
    "separate_archives": True,
    "category_subfolders": {},
    "rename_with_date_prefix": False,
    "sanitize_filenames": True,
    "add_readme_notes": True,
    "archive_older_than_days": 365,
    "custom_structure_text": "",
    "use_ollama_suggest": False,
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
            # Migrate legacy media_by_date bool → media_date_depth
            if "media_date_depth" not in raw and "media_by_date" in raw:
                legacy = raw.get("media_by_date")
                if isinstance(legacy, bool):
                    data["media_date_depth"] = "year_month" if legacy else "none"
                else:
                    data["media_date_depth"] = "year_month"
            if not isinstance(data.get("category_subfolders"), dict):
                data["category_subfolders"] = {}
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
