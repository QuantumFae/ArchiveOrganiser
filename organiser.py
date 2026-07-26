"""Suggest and apply a tidy folder layout. Never deletes files."""

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from models import FileInfo


@dataclass
class OrganisePlanItem:
    """One planned move: from current path to a suggested path."""

    source: Path
    destination: Path
    category: str


@dataclass
class OrganisePlan:
    items: list[OrganisePlanItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _year_month(modified: float) -> tuple[str, str]:
    dt = datetime.fromtimestamp(modified)
    return f"{dt.year:04d}", f"{dt.month:02d}"


def suggest_destination(info: FileInfo, dest_root: Path) -> Path:
    """
    Build a tidy destination path, for example:
    Dest/Photos/2022/07/holiday.jpg
    Dest/Documents/pdf/report.pdf
    """
    category = info.category
    name = info.path.name

    if category in ("Photos", "Videos", "Audio"):
        year, month = _year_month(info.modified)
        return dest_root / category / year / month / name

    if category == "Documents":
        ext = info.path.suffix.lower().lstrip(".") or "unknown"
        return dest_root / "Documents" / ext / name

    if category == "Archives":
        return dest_root / "Archives" / name

    return dest_root / "Other" / name


def unique_path(path: Path) -> Path:
    """If the destination exists, add _1, _2, … before the extension."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_organise_plan(
    files: list[FileInfo],
    dest_root: str,
    only_categories: Optional[set[str]] = None,
) -> OrganisePlan:
    """
    Create a plan of moves. Does not change any files yet.
    Skips files that are already under the destination root.
    """
    plan = OrganisePlan()
    dest = Path(dest_root).expanduser().resolve()

    for info in files:
        if only_categories and info.category not in only_categories:
            continue
        try:
            current = info.path.resolve()
        except OSError:
            plan.skipped.append(f"Could not resolve: {info.path}")
            continue

        # Do not move files that are already inside the destination tree
        try:
            current.relative_to(dest)
            plan.skipped.append(f"Already in destination: {current}")
            continue
        except ValueError:
            pass

        suggested = suggest_destination(info, dest)
        plan.items.append(
            OrganisePlanItem(
                source=current,
                destination=suggested,
                category=info.category,
            )
        )
    return plan


def apply_organise_plan(
    plan: OrganisePlan,
    dry_run: bool = True,
    status_cb: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """
    Carry out the organise plan.
    dry_run=True means only describe what would happen (safe default).
    """
    log: list[str] = []
    for item in plan.items:
        final_dest = unique_path(item.destination) if not dry_run else item.destination
        message = f"{item.source}  →  {final_dest}"
        if dry_run:
            log.append(f"[preview] {message}")
            continue
        try:
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.source), str(final_dest))
            log.append(f"[moved] {message}")
            if status_cb:
                status_cb(f"Moved: {item.source.name}")
        except OSError as exc:
            log.append(f"[error] {item.source}: {exc}")
    return log
