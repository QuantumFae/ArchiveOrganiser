"""
Archive Organiser look & feel + Linux scroll fix.

Light-first professional palette (cool neutrals, one restrained teal accent).
Not a purple-gradient or cream/terracotta marketing theme — this is a desktop tool.
"""

from __future__ import annotations

import customtkinter as ctk

# --- Color tokens ---
PRIMARY = "#1f6f5b"
PRIMARY_HOVER = "#185a4a"
ACCENT = "#2c6e8a"
WARNING = "#9a5b12"
DANGER = "#8b1e1e"
DANGER_HOVER = "#6e1515"
MUTED = ("gray40", "gray65")
SURFACE = ("gray92", "gray17")
SELECT = ("#3a7ebf", "#1f538d")
LIST_BTN = ("gray78", "gray32")
LIST_BTN_TEXT = ("gray10", "gray90")

# PanedWindow sash background for light / dark
PANED_BG_LIGHT = "#d0d4d8"
PANED_BG_DARK = "#3a3a3a"


def paned_bg() -> str:
    mode = ctk.get_appearance_mode()
    if mode == "Dark":
        return PANED_BG_DARK
    return PANED_BG_LIGHT


def apply_theme() -> None:
    """Install scroll fix and set a clean default CTk theme."""
    _apply_scroll_fix()
    ctk.set_default_color_theme("green")


def primary_button(parent, **kwargs):
    kwargs.setdefault("fg_color", PRIMARY)
    kwargs.setdefault("hover_color", PRIMARY_HOVER)
    return ctk.CTkButton(parent, **kwargs)


def danger_button(parent, **kwargs):
    kwargs.setdefault("fg_color", DANGER)
    kwargs.setdefault("hover_color", DANGER_HOVER)
    return ctk.CTkButton(parent, **kwargs)


# --- Linux scroll fix (was ctk_fix.py) ---
_original_check = ctk.CTkScrollableFrame._check_if_valid_scroll


def _safe_check_if_valid_scroll(self, widget):
    if isinstance(widget, str):
        try:
            widget = self._parent_canvas.nametowidget(widget)
        except Exception:
            return False
    try:
        return _original_check(self, widget)
    except AttributeError:
        return False


def _apply_scroll_fix() -> None:
    ctk.CTkScrollableFrame._check_if_valid_scroll = _safe_check_if_valid_scroll


def apply() -> None:
    """Back-compat alias used by main.py."""
    apply_theme()
