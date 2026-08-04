"""Make on-screen text easy to select and copy (CustomTkinter / Tk)."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Optional


def _ctrl_held(event: tk.Event) -> bool:
    # 0x4 = Control, 0x8 = Mod1 (often Alt), 0x20000 / 0x10 = Command/Meta on some platforms
    return bool(event.state & (0x4 | 0x8 | 0x10 | 0x20000))


def copy_text_to_clipboard(widget: Any, text: str) -> bool:
    """Put text on the clipboard. Returns True if anything was copied."""
    if not text:
        return False
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
        # Keep clipboard after app focus changes (X11)
        widget.update_idletasks()
        return True
    except tk.TclError:
        return False


def make_textbox_readonly_copyable(box: Any) -> None:
    """
    Leave a CTkTextbox / Text editable for selection + Ctrl+C / Ctrl+A,
    but block typing, paste, and cut.
    """
    if getattr(box, "_readonly_copyable", False):
        return
    box._readonly_copyable = True
    try:
        box.configure(state="normal")
    except Exception:
        pass

    def on_key(event: tk.Event):
        if _ctrl_held(event) and event.keysym.lower() in ("a", "c", "insert"):
            return None
        if event.keysym in (
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Meta_L",
            "Meta_R",
            "Caps_Lock",
            "Tab",
            "ISO_Left_Tab",
            "Escape",
            "Return",  # allow Enter for focus move; still won't insert if we break... actually Return inserts newline
        ):
            # Block Return/Enter from inserting a newline in readonly boxes
            if event.keysym == "Return":
                return "break"
            return None
        return "break"

    box.bind("<Key>", on_key, add="+")
    box.bind("<<Paste>>", lambda _e: "break", add="+")
    box.bind("<<Cut>>", lambda _e: "break", add="+")
    _bind_copy_menu(box, lambda: _textbox_selected_or_all(box))


def _textbox_selected_or_all(box: Any) -> str:
    try:
        selected = box.get("sel.first", "sel.last")
        if selected:
            return selected
    except tk.TclError:
        pass
    try:
        return box.get("1.0", "end-1c")
    except Exception:
        return ""


def make_label_copyable(
    label: Any,
    *,
    on_copied: Optional[Callable[[str], None]] = None,
) -> None:
    """Right-click or double-click a label/button to copy its text."""
    if getattr(label, "_text_copyable", False):
        return
    label._text_copyable = True

    def current_text() -> str:
        try:
            return str(label.cget("text") or "")
        except Exception:
            return ""

    def do_copy(_event=None):
        text = current_text().strip()
        if copy_text_to_clipboard(label, text):
            if on_copied:
                on_copied(text)
        return "break"

    _bind_copy_menu(label, current_text, on_copied=on_copied)
    # Double-click copies labels only (buttons already use click to run a command)
    if label.__class__.__name__ == "CTkLabel":
        label.bind("<Double-Button-1>", do_copy, add="+")


def _bind_copy_menu(
    widget: Any,
    get_text: Callable[[], str],
    *,
    on_copied: Optional[Callable[[str], None]] = None,
) -> None:
    menu = tk.Menu(widget, tearoff=0)

    def copy_now() -> None:
        text = (get_text() or "").strip()
        if copy_text_to_clipboard(widget, text) and on_copied:
            on_copied(text)

    menu.add_command(label="Copy", command=copy_now)

    def popup(event: tk.Event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    widget.bind("<Button-3>", popup, add="+")
    # Button-2 is middle click on Linux; some trackpads use it for secondary
    widget.bind("<Button-2>", popup, add="+")
    # macOS often maps secondary click to Control + click
    widget.bind("<Control-Button-1>", popup, add="+")


def enable_copyable_text(
    root: Any,
    *,
    editable_textboxes: Optional[set[Any]] = None,
    on_copied: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Walk a widget tree and make labels/buttons/textboxes copy-friendly.
    Pass widgets that must stay fully editable in editable_textboxes.
    """
    skip_edit = editable_textboxes or set()

    def walk(widget: Any) -> None:
        name = widget.__class__.__name__
        if name == "CTkTextbox":
            if widget not in skip_edit and not getattr(widget, "_allow_edit", False):
                make_textbox_readonly_copyable(widget)
        elif name in ("CTkLabel", "CTkButton", "CTkCheckBox", "CTkRadioButton"):
            # Skip empty image-only labels
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                text = ""
            if text.strip():
                make_label_copyable(widget, on_copied=on_copied)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            walk(child)

    walk(root)
