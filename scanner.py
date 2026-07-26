"""Scan folders and drives for files. Runs only on your computer."""

import hashlib
import os
from pathlib import Path
from typing import Callable, Optional

from models import FileInfo, ScanResult, category_for

# Skip these folder names (system / junk / our own quarantine)
SKIP_DIR_NAMES = {
    ".git",
    ".Trash",
    ".trashes",
    "$RECYCLE.BIN",
    "System Volume Information",
    "__MACOSX",
    "node_modules",
    ".cache",
    "ArchiveOrganiser_Quarantine",
}

# How many bytes to read at a time when hashing
CHUNK_SIZE = 1024 * 1024  # 1 MB


def file_hash(path: Path, progress_cb: Optional[Callable[[], None]] = None) -> str:
    """
    Create a fingerprint (SHA-256) of a file's contents.
    Two files with the same hash are identical.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            if progress_cb:
                progress_cb()
    return hasher.hexdigest()


def should_skip_dir(name: str) -> bool:
    """Return True if this folder name should be ignored."""
    return name in SKIP_DIR_NAMES or name.startswith(".")


def scan_paths(
    roots: list[str],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ScanResult:
    """
    Walk every chosen folder/drive and collect file info.
    Does not move or delete anything.
    """
    result = ScanResult()

    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            result.errors.append(f"Path not found: {root}")
            continue
        if not root_path.is_dir():
            result.errors.append(f"Not a folder: {root}")
            continue

        if status_cb:
            status_cb(f"Scanning: {root_path}")

        for dirpath, dirnames, filenames in os.walk(root_path):
            if should_cancel and should_cancel():
                result.errors.append("Scan cancelled by user.")
                return result

            # Prune folders we do not want to enter
            dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

            for filename in filenames:
                if should_cancel and should_cancel():
                    result.errors.append("Scan cancelled by user.")
                    return result

                full = Path(dirpath) / filename
                try:
                    if not full.is_file() or full.is_symlink():
                        result.skipped += 1
                        continue
                    stat = full.stat()
                    info = FileInfo(
                        path=full,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        category=category_for(full),
                        source_root=str(root_path),
                    )
                    result.files.append(info)
                except OSError as exc:
                    result.errors.append(f"Could not read {full}: {exc}")
                    result.skipped += 1

        if status_cb:
            status_cb(f"Found {len(result.files)} files so far…")

    if status_cb:
        status_cb(f"Scan complete: {len(result.files)} files.")
    return result


def add_hashes(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Fill in hash_value for each file.
    Used for accurate duplicate detection.
    """
    total = len(files)
    for index, info in enumerate(files, start=1):
        if should_cancel and should_cancel():
            if status_cb:
                status_cb("Hashing cancelled.")
            return
        try:
            if status_cb and (index == 1 or index % 25 == 0 or index == total):
                status_cb(f"Fingerprinting files: {index}/{total}")
            info.hash_value = file_hash(info.path)
        except OSError as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash {info.path.name}: {exc}")
