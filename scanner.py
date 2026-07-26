"""Scan folders and drives for files. Runs only on your computer.

Tuned for large (hundreds of GB / 1TB+) drives:
- stays on one filesystem by default (won't follow other mounts)
- streams hashes in chunks (never loads a whole huge file into RAM)
- reports size + file progress so long scans don't look frozen
- empty files get an instant fingerprint
"""

from __future__ import annotations

import hashlib
import os
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from helpers import ElapsedTimer, format_bytes, format_duration
from models import (
    ARCHIVE_EXTS,
    SCANNABLE_ARCHIVE_EXTS,
    FileInfo,
    ScanResult,
    category_for,
    category_for_name,
)

# Skip these folder names unless the user opts in to junk/system folders
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

# Larger chunks = fewer syscalls on multi‑GB files (still streaming, low RAM)
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB
# How often to refresh status while hashing one large file
LARGE_HASH_STATUS_EVERY = 64 * 1024 * 1024  # 64 MB


@dataclass
class ScanOptions:
    """User choices for how deep / wide the scan goes."""

    include_junk_system: bool = False
    scan_zip_contents: bool = True
    # Always keep our quarantine folder out of scans for safety
    always_skip_quarantine: bool = True
    # Do not descend into folders on a different mount/device (safer for huge drives)
    stay_on_device: bool = True


def should_skip_dir(name: str, options: Optional[ScanOptions] = None) -> bool:
    """Return True if this folder name should be ignored."""
    opts = options or ScanOptions()
    if opts.always_skip_quarantine and name == "ArchiveOrganiser_Quarantine":
        return True
    if opts.include_junk_system:
        # Still skip our quarantine; optionally still skip .git for speed
        return False
    return name in SKIP_DIR_NAMES or name.startswith(".")


def file_hash(path: Path, progress_cb: Optional[Callable[[int], None]] = None) -> str:
    """Create a SHA-256 fingerprint of a normal on-disk file (streaming)."""
    hasher = hashlib.sha256()
    done = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(done)
    return hasher.hexdigest()


def file_crc32(path: Path, progress_cb: Optional[Callable[[int], None]] = None) -> int:
    """CRC32 of a disk file (streaming — same format zip stores)."""
    crc = 0
    done = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            done += len(chunk)
            if progress_cb:
                progress_cb(done)
    return crc & 0xFFFFFFFF


def archive_member_hash(container: Path, member: str) -> str:
    """SHA-256 of one file stored inside a zip (slow — prefer zip CRC when possible)."""
    hasher = hashlib.sha256()
    with zipfile.ZipFile(container, "r") as zf:
        with zf.open(member, "r") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    return hasher.hexdigest()


