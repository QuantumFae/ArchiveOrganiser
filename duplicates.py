"""Find duplicate files by content fingerprint (hash)."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from models import FileInfo
from scanner import add_hashes


@dataclass
class DuplicateGroup:
    """A set of files that have the exact same contents."""

    hash_value: str
    size: int
    files: list[FileInfo] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        """Space used by copies beyond the first one."""
        if len(self.files) < 2:
            return 0
        return self.size * (len(self.files) - 1)


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)

    @property
    def duplicate_file_count(self) -> int:
        """How many files are extras (not counting one keeper per group)."""
        return sum(max(0, len(g.files) - 1) for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)


def find_duplicates(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> DuplicateReport:
    """
    Find exact duplicates.

    Speed trick:
    1. Group by file size first (different sizes cannot be duplicates).
    2. Only hash files that share a size with at least one other file.
    """
    report = DuplicateReport()

    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for info in files:
        by_size[info.size].append(info)

    candidates: list[FileInfo] = []
    for size, group in by_size.items():
        if size == 0:
            # Empty files: treat all empty files as one duplicate group later
            if len(group) > 1:
                candidates.extend(group)
            continue
        if len(group) > 1:
            candidates.extend(group)

    if status_cb:
        status_cb(f"Possible duplicates to check: {len(candidates)} files")

    add_hashes(candidates, status_cb=status_cb, should_cancel=should_cancel)

    by_hash: dict[str, list[FileInfo]] = defaultdict(list)
    for info in candidates:
        if info.hash_value:
            by_hash[info.hash_value].append(info)

    for hash_value, group_files in by_hash.items():
        if len(group_files) < 2:
            continue
        # Prefer keeping the oldest modified file as "original" (first in list)
        ordered = sorted(group_files, key=lambda f: (f.modified, str(f.path)))
        report.groups.append(
            DuplicateGroup(
                hash_value=hash_value,
                size=ordered[0].size,
                files=ordered,
            )
        )

    report.groups.sort(key=lambda g: g.wasted_bytes, reverse=True)

    if status_cb:
        status_cb(
            f"Found {len(report.groups)} duplicate groups "
            f"({report.duplicate_file_count} extra files)."
        )
    return report
