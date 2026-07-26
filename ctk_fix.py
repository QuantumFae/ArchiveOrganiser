"""
Fix a CustomTkinter bug on Linux.

When you scroll the mouse wheel, Tk sometimes passes a widget *path string*
(like ".!ctkframe3") instead of a real widget object. CustomTkinter then
crashes with: AttributeError: 'str' object has no attribute 'master'

This patch turns that string back into a widget before checking scroll targets.
"""

import customtkinter as ctk


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
        # Still not a real widget somehow — ignore this scroll event safely
        return False


def apply() -> None:
    """Install the scroll fix. Call once before opening the window."""
    ctk.CTkScrollableFrame._check_if_valid_scroll = _safe_check_if_valid_scroll
