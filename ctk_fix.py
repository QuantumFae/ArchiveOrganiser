"""
Fix a CustomTkinter bug on Linux.

Kept for compatibility — theme + scroll fix live in ctk_theme.py.
"""

from ctk_theme import apply_theme


def apply() -> None:
    apply_theme()
