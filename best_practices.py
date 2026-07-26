"""
Best-practice helpers drawn from personal digital archiving guides:
- Westminster arrangement / naming / copy-then-verify
- Life-area folder structures
- PARA (Projects / Areas / Resources / Archives)
- Active vs Archive
- YYYY-MM-DD naming, version clarity, shallow hierarchy
- Inventory / README notes for sets of files
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from models import FileInfo

FINANCE_KEYWORDS = (
    "tax", "invoice", "bank", "insurance", "mortgage", "receipt", "salary",
    "payslip", "pension", "statement", "budget", "finance", "loan", "credit",
)

# Characters that often break cross-platform sharing / search
UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def looks_financial(name: str) -> bool:
    lower = name.lower()
    return any(word in lower for word in FINANCE_KEYWORDS)


def sanitize_filename(name: str) -> str:
    """Clean a filename for safer sharing across devices (keep extension)."""
    path = Path(name)
    stem = UNSAFE_NAME_CHARS.sub("_", path.stem).strip(" ._")
    stem = re.sub(r"_+", "_", stem)
    if not stem:
        stem = "file"
    return f"{stem}{path.suffix.lower()}"


def date_prefixed_name(name: str, modified: float) -> str:
    """Put YYYY-MM-DD at the start of the filename (common archive advice)."""
    stamp = datetime.fromtimestamp(modified).strftime("%Y-%m-%d")
    # Avoid doubling the prefix if already present
    if re.match(r"^\d{4}-\d{2}-\d{2}[_ ]", name):
        return name
    return f"{stamp}_{name}"


def apply_naming(name: str, modified: float, *, date_prefix: bool, sanitize: bool) -> str:
    result = name
    if sanitize:
        result = sanitize_filename(result)
    if date_prefix:
        result = date_prefixed_name(result, modified)
    return result


def organisation_advice(files: list[FileInfo]) -> list[str]:
    """
    Practical tips based on the scan, aligned with digital organisation guides.
    """
    tips: list[str] = []
    if not files:
        tips.append("Scan a folder first so advice can match what you actually have.")
        return tips

    total = len(files)
    tips.append(
        "Guides agree: keep folder trees shallow, use clear names, "
        "and separate Active work from long-term Archive."
    )
    tips.append(
        "Safer transfers: prefer Copy (then delete originals later) over Move "
        "when building an archive (Westminster digital archive guidance)."
    )
    tips.append(
        "Prefer YYYY-MM-DD at the start of important filenames so sorting by name "
        "matches chronology even if metadata is lost."
    )

    # Deep path warning
    deep = sum(1 for f in files if len(f.path.parts) >= 10)
    if deep:
        tips.append(
            f"{deep} file(s) sit in deep folder paths — prefer fewer levels "
            "(many guides warn against over-nesting)."
        )

    # Large library
    if total >= 100:
        tips.append(
            "Larger libraries benefit from a top-level life-area or PARA layout, "
            "plus regular archiving of older material."
        )

    # Media heavy
    media = sum(1 for f in files if f.category in {"Photos", "Videos", "Audio"})
    if media / total >= 0.5:
        tips.append(
            "Media-heavy collection: Media library or Photos/YYYY/MM structures "
            "work well; keep originals and avoid relying only on embedded metadata."
        )

    docs = sum(1 for f in files if f.category == "Documents")
    if docs:
        tips.append(
            "For documents: descriptive names + optional V001 version suffixes; "
            "consider Finance vs Personal life-area split for money-related files."
        )

    tips.append(
        "After organising: spot-check a few files opened from the new location, "
        "export an inventory, and keep a README in key folders describing the system."
    )
    tips.append(
        "Ongoing habit: process an Inbox/Downloads regularly; remove duplicates; "
        "review monthly (declutter + archive)."
    )
    return tips


def build_inventory_text(files: list[FileInfo], title: str = "File inventory") -> str:
    """Create a simple inventory list (personal digital archive practice)."""
    lines = [
        title,
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Files: {len(files)}",
        "",
        "Path | Category | Size bytes | Modified",
        "-" * 72,
    ]
    for info in sorted(files, key=lambda f: str(f.path).lower()):
        when = datetime.fromtimestamp(info.modified).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{info.path} | {info.category} | {info.size} | {when}")
    lines.append("")
    lines.append(
        "Tip: keep this inventory with your archive folder. "
        "Guides recommend documenting how sets of files are organised."
    )
    return "\n".join(lines)


def readme_for_folder(folder_name: str, layout_name: str) -> str:
    return (
        f"Archive Organiser — folder note\n"
        f"===============================\n\n"
        f"Folder: {folder_name}\n"
        f"Layout: {layout_name}\n"
        f"Created: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"This README marks how files here were arranged. "
        f"Personal digital archive guides recommend a short note with "
        f"complicated sets of files so future-you (or family) can understand the system.\n"
    )
