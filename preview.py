"""Load simple local previews so you can compare duplicate files visually."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from models import IMAGE_EXTS, DOCUMENT_EXTS
from quarantine import format_bytes

# Extensions we can show as plain text in the compare panel
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm",
    ".log", ".ini", ".cfg", ".py", ".js", ".css", ".rtf",
}

PREVIEW_MAX_CHARS = 4000
THUMB_MAX_SIZE = (320, 320)


@dataclass
class FilePreview:
    """What the GUI shows for one file in a duplicate group."""

    path: Path
    kind: str  # "image", "text", or "none"
    info_text: str
    image: Optional[object] = None  # PIL Image, if kind == "image"
    text_content: str = ""
    error: str = ""


def build_info_text(path: Path, size: int, modified: float, category: str, role: str) -> str:
    """Human-readable file details for the compare card."""
    from datetime import datetime

    when = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"Role: {role}\n"
        f"Name: {path.name}\n"
        f"Path: {path}\n"
        f"Category: {category}\n"
        f"Size: {format_bytes(size)} ({size} bytes)\n"
        f"Modified: {when}\n"
        f"Type: {path.suffix.lower() or '(none)'}"
    )


def load_preview(path: Path, size: int, modified: float, category: str, role: str) -> FilePreview:
    """
    Try to load a visual or text preview of a file.
    Never uploads anything — reads only from your disk.
    """
    info = build_info_text(path, size, modified, category, role)
    if not path.exists():
        return FilePreview(path=path, kind="none", info_text=info, error="File missing on disk")

    ext = path.suffix.lower()

    if ext in IMAGE_EXTS:
        try:
            from PIL import Image

            img = Image.open(path)
            img = img.convert("RGB")
            img.thumbnail(THUMB_MAX_SIZE)
            return FilePreview(path=path, kind="image", info_text=info, image=img)
        except Exception as exc:  # noqa: BLE001 — show any preview failure to the user
            return FilePreview(
                path=path, kind="none", info_text=info, error=f"Could not open image: {exc}"
            )

    if ext in TEXT_EXTS or (ext in DOCUMENT_EXTS and ext in {".txt", ".md", ".csv"}):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            if len(raw) > PREVIEW_MAX_CHARS:
                raw = raw[:PREVIEW_MAX_CHARS] + "\n\n… (preview truncated)"
            return FilePreview(path=path, kind="text", info_text=info, text_content=raw)
        except Exception as exc:  # noqa: BLE001
            return FilePreview(
                path=path, kind="none", info_text=info, error=f"Could not read text: {exc}"
            )

    # Binary / unsupported: still show file information
    return FilePreview(
        path=path,
        kind="none",
        info_text=info,
        error="No content preview for this file type (info only).",
    )
