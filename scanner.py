"""Scan folders and drives for files. Runs only on your computer.

Tuned for large (hundreds of GB / 1TB+) drives:
- os.scandir walk with one stat per entry (fewer syscalls)
- writes metadata to SQLite in large batches (fewer commits)
- stays on one filesystem by default (won't follow other mounts)
- streams hashes in chunks (never loads a whole huge file into RAM)
- partial CRC first, full CRC only on collisions
- caps zip member expansion; reports size + file progress
- optional physical unzip beside each .zip (opt-in; keeps the original zip
  unless the low-space option deletes it after a successful extract)
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat as stat_module
import shutil
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from helpers import ElapsedTimer, format_bytes, format_duration
from models import (
    ARCHIVE_EXTS,
    SCANNABLE_ARCHIVE_EXTS,
    FileInfo,
    ScanResult,
    category_for_ext,
    category_for_name,
)
from scan_store import ScanStore

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

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB
LARGE_HASH_STATUS_EVERY = 64 * 1024 * 1024  # 64 MB
# First-pass fingerprint for exact dups (full CRC only when this collides)
PARTIAL_CRC_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_ZIP_MEMBERS_PER_ARCHIVE = 5_000
MAX_ZIP_MEMBERS_TOTAL = 100_000
# Sibling folder naming: Vacation.zip → Vacation_unzipped/
UNZIPPED_FOLDER_SUFFIX = "_unzipped"
UNZIPPED_NOTE_NAME = "UNZIPPED.txt"
MAX_UNZIPPED_NAME_TRIES = 50
# Extra free space required beyond zip uncompressed size before we treat the drive as "OK"
EXTRACT_SPACE_MARGIN_BYTES = 64 * 1024 * 1024  # 64 MiB
EXTRACT_SPACE_MARGIN_RATIO = 0.10  # or +10% of uncompressed size, whichever is larger


@dataclass
class ScanOptions:
    """User choices for how deep / wide the scan goes."""

    include_junk_system: bool = False
    scan_zip_contents: bool = False
    # Physically extract .zip beside the archive (uses disk; keeps the .zip by default)
    extract_zips: bool = False
    # After a successful extract, always delete the original .zip (then scan *_unzipped/)
    delete_zip_after_extract: bool = False
    # If extract is on and free space looks tight: after a successful extract, delete the .zip
    # (ignored when delete_zip_after_extract is on)
    delete_zip_if_low_space: bool = False
    always_skip_quarantine: bool = True
    stay_on_device: bool = True
    # Write metadata to SQLite (recommended for large drives)
    use_sqlite: bool = True
    max_zip_members_per_archive: int = MAX_ZIP_MEMBERS_PER_ARCHIVE
    max_zip_members_total: int = MAX_ZIP_MEMBERS_TOTAL


def should_skip_dir(name: str, options: Optional[ScanOptions] = None) -> bool:
    """Return True if this folder name should be ignored."""
    opts = options or ScanOptions()
    if opts.always_skip_quarantine and name == "ArchiveOrganiser_Quarantine":
        return True
    if opts.include_junk_system:
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


def file_crc32(
    path: Path,
    progress_cb: Optional[Callable[[int], None]] = None,
    byte_limit: Optional[int] = None,
) -> int:
    """CRC32 of a disk file (streaming). Optional byte_limit for partial fingerprints."""
    crc = 0
    done = 0
    with path.open("rb") as handle:
        while True:
            to_read = CHUNK_SIZE
            if byte_limit is not None:
                remaining = byte_limit - done
                if remaining <= 0:
                    break
                to_read = min(CHUNK_SIZE, remaining)
            chunk = handle.read(to_read)
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
    partial: bool = False,
) -> str:
    """
    Fingerprint for exact-duplicate matching.

    Zip members: CRC32 from the zip directory (instant).
    Disk: CRC32 streaming; empty files instant.
    partial=True uses only the first PARTIAL_CRC_BYTES (then full pass on collisions).
    """
    if info.archive_container and info.archive_member:
        if info.zip_crc is not None:
            return f"crc32:{info.size}:{info.zip_crc:08x}"
        try:
            with zipfile.ZipFile(info.archive_container, "r") as zf:
                crc = zf.getinfo(info.archive_member).CRC & 0xFFFFFFFF
            return f"crc32:{info.size}:{crc:08x}"
        except (OSError, KeyError, zipfile.BadZipFile):
            return f"sha256:{archive_member_hash(info.archive_container, info.archive_member)}"
    if info.size == 0:
        return "crc32:0:00000000"
    if partial and info.size > PARTIAL_CRC_BYTES:
        crc = file_crc32(info.path, progress_cb=progress_cb, byte_limit=PARTIAL_CRC_BYTES)
        return f"pcrc32:{info.size}:{crc:08x}"
    crc = file_crc32(info.path, progress_cb=progress_cb)
    return f"crc32:{info.size}:{crc:08x}"


def _add_file(
    result: ScanResult,
    info: FileInfo,
    store: Optional[ScanStore],
) -> None:
    if store is not None:
        store.insert_file(info)
    else:
        result.files.append(info)
    result.file_count += 1
    result.total_bytes += info.size


def _remove_file_from_result(
    result: ScanResult,
    path: Path,
    store: Optional[ScanStore],
    size: int = 0,
) -> None:
    """Drop a path from the in-progress scan (e.g. .zip deleted after extract)."""
    path_str = str(path)
    if store is not None:
        try:
            store.flush()
            store.remove_paths([path_str])
        except Exception:
            pass
    if result.files:
        result.files = [f for f in result.files if str(f.path) != path_str]
    result.file_count = max(0, result.file_count - 1)
    if size > 0:
        result.total_bytes = max(0, result.total_bytes - size)


def _list_zip_members(
    zip_path: Path,
    root_path: Path,
    result: ScanResult,
    opts: ScanOptions,
    store: Optional[ScanStore],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    zip_mtime: Optional[float] = None,
) -> None:
    """Add FileInfo rows for files inside a .zip, respecting caps."""
    if result.archive_members >= opts.max_zip_members_total:
        result.zip_members_capped += 1
        if status_cb:
            status_cb(
                f"Zip member cap reached ({opts.max_zip_members_total:,}); "
                f"skipping further zip contents"
            )
        return
    try:
        # Open directly — BadZipFile covers non-zips (avoids double disk read)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members_here = 0
            for info in zf.infolist():
                if should_cancel and should_cancel():
                    return
                if info.is_dir():
                    continue
                if members_here >= opts.max_zip_members_per_archive:
                    result.zip_members_capped += 1
                    if status_cb:
                        status_cb(
                            f"Capped {zip_path.name} at "
                            f"{opts.max_zip_members_per_archive:,} members"
                        )
                    break
                if result.archive_members >= opts.max_zip_members_total:
                    result.zip_members_capped += 1
                    break
                member = info.filename
                try:
                    date_tuple = info.date_time
                    try:
                        mtime = time.mktime(date_tuple + (0, 0, -1))
                    except (OverflowError, ValueError):
                        mtime = zip_mtime if zip_mtime is not None else zip_path.stat().st_mtime
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
                    _add_file(result, entry, store)
                    result.archive_members += 1
                    members_here += 1
                    if status_cb and members_here % 400 == 0:
                        status_cb(
                            f"Listing zip {zip_path.name}: "
                            f"{members_here} entries "
                            f"({result.archive_members} zip files total)"
                        )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"Zip member {zip_path} → {member}: {exc}")
                    result.skipped += 1
        if status_cb and members_here:
            status_cb(f"Listed {members_here} files in {zip_path.name}")
    except (OSError, zipfile.BadZipFile) as exc:
        if isinstance(exc, zipfile.BadZipFile):
            result.skipped += 1
            return
        result.errors.append(f"Could not open zip {zip_path}: {exc}")
        result.skipped += 1


def unzipped_folder_basename(zip_path: Path) -> str:
    """Readable folder name: Vacation.zip → Vacation_unzipped."""
    # Path('.zip') has empty suffix and stem '.zip' — treat that as no name.
    stem = zip_path.stem.strip()
    if not stem or stem.startswith(".") and zip_path.suffix == "":
        stem = "archive"
    if stem.startswith("."):
        stem = stem.lstrip(".") or "archive"
    return f"{stem}{UNZIPPED_FOLDER_SUFFIX}"


def _note_matches_zip(folder: Path, zip_path: Path) -> bool:
    """True if UNZIPPED.txt says this folder came from zip_path."""
    note = folder / UNZIPPED_NOTE_NAME
    if not note.is_file():
        return False
    try:
        text = note.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    resolved = str(zip_path.resolve())
    return resolved in text or str(zip_path) in text


def find_existing_unzipped_folder(zip_path: Path) -> Optional[Path]:
    """Find a sibling *_unzipped folder already created from this zip."""
    parent = zip_path.parent
    base = unzipped_folder_basename(zip_path)
    names = [base] + [f"{base}_{n}" for n in range(2, MAX_UNZIPPED_NAME_TRIES + 1)]
    for name in names:
        candidate = parent / name
        if candidate.is_dir() and _note_matches_zip(candidate, zip_path):
            return candidate
    return None


def _choose_extract_folder(zip_path: Path) -> Optional[Path]:
    """
    Pick a new sibling folder for extraction.

    - Prefer Vacation_unzipped
    - Reuse if the folder exists but is empty
    - If a non-empty folder already exists (user data), use Vacation_unzipped_2, …
    - Never overwrite / wipe an existing non-empty folder
    """
    parent = zip_path.parent
    base = unzipped_folder_basename(zip_path)
    names = [base] + [f"{base}_{n}" for n in range(2, MAX_UNZIPPED_NAME_TRIES + 1)]
    for name in names:
        candidate = parent / name
        if not candidate.exists():
            return candidate
        if candidate.is_dir():
            try:
                if not any(candidate.iterdir()):
                    return candidate
            except OSError:
                continue
        # Non-empty dir or a file with that name — try next suffix
    return None


def _safe_member_dest(dest_root: Path, member_name: str) -> Optional[Path]:
    """
    Resolve where a zip entry should land. Reject path traversal (zip-slip).
    Returns None for directories or unsafe names.
    """
    cleaned = member_name.replace("\\", "/").strip()
    if not cleaned or cleaned.endswith("/"):
        return None
    # Drop absolute / drive-style prefixes
    while cleaned.startswith("/"):
        cleaned = cleaned[1:]
    if not cleaned or ":" in Path(cleaned).parts[0]:
        return None

    dest_root_resolved = dest_root.resolve()
    target = (dest_root / cleaned).resolve()
    try:
        target.relative_to(dest_root_resolved)
    except ValueError:
        return None
    # Also reject any ".." that somehow survived
    if ".." in Path(cleaned).parts:
        return None
    return target


def _write_unzipped_note(
    dest_folder: Path,
    zip_path: Path,
    *,
    zip_deleted: bool = False,
    zip_path_display: Optional[str] = None,
    deleted_for_space: bool = False,
) -> None:
    """Leave a short note so the user knows why this folder appeared."""
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if zip_deleted:
        if deleted_for_space:
            zip_line = (
                "The original .zip file was deleted after a successful extract "
                "to free disk space (you turned on that option).\n"
            )
        else:
            zip_line = (
                "The original .zip file was deleted after a successful extract "
                "(you turned on “delete .zip after unzip”).\n"
            )
    else:
        zip_line = "The original .zip file was kept (not deleted).\n"
    source_line = zip_path_display
    if not source_line:
        try:
            source_line = str(zip_path.resolve())
        except OSError:
            source_line = str(zip_path)
    text = (
        "This folder was created by Archive Organiser during a scan.\n"
        "\n"
        f"Source zip: {source_line}\n"
        f"Extracted at: {when}\n"
        "\n"
        f"{zip_line}"
        "Folder naming: <zip name>_unzipped "
        "(e.g. Vacation.zip → Vacation_unzipped/).\n"
    )
    (dest_folder / UNZIPPED_NOTE_NAME).write_text(text, encoding="utf-8")


def estimated_zip_uncompressed_bytes(zip_path: Path) -> Optional[int]:
    """Sum of uncompressed member sizes from the zip directory, or None if unreadable."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return sum(info.file_size for info in zf.infolist() if not info.is_dir())
    except (OSError, zipfile.BadZipFile):
        return None