def content_hash(
    info: FileInfo,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> str:
    """
    Fingerprint for exact-duplicate matching.

    Zip members: use CRC32 from the zip directory (instant — no byte read).
    Disk files: CRC32 of file bytes (one streaming read; matches zip CRC).
    Empty disk files: instant (no read).
    """
    if info.archive_container and info.archive_member:
        if info.zip_crc is not None:
            return f"crc32:{info.size}:{info.zip_crc:08x}"
        # Fallback: look up CRC from the zip without extracting full SHA
        try:
            with zipfile.ZipFile(info.archive_container, "r") as zf:
                crc = zf.getinfo(info.archive_member).CRC & 0xFFFFFFFF
            return f"crc32:{info.size}:{crc:08x}"
        except (OSError, KeyError, zipfile.BadZipFile):
            return f"sha256:{archive_member_hash(info.archive_container, info.archive_member)}"
    if info.size == 0:
        return "crc32:0:00000000"
    crc = file_crc32(info.path, progress_cb=progress_cb)
    return f"crc32:{info.size}:{crc:08x}"


def _list_zip_members(
    zip_path: Path,
    root_path: Path,
    result: ScanResult,
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Add one FileInfo per file stored inside a .zip (folders are skipped)."""
    try:
        if not zipfile.is_zipfile(zip_path):
            return
        with zipfile.ZipFile(zip_path, "r") as zf:
            members_here = 0
            for info in zf.infolist():
                if should_cancel and should_cancel():
                    return
                if info.is_dir():
                    continue
                member = info.filename
                try:
                    date_tuple = info.date_time
                    try:
                        mtime = time.mktime(date_tuple + (0, 0, -1))
                    except (OverflowError, ValueError):
                        mtime = zip_path.stat().st_mtime
                    member_path = Path(f"{zip_path}|{member}")
                    entry = FileInfo(
                        path=member_path,
                        size=info.file_size,
                        modified=mtime,
                        category=category_for_name(member),
                        source_root=str(root_path),
                        archive_container=zip_path,
                        archive_member=member,
                        zip_crc=info.CRC & 0xFFFFFFFF,
                    )
                    result.files.append(entry)
                    result.total_bytes += info.file_size
                    result.archive_members += 1
                    members_here += 1
                    if status_cb and members_here % 400 == 0:
                        status_cb(
                            f"Listing zip {zip_path.name}: "
                            f"{members_here} entries "
                            f"({result.archive_members} zip files total)"
                        )
                except Exception as exc:  # noqa: BLE001 — keep scanning other members
                    result.errors.append(f"Zip member {zip_path} → {member}: {exc}")
                    result.skipped += 1
        if status_cb and members_here:
            status_cb(f"Listed {members_here} files in {zip_path.name}")
    except (OSError, zipfile.BadZipFile) as exc:
        result.errors.append(f"Could not open zip {zip_path}: {exc}")
        result.skipped += 1


def scan_paths(
    roots: list[str],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    options: Optional[ScanOptions] = None,
) -> ScanResult:
    """
    Walk every chosen folder/drive and collect file info.
    Does not move or delete anything.

    options.include_junk_system — also enter Trash, System Volume Information, dot-folders, etc.
    options.scan_zip_contents — also list files stored inside .zip archives.
    options.stay_on_device — do not follow folders on another mount (default True).
    """
    opts = options or ScanOptions()
    result = ScanResult()
    timer = ElapsedTimer()
    last_status_at = 0.0

    def maybe_status(msg: str, force: bool = False) -> None:
        nonlocal last_status_at
        if not status_cb:
            return
        now = time.monotonic()
        if force or (now - last_status_at) >= 1.0:
            status_cb(timer.stamp(msg))
            last_status_at = now

    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            result.errors.append(f"Path not found: {root}")
            continue
        if not root_path.is_dir():
            result.errors.append(f"Not a folder: {root}")
            continue

        try:
            root_dev = root_path.stat().st_dev
        except OSError as exc:
            result.errors.append(f"Could not read {root_path}: {exc}")
            continue

        maybe_status(f"Scanning: {root_path}", force=True)

        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            if should_cancel and should_cancel():
                result.errors.append("Scan cancelled by user.")
                result.duration_seconds = timer.seconds()
                return result

            current = Path(dirpath)
            try:
                if opts.stay_on_device and current.stat().st_dev != root_dev:
                    result.cross_device_skipped += 1
                    dirnames[:] = []
                    continue
            except OSError:
                dirnames[:] = []
                result.skipped += 1
                continue

            junk_here = any(
                part in SKIP_DIR_NAMES or part.startswith(".")
                for part in current.relative_to(root_path).parts
            ) if current != root_path else False

            # Prune skipped names, then drop other-device children before descending
            kept_dirs: list[str] = []
            for d in dirnames:
                if should_skip_dir(d, opts):
                    continue
                child = current / d
                if opts.stay_on_device:
                    try:
                        if child.stat().st_dev != root_dev:
                            result.cross_device_skipped += 1
                            continue
                    except OSError:
                        result.skipped += 1
                        continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            for filename in filenames:
                if should_cancel and should_cancel():
                    result.errors.append("Scan cancelled by user.")
                    result.duration_seconds = timer.seconds()
                    return result

                full = Path(dirpath) / filename
                try:
                    if full.is_symlink() or not full.is_file():
                        result.skipped += 1
                        continue
                    stat = full.stat()
                    if opts.stay_on_device and stat.st_dev != root_dev:
                        result.cross_device_skipped += 1
                        continue
                    info = FileInfo(
                        path=full,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        category=category_for(full),
                        source_root=str(root_path),
                        is_junk_location=junk_here or (
                            opts.include_junk_system and filename.startswith(".")
                        ),
                    )
                    result.files.append(info)
                    result.total_bytes += info.size

                    if (
                        opts.scan_zip_contents
                        and full.suffix.lower() in SCANNABLE_ARCHIVE_EXTS
                    ):
                        _list_zip_members(
                            full,
                            root_path,
                            result,
                            status_cb=maybe_status,
                            should_cancel=should_cancel,
                        )
                        maybe_status(
                            f"Scanning… {len(result.files)} files · "
                            f"{format_bytes(result.total_bytes)} "
                            f"({result.archive_members} in zips)"
                        )
                    elif (
                        opts.scan_zip_contents
                        and full.suffix.lower() in ARCHIVE_EXTS
                        and full.suffix.lower() not in SCANNABLE_ARCHIVE_EXTS
                    ):
                        pass

                    if len(result.files) % 250 == 0:
                        maybe_status(
                            f"Scanning… {len(result.files)} files · "
                            f"{format_bytes(result.total_bytes)}"
                            + (
                                f" ({result.archive_members} in zips)"
                                if result.archive_members
                                else ""
                            )
                        )
                except OSError as exc:
                    result.errors.append(f"Could not read {full}: {exc}")
                    result.skipped += 1

        maybe_status(
            f"Finished folder: {root_path} — {len(result.files)} files · "
            f"{format_bytes(result.total_bytes)} so far",
            force=True,
        )

    result.duration_seconds = timer.seconds()
    if status_cb:
        extra = f" (including {result.archive_members} inside zips)" if result.archive_members else ""
        cross = (
            f", skipped {result.cross_device_skipped} other-mount folder(s)"
            if result.cross_device_skipped
            else ""
        )
        status_cb(
            timer.stamp(
                f"Scan complete: {len(result.files)} files · "
                f"{format_bytes(result.total_bytes)}{extra}{cross} "
                f"in {format_duration(result.duration_seconds)}"
            )
        )
    return result


def add_hashes(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Fill in hash_value for each file.
    Zip members use stored CRC (instant). Disk files are CRC32-read once (streamed).
    Large files report byte progress so 1TB drives don't look frozen.
    """
    # Process instant zip CRCs first so progress jumps quickly on huge zip libraries
    ordered = sorted(files, key=lambda f: (0 if f.is_inside_archive else 1, str(f.path)))
    total = len(ordered)
    step = 500 if total >= 20_000 else 200 if total >= 5000 else 50
    last_large_status = 0.0

    for index, info in enumerate(ordered, start=1):
        if should_cancel and should_cancel():
            if status_cb:
                status_cb("Hashing cancelled.")
            return
        if info.hash_value:
            continue
        try:
            if status_cb and (index == 1 or index % step == 0 or index == total):
                kind = "zip-crc" if info.is_inside_archive else "disk-crc"
                status_cb(
                    f"Fingerprinting files: {index}/{total} ({kind}) · "
                    f"{format_bytes(info.size)} · {info.name[:40]}"
                )

            def on_bytes(done: int, _info: FileInfo = info) -> None:
                nonlocal last_large_status
                if not status_cb or _info.size < LARGE_HASH_STATUS_EVERY:
                    return
                now = time.monotonic()
                if done < _info.size and (now - last_large_status) < 1.0:
                    return
                last_large_status = now
                status_cb(
                    f"Reading large file {index}/{total}: {_info.name[:36]} · "
                    f"{format_bytes(done)}/{format_bytes(_info.size)}"
                )

            info.hash_value = content_hash(info, progress_cb=on_bytes)
        except OSError as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash {info.name}: {exc}")
        except (zipfile.BadZipFile, KeyError) as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash zip member {info.name}: {exc}")
