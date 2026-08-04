"""Suggest and apply a tidy folder layout.

Additive / non-destructive to the destination:
- Creates missing folders only; never deletes or clears destination content.
- Never overwrites an existing file at the target path (uses unique_path).
- Copy mode leaves sources in place; Move removes from the source only.
"""

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Set

from models import FileInfo
from quarantine import DEFAULT_QUARANTINE_NAME
from best_practices import apply_naming, looks_financial, readme_for_folder
from custom_structure import (
    CustomStructure,
    custom_destination,
    custom_tree_preview,
    parse_custom_structure,
)


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
    layout_id: str = "type_date"
    layout_name: str = ""
    layout_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutPreset:
    """One pre-defined folder layout the user can choose."""

    id: str
    name: str
    description: str
    example: str
    # Categories that make this layout especially useful (empty = always useful)
    useful_for: tuple[str, ...] = ()


LAYOUT_PRESETS: list[LayoutPreset] = [
    LayoutPreset(
        id="type_date",
        name="Type + date",
        description="Photos/Videos/Audio by year/month; documents by file type.",
        example="Photos/2022/07/ · Documents/pdf/ · Archives/",
        useful_for=("Photos", "Videos", "Audio", "Documents", "Other"),
    ),
    LayoutPreset(
        id="life_areas",
        name="Life areas (Personal / Media / Finance / Archive)",
        description="Top-level life areas used in personal folder guides — clear homes for everyday files.",
        example="Personal/ · Media/Photos/ · Finance/ · Archive/",
        useful_for=("Photos", "Videos", "Documents", "Other"),
    ),
    LayoutPreset(
        id="para",
        name="PARA (Areas / Resources / Archives)",
        description="PARA-inspired: Areas for ongoing life categories, Resources for reference, Archives for finished/old.",
        example="Areas/Media/ · Areas/Documents/ · Resources/ · Archives/",
        useful_for=("Photos", "Documents", "Other"),
    ),
    LayoutPreset(
        id="active_archive",
        name="Active vs Archive",
        description="Keep recent files under Active/; older files under Archive/ (age threshold configurable).",
        example="Active/Photos/ · Archive/Documents/",
        useful_for=("Photos", "Videos", "Documents", "Other"),
    ),
    LayoutPreset(
        id="by_type",
        name="By type only",
        description="Simple folders by category — no year/month subfolders.",
        example="Photos/ · Videos/ · Documents/ · Audio/ · Other/",
        useful_for=("Photos", "Videos", "Documents", "Audio", "Archives", "Other"),
    ),
    LayoutPreset(
        id="by_year_month",
        name="By year / month",
        description="Everything sorted by date first, then category inside each month.",
        example="2022/07/Photos/ · 2022/07/Documents/",
        useful_for=("Photos", "Videos", "Audio"),
    ),
    LayoutPreset(
        id="by_extension",
        name="By file extension",
        description="One folder per extension (jpg, pdf, mp4, dng, …).",
        example="jpg/ · pdf/ · mp4/ · dng/",
        useful_for=("Documents", "Other", "Photos"),
    ),
    LayoutPreset(
        id="media_library",
        name="Media library",
        description="Media by date; documents and other files kept separate.",
        example="Photos/YYYY/MM/ · Videos/YYYY/MM/ · Documents/ · Other/",
        useful_for=("Photos", "Videos", "Audio"),
    ),
    LayoutPreset(
        id="documents_first",
        name="Documents first",
        description="Documents sorted by type; everything else under Media or Other.",
        example="Documents/pdf/ · Media/Photos/ · Other/",
        useful_for=("Documents",),
    ),
    LayoutPreset(
        id="by_source_root",
        name="Keep source folders",
        description="Preserve each file’s path under its source root name (DriveName/…/file).",
        example="USB_Stick/Vacation/img.jpg · HDD/Docs/report.pdf",
        useful_for=("Photos", "Videos", "Documents", "Other"),
    ),
    LayoutPreset(
        id="shallow_by_type",
        name="Shallow by type",
        description="Category folders only — no year/month or extension nesting.",
        example="Photos/ · Videos/ · Documents/ · Other/",
        useful_for=("Photos", "Videos", "Documents", "Audio", "Archives", "Other"),
    ),
    LayoutPreset(
        id="custom",
        name="Custom structure (you define folders)",
        description="Type your own folder tree and mapping rules (category → path).",
        example="Photos = MyArchive/Photos/{year}/{month}",
        useful_for=(),
    ),
]