def needed_extract_bytes(uncompressed_bytes: int) -> int:
    """Uncompressed size plus a safety margin (10% or 64 MiB, whichever is larger)."""
    margin = max(EXTRACT_SPACE_MARGIN_BYTES, int(uncompressed_bytes * EXTRACT_SPACE_MARGIN_RATIO))
    return uncompressed_bytes + margin


def free_bytes_on_volume(path: Path) -> Optional[int]:
    """Free bytes on the volume that holds path, or None if we cannot check."""
    try:
        return int(shutil.disk_usage(os.fspath(path)).free)
    except OSError:
        return None


def disk_space_is_low_for_extract(path: Path, uncompressed_bytes: int) -> bool:
    """True when free space on path's volume is below estimate + margin."""
    free = free_bytes_on_volume(path)
    if free is None:
        return False
    return free < needed_extract_bytes(uncompressed_bytes)


def _is_no_space_error(exc: BaseException) -> bool:
    """True for 'no space left on device' style errors."""
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC,):
        return True
    text = str(exc).lower()
    return "no space" in text or "not enough space" in text


def extract_zip_beside(
    zip_path: Path,
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    delete_zip_if_low_space: bool = False,
    delete_zip_after_extract: bool = False,
) -> tuple[Optional[Path], str]:
    """
    Extract a .zip into a new sibling folder named like Vacation_unzipped/.

    Returns (folder_path, message). folder_path is None on skip/failure.
    Never wipes an existing non-empty folder.
    Never deletes the original zip unless extract fully succeeded and either
    delete_zip_after_extract is on, or delete_zip_if_low_space is on and
    free space looked tight before extract.
    """
    existing = find_existing_unzipped_folder(zip_path)
    if existing is not None:
        return existing, f"Already extracted: {existing.name}"

    if not zipfile.is_zipfile(zip_path):
        return None, f"Not a readable zip: {zip_path.name}"

    dest = _choose_extract_folder(zip_path)
    if dest is None:
        return None, (
            f"Skipped extract for {zip_path.name}: "
            f"no free {unzipped_folder_basename(zip_path)} name "
            "(existing folders left untouched)"
        )

    # Estimate space before we start writing (used for optional low-space delete)
    space_was_low = False
    uncompressed = estimated_zip_uncompressed_bytes(zip_path)
    if uncompressed is not None:
        space_was_low = disk_space_is_low_for_extract(zip_path.parent, uncompressed)
        if space_was_low and status_cb:
            will_delete = delete_zip_after_extract or delete_zip_if_low_space
            status_cb(
                f"Low free space for {zip_path.name} "
                f"(need about {format_bytes(needed_extract_bytes(uncompressed))}); "
                "will extract first"
                + (", then delete the zip if extract succeeds" if will_delete else "")
            )

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"Could not create {dest.name}: {exc}"

    written = 0
    skipped_unsafe = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [info for info in zf.infolist() if not info.is_dir()]
            total = len(members)
            for index, info in enumerate(members, start=1):
                if should_cancel and should_cancel():
                    return dest, f"Extract cancelled at {zip_path.name}"

                target = _safe_member_dest(dest, info.filename)
                if target is None:
                    skipped_unsafe += 1
                    continue

                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, target.open("wb") as out:
                        while True:
                            if should_cancel and should_cancel():
                                return dest, f"Extract cancelled at {zip_path.name}"
                            chunk = src.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            out.write(chunk)
                    written += 1
                except OSError as exc:
                    extra = " (disk full?)" if _is_no_space_error(exc) else ""
                    return None, (
                        f"Extract failed for {zip_path.name} → {info.filename}: {exc}{extra}"
                    )

                if status_cb and (index == 1 or index % 50 == 0 or index == total):
                    status_cb(
                        f"Extracting {zip_path.name}: {index}/{total} files → {dest.name}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        extra = " (disk full?)" if _is_no_space_error(exc) else ""
        return None, f"Could not extract {zip_path.name}: {exc}{extra}"

    # Success path only — never delete on cancel/failure above
    zip_deleted = False
    delete_note = ""
    # Resolve while the zip still exists (note text needs the original path)
    try:
        zip_path_display = str(zip_path.resolve())
    except OSError:
        zip_path_display = str(zip_path)

    should_delete = delete_zip_after_extract or (
        delete_zip_if_low_space and space_was_low
    )
    if should_delete:
        try:
            zip_path.unlink()
            zip_deleted = True
            if delete_zip_after_extract:
                delete_note = "; deleted zip after successful unzip"
            else:
                delete_note = "; deleted zip to free disk space"
        except OSError as exc:
            delete_note = f"; wanted to delete zip but failed: {exc}"

    try:
        _write_unzipped_note(
            dest,
            zip_path,
            zip_deleted=zip_deleted,
            zip_path_display=zip_path_display,
            deleted_for_space=bool(zip_deleted and not delete_zip_after_extract),
        )
    except OSError as exc:
        return dest, (
            f"Extracted {written} files but could not write note: {exc}{delete_note}"
        )

    extra = f", skipped {skipped_unsafe} unsafe path(s)" if skipped_unsafe else ""
    return (
        dest,
        f"Extracted {written} files from {zip_path.name} → {dest.name}{extra}{delete_note}",
    )



def _suffix_lower(name: str) -> str:
    """File extension including dot, lowercase (no Path object)."""
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot:].lower()


