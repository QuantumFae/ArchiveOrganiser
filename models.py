"""Shared file information used across the app."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Common file type groups for mixed personal archives
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".raw", ".cr2", ".nef"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}
DOCUMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".rtf", ".odt", ".ods", ".odp", ".csv", ".md",
}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}


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


@dataclass
class FileInfo:
    """One file found during a scan."""

    path: Path
    size: int
    modified: float
    category: str
    hash_value: Optional[str] = None
    source_root: str = ""

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ScanResult:
    """Everything found after scanning one or more folders."""

    files: list[FileInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