ALL_CATEGORIES = ("Photos", "Videos", "Audio", "Documents", "Archives", "Other")

# Per-category nesting modes (advanced Organise options)
CATEGORY_SUBFOLDER_MODES = (
    "layout_default",
    "flat",
    "year",
    "year_month",
    "extension",
)

MEDIA_DATE_DEPTHS = ("none", "year", "year_month")

CATEGORY_MODE_LABELS = {
    "layout_default": "Follow layout",
    "flat": "Flat",
    "year": "By year",
    "year_month": "By year + month",
    "extension": "By extension",
}

MEDIA_DATE_DEPTH_LABELS = {
    "none": "None (flat)",
    "year": "Year only",
    "year_month": "Year + month",
}


@dataclass
class OrganiseOptions:
    """Extra choices: categories, subfolders, and archive best-practice options."""

    categories: Optional[set[str]] = None  # None = all categories
    # Media date nesting: none | year | year_month
    media_date_depth: str = "year_month"
    documents_by_ext: bool = True
    separate_archives: bool = True
    # Per-category override of nesting under the layout’s category folder
    category_subfolders: dict[str, str] = field(default_factory=dict)
    unknown_extension_folder: str = "unknown"
    # Best-practice options from digital organisation guides
    copy_instead_of_move: bool = True  # safer default for archives
    rename_with_date_prefix: bool = False
    sanitize_filenames: bool = True
    add_readme_notes: bool = True
    archive_older_than_days: int = 365  # for active_archive layout
    custom_structure_text: str = ""  # used when layout_id == "custom"

    @property
    def media_by_date(self) -> bool:
        """True when media files get any date nesting (year or year+month)."""
        return self.media_date_depth != "none"


def get_layout(layout_id: str) -> LayoutPreset:
    for preset in LAYOUT_PRESETS:
        if preset.id == layout_id:
            return preset
    return LAYOUT_PRESETS[0]


def normalize_layout_ids(
    layout_ids: Optional[list[str]] = None,
    layout_id: str = "type_date",
) -> list[str]:
    """
    Clean layout selection for organise.
    - At least one id
    - Unique, ordered like LAYOUT_PRESETS (stable combine order)
    - Custom cannot combine with other layouts (custom wins alone)
    """
    raw = [x for x in (layout_ids or []) if x] or [layout_id or "type_date"]
    seen: list[str] = []
    for item in raw:
        if item not in seen:
            seen.append(item)
    if "custom" in seen:
        return ["custom"]
    order = {p.id: i for i, p in enumerate(LAYOUT_PRESETS)}
    return sorted(seen, key=lambda x: order.get(x, 999))


def layout_combo_label(layout_ids: list[str]) -> str:
    ids = normalize_layout_ids(layout_ids)
    names = [get_layout(i).name for i in ids]
    if len(names) == 1:
        return names[0]
    return " + ".join(names)


def layout_combine_order_label(layout_ids: list[str]) -> str:
    """Human-readable combine order for the Organise UI."""
    ids = normalize_layout_ids(layout_ids)
    names = [get_layout(i).name for i in ids]
    if len(names) == 1:
        return f"Combine order: {names[0]}"
    return "Combine order: " + " → ".join(names)


def normalize_media_date_depth(value: object, fallback: str = "year_month") -> str:
    """Accept new depth strings or legacy bool-like values."""
    if isinstance(value, bool):
        return "year_month" if value else "none"
    text = str(value or "").strip().lower()
    if text in ("true", "1", "yes"):
        return "year_month"
    if text in ("false", "0", "no"):
        return "none"
    if text in MEDIA_DATE_DEPTHS:
        return text
    return fallback if fallback in MEDIA_DATE_DEPTHS else "year_month"


