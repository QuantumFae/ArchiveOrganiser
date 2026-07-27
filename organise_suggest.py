"""
Rules-based (offline) suggestions for Organise options.

Looks at scan category counts, sample path names, and last saved settings.
Optionally asks a local Ollama model (Stage B); falls back to rules on any failure.
Never applies file moves — only proposes knobs for the Organise UI.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from models import FileInfo
from organiser import (
    ALL_CATEGORIES,
    CATEGORY_MODE_LABELS,
    CATEGORY_SUBFOLDER_MODES,
    LAYOUT_PRESETS,
    MEDIA_DATE_DEPTH_LABELS,
    MEDIA_DATE_DEPTHS,
    category_counts,
    get_layout,
    normalize_category_subfolders,
    normalize_layout_ids,
    normalize_media_date_depth,
    recommended_layout_ids,
)

# Path-name clues (folder segments only — no file contents)
_YEAR_MONTH_RE = re.compile(
    r"(?:^|/)((?:19|20)\d{2})[/_\-](0[1-9]|1[0-2])(?:/|$)"
)
_YEAR_ONLY_RE = re.compile(r"(?:^|/)((?:19|20)\d{2})(?:/|$)")
_EXT_FOLDER_RE = re.compile(
    r"(?:^|/)(jpe?g|png|gif|webp|heic|tiff?|pdf|docx?|xlsx?|pptx?|"
    r"txt|csv|mp4|mov|mkv|avi|mp3|wav|flac|zip|rar|7z)(?:/|$)",
    re.IGNORECASE,
)
# Common “everything dumped in one place” folder names
_FLAT_HINT_RE = re.compile(
    r"(?:^|/)(downloads?|desktop|misc|unsorted|dump|inbox|temp|tmp)(?:/|$)",
    re.IGNORECASE,
)

# Local Ollama only (never a cloud URL by default)
OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"
# Prefer smaller / common chat models when auto-picking
_OLLAMA_MODEL_PREFERENCE = (
    "llama3.2",
    "llama3.1",
    "llama3",
    "mistral",
    "qwen2.5",
    "qwen2",
    "phi3",
    "gemma2",
    "gemma",
)

# Layout ids the AI may choose (custom needs free-text rules — keep rules-only)
_AI_LAYOUT_IDS = frozenset(p.id for p in LAYOUT_PRESETS if p.id != "custom")


@dataclass
class OrganiseSuggestion:
    """Proposed Organise knobs plus short reasons (for the UI summary)."""

    layout_ids: list[str]
    media_date_depth: str
    category_subfolders: dict[str, str]
    documents_by_ext: bool = True
    layout_reason: str = ""
    media_date_reason: str = ""
    category_reasons: dict[str, str] = field(default_factory=dict)
    documents_by_ext_reason: str = ""
    summary_lines: list[str] = field(default_factory=list)
    # rules | ollama | rules_fallback
    source: str = "rules"
    source_note: str = ""


def _path_text(info: FileInfo) -> str:
    """Normalised path string used for pattern matching."""
    text = info.display_path.replace("\\", "/")
    # Drop the filename so we mainly see folder structure
    try:
        parent = Path(info.path).parent.as_posix()
    except Exception:
        parent = text.rsplit("/", 1)[0] if "/" in text else text
    return parent.lower()


def _sample_paths(files: list[FileInfo], limit: int = 200) -> list[str]:
    """Take a spread of path parents across the list (not only the first N)."""
    if not files:
        return []
    if len(files) <= limit:
        return [_path_text(f) for f in files]
    step = max(1, len(files) // limit)
    picked = files[::step][:limit]
    return [_path_text(f) for f in picked]


def _sample_basenames(files: list[FileInfo], limit: int = 40) -> list[str]:
    """File names only (no folder contents) — safe to send to local Ollama."""
    if not files:
        return []
    if len(files) <= limit:
        picked = files
    else:
        step = max(1, len(files) // limit)
        picked = files[::step][:limit]
    names: list[str] = []
    for info in picked:
        name = (info.name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _path_signals(paths: list[str]) -> dict[str, int]:
    """Count how often date / extension / flat clues appear in folder paths."""
    year_month = 0
    year_only = 0
    ext_folder = 0
    flat_hint = 0
    shallow = 0
    for p in paths:
        if _YEAR_MONTH_RE.search(p):
            year_month += 1
        elif _YEAR_ONLY_RE.search(p):
            year_only += 1
        if _EXT_FOLDER_RE.search(p):
            ext_folder += 1
        if _FLAT_HINT_RE.search(p):
            flat_hint += 1
        # Shallow: few folder segments under the source root
        depth = p.count("/")
        if depth <= 2:
            shallow += 1
    return {
        "year_month": year_month,
        "year_only": year_only,
        "ext_folder": ext_folder,
        "flat_hint": flat_hint,
        "shallow": shallow,
        "total": len(paths),
    }


def _suggest_media_date_depth(
    signals: dict[str, int],
    counts: dict[str, int],
    saved_depth: str,
) -> tuple[str, str]:
    """Pick none / year / year_month from path clues and category mix."""
    total = max(1, signals["total"])
    media = counts.get("Photos", 0) + counts.get("Videos", 0) + counts.get("Audio", 0)
    all_files = max(1, sum(counts.values()))
    media_share = media / all_files

    ym_share = signals["year_month"] / total
    y_share = signals["year_only"] / total
    flat_hint_share = signals["flat_hint"] / total
    shallow_share = signals["shallow"] / total
    flat_share = (flat_hint_share + shallow_share) / 2

    if ym_share >= 0.15:
        return (
            "year_month",
            "Many folders already look like Year/Month (e.g. 2022/07).",
        )
    if y_share >= 0.15 and ym_share < 0.08:
        return (
            "year",
            "Paths often include a year folder, but not month folders.",
        )
    # Explicit dump/inbox folders outweigh a 50/50 media mix
    if flat_hint_share >= 0.35 or flat_share >= 0.55:
        return (
            "none",
            "Paths look mostly flat or like Downloads/unsorted — skip date subfolders.",
        )
    if media_share >= 0.45:
        # Media-heavy archives usually benefit from date nesting
        if saved_depth in ("year", "year_month"):
            depth = saved_depth
            label = MEDIA_DATE_DEPTH_LABELS.get(depth, depth)
            return (
                depth,
                f"Scan is media-heavy — using your last date setting ({label}).",
            )
        return (
            "year_month",
            "Scan is media-heavy — nesting media by year + month.",
        )
    if media_share < 0.2:
        return (
            "none",
            "Paths look mostly flat or light on media — skip date subfolders.",
        )
    # Ambiguous: keep last saved preference
    return (
        saved_depth,
        f"Mixed clues — keeping your last media date setting "
        f"({MEDIA_DATE_DEPTH_LABELS.get(saved_depth, saved_depth)}).",
    )


def _suggest_category_mode(
    category: str,
    cat_paths: list[str],
    media_date_depth: str,
) -> tuple[str, str]:
    """
    Propose a per-category mode when path patterns are clear.
    Returns layout_default when there is no strong signal.
    """
    if not cat_paths:
        return "layout_default", ""

    signals = _path_signals(cat_paths)
    total = max(1, signals["total"])
    ym = signals["year_month"] / total
    y = signals["year_only"] / total
    ext = signals["ext_folder"] / total
    flat = (signals["flat_hint"] + signals["shallow"]) / (2 * total)

    if category == "Documents" and ext >= 0.2:
        return "extension", "Document paths often sit under extension folders (pdf, docx, …)."
    if category in ("Photos", "Videos", "Audio"):
        if ym >= 0.2:
            return "year_month", f"{category} paths already look dated by year and month."
        if y >= 0.2:
            return "year", f"{category} paths often include a year folder."
        if flat >= 0.6 and media_date_depth != "none":
            return "flat", f"{category} look dumped flat — override layout date nesting."
    if category in ("Archives", "Other") and flat >= 0.55:
        return "flat", f"{category} look shallow — keep them flat."
    if category == "Documents" and flat >= 0.55 and ext < 0.1:
        return "flat", "Documents look flat — no extension subfolders needed."
    return "layout_default", ""


def _suggest_documents_by_ext(
    doc_paths: list[str],
    saved: bool,
) -> tuple[bool, str]:
    if not doc_paths:
        return saved, "No documents in this scan — leaving the documents-by-extension option as saved."
    signals = _path_signals(doc_paths)
    total = max(1, signals["total"])
    if signals["ext_folder"] / total >= 0.2:
        return True, "Document folders already use extension names — keep subfolders per type."
    if signals["shallow"] / total >= 0.6 and signals["ext_folder"] / total < 0.08:
        return False, "Documents look flat — turn off extension subfolders."
    return saved, "Keeping your last ‘documents by extension’ preference."


def suggest_organise_options(
    files: list[FileInfo],
    saved_settings: Optional[dict[str, Any]] = None,
    sample_limit: int = 200,
) -> OrganiseSuggestion:
    """
    Build an offline suggestion from scan files + optional saved settings.

    Uses existing ``recommended_layout_ids`` for layout choice, then path
    heuristics for media date depth and per-category modes.
    """
    saved = dict(saved_settings or {})
    counts = category_counts(files)
    ranked = recommended_layout_ids(files)
    layout_ids = normalize_layout_ids([ranked[0]] if ranked else ["type_date"])

    layout = get_layout(layout_ids[0])
    if not counts:
        layout_reason = "No files scanned yet — defaulting to Type + date."
    else:
        parts = [f"{cat}: {n}" for cat, n in sorted(counts.items(), key=lambda x: -x[1])]
        layout_reason = (
            f"Best fit for this scan ({', '.join(parts[:4])}"
            f"{', …' if len(parts) > 4 else ''}): {layout.name}."
        )

    # Honour a previously accepted multi-layout only when it still includes the top pick
    saved_layouts = normalize_layout_ids(
        [str(x) for x in (saved.get("layout_ids") or []) if str(x).strip()]
    )
    if (
        len(saved_layouts) > 1
        and "custom" not in saved_layouts
        and layout_ids[0] in saved_layouts
    ):
        # Keep combine order, but ensure top recommended stays first among ticks
        layout_ids = normalize_layout_ids(
            [layout_ids[0]] + [x for x in saved_layouts if x != layout_ids[0]]
        )
        layout_reason += " Keeping your previous combined layouts that still include this pick."

    all_paths = _sample_paths(files, limit=sample_limit)
    signals = _path_signals(all_paths)
    saved_depth = normalize_media_date_depth(saved.get("media_date_depth", "year_month"))
    media_date_depth, media_date_reason = _suggest_media_date_depth(
        signals, counts, saved_depth
    )

    # Per-category samples
    by_cat: dict[str, list[FileInfo]] = {c: [] for c in ALL_CATEGORIES}
    for info in files:
        if info.category in by_cat:
            by_cat[info.category].append(info)

    category_subfolders: dict[str, str] = {}
    category_reasons: dict[str, str] = {}
    for cat in ALL_CATEGORIES:
        cat_files = by_cat.get(cat) or []
        if not cat_files:
            category_subfolders[cat] = "layout_default"
            continue
        cat_paths = _sample_paths(cat_files, limit=min(80, sample_limit))
        mode, reason = _suggest_category_mode(cat, cat_paths, media_date_depth)
        category_subfolders[cat] = mode
        if reason:
            category_reasons[cat] = reason

    # Soft-merge saved overrides when we had no strong signal
    saved_modes = normalize_category_subfolders(saved.get("category_subfolders") or {})
    for cat in ALL_CATEGORIES:
        if category_subfolders.get(cat, "layout_default") == "layout_default":
            prev = saved_modes.get(cat, "layout_default")
            if prev != "layout_default":
                category_subfolders[cat] = prev
                category_reasons[cat] = (
                    f"No strong path clue for {cat} — keeping your last override "
                    f"({CATEGORY_MODE_LABELS.get(prev, prev)})."
                )

    category_subfolders = normalize_category_subfolders(category_subfolders)

    saved_docs_ext = bool(saved.get("documents_by_ext", True))
    doc_paths = _sample_paths(by_cat.get("Documents") or [], limit=80)
    documents_by_ext, documents_by_ext_reason = _suggest_documents_by_ext(
        doc_paths, saved_docs_ext
    )

    suggestion = OrganiseSuggestion(
        layout_ids=layout_ids,
        media_date_depth=media_date_depth,
        category_subfolders=category_subfolders,
        documents_by_ext=documents_by_ext,
        layout_reason=layout_reason,
        media_date_reason=media_date_reason,
        category_reasons=category_reasons,
        documents_by_ext_reason=documents_by_ext_reason,
        source="rules",
        source_note="",
    )
    suggestion.summary_lines = format_suggestion_summary(suggestion)
    return suggestion


def format_suggestion_summary(suggestion: OrganiseSuggestion) -> list[str]:
    """Human-readable lines for the Suggest dialog."""
    names = [get_layout(i).name for i in suggestion.layout_ids]
    layout_txt = " + ".join(names) if names else "(none)"
    depth_label = MEDIA_DATE_DEPTH_LABELS.get(
        suggestion.media_date_depth, suggestion.media_date_depth
    )
    if suggestion.source == "ollama":
        header = "Local AI suggestion (Ollama on this computer — nothing uploaded)."
    elif suggestion.source == "rules_fallback":
        header = "Offline suggestion (rules) — local AI was unavailable or invalid."
    else:
        header = "Offline suggestion (rules only — nothing is uploaded)."

    lines = [header]
    if suggestion.source_note:
        lines.append(suggestion.source_note)
    lines.extend(
        [
            "",
            f"Layout: {layout_txt}",
            f"  → {suggestion.layout_reason}",
            "",
            f"Media date folders: {depth_label}",
            f"  → {suggestion.media_date_reason}",
            "",
            f"Documents by extension: {'on' if suggestion.documents_by_ext else 'off'}",
            f"  → {suggestion.documents_by_ext_reason}",
        ]
    )
    overrides = [
        (cat, mode)
        for cat, mode in suggestion.category_subfolders.items()
        if mode and mode != "layout_default"
    ]
    lines.append("")
    if overrides:
        lines.append("Per-category folder overrides:")
        for cat, mode in overrides:
            label = CATEGORY_MODE_LABELS.get(mode, mode)
            reason = suggestion.category_reasons.get(cat, "")
            lines.append(f"  • {cat}: {label}")
            if reason:
                lines.append(f"      → {reason}")
    else:
        lines.append("Per-category folders: follow each layout (no overrides).")
        for cat, reason in suggestion.category_reasons.items():
            if reason:
                lines.append(f"  • {cat}: {reason}")

    lines.extend(
        [
            "",
            "Apply fills the Organise controls only.",
            "You still need Preview plan → Apply organise (nothing moves yet).",
        ]
    )
    return lines


def suggestion_to_settings_patch(suggestion: OrganiseSuggestion) -> dict[str, Any]:
    """Keys suitable for merging into app_settings / _collect_settings."""
    return {
        "layout_ids": list(suggestion.layout_ids),
        "media_date_depth": suggestion.media_date_depth,
        "category_subfolders": dict(suggestion.category_subfolders),
        "documents_by_ext": bool(suggestion.documents_by_ext),
    }


# ---------------------------------------------------------------------------
# Stage B — optional local Ollama
# ---------------------------------------------------------------------------


def _ollama_base(host: Optional[str] = None) -> str:
    base = (host or OLLAMA_DEFAULT_HOST).strip().rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "http://" + base
    return base


def _http_json(
    url: str,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 3.0,
) -> Any:
    """GET (body=None) or POST JSON to a local URL."""
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if body is not None:
        method = "POST"
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def ollama_reachable(host: Optional[str] = None, timeout: float = 1.5) -> bool:
    """True when a local Ollama server answers on /api/tags."""
    try:
        payload = _http_json(f"{_ollama_base(host)}/api/tags", timeout=timeout)
        return isinstance(payload, dict) and "models" in payload
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return False


def list_ollama_models(host: Optional[str] = None, timeout: float = 2.0) -> list[str]:
    """Return installed Ollama model names (may be empty)."""
    try:
        payload = _http_json(f"{_ollama_base(host)}/api/tags", timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
    return names


def pick_ollama_model(
    host: Optional[str] = None,
    preferred: Optional[str] = None,
) -> str:
    """
    Choose a model name: explicit preferred if installed, else a known-good
    name, else the first installed model. Raises if none available.
    """
    names = list_ollama_models(host=host)
    if not names:
        raise RuntimeError("Ollama has no models installed.")
    if preferred:
        pref = preferred.strip()
        for name in names:
            if name == pref or name.startswith(pref + ":"):
                return name
    lower_map = {n.lower(): n for n in names}
    for hint in _OLLAMA_MODEL_PREFERENCE:
        for key, original in lower_map.items():
            if key == hint or key.startswith(hint + ":"):
                return original
    return names[0]


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output (allows ``` fences)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model response.")
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop first fence line and optional trailing fence
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model response.")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object.")
    return data


def _short_reason(value: object, fallback: str, max_len: int = 180) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def validate_suggestion_payload(
    data: dict[str, Any],
    *,
    source: str = "ollama",
    source_note: str = "",
) -> OrganiseSuggestion:
    """
    Strictly validate a dict into OrganiseSuggestion.
    Raises ValueError when required fields are missing or illegal.
    """
    if not isinstance(data, dict):
        raise ValueError("Suggestion must be a JSON object.")

    raw_layouts = data.get("layout_ids", data.get("layout_id"))
    if isinstance(raw_layouts, str):
        raw_list = [raw_layouts]
    elif isinstance(raw_layouts, list):
        raw_list = [str(x).strip() for x in raw_layouts if str(x).strip()]
    else:
        raise ValueError("layout_ids must be a list of known layout ids.")
    if not raw_list:
        raise ValueError("layout_ids must not be empty.")
    for lid in raw_list:
        if lid not in _AI_LAYOUT_IDS:
            raise ValueError(f"Unknown or disallowed layout id: {lid!r}")
    layout_ids = normalize_layout_ids(raw_list)
    if "custom" in layout_ids:
        raise ValueError("layout id 'custom' is not allowed from AI.")

    depth = str(data.get("media_date_depth", "")).strip().lower()
    if depth not in MEDIA_DATE_DEPTHS:
        raise ValueError(
            f"media_date_depth must be one of {MEDIA_DATE_DEPTHS}, got {depth!r}"
        )

    if "documents_by_ext" not in data:
        raise ValueError("documents_by_ext is required.")
    docs_flag = data.get("documents_by_ext")
    if not isinstance(docs_flag, bool):
        # Accept simple strings only
        if isinstance(docs_flag, str) and docs_flag.strip().lower() in ("true", "false"):
            docs_flag = docs_flag.strip().lower() == "true"
        else:
            raise ValueError("documents_by_ext must be a boolean.")

    raw_modes = data.get("category_subfolders", {})
    if raw_modes is None:
        raw_modes = {}
    if not isinstance(raw_modes, dict):
        raise ValueError("category_subfolders must be an object.")
    cleaned_modes: dict[str, str] = {}
    for key, value in raw_modes.items():
        cat = str(key).strip()
        if cat not in ALL_CATEGORIES:
            raise ValueError(f"Unknown category in category_subfolders: {cat!r}")
        mode = str(value or "").strip()
        if mode not in CATEGORY_SUBFOLDER_MODES:
            raise ValueError(f"Invalid mode for {cat}: {mode!r}")
        cleaned_modes[cat] = mode
    category_subfolders = normalize_category_subfolders(cleaned_modes)

    raw_cat_reasons = data.get("category_reasons") or {}
    if raw_cat_reasons and not isinstance(raw_cat_reasons, dict):
        raise ValueError("category_reasons must be an object.")
    category_reasons: dict[str, str] = {}
    if isinstance(raw_cat_reasons, dict):
        for key, value in raw_cat_reasons.items():
            cat = str(key).strip()
            if cat in ALL_CATEGORIES and str(value or "").strip():
                category_reasons[cat] = _short_reason(value, "")

    suggestion = OrganiseSuggestion(
        layout_ids=layout_ids,
        media_date_depth=depth,
        category_subfolders=category_subfolders,
        documents_by_ext=bool(docs_flag),
        layout_reason=_short_reason(
            data.get("layout_reason"), "Local AI chose this layout for your scan."
        ),
        media_date_reason=_short_reason(
            data.get("media_date_reason"),
            f"Local AI set media date folders to {MEDIA_DATE_DEPTH_LABELS.get(depth, depth)}.",
        ),
        category_reasons=category_reasons,
        documents_by_ext_reason=_short_reason(
            data.get("documents_by_ext_reason"),
            "Local AI chose documents-by-extension from your sample names.",
        ),
        source=source,
        source_note=source_note,
    )
    suggestion.summary_lines = format_suggestion_summary(suggestion)
    return suggestion


def build_ollama_prompt(
    files: list[FileInfo],
    saved_settings: Optional[dict[str, Any]] = None,
    basename_limit: int = 40,
) -> str:
    """
    Small local-only prompt: category counts + sample basenames.
    No file contents, no upload destinations.
    """
    counts = category_counts(files)
    basenames = _sample_basenames(files, limit=basename_limit)
    saved = dict(saved_settings or {})
    layout_options = [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in LAYOUT_PRESETS
        if p.id != "custom"
    ]
    payload = {
        "category_counts": counts,
        "sample_basenames": basenames,
        "last_saved": {
            "layout_ids": list(saved.get("layout_ids") or []),
            "media_date_depth": saved.get("media_date_depth", "year_month"),
            "documents_by_ext": bool(saved.get("documents_by_ext", True)),
            "category_subfolders": saved.get("category_subfolders") or {},
        },
        "allowed_layout_ids": sorted(_AI_LAYOUT_IDS),
        "allowed_media_date_depth": list(MEDIA_DATE_DEPTHS),
        "allowed_category_modes": list(CATEGORY_SUBFOLDER_MODES),
        "categories": list(ALL_CATEGORIES),
        "layout_options": layout_options,
    }
    return (
        "You help organise personal archive folders on the user's own computer.\n"
        "Reply with ONE JSON object only (no markdown, no extra text).\n"
        "Required keys:\n"
        "  layout_ids: array of 1+ ids from allowed_layout_ids\n"
        "  media_date_depth: one of allowed_media_date_depth\n"
        "  documents_by_ext: boolean\n"
        "  category_subfolders: object mapping categories to allowed_category_modes\n"
        "  layout_reason, media_date_reason, documents_by_ext_reason: short strings\n"
        "  category_reasons: optional object of short strings\n"
        "Use only the scan summary below (counts + file names). Do not invent paths.\n"
        "Prefer simple, practical folder setups for mixed personal archives.\n\n"
        f"SCAN_SUMMARY_JSON:\n{json.dumps(payload, indent=2)}\n"
    )


def suggest_via_ollama(
    files: list[FileInfo],
    saved_settings: Optional[dict[str, Any]] = None,
    host: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 90.0,
) -> OrganiseSuggestion:
    """
    Ask local Ollama for a suggestion. Raises on unreachable server,
    empty models, bad JSON, or failed validation.
    """
    if not ollama_reachable(host=host):
        raise RuntimeError("Ollama is not reachable on this computer.")
    chosen = pick_ollama_model(host=host, preferred=model)
    prompt = build_ollama_prompt(files, saved_settings=saved_settings)
    body = {
        "model": chosen,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    try:
        payload = _http_json(
            f"{_ollama_base(host)}/api/generate",
            body=body,
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Ollama HTTP error: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Unexpected Ollama response shape.")
    response_text = str(payload.get("response") or "")
    data = _extract_json_object(response_text)
    return validate_suggestion_payload(
        data,
        source="ollama",
        source_note=f"Model: {chosen} (local).",
    )


def suggest_organise_options_auto(
    files: list[FileInfo],
    saved_settings: Optional[dict[str, Any]] = None,
    *,
    use_ollama: bool = False,
    host: Optional[str] = None,
    model: Optional[str] = None,
) -> OrganiseSuggestion:
    """
    Main entry for the UI: rules by default; optional Ollama with strict
    fallback to Stage A rules on any failure.
    """
    if not use_ollama:
        return suggest_organise_options(files, saved_settings=saved_settings)
    try:
        return suggest_via_ollama(
            files,
            saved_settings=saved_settings,
            host=host,
            model=model,
        )
    except Exception as exc:
        suggestion = suggest_organise_options(files, saved_settings=saved_settings)
        suggestion.source = "rules_fallback"
        suggestion.source_note = (
            f"Local AI skipped ({type(exc).__name__}: {exc}). Used offline rules instead."
        )
        suggestion.summary_lines = format_suggestion_summary(suggestion)
        return suggestion
