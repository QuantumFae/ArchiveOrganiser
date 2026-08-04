"""Shared file information used across the app."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Common file type groups for mixed personal archives
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff",
    ".raw", ".cr2", ".nef", ".dng", ".arw", ".orf", ".rw2", ".raf",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}
DOCUMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".rtf", ".odt", ".ods", ".odp", ".csv", ".md",
}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
# Zip-compatible formats we can list/read without extra tools
SCANNABLE_ARCHIVE_EXTS = {".zip"}


def category_for_ext(ext: str) -> str:
    """Return a category from a lowercase extension including the leading dot."""
    if ext in IMAGE_EXTS:
        return "Photos"
    if ext in VIDEO_EXTS:
        return "Videos"
    if ext in AUDIO_EXTS:
        return "Audio"
    if ext in DOCUMENT_EXTS:
        return "Documents"
    if ext in ARCHIVE_EXTS:
        return "Archives"
    return "Other"


def category_for(path: Path) -> str:
    """Return a simple category name for a file."""
    return category_for_ext(path.suffix.lower())


def category_for_name(name: str) -> str:
    """Category from a filename or zip member name (no Path allocation)."""
    # Fast suffix: last '.' after the last path separator
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = base.rfind(".")
    if dot <= 0:
        return "Other"
    return category_for_ext(base[dot:].lower())


@dataclass(slots=True)
class FileInfo:
    """One file found during a scan (disk file or item inside a zip)."""

    path: Path
    size: int
    modified: float
    category: str
    hash_value: Optional[str] = None
    source_root: str = ""
    # When set, this entry lives inside a zip/archive
    archive_container: Optional[Path] = None
    archive_member: Optional[str] = None
    # CRC32 from zip central directory (free — no need to read file bytes)
    zip_crc: Optional[int] = None
    is_junk_location: bool = False
    # Similarity helpers filled during duplicate search
    visual_hash: Optional[str] = None
    content_fingerprint: Optional[str] = None
    # Row id when loaded from ScanStore (SQLite)
    store_id: Optional[int] = None

    @property
    def name(self) -> str:
        if self.archive_member:
            return Path(self.archive_member).name
        return self.path.name

    @property
    def is_inside_archive(self) -> bool:
        return self.archive_container is not None

    @property
    def display_path(self) -> str:
        if self.archive_container and self.archive_member:
            return f"{self.archive_container} → {self.archive_member}"
        return str(self.path)

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class ScanResult:
    """Everything found after scanning one or more folders.

    Large scans write into ``store`` (SQLite) and leave ``files`` empty until
    ``ensure_files_loaded()`` is called (organise / inventory). Exact duplicate
    search can query the store by size without loading every FileInfo.
    """

    files: list[FileInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    archive_members: int = 0
    duration_seconds: float = 0.0
    # Sum of on-disk file sizes (zip members counted separately via their listed size)
    total_bytes: int = 0
    # Folders skipped because they lived on another filesystem/mount
    cross_device_skipped: int = 0
    # Running count while scanning into SQLite (avoids loading all rows)
    file_count: int = 0
    # Zip members skipped due to per-zip / total caps
    zip_members_capped: int = 0
    # Zips physically extracted to sibling *_unzipped folders during scan
    zips_extracted: int = 0
    # Zips removed after a successful extract because free space was low (opt-in)
    zips_deleted_low_space: int = 0
    # Optional SQLite index (see scan_store.ScanStore)
    store: object = None

    def ensure_files_loaded(self) -> list[FileInfo]:
        """Load FileInfo list from the store if needed; return ``files``."""
        if self.files:
            return self.files
        store = self.store
        if store is not None:
            self.files = store.load_all_files()  # type: ignore[attr-defined]
            self.file_count = len(self.files)
        return self.files