def normalize_category_subfolders(raw: object) -> dict[str, str]:
    """Clean a per-category mode map from settings or UI."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for cat in ALL_CATEGORIES:
        mode = str(raw.get(cat, "layout_default") or "layout_default")
        if mode not in CATEGORY_SUBFOLDER_MODES:
            mode = "layout_default"
        out[cat] = mode
    return out


def suggest_destination_combined(
    info: FileInfo,
    dest_root: Path,
    layout_ids: Optional[list[str]] = None,
    layout_id: str = "type_date",
    options: Optional[OrganiseOptions] = None,
) -> Path:
    """
    Build a destination path using one or more layouts.
    Multiple layouts nest folder parts (duplicates skipped) then use one filename.
    """
    ids = normalize_layout_ids(layout_ids, layout_id)
    if len(ids) == 1:
        return suggest_destination(info, dest_root, layout_id=ids[0], options=options)

    folder_parts: list[str] = []
    file_name = info.path.name
    for lid in ids:
        path = suggest_destination(info, dest_root, layout_id=lid, options=options)
        file_name = path.name
        try:
            rel_parent = path.parent.relative_to(dest_root.resolve())
        except ValueError:
            try:
                rel_parent = path.parent.relative_to(dest_root)
            except ValueError:
                continue
        if str(rel_parent) == ".":
            continue
        for part in rel_parent.parts:
            if part not in folder_parts:
                folder_parts.append(part)
    if folder_parts:
        return dest_root.joinpath(*folder_parts) / file_name
    return dest_root / file_name


def category_counts(files: list[FileInfo]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for info in files:
        counts[info.category] = counts.get(info.category, 0) + 1
    return counts


def recommended_layout_core(files: list[FileInfo]) -> list[str]:
    """
    Layout ids that best fit the scan (not the full preset list).
    First item is the best default.
    """
    counts = category_counts(files)
    if not counts:
        return ["type_date"]

    total = sum(counts.values())
    photos = counts.get("Photos", 0)
    videos = counts.get("Videos", 0)
    audio = counts.get("Audio", 0)
    docs = counts.get("Documents", 0)
    media = photos + videos + audio

    if media / total >= 0.6 and docs / total <= 0.25:
        return ["media_library", "life_areas", "type_date", "by_year_month", "active_archive"]
    if docs / total >= 0.5:
        return ["life_areas", "documents_first", "para", "by_extension", "type_date"]
    if len(counts) >= 3:
        return ["life_areas", "type_date", "para", "active_archive", "by_type"]
    return ["by_type", "life_areas", "type_date", "by_extension"]


def recommended_layout_ids(files: list[FileInfo]) -> list[str]:
    """
    Suggest layouts that fit what was found in the scan.
    First item is the best default; remaining presets follow for the picker.
    """
    ranked = list(recommended_layout_core(files))
    # Always offer all presets; recommended ones stay at the front
    for preset in LAYOUT_PRESETS:
        if preset.id not in ranked:
            ranked.append(preset.id)
    return ranked


def _year_month(modified: float) -> tuple[str, str]:
    dt = datetime.fromtimestamp(modified)
    return f"{dt.year:04d}", f"{dt.month:02d}"


def suggest_destination(
    info: FileInfo,
    dest_root: Path,
    layout_id: str = "type_date",
    options: Optional[OrganiseOptions] = None,
) -> Path:
    """
    Build a destination path for one file using the chosen layout preset
    and sub-folder options.
    """
    opts = options or OrganiseOptions()
    category = info.category
    name = apply_naming(
        info.path.name,
        info.modified,
        date_prefix=opts.rename_with_date_prefix,
        sanitize=opts.sanitize_filenames,
    )
    unknown = (opts.unknown_extension_folder or "unknown").strip() or "unknown"
    ext = Path(name).suffix.lower().lstrip(".") or unknown
    year, month = _year_month(info.modified)
    age_days = (datetime.now().timestamp() - info.modified) / 86400.0
    depth = normalize_media_date_depth(opts.media_date_depth)
    cat_modes = normalize_category_subfolders(opts.category_subfolders)
    cat_mode = cat_modes.get(category, "layout_default")

    def _with_depth(base: Path, use_depth: str) -> Path:
        if use_depth == "year_month":
            return base / year / month / name
        if use_depth == "year":
            return base / year / name
        return base / name

    def nest_under(base: Path, default_kind: str) -> Path:
        """
        Put the file under base using layout defaults, unless a per-category
        override is set (flat / year / year_month / extension).
        default_kind: media | docs | flat
        """
        mode = cat_mode
        if mode == "layout_default":
            if default_kind == "media":
                return _with_depth(base, depth)
            if default_kind == "docs":
                if opts.documents_by_ext:
                    return base / ext / name
                return base / name
            return base / name
        if mode == "flat":
            return base / name
        if mode == "year":
            return base / year / name
        if mode == "year_month":
            return base / year / month / name
        if mode == "extension":
            return base / ext / name
        return base / name

    def media_path(base: Path) -> Path:
        return nest_under(base, "media")

    def docs_path(base: Path) -> Path:
        return nest_under(base, "docs")

    def flat_path(base: Path) -> Path:
        return nest_under(base, "flat")

    def archives_or_other(cat: str) -> Path:
        if cat == "Archives" and opts.separate_archives:
            return flat_path(dest_root / "Archives")
        if cat == "Archives" and not opts.separate_archives:
            return flat_path(dest_root / "Other")
        return flat_path(dest_root / "Other")

    # --- User-defined custom folder tree ---
    if layout_id == "custom":
        structure = parse_custom_structure(opts.custom_structure_text or "")
        return custom_destination(info, dest_root, structure, name)

    # --- Keep relative path under each source root name ---
    if layout_id == "by_source_root":
        root_label = Path(info.source_root).name if info.source_root else "Source"
        try:
            src_root = Path(info.source_root).expanduser().resolve()
            rel = info.path.resolve().relative_to(src_root)
            if len(rel.parts) <= 1:
                return dest_root / root_label / name
            return dest_root / root_label / Path(*rel.parts[:-1]) / name
        except (OSError, ValueError):
            return flat_path(dest_root / root_label / category)

    # --- Category only (always shallow) ---
    if layout_id == "shallow_by_type":
        if category == "Archives" and not opts.separate_archives:
            return dest_root / "Other" / name
        return dest_root / category / name

    # --- Best-practice life-area / PARA / active-archive layouts ---
    if layout_id == "life_areas":
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / "Media" / category)
        if category == "Documents" and looks_financial(info.path.name):
            return docs_path(dest_root / "Finance" / "Documents")
        if category == "Documents":
            return docs_path(dest_root / "Personal" / "Documents")
        if category == "Archives" or age_days > opts.archive_older_than_days:
            return flat_path(dest_root / "Archive" / category)
        return flat_path(dest_root / "Personal" / "Other")

    if layout_id == "para":
        # PARA-inspired personal archive (Projects left as inbox for manual use)
        if age_days > opts.archive_older_than_days or category == "Archives":
            return flat_path(dest_root / "Archives" / category)
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / "Areas" / "Media" / category)
        if category == "Documents":
            return docs_path(dest_root / "Areas" / "Documents")
        return flat_path(dest_root / "Resources" / category)

    if layout_id == "active_archive":
        bucket = "Archive" if age_days > opts.archive_older_than_days else "Active"
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / bucket / category)
        if category == "Documents":
            return docs_path(dest_root / bucket / "Documents")
        return flat_path(dest_root / bucket / category)

    if layout_id == "by_type":
        if category == "Documents":
            return docs_path(dest_root / "Documents")
        if category == "Archives":
            return archives_or_other(category)
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / category)
        return flat_path(dest_root / category)

    if layout_id == "by_year_month":
        # Date first; category still used inside the month
        if category == "Documents":
            return nest_under(dest_root / year / month / "Documents", "docs")
        if category == "Archives":
            if opts.separate_archives:
                return nest_under(dest_root / year / month / "Archives", "flat")
            return nest_under(dest_root / year / month / "Other", "flat")
        return nest_under(dest_root / year / month / category, "flat")

    if layout_id == "by_extension":
        return dest_root / ext / name

    if layout_id == "media_library":
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / category)
        if category == "Documents":
            return docs_path(dest_root / "Documents")
        return archives_or_other(category)

    if layout_id == "documents_first":
        if category == "Documents":
            return docs_path(dest_root / "Documents")
        if category in ("Photos", "Videos", "Audio"):
            return media_path(dest_root / "Media" / category)
        return archives_or_other(category)

    # Default: type_date
    if category in ("Photos", "Videos", "Audio"):
        return media_path(dest_root / category)
    if category == "Documents":
        return docs_path(dest_root / "Documents")
    return archives_or_other(category)


def unique_path(path: Path, reserved: Optional[Set[Path]] = None) -> Path:
    """
    If the destination already exists on disk or is reserved in this run,
    add _1, _2, … before the extension (never overwrite).
    """
    claimed = reserved if reserved is not None else set()

    def is_taken(candidate: Path) -> bool:
        return candidate.exists() or candidate in claimed

    if not is_taken(path):
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not is_taken(candidate):
            return candidate
        counter += 1


def _is_under_quarantine(path: Path) -> bool:
    return DEFAULT_QUARANTINE_NAME in path.parts


def build_organise_plan(
    files: list[FileInfo],
    dest_root: str,
    layout_id: str = "type_date",
    only_categories: Optional[set[str]] = None,
    options: Optional[OrganiseOptions] = None,
    layout_ids: Optional[list[str]] = None,
) -> OrganisePlan:
    """
    Create a plan of moves. Does not change any files yet.
    Skips files already under the destination root, and quarantine files.
    Pass layout_ids to combine multiple folder layouts (nested).
    """
    opts = options or OrganiseOptions(categories=only_categories)
    if only_categories is not None and opts.categories is None:
        opts.categories = only_categories

    ids = normalize_layout_ids(layout_ids, layout_id)
    label = layout_combo_label(ids)
    plan = OrganisePlan(
        layout_id="+".join(ids),
        layout_name=label,
        layout_ids=ids,
    )
    dest = Path(dest_root).expanduser().resolve()

    for info in files:
        if opts.categories is not None and info.category not in opts.categories:
            continue
        # Organise acts on real disk files; zip members are for scan/duplicates only
        if info.is_inside_archive:
            plan.skipped.append(f"Inside archive (organise the .zip itself): {info.display_path}")
            continue
        try:
            current = info.path.resolve()
        except OSError:
            plan.skipped.append(f"Could not resolve: {info.path}")
            continue

        if _is_under_quarantine(current):
            plan.skipped.append(f"In quarantine (left alone): {current}")
            continue

        # Do not move files that are already inside the destination tree
        try:
            current.relative_to(dest)
            plan.skipped.append(f"Already in destination: {current}")
            continue
        except ValueError:
            pass

        if not current.exists():
            plan.skipped.append(f"Missing on disk: {current}")
            continue

        suggested = suggest_destination_combined(
            info, dest, layout_ids=ids, options=opts
        )
        plan.items.append(
            OrganisePlanItem(
                source=current,
                destination=suggested,
                category=info.category,
            )
        )
    return plan


def layout_tree_text(
    files: list[FileInfo],
    dest_root: str,
    layout_id: str = "type_date",
    options: Optional[OrganiseOptions] = None,
    max_folders: int = 100,
    layout_ids: Optional[list[str]] = None,
) -> str:
    """
    Build a visual folder-tree preview of the selected layout(s)
    using the real scanned files (folder paths only).
    """
    ids = normalize_layout_ids(layout_ids, layout_id)
    label = layout_combo_label(ids)
    opts = options or OrganiseOptions()
    display_root = dest_root.strip() if dest_root.strip() else "Destination"
    placeholder = Path("/__preview__")

    if ids == ["custom"]:
        structure = parse_custom_structure(opts.custom_structure_text or "")
        header = custom_tree_preview(structure, display_root)
        if not files or not structure.rules:
            return header
        custom_header = header + "\n\nFolders your rules create for this scan:\n"
    else:
        custom_header = ""

    folder_paths: set[Path] = set()
    included = 0
    for info in files:
        if opts.categories is not None and info.category not in opts.categories:
            continue
        if info.is_inside_archive:
            continue
        suggested = suggest_destination_combined(
            info, placeholder, layout_ids=ids, options=opts
        )
        rel = suggested.parent.relative_to(placeholder)
        if str(rel) == ".":
            continue
        parts = rel.parts
        for i in range(1, len(parts) + 1):
            folder_paths.add(Path(*parts[:i]))
        included += 1

    lines = [
        f"Layout: {label}",
        f"Files included: {included}",
        "",
        f"{display_root}/",
    ]
    if custom_header:
        lines = custom_header.splitlines() + ["", f"{display_root}/"]
    if not folder_paths:
        lines.append("  (no folders — scan files and tick categories)")
        return "\n".join(lines)

    sorted_folders = sorted(folder_paths, key=lambda p: [x.lower() for x in p.parts])
    shown = sorted_folders[:max_folders]

    for folder in shown:
        depth = len(folder.parts)
        indent = "    " * (depth - 1) + "├── "
        lines.append(f"{indent}{folder.name}/")
    if len(sorted_folders) > max_folders:
        lines.append(f"    … and {len(sorted_folders) - max_folders} more folders")
    lines.append("")
    if len(ids) == 1:
        lines.append(f"Pattern: {get_layout(ids[0]).example}")
    else:
        lines.append("Combined layouts (folders nested, duplicate names skipped):")
        for lid in ids:
            lines.append(f"  • {get_layout(lid).name}: {get_layout(lid).example}")
    return "\n".join(lines)


def apply_organise_plan(
    plan: OrganisePlan,
    dry_run: bool = True,
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    options: Optional[OrganiseOptions] = None,
    dest_root: Optional[str] = None,
) -> list[str]:
    """
    Carry out the organise plan (additive merge into dest_root).

    dry_run=True means only describe what would happen (safe default).
    Prefer copy_instead_of_move (archive best practice) when options say so.

    Never deletes destination files/folders. Never overwrites an existing
    destination file — collisions get a unique name (name_1.ext, …).
    Creates missing parent folders with mkdir(..., exist_ok=True) only.
    """
    opts = options or OrganiseOptions()
    mode_tag = "copy" if opts.copy_instead_of_move else "move"
    log: list[str] = []
    total = len(plan.items)
    written_dests: list[Path] = []
    # Same-batch name collisions + existing files both go through unique_path
    reserved: set[Path] = set()

    for index, item in enumerate(plan.items, start=1):
        if should_cancel and should_cancel():
            log.append("[cancelled] Organise stopped by user.")
            break
        final_dest = unique_path(item.destination, reserved)
        # Last-moment guard: never write onto an existing path
        if not dry_run and final_dest.exists():
            final_dest = unique_path(final_dest, reserved)
        reserved.add(final_dest)
        renamed = final_dest != item.destination
        message = f"{item.source}  →  {final_dest}"
        if renamed:
            message += "  (name taken; used unique name)"
        if dry_run:
            tag = f"preview-{mode_tag}-unique" if renamed else f"preview-{mode_tag}"
            log.append(f"[{tag}] {message}")
            continue
        try:
            # Only create missing folders — never wipe or alter existing ones
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            if final_dest.exists():
                # Another process created the file between unique_path and here
                reserved.discard(final_dest)
                final_dest = unique_path(item.destination, reserved)
                reserved.add(final_dest)
                message = (
                    f"{item.source}  →  {final_dest}  (name taken; used unique name)"
                )
            if final_dest.exists():
                log.append(
                    f"[skipped] destination exists, could not find free name: "
                    f"{item.source}  →  {item.destination}"
                )
                reserved.discard(final_dest)
                continue
            if opts.copy_instead_of_move:
                shutil.copy2(str(item.source), str(final_dest))
                log.append(f"[copied] {message}")
            else:
                shutil.move(str(item.source), str(final_dest))
                log.append(f"[moved] {message}")
            written_dests.append(final_dest)
            if status_cb and (index == 1 or index % 10 == 0 or index == total):
                verb = "Copying" if opts.copy_instead_of_move else "Moving"
                status_cb(f"{verb} files: {index}/{total}")
        except OSError as exc:
            reserved.discard(final_dest)
            log.append(f"[error] {item.source}: {exc}")

    if not dry_run and opts.add_readme_notes and dest_root and written_dests:
        try:
            root = Path(dest_root).expanduser().resolve()
            children: set[Path] = set()
            for dest in written_dests:
                try:
                    rel = dest.resolve().relative_to(root)
                    if rel.parts:
                        children.add(root / rel.parts[0])
                except ValueError:
                    continue
            for folder in sorted(children):
                note = folder / "README.txt"
                # Never clobber a user's existing README
                if note.exists():
                    log.append(f"[readme-skip] already exists: {note}")
                    continue
                note.write_text(
                    readme_for_folder(folder.name, plan.layout_name),
                    encoding="utf-8",
                )
                log.append(f"[readme] {note}")
        except OSError as exc:
            log.append(f"[readme-error] {exc}")

    return log


def destination_conflicts_with_sources(dest: str, sources: list[str]) -> Optional[str]:
    """
    Return a warning message if the destination is the same as,
    or inside, a scan source (risky).

    An existing archive folder outside your sources is fine — organise
    merges into it without removing what is already there.
    """
    try:
        dest_path = Path(dest).expanduser().resolve()
    except OSError:
        return None
    for source in sources:
        try:
            source_path = Path(source).expanduser().resolve()
        except OSError:
            continue
        if dest_path == source_path:
            return (
                f"Destination is the same as a scan source:\n{dest_path}\n\n"
                "Choose a different tidy folder so you do not reorganise a source onto itself."
            )
        try:
            dest_path.relative_to(source_path)
            return (
                f"Destination is inside a scan source:\n{dest_path}\n"
                f"(source: {source_path})\n\n"
                "This can make results confusing. Choose a destination outside your "
                "scan sources (an existing archive root is OK)."
            )
        except ValueError:
            pass
    return None
