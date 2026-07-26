"""Safe quarantine: move unwanted files aside instead of deleting them."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


DEFAULT_QUARANTINE_NAME = "ArchiveOrganiser_Quarantine"


def quarantine_root(base: Optional[str] = None) -> Path:
    """
    Where quarantined files go.
    Default: a folder next to this project, easy to find and restore from.
    """
    if base:
        return Path(base).expanduser().resolve()
    project = Path(__file__).resolve().parent
    return project / DEFAULT_QUARANTINE_NAME


def _session_folder(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = root / stamp
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def move_to_quarantine(
    paths: list[Path],
    base: Optional[str] = None,
) -> tuple[Path, list[str]]:
    """
    Move files into a dated quarantine folder.
    Writes a small manifest so you can see where each file came from.
    Returns (session_folder, log_lines).
    """
    root = quarantine_root(base)
    root.mkdir(parents=True, exist_ok=True)
    session = _session_folder(root)
    log: list[str] = []
    manifest: list[dict] = []

    for path in paths:
        src = Path(path)
        if not src.exists() or not src.is_file():
            log.append(f"[skip] Missing: {src}")
            continue
        dest = session / src.name
        # Avoid name clashes inside the same session
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            n = 1
            while True:
                candidate = session / f"{stem}_{n}{suffix}"
                if not candidate.exists():
                    dest = candidate
                    break
                n += 1
        try:
            shutil.move(str(src), str(dest))
            entry = {"original": str(src), "quarantine": str(dest)}
            manifest.append(entry)
            log.append(f"[quarantined] {src} → {dest}")
        except OSError as exc:
            log.append(f"[error] {src}: {exc}")

    manifest_path = session / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.append(f"Manifest saved: {manifest_path}")
    return session, log


def format_bytes(num: int) -> str:
    """Human-readable size, e.g. 12.3 MB."""
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < step or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{num} B"


def permanently_delete(paths: list[Path]) -> list[str]:
    """
    Permanently delete files from disk.
    Prefer move_to_quarantine() unless the user clearly chose delete.
    """
    log: list[str] = []
    for path in paths:
        src = Path(path)
        try:
            if not src.exists():
                log.append(f"[skip] Missing: {src}")
                continue
            src.unlink()
            log.append(f"[deleted] {src}")
        except OSError as exc:
            log.append(f"[error] {src}: {exc}")
    return log