def scan_paths(
    roots: list[str],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    options: Optional[ScanOptions] = None,
    store_path: Optional[Path] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ScanResult:
    """
    Walk every chosen folder/drive and collect file info.
    Does not move or delete anything.

    Uses os.scandir + one stat per entry (faster than Path.is_file/is_symlink/stat).
    If store_path is set, the SQLite index is kept on disk for reload later.
    progress_cb(current_root_index, total_roots) reports coarse scan progress.
    """
    opts = options or ScanOptions()
    result = ScanResult()
    store: Optional[ScanStore] = None
    if opts.use_sqlite:
        store = ScanStore(store_path)
        if store_path is not None:
            store.clear_files()
        result.store = store
    timer = ElapsedTimer()
    last_status_at = 0.0
    total_roots = max(1, len(roots))

    def maybe_status(msg: str, force: bool = False) -> None:
        nonlocal last_status_at
        if not status_cb:
            return
        now = time.monotonic()
        if force or (now - last_status_at) >= 1.25:
            status_cb(timer.stamp(msg))
            last_status_at = now

    def finish_cancelled() -> ScanResult:
        result.errors.append("Scan cancelled by user.")
        if store:
            store.flush(commit=True)
        result.duration_seconds = timer.seconds()
        return result

    for root_index, root in enumerate(roots, start=1):
        if progress_cb:
            progress_cb(root_index - 1, total_roots)
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

        root_str = str(root_path)
        maybe_status(
            f"Scanning source {root_index}/{total_roots}: {root_path}",
            force=True,
        )

        # Stack of (dir_path_str, junk_here). Depth-first scandir walk.
        stack: list[tuple[str, bool]] = [(root_str, False)]
        while stack:
            if should_cancel and should_cancel():
                return finish_cancelled()

            dirpath, junk_here = stack.pop()
            try:
                with os.scandir(dirpath) as entries:
                    subdirs: list[tuple[str, bool]] = []
                    for entry in entries:
                        if should_cancel and should_cancel():
                            return finish_cancelled()
                        name = entry.name
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            result.skipped += 1
                            continue

                        mode = st.st_mode
                        if stat_module.S_ISLNK(mode):
                            result.skipped += 1
                            continue

                        if stat_module.S_ISDIR(mode):
                            if should_skip_dir(name, opts):
                                continue
                            if opts.stay_on_device and st.st_dev != root_dev:
                                result.cross_device_skipped += 1
                                continue
                            child_junk = junk_here or (
                                name in SKIP_DIR_NAMES or name.startswith(".")
                            )
                            subdirs.append((entry.path, child_junk))
                            continue

                        if not stat_module.S_ISREG(mode):
                            result.skipped += 1
                            continue
                        if opts.stay_on_device and st.st_dev != root_dev:
                            result.cross_device_skipped += 1
                            continue

                        suffix = _suffix_lower(name)
                        full = Path(entry.path)
                        info = FileInfo(
                            path=full,
                            size=st.st_size,
                            modified=st.st_mtime,
                            category=category_for_ext(suffix),
                            source_root=root_str,
                            is_junk_location=junk_here
                            or (opts.include_junk_system and name.startswith(".")),
                        )
                        _add_file(result, info, store)

                        used_on_disk_extract = False
                        if opts.extract_zips and suffix in SCANNABLE_ARCHIVE_EXTS:
                            dest_folder, extract_msg = extract_zip_beside(
                                full,
                                status_cb=maybe_status,
                                should_cancel=should_cancel,
                                delete_zip_if_low_space=opts.delete_zip_if_low_space,
                                delete_zip_after_extract=opts.delete_zip_after_extract,
                            )
                            maybe_status(extract_msg, force=True)
                            if dest_folder is not None:
                                used_on_disk_extract = True
                                if extract_msg.startswith("Extracted "):
                                    result.zips_extracted += 1
                                    if "deleted zip" in extract_msg:
                                        result.zips_deleted_low_space += 1
                                        # Zip is gone — drop it from the scan index
                                        _remove_file_from_result(
                                            result, full, store, size=info.size
                                        )
                                # Visit extracted sibling during this scan
                                if dest_folder.is_dir() and not should_skip_dir(
                                    dest_folder.name, opts
                                ):
                                    subdirs.append((str(dest_folder), junk_here))
                            elif extract_msg:
                                result.errors.append(extract_msg)

                        if should_cancel and should_cancel():
                            return finish_cancelled()

                        # Prefer on-disk extracted files over virtual zip listing
                        if (
                            opts.scan_zip_contents
                            and suffix in SCANNABLE_ARCHIVE_EXTS
                            and not used_on_disk_extract
                        ):
                            _list_zip_members(
                                full,
                                root_path,
                                result,
                                opts,
                                store,
                                status_cb=maybe_status,
                                should_cancel=should_cancel,
                                zip_mtime=st.st_mtime,
                            )
                            maybe_status(
                                f"Scanning… {result.file_count} files · "
                                f"{format_bytes(result.total_bytes)} "
                                f"({result.archive_members} in zips)"
                            )
                        elif opts.scan_zip_contents and suffix in ARCHIVE_EXTS:
                            pass

                        if result.file_count % 500 == 0:
                            maybe_status(
                                f"Scanning… {result.file_count} files · "
                                f"{format_bytes(result.total_bytes)}"
                                + (
                                    f" ({result.archive_members} in zips)"
                                    if result.archive_members
                                    else ""
                                )
                            )
            except OSError as exc:
                result.errors.append(f"Could not read folder {dirpath}: {exc}")
                result.skipped += 1
                continue

            for item in reversed(subdirs):
                stack.append(item)

        maybe_status(
            f"Finished folder: {root_path} — {result.file_count} files · "
            f"{format_bytes(result.total_bytes)} so far",
            force=True,
        )
        if progress_cb:
            progress_cb(root_index, total_roots)

    if store:
        store.flush(commit=True)
        result.file_count = store.count()
        result.total_bytes = store.total_bytes()

    result.duration_seconds = timer.seconds()
    if progress_cb:
        progress_cb(total_roots, total_roots)
    if status_cb:
        extra = f" (including {result.archive_members} inside zips)" if result.archive_members else ""
        cross = (
            f", skipped {result.cross_device_skipped} other-mount folder(s)"
            if result.cross_device_skipped
            else ""
        )
        capped = (
            f", zip caps hit {result.zip_members_capped} time(s)"
            if result.zip_members_capped
            else ""
        )
        unzipped = (
            f", unzipped {result.zips_extracted} archive(s)"
            if result.zips_extracted
            else ""
        )
        deleted = (
            f", deleted {result.zips_deleted_low_space} zip(s) after unzip"
            if result.zips_deleted_low_space
            else ""
        )
        status_cb(
            timer.stamp(
                f"Scan complete: {result.file_count} files · "
                f"{format_bytes(result.total_bytes)}{extra}{cross}{capped}"
                f"{unzipped}{deleted} "
                f"in {format_duration(result.duration_seconds)}"
            )
        )
    return result


def add_hashes(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    store: Optional[ScanStore] = None,
    use_partial: bool = True,
) -> None:
    """
    Fill hash_value for each file.

    When use_partial is True (default for disk files larger than PARTIAL_CRC_BYTES):
    1) compute partial CRC for all candidates
    2) only run full CRC on groups that still collide on (size + partial)
    Zip members always use the stored full CRC (instant).
    """
    ordered = sorted(files, key=lambda f: (0 if f.is_inside_archive else 1, str(f.path)))
    total = len(ordered)
    step = 500 if total >= 20_000 else 200 if total >= 5000 else 50
    last_large_status = 0.0

    def on_bytes_factory(index: int, info: FileInfo) -> Callable[[int], None]:
        def on_bytes(done: int) -> None:
            nonlocal last_large_status
            if not status_cb or info.size < LARGE_HASH_STATUS_EVERY:
                return
            now = time.monotonic()
            if done < info.size and (now - last_large_status) < 1.0:
                return
            last_large_status = now
            status_cb(
                f"Reading large file {index}/{total}: {info.name[:36]} · "
                f"{format_bytes(done)}/{format_bytes(info.size)}"
            )

        return on_bytes

    def apply_hash(info: FileInfo, value: str) -> None:
        info.hash_value = value
        sid = info.store_id
        if store is not None and sid is not None:
            store.update_hash(sid, value)
            if not info.is_inside_archive:
                store.store_hash_cache(str(info.path), info.size, info.modified, value)
            if sid % 500 == 0:
                store.flush_hash_updates(commit=True)

    # --- Pass 1: cache / zip / partial ---
    partial_map: dict[str, list[FileInfo]] = {}
    for index, info in enumerate(ordered, start=1):
        if should_cancel and should_cancel():
            if status_cb:
                status_cb("Hashing cancelled.")
            if store:
                store.commit()
            return
        if info.hash_value:
            continue

        # Hash cache (disk files only)
        if store is not None and not info.is_inside_archive:
            cached = store.cached_hash(str(info.path), info.size, info.modified)
            if cached:
                apply_hash(info, cached)
                continue

        try:
            if status_cb and (index == 1 or index % step == 0 or index == total):
                kind = "zip-crc" if info.is_inside_archive else "partial-crc" if use_partial else "disk-crc"
                status_cb(
                    f"Fingerprinting files: {index}/{total} ({kind}) · "
                    f"{format_bytes(info.size)} · {info.name[:40]}"
                )

            need_partial = (
                use_partial
                and not info.is_inside_archive
                and info.size > PARTIAL_CRC_BYTES
            )
            if need_partial:
                ph = content_hash(info, progress_cb=on_bytes_factory(index, info), partial=True)
                partial_map.setdefault(ph, []).append(info)
            else:
                apply_hash(
                    info,
                    content_hash(info, progress_cb=on_bytes_factory(index, info), partial=False),
                )
        except OSError as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash {info.name}: {exc}")
        except (zipfile.BadZipFile, KeyError) as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash zip member {info.name}: {exc}")

    # --- Pass 2: full CRC only where partial fingerprints collided ---
    to_full: list[FileInfo] = []
    for partial_key, group in partial_map.items():
        if len(group) >= 2:
            to_full.extend(group)
        elif len(group) == 1:
            # Unique among same-size candidates — no full read needed
            apply_hash(group[0], partial_key)

    full_total = len(to_full)
    for index, info in enumerate(to_full, start=1):
        if should_cancel and should_cancel():
            if status_cb:
                status_cb("Hashing cancelled.")
            if store:
                store.commit()
            return
        try:
            if status_cb and (index == 1 or index % max(1, step // 2) == 0 or index == full_total):
                status_cb(
                    f"Full fingerprint (collision check): {index}/{full_total} · "
                    f"{info.name[:40]}"
                )
            apply_hash(
                info,
                content_hash(info, progress_cb=on_bytes_factory(index, info), partial=False),
            )
        except OSError as exc:
            info.hash_value = None
            if status_cb:
                status_cb(f"Could not hash {info.name}: {exc}")

    if store:
        store.commit()
