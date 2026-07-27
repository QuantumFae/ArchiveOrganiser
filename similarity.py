"""
Similar-file helpers: photo visual fingerprints and document text fingerprints.

Uses only packages already in the project (Pillow, numpy, pymupdf, python-docx).
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from models import DOCUMENT_EXTS, IMAGE_EXTS, FileInfo

# Average-hash size (8 → 64-bit hash). Hamming distance ≤ this ≈ visually similar.
PHOTO_HASH_SIZE = 8
PHOTO_MAX_DISTANCE = 8
# Name similarity 0..1
NAME_SIMILAR_THRESHOLD = 0.82
# Document text fingerprint: share enough tokens or near-identical content hash
DOC_TOKEN_OVERLAP = 0.72


def normalize_name(name: str) -> str:
    """Lowercase stem without copy suffixes like (1), _copy, final."""
    stem = Path(name).stem.lower()
    stem = re.sub(r"[\s._-]*\(\d+\)$", "", stem)
    stem = re.sub(r"[\s._-]*(copy|副本|final|edited|edit|dup|duplicate)\d*$", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "", stem)
    return stem


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def names_look_similar(a: str, b: str) -> bool:
    return name_similarity(a, b) >= NAME_SIMILAR_THRESHOLD


def _open_bytes(info: FileInfo, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Read file bytes from disk or zip, capped so multi-GB files never fill RAM."""
    if info.archive_container and info.archive_member:
        with zipfile.ZipFile(info.archive_container, "r") as zf:
            with zf.open(info.archive_member, "r") as handle:
                return handle.read(max_bytes)
    with info.path.open("rb") as handle:
        return handle.read(max_bytes)


def _pil_from_info(info: FileInfo):
    """Load a small RGB image for hashing, or None."""
    from PIL import Image

    ext = info.suffix
    try:
        data = _open_bytes(info)
    except OSError:
        return None

    if ext in {".dng", ".cr2", ".nef", ".arw", ".orf", ".rw2", ".raf", ".raw"}:
        try:
            import rawpy
            import numpy as np

            with rawpy.imread(io.BytesIO(data)) as raw:
                rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True)
            return Image.fromarray(rgb)
        except Exception:
            return None

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


def average_hash(info: FileInfo, hash_size: int = PHOTO_HASH_SIZE) -> Optional[str]:
    """
    Simple perceptual hash (average hash).
    Similar-looking photos get similar bit patterns.
    """
    from PIL import Image

    img = _pil_from_info(info)
    if img is None:
        return None
    try:
        gray = img.convert("L").resize((hash_size, hash_size), Image.Resampling.LANCZOS)
        pixels = list(gray.getdata())
        avg = sum(pixels) / len(pixels)
        bits = 0
        for i, value in enumerate(pixels):
            if value >= avg:
                bits |= 1 << i
        return f"{bits:0{hash_size * hash_size // 4}x}"
    except Exception:
        return None


def hamming_distance(hash_a: str, hash_b: str) -> int:
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return 999
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
    except ValueError:
        return 999


def photos_visually_similar(a: FileInfo, b: FileInfo) -> bool:
    if not a.visual_hash or not b.visual_hash:
        return False
    return hamming_distance(a.visual_hash, b.visual_hash) <= PHOTO_MAX_DISTANCE


def _extract_text(info: FileInfo) -> str:
    """Best-effort plain text for documents."""
    ext = info.suffix
    try:
        data = _open_bytes(info)
    except OSError:
        return ""

    if ext in {".txt", ".md", ".csv", ".rtf", ".log"}:
        return data.decode("utf-8", errors="ignore")[:200_000]

    if ext == ".pdf":
        try:
            import fitz

            doc = fitz.open(stream=data, filetype="pdf")
            parts = []
            for page in doc[:8]:
                parts.append(page.get_text() or "")
            doc.close()
            return "\n".join(parts)
        except Exception:
            return ""

    if ext == ".docx":
        try:
            import docx

            document = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in document.paragraphs)
        except Exception:
            return ""

    if ext in {".xlsx", ".xls"}:
        try:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets[:3]:
                for row in sheet.iter_rows(max_row=40, values_only=True):
                    parts.append(" ".join("" if c is None else str(c) for c in row))
            wb.close()
            return "\n".join(parts)
        except Exception:
            return ""

    # Unknown binary office — fingerprint raw bytes lightly
    return ""


def document_fingerprint(info: FileInfo) -> Optional[str]:
    """
    Content fingerprint for documents:
    - SHA of normalised text when text exists
    - else None (caller may fall back to byte hash)
    """
    text = _extract_text(info)
    if not text or not text.strip():
        return None
    cleaned = re.sub(r"\s+", " ", text.lower()).strip()
    if len(cleaned) < 20:
        return None
    return hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()


def document_token_set(info: FileInfo) -> set[str]:
    text = _extract_text(info)
    if not text:
        return set()
    tokens = re.findall(r"[a-z0-9]{4,}", text.lower())
    return set(tokens[:2000])


def documents_content_similar(a: FileInfo, b: FileInfo) -> bool:
    if a.content_fingerprint and b.content_fingerprint:
        if a.content_fingerprint == b.content_fingerprint:
            return True
    ta, tb = document_token_set(a), document_token_set(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= DOC_TOKEN_OVERLAP


def is_photo(info: FileInfo) -> bool:
    return info.category == "Photos" or info.suffix in IMAGE_EXTS


def is_document(info: FileInfo) -> bool:
    return info.category == "Documents" or info.suffix in DOCUMENT_EXTS
