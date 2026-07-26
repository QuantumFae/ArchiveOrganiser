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


def category_for(path: Path) -> str:
    """Return a simple category name for a file."""
    ext = path.suffix.lower()
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


def category_for_name(name: str) -> str:
    """Category from a filename or zip member name."""
    return category_for(Path(name))


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
    """Everything found after scanning one or more folders."""

    files: list[FileInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    archive_members: int = 0
    duration_seconds: float = 0.0
    # Sum of on-disk file sizes (zip members counted separately via their listed size)
    total_bytes: int = 0
    # Folders skipped because they lived on another filesystem/mount
    cross_device_skipped: int = 0
