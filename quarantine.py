"""Safe quarantine: move unwanted files aside instead of deleting them."""

from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# Re-export for older imports (preview/gui historically used quarantine.format_bytes)
from helpers import format_bytes

DEFAULT_QUARANTINE_NAME = "ArchiveOrganiser_Quarantine"
# Parallel unlinks help on fast local disks; on FAT/exFAT USB (often mounted with
# "flush") extra workers usually make deletes slower, so we stay sequential there.
_DELETE_WORKERS = 4
_PROGRESS_EVERY = 25
_SLOW_FS_TYPES = frozenset({"vfat", "msdos", "exfat", "fuseblk", "ntfs", "fuse.ntfs"})


def _delete_workers_for_paths(paths: list[Path], requested: int) -> int:
    """Use 1 worker when deleting from slow removable filesystems."""
    if requested <= 1 or not paths:
        return 1
    try:
        import os

        dev_to_fstype: dict[int, str] = {}
        with open("/proc/mounts", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fstype = parts[1], parts[2]
                try:
                    dev_to_fstype[os.stat(mount_point).st_dev] = fstype.lower()
                except OSError:
                    continue
        sample = paths[: min(12, len(paths))]
        for path in sample:
            try:
                fstype = dev_to_fstype.get(os.stat(path).st_dev, "")
            except OSError:
                continue
            if fstype in _SLOW_FS_TYPES:
                return 1
    except OSError:
        pass
    return max(1, requested)


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


def latest_quarantine_session(base: Optional[str] = None) -> Optional[Path]:
    """
    Newest dated quarantine session folder, if any.
    Prefers folders that look like YYYYMMDD_HHMMSS.
    """
    root = quarantine_root(base)
    if not root.exists():
        return None
    sessions: list[Path] = []
    try:
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                sessions.append(child)
    except OSError:
        return None
    if not sessions:
        return None
    sessions.sort(key=lambda p: p.name, reverse=True)
    return sessions[0]


def move_to_quarantine(
    paths: list[Path],
    base: Optional[str] = None,
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
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
    total = len(paths)
    done = 0
    moved = 0
    errors = 0
    skipped = 0

    for path in paths:
        if should_cancel and should_cancel():
            log.append("[cancelled] Quarantine stopped early.")
            break
        src = Path(path)
        if not src.exists() or not src.is_file():
            log.append(f"[skip] Missing: {src}")
            skipped += 1
            done += 1
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
            moved += 1
        except OSError as exc:
            log.append(f"[error] {src}: {exc}")
            errors += 1
        done += 1
        if status_cb and (done == 1 or done % _PROGRESS_EVERY == 0 or done == total):
            status_cb(f"Quarantining files: {done}/{total}")

    manifest_path = session / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.append(f"Manifest saved: {manifest_path}")
    log.append(
        f"Summary: moved={moved} skipped={skipped} errors={errors} of {total}"
    )
    return session, log


def _unlink_one(path: Path) -> str:
    """Delete one file; return a short log line."""
    src = Path(path)
    try:
        # Fast path: try unlink first (common case = file exists)
        os.unlink(src)
        return f"[deleted] {src}"
    except FileNotFoundError:
        return f"[skip] Missing: {src}"
    except OSError as exc:
        # Fallback for odd path types
        try:
            if not src.exists():
                return f"[skip] Missing: {src}"
            src.unlink()
            return f"[deleted] {src}"
        except OSError as exc2:
            return f"[error] {src}: {exc2 or exc}"


def permanently_delete(
    paths: list[Path],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    workers: int = _DELETE_WORKERS,
) -> list[str]:
    """
    Permanently delete files from disk.
    Prefer move_to_quarantine() unless the user clearly chose delete.

    Uses a small thread pool for many files so bulk delete finishes sooner
    and can report progress. Cancel stops scheduling new work.
    """
    total = len(paths)
    if total == 0:
        return ["Nothing to delete."]

    deleted = 0
    skipped = 0
    errors = 0
    done = 0
    lock = threading.Lock()

    def note_progress() -> None:
        if not status_cb:
            return
        if done == 1 or done % _PROGRESS_EVERY == 0 or done == total:
            status_cb(f"Deleting files: {done}/{total}")

    # Small batches / slow USB filesystems: sequential (less overhead, less thrashing)
    worker_n = _delete_workers_for_paths(paths, max(1, workers))
    use_pool = total >= 20 and worker_n > 1
    # Keep (index, line) so parallel completion can be re-sorted into path order
    indexed: list[tuple[int, str]] = []
    cancelled_note = ""

    if not use_pool:
        for index, path in enumerate(paths):
            if should_cancel and should_cancel():
                cancelled_note = "[cancelled] Delete stopped early."
                break
            line = _unlink_one(path)
            indexed.append((index, line))
            if line.startswith("[deleted]"):
                deleted += 1
            elif line.startswith("[skip]"):
                skipped += 1
            else:
                errors += 1
            done += 1
            note_progress()
    else:
        worker_n = max(1, min(worker_n, 8, total))
        with ThreadPoolExecutor(max_workers=worker_n) as pool:
            futures: dict = {}
            for index, path in enumerate(paths):
                if should_cancel and should_cancel():
                    cancelled_note = "[cancelled] Delete stopped early."
                    break
                futures[pool.submit(_unlink_one, path)] = index
            for fut in as_completed(futures):
                index = futures[fut]
                line = fut.result()
                with lock:
                    indexed.append((index, line))
                    if line.startswith("[deleted]"):
                        deleted += 1
                    elif line.startswith("[skip]"):
                        skipped += 1
                    else:
                        errors += 1
                    done += 1
                    note_progress()

    indexed.sort(key=lambda item: item[0])
    log = [line for _index, line in indexed]
    if cancelled_note:
        log.append(cancelled_note)
    log.append(
        f"Summary: deleted={deleted} skipped={skipped} errors={errors} of {total}"
    )
    return log
