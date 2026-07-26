"""
Custom folder structure for organising files.

Users describe a tree and simple mapping rules, for example:

    MyArchive/
      Photos/
        Family/
        Travel/
      Documents/
        Finance/
      Other/

    Photos = MyArchive/Photos
    Documents = MyArchive/Documents
    * = MyArchive/Other

Placeholders in paths: {year} {month} {ext} {category} {name}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import FileInfo


DEFAULT_CUSTOM_TEMPLATE = """\
MyArchive/
  Photos/
  Videos/
  Documents/
  Audio/
  Archives/
  Other/

Photos = MyArchive/Photos/{year}/{month}
Videos = MyArchive/Videos/{year}/{month}
Documents = MyArchive/Documents/{ext}
Audio = MyArchive/Audio
Archives = MyArchive/Archives
* = MyArchive/Other
"""


@dataclass
class CustomStructure:
    """Parsed custom layout: folder tree lines + category → path rules."""

    tree_lines: list[str] = field(default_factory=list)
    rules: dict[str, str] = field(default_factory=dict)  # category or * → relative path template
    raw_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.rules and not self.tree_lines


def parse_custom_structure(text: str) -> CustomStructure:
    """
    Parse a user-written structure.

    Lines with '=' are mapping rules: Category = relative/path/{year}
    Other non-empty lines are tree documentation (shown in the preview).
    """
    structure = CustomStructure(raw_text=text)
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped and not stripped.startswith("-"):
            left, right = stripped.split("=", 1)
            key = left.strip()
            value = right.strip().strip("/")
            if key and value:
                structure.rules[key] = value
            continue
        structure.tree_lines.append(line)
    return structure


def _fill_template(template: str, info: FileInfo) -> str:
    dt = datetime.fromtimestamp(info.modified)
    year = f"{dt.year:04d}"
    month = f"{dt.month:02d}"
    ext = info.suffix.lstrip(".") or "unknown"
    name = info.name
    return (
        template.replace("{year}", year)
        .replace("{month}", month)
        .replace("{ext}", ext)
        .replace("{category}", info.category)
        .replace("{name}", name)
    )


def custom_destination(
    info: FileInfo,
    dest_root: Path,
    structure: CustomStructure,
    filename: str,
) -> Path:
    """
    Resolve where one file should go under a custom structure.
    Falls back to dest_root/{category}/ if no rule matches.
    """
    template = structure.rules.get(info.category) or structure.rules.get("*")
    if not template:
        return dest_root / info.category / filename
    rule = structure.rules.get(info.category) or structure.rules.get("*") or ""
    filled = _fill_template(template, info)
    if "{name}" in rule:
        return dest_root / filled
    return dest_root / filled / filename


def custom_tree_preview(structure: CustomStructure, dest_name: str = "Destination") -> str:
    """Text preview of the custom tree for the layout panel."""
    lines = [f"Custom structure → {dest_name}/", ""]
    if structure.tree_lines:
        lines.extend(structure.tree_lines)
        lines.append("")
    if structure.rules:
        lines.append("Mapping rules:")
        for key in sorted(structure.rules):
            lines.append(f"  {key} → {structure.rules[key]}")
    else:
        lines.append("(No mapping rules yet — add lines like: Photos = MyArchive/Photos)")
    return "\n".join(lines)
