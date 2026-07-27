"""
Friendly folder / drive picker.

Looks more like a normal file manager: places sidebar, folder list,
path bar, and simple navigation (Up / Home).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import customtkinter as ctk


def _safe_iter_dirs(path: Path) -> list[Path]:
    """List subfolders the user is allowed to see."""
    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name
                # Hide dotfolders (same idea as file managers with “hidden files” off)
                if name.startswith("."):
                    continue
                entries.append(Path(entry.path))
        return sorted(entries, key=lambda p: p.name.lower())
    except OSError:
        return []


def _mounted_drives() -> list[tuple[str, Path]]:
    """Find likely external / removable mounts (HDD, USB, SD)."""
    home = Path.home()
    candidates: list[Path] = []
    search_roots = [
        Path("/run/media") / home.name,
        Path("/media") / home.name,
        Path("/media"),
        Path("/mnt"),
    ]
    seen: set[Path] = set()
    results: list[tuple[str, Path]] = []

    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir():
                    continue
                try:
                    resolved = child.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                # Skip the /media/<user> container itself when listing /media
                if child == Path("/media") / home.name:
                    continue
                seen.add(resolved)
                results.append((f"Drive: {child.name}", child))
        except OSError:
            continue

    # Also read /proc/mounts for anything under /media, /mnt, /run/media
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 2:
                    continue
                mount = Path(parts[1])
                mount_s = str(mount)
                if not (
                    mount_s.startswith("/media/")
                    or mount_s.startswith("/mnt/")
                    or mount_s.startswith("/run/media/")
                ):
                    continue
                if mount_s in {"/media", "/mnt", "/run/media"}:
                    continue
                try:
                    resolved = mount.resolve()
                except OSError:
                    continue
                if resolved in seen or not mount.is_dir():
                    continue
                seen.add(resolved)
                results.append((f"Drive: {mount.name}", mount))
    except OSError:
        pass

    return results


def _default_places() -> list[tuple[str, Path]]:
    home = Path.home()
    places: list[tuple[str, Path]] = [("Home", home)]
    for label, rel in [
        ("Desktop", "Desktop"),
        ("Documents", "Documents"),
        ("Downloads", "Downloads"),
        ("Pictures", "Pictures"),
        ("Videos", "Videos"),
    ]:
        path = home / rel
        if path.is_dir():
            places.append((label, path))
    places.append(("Computer (/)", Path("/")))
    places.extend(_mounted_drives())
    return places


class FolderPickerDialog(ctk.CTkToplevel):
    """Modal folder browser with sidebar places and a main folder list."""

    def __init__(
        self,
        parent,
        title: str = "Choose folder or drive",
        initialdir: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("960x600")
        self.minsize(720, 480)
        self.transient(parent)
        self.grab_set()
        self.result: Optional[str] = None

        start = Path(initialdir).expanduser() if initialdir else Path.home()
        if not start.is_dir():
            start = Path.home()
        self.current = start.resolve()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- top path / nav ---
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        nav.grid_columnconfigure(2, weight=1)

        ctk.CTkButton(nav, text="Up", width=70, command=self.go_up).grid(
            row=0, column=0, padx=(0, 6)
        )
        ctk.CTkButton(nav, text="Home", width=70, command=self.go_home).grid(
            row=0, column=1, padx=(0, 6)
        )
        self.path_entry = ctk.CTkEntry(nav)
        self.path_entry.grid(row=0, column=2, sticky="ew", padx=(0, 6))
        self.path_entry.bind("<Return>", lambda _e: self.go_path_entry())
        ctk.CTkButton(nav, text="Go", width=60, command=self.go_path_entry).grid(
            row=0, column=3
        )

        # --- left places ---
        side = ctk.CTkFrame(self, width=220)
        side.grid(row=1, column=0, sticky="nsw", padx=(12, 6), pady=6)
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(side, text="Places & drives", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        self.places_frame = ctk.CTkScrollableFrame(side, width=200)
        self.places_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.places_frame.grid_columnconfigure(0, weight=1)
        self._build_places()

        # --- main folder list ---
        main = ctk.CTkFrame(self)
        main.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            main,
            text="Folders (double-click to open)",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self.folder_frame = ctk.CTkScrollableFrame(main)
        self.folder_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.folder_frame.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(self, text="", anchor="w")
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 4))

        # --- bottom buttons ---
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 12))
        ctk.CTkButton(bottom, text="Cancel", width=120, height=36, command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(
            bottom,
            text="Select this folder",
            width=200,
            height=36,
            fg_color="#1f6f5b",
            hover_color="#185a4a",
            command=self._select,
        ).pack(side="right")
        ctk.CTkButton(
            bottom,
            text="Refresh drives",
            width=130,
            height=36,
            command=self._refresh_places,
        ).pack(side="left")

        self._selected_child: Optional[Path] = None
        self._folder_buttons: list[ctk.CTkButton] = []
        self.refresh()

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(50, self._focus_and_lift)

    def _focus_and_lift(self) -> None:
        self.lift()
        self.focus_force()
        self.path_entry.focus_set()

    def _build_places(self) -> None:
        for child in self.places_frame.winfo_children():
            child.destroy()
        for index, (label, path) in enumerate(_default_places()):
            btn = ctk.CTkButton(
                self.places_frame,
                text=label,
                anchor="w",
                height=36,
                fg_color=("gray80", "gray30"),
                text_color=("gray10", "gray90"),
                command=lambda p=path: self.go_to(p),
            )
            btn.pack(fill="x", padx=2, pady=2)

    def _refresh_places(self) -> None:
        self._build_places()
        self.status.configure(text="Places & drives refreshed.")

    def refresh(self) -> None:
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, str(self.current))
        self._selected_child = None

        for child in self.folder_frame.winfo_children():
            child.destroy()
        self._folder_buttons.clear()

        # Parent shortcut row
        if self.current.parent != self.current:
            parent_btn = ctk.CTkButton(
                self.folder_frame,
                text="..  (parent folder)",
                anchor="w",
                height=34,
                fg_color=("gray70", "gray25"),
                command=self.go_up,
            )
            parent_btn.pack(fill="x", padx=2, pady=(2, 6))

        dirs = _safe_iter_dirs(self.current)
        if not dirs:
            ctk.CTkLabel(
                self.folder_frame,
                text="(no subfolders here — you can still Select this folder)",
                anchor="w",
                justify="left",
                wraplength=480,
            ).pack(fill="x", padx=6, pady=8)
            self.status.configure(text=f"Current: {self.current}")
            return

        for path in dirs:
            btn = ctk.CTkButton(
                self.folder_frame,
                text=path.name,
                anchor="w",
                height=34,
                fg_color=("gray85", "gray35"),
                text_color=("gray10", "gray90"),
            )
            btn.configure(command=lambda p=path, b=btn: self._on_folder_click(p, b))
            btn.pack(fill="x", padx=2, pady=2)
            btn.bind("<Double-Button-1>", lambda _e, p=path: self.go_to(p))
            self._folder_buttons.append(btn)

        self.status.configure(text=f"{len(dirs)} folder(s) in {self.current}")

    def _on_folder_click(self, path: Path, button: Optional[ctk.CTkButton] = None) -> None:
        self._selected_child = path
        for btn in self._folder_buttons:
            btn.configure(fg_color=("gray85", "gray35"), text_color=("gray10", "gray90"))
        if button is not None:
            button.configure(fg_color=("#3a7ebf", "#1f538d"), text_color="white")
        self.status.configure(text=f"Selected: {path}")

    def go_to(self, path: Path) -> None:
        try:
            path = path.expanduser().resolve()
        except OSError:
            self.status.configure(text=f"Cannot open: {path}")
            return
        if not path.is_dir():
            self.status.configure(text=f"Not a folder: {path}")
            return
        self.current = path
        self.refresh()

    def go_up(self) -> None:
        parent = self.current.parent
        if parent != self.current:
            self.go_to(parent)

    def go_home(self) -> None:
        self.go_to(Path.home())

    def go_path_entry(self) -> None:
        typed = self.path_entry.get().strip()
        if not typed:
            return
        self.go_to(Path(typed))

    def _select(self) -> None:
        # If user highlighted a child folder, select that; otherwise current folder
        chosen = self._selected_child if self._selected_child is not None else self.current
        if not chosen.is_dir():
            self.status.configure(text=f"Not a folder: {chosen}")
            return
        self.result = str(chosen)
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


def ask_folder(
    parent,
    title: str = "Choose folder or drive",
    initialdir: Optional[str] = None,
) -> Optional[str]:
    """Open the friendly folder picker and return a path string, or None."""
    dialog = FolderPickerDialog(parent, title=title, initialdir=initialdir)
    parent.wait_window(dialog)
    return dialog.result
