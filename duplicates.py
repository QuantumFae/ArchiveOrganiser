"""Find exact and similar duplicate files — tuned for large drives."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from helpers import ElapsedTimer, format_duration
from models import FileInfo
from scanner import add_hashes
from similarity import (
    average_hash,
    document_fingerprint,
    is_document,
    is_photo,
    normalize_name,
    photos_visually_similar,
)

# Hard caps so whole-drive searches finish in reasonable time
MAX_SIMILAR_PHOTOS = 8000
MAX_SIMILAR_DOCS = 12000
# Skip zip members for similar (exact still includes them) — zip text scans are huge
SIMILAR_SKIP_ARCHIVE_MEMBERS = True


@dataclass
class DuplicateGroup:
    """A set of files that match (exact content or similar photo/document)."""

    hash_value: str
    size: int
    files: list[FileInfo] = field(default_factory=list)
    kind: str = "exact"  # exact | similar_photo | similar_document
    reason: str = ""

    @property
    def wasted_bytes(self) -> int:
        if len(self.files) < 2:
            return 0
        if self.kind == "exact":
            return self.size * (len(self.files) - 1)
        return sum(f.size for f in self.files[1:])

    @property
    def label(self) -> str:
        if self.reason:
            return self.reason
        labels = {
            "exact": "Exact copy",
            "similar_photo": "Similar photo",
            "similar_document": "Similar document",
        }
        return labels.get(self.kind, self.kind)


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def duplicate_file_count(self) -> int:
        return sum(max(0, len(g.files) - 1) for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)


@dataclass
class DuplicateSearchOptions:
    exact: bool = True
    similar_photos: bool = True
    similar_documents: bool = True


def find_duplicates(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> DuplicateReport:
    """Exact duplicates only (same bytes)."""
    return find_duplicate_matches(
        files,
        options=DuplicateSearchOptions(
            exact=True, similar_photos=False, similar_documents=False
        ),
        status_cb=status_cb,
        should_cancel=should_cancel,
    )


def find_duplicate_matches(
    files: list[FileInfo],
    options: Optional[DuplicateSearchOptions] = None,
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    store=None,
) -> DuplicateReport:
    """
    Find exact duplicates and/or similar photos / documents.

    Similar modes use bucketing (not full pairwise) so large drives finish.
    Zip members are included in exact search, but skipped for similar.
    When ``store`` is set, exact search loads only same-size candidates from SQLite.
    """
    opts = options or DuplicateSearchOptions()
    timer = ElapsedTimer()
    report = DuplicateReport()

    def status(msg: str) -> None:
        if status_cb:
            status_cb(timer.stamp(msg))

    if opts.exact:
        status("Looking for exact duplicates…")
        exact_report = _find_exact(
            files, status_cb=status, should_cancel=should_cancel, store=store
        )
        report.groups.extend(exact_report.groups)
        status(f"Exact duplicates: {len(exact_report.groups)} groups")

    if should_cancel and should_cancel():
        report.duration_seconds = timer.seconds()
        report.notes.append("Cancelled early.")
        return report

    # Similar modes need a concrete file list (load from store if needed)
    work_files = files
    if (opts.similar_photos or opts.similar_documents) and not work_files and store is not None:
        status("Loading file list for similar search…")
        work_files = store.load_all_files()

    if opts.similar_photos:
        status("Comparing photos by name and look (fast buckets)…")
        photo_groups, photo_notes = _find_similar_photos(
            work_files, status_cb=status, should_cancel=should_cancel
        )
        report.groups.extend(photo_groups)
        report.notes.extend(photo_notes)
        status(f"Similar photos: {len(photo_groups)} groups")

    if should_cancel and should_cancel():
        report.duration_seconds = timer.seconds()
        report.notes.append("Cancelled early.")
        return report

    if opts.similar_documents:
        status("Comparing documents by name and content (fast buckets)…")
        doc_groups, doc_notes = _find_similar_documents(
            work_files, status_cb=status, should_cancel=should_cancel
        )
        report.groups.extend(doc_groups)
        report.notes.extend(doc_notes)
        status(f"Similar documents: {len(doc_groups)} groups")

    report.groups = _dedupe_against_exact(report.groups)
    report.groups.sort(key=lambda g: (g.kind != "exact", -g.wasted_bytes))
    report.duration_seconds = timer.seconds()

    status(
        f"Found {len(report.groups)} groups "
        f"({report.duplicate_file_count} extras) in {format_duration(report.duration_seconds)}"
    )
    return report


def _dedupe_against_exact(groups: list[DuplicateGroup]) -> list[DuplicateGroup]:
    exact_sets = [
        {f.display_path for f in g.files}
        for g in groups
        if g.kind == "exact"
    ]
    kept: list[DuplicateGroup] = []
    for group in groups:
        if group.kind == "exact":
            kept.append(group)
            continue
        paths = {f.display_path for f in group.files}
        if any(paths <= exact for exact in exact_sets):
            continue
        kept.append(group)
    return kept


def _find_exact(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    store=None,
) -> DuplicateReport:
    report = DuplicateReport()
    candidates: list[FileInfo] = []

    if store is not None:
        size_groups = list(store.iter_same_size_groups())
        if status_cb:
            status_cb(f"Same-size buckets to check: {len(size_groups)}")
        for _size, ids in size_groups:
            if should_cancel and should_cancel():
                return report
            candidates.extend(store.load_files_by_ids(ids))
    else:
        by_size: dict[int, list[FileInfo]] = defaultdict(list)
        for info in files:
            by_size[info.size].append(info)
        for group in by_size.values():
            if len(group) > 1:
                candidates.extend(group)

    if status_cb:
        status_cb(f"Possible exact duplicates to hash: {len(candidates)} files")

    add_hashes(
        candidates,
        status_cb=status_cb,
        should_cancel=should_cancel,
        store=store,
        use_partial=True,
    )

    by_hash: dict[str, list[FileInfo]] = defaultdict(list)
    for info in candidates:
        if info.hash_value:
            by_hash[info.hash_value].append(info)

    for hash_value, group_files in by_hash.items():
        if len(group_files) < 2:
            continue
        # Partial-only keys must not form groups (those were unique → already filtered)
        # Full CRC groups and zip CRC groups are real matches.
        ordered = sorted(group_files, key=lambda f: (f.modified, f.display_path))
        report.groups.append(
            DuplicateGroup(
                hash_value=hash_value,
                size=ordered[0].size,
                files=ordered,
                kind="exact",
                reason="Exact content match (size + CRC32)",
            )
        )
    return report


def _disk_only(files: list[FileInfo]) -> list[FileInfo]:
    if not SIMILAR_SKIP_ARCHIVE_MEMBERS:
        return files
    return [f for f in files if not f.is_inside_archive]


def _groups_from_buckets(
    buckets: dict[str, list[FileInfo]],
    kind: str,
    reason: str,
    key_attr: str,
) -> list[DuplicateGroup]:
    result: list[DuplicateGroup] = []
    for key, group_files in buckets.items():
        if not key or len(group_files) < 2:
            continue
        ordered = sorted(group_files, key=lambda f: (f.modified, f.display_path))
        result.append(
            DuplicateGroup(
                hash_value=key,
                size=ordered[0].size,
                files=ordered,
                kind=kind,
                reason=reason,
            )
        )
    return result


def _find_similar_photos(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[list[DuplicateGroup], list[str]]:
    notes: list[str] = []
    photos = [f for f in _disk_only(files) if is_photo(f)]
    if len(photos) > MAX_SIMILAR_PHOTOS:
        notes.append(
            f"Similar photos limited to first {MAX_SIMILAR_PHOTOS} of {len(photos)} "
            "(largest first) so the search can finish."
        )
        photos = sorted(photos, key=lambda f: f.size, reverse=True)[:MAX_SIMILAR_PHOTOS]

    total = len(photos)
    if status_cb:
        status_cb(f"Photo set size: {total}")

    # 1) Same normalised name → similar
    by_name: dict[str, list[FileInfo]] = defaultdict(list)
    for info in photos:
        key = normalize_name(info.name)
        if key:
            by_name[key].append(info)
    name_groups = _groups_from_buckets(
        by_name, "similar_photo", "Similar photo (similar name)", "name"
    )

    # 2) Visual hashes — bucket by exact aHash, then near-match within prefix
    for index, info in enumerate(photos, start=1):
        if should_cancel and should_cancel():
            return name_groups, notes + ["Photo compare cancelled."]
        if status_cb and (index == 1 or index % 50 == 0 or index == total):
            status_cb(f"Photo visual fingerprints: {index}/{total}")
        if not info.visual_hash:
            info.visual_hash = average_hash(info)

    by_hash: dict[str, list[FileInfo]] = defaultdict(list)
    for info in photos:
        if info.visual_hash:
            by_hash[info.visual_hash].append(info)
    look_groups = _groups_from_buckets(
        by_hash, "similar_photo", "Similar photo (similar look)", "hash"
    )

    # Near-duplicates: same 4-char hash prefix, then hamming ≤ threshold (small buckets only)
    by_prefix: dict[str, list[FileInfo]] = defaultdict(list)
    for info in photos:
        if info.visual_hash and len(info.visual_hash) >= 4:
            by_prefix[info.visual_hash[:4]].append(info)

    near_groups: list[DuplicateGroup] = []
    for prefix, bucket in by_prefix.items():
        if should_cancel and should_cancel():
            break
        if len(bucket) < 2 or len(bucket) > 80:
            continue
        # Union-find only inside this small bucket
        parent = list(range(len(bucket)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                if photos_visually_similar(bucket[i], bucket[j]):
                    union(i, j)
        clusters: dict[int, list[FileInfo]] = defaultdict(list)
        for i, info in enumerate(bucket):
            clusters[find(i)].append(info)
        for cluster in clusters.values():
            if len(cluster) < 2:
                continue
            # Skip if already exact same hash group
            hashes = {f.visual_hash for f in cluster}
            if len(hashes) == 1:
                continue
            ordered = sorted(cluster, key=lambda f: (f.modified, f.display_path))
            near_groups.append(
                DuplicateGroup(
                    hash_value=ordered[0].visual_hash or prefix,
                    size=ordered[0].size,
                    files=ordered,
                    kind="similar_photo",
                    reason="Similar photo (near look)",
                )
            )

    # Merge unique groups (by frozenset of paths)
    merged: dict[frozenset[str], DuplicateGroup] = {}
    for group in name_groups + look_groups + near_groups:
        key = frozenset(f.display_path for f in group.files)
        if key not in merged:
            merged[key] = group
        else:
            # Prefer richer reason
            if "look" in group.reason and "look" not in merged[key].reason:
                merged[key].reason = group.reason
    return list(merged.values()), notes


def _find_similar_documents(
    files: list[FileInfo],
    status_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[list[DuplicateGroup], list[str]]:
    """
    Scalable document similarity:
    - same normalised filename
    - same text content fingerprint
    No O(n²) pairwise token compares (that hung 28k-doc drives for 30+ minutes).
    """
    notes: list[str] = []
    docs = [f for f in _disk_only(files) if is_document(f)]
    archived = sum(1 for f in files if is_document(f) and f.is_inside_archive)
    if archived and SIMILAR_SKIP_ARCHIVE_MEMBERS:
        notes.append(
            f"Skipped {archived} documents inside zips for similar-doc search "
            "(exact search still checks them). Untick zip scan next time if you only need disk files."
        )

    if len(docs) > MAX_SIMILAR_DOCS:
        notes.append(
            f"Similar documents limited to first {MAX_SIMILAR_DOCS} of {len(docs)} "
            "(largest first) so the search can finish."
        )
        docs = sorted(docs, key=lambda f: f.size, reverse=True)[:MAX_SIMILAR_DOCS]

    total = len(docs)
    if status_cb:
        status_cb(f"Document set size: {total}")

    # Name buckets — instant
    by_name: dict[str, list[FileInfo]] = defaultdict(list)
    for info in docs:
        key = normalize_name(info.name)
        if key:
            by_name[key].append(info)
    name_groups = _groups_from_buckets(
        by_name, "similar_document", "Similar document (similar name)", "name"
    )

    # Content fingerprints — only for on-disk docs; skip tiny empties quickly
    for index, info in enumerate(docs, start=1):
        if should_cancel and should_cancel():
            return name_groups, notes + ["Document compare cancelled."]
        if status_cb and (index == 1 or index % 100 == 0 or index == total):
            status_cb(f"Document content fingerprints: {index}/{total}")
        if info.size == 0:
            info.content_fingerprint = "empty"
            continue
        if not info.content_fingerprint:
            # Cap work: skip huge binaries that won't yield text quickly
            if info.size > 40_000_000:  # 40 MB
                continue
            info.content_fingerprint = document_fingerprint(info)

    by_fp: dict[str, list[FileInfo]] = defaultdict(list)
    for info in docs:
        if info.content_fingerprint:
            by_fp[info.content_fingerprint].append(info)
    content_groups = _groups_from_buckets(
        by_fp, "similar_document", "Similar document (similar content)", "fp"
    )

    merged: dict[frozenset[str], DuplicateGroup] = {}
    for group in name_groups + content_groups:
        key = frozenset(f.display_path for f in group.files)
        if key not in merged:
            merged[key] = group
        else:
            existing = merged[key]
            if "content" in group.reason and "content" not in existing.reason:
                existing.reason = (
                    "Similar document (similar name + similar content)"
                    if "name" in existing.reason
                    else group.reason
                )
            elif "name" in group.reason and "name" not in existing.reason:
                existing.reason = (
                    "Similar document (similar name + similar content)"
                    if "content" in existing.reason
                    else group.reason
                )
    return list(merged.values()), notes
