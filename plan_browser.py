"""
File-manager style browser for an organise dry-run plan.

Shows the planned destination tree (folders + files) like Dolphin / Files:
sidebar of top folders, main list to navigate, Up / path bar.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from organiser import OrganisePlan, OrganisePlanItem
from copyable_text import enable_copyable_text


class PlanBrowserWindow(ctk.CTkToplevel):
    """Navigate a virtual destination tree built from an OrganisePlan."""

    def __init__(self, master, plan: OrganisePlan, dest_root: str, title: str = "Dry-run browser"):
        super().__init__(master)
        self.title(title)
        self.geometry("900x560")
        self.minsize(640, 400)

        self.plan = plan
        self.dest_root = Path(dest_root).expanduser()
        self.virtual_root = Path("__plan__")
        self.cwd = self.virtual_root

        # Build virtual tree: folder -> list of (name, is_dir, item|None)
        self.children: dict[Path, list[tuple[str, bool, Optional[OrganisePlanItem]]]] = {}
        self._build_tree()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Path / navigation bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        bar.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(bar, text="Up", width=60, command=self.go_up).grid(row=0, column=0, padx=(0, 4))
        ctk.CTkButton(bar, text="Home", width=70, command=self.go_home).grid(row=0, column=1, padx=(0, 4))
        self.path_var = tk.StringVar(value=str(self.dest_root))
        self.path_entry = ctk.CTkEntry(bar, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=2, sticky="ew")
        self.path_entry.bind("<Return>", lambda _e: self.go_path())

        # Split: sidebar places + main listing
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6, bg="#2b2b2b")
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        side = ctk.CTkFrame(paned, width=180)
        main = ctk.CTkFrame(paned)
        paned.add(side, minsize=140)
        paned.add(main, minsize=300)

        ctk.CTkLabel(side, text="Top folders", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(8, 4)
        )
        self.side_list = ctk.CTkScrollableFrame(side)
        self.side_list.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(main, text="Contents (planned)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=8, pady=(8, 4)
        )
        self.main_list = ctk.CTkScrollableFrame(main)
        self.main_list.pack(fill="both", expand=True, padx=4, pady=4)

        hint = ctk.CTkLabel(
            self,
            text="This is a preview only — no files are moved. Double-click a folder to open it.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        )
        hint.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))

        self._fill_sidebar()
        self.refresh_listing()
        enable_copyable_text(self)

    def _rel_from_dest(self, dest: Path) -> Path:
        try:
            return dest.relative_to(self.dest_root.resolve())
        except Exception:
            try:
                return dest.relative_to(self.dest_root)
            except Exception:
                return Path(dest.name)

    def _build_tree(self) -> None:
        self.children = {self.virtual_root: []}
        seen_dirs: set[Path] = {self.virtual_root}

        def ensure_dir(folder: Path) -> None:
            if folder in seen_dirs:
                return
            seen_dirs.add(folder)
            parent = folder.parent
            ensure_dir(parent)
            self.children.setdefault(parent, [])
            name = folder.name
            if not any(n == name and is_dir for n, is_dir, _ in self.children[parent]):
                self.children[parent].append((name, True, None))
            self.children.setdefault(folder, [])

        ensure_dir(self.virtual_root)
        for item in self.plan.items:
            rel = self._rel_from_dest(item.destination)
            folder = self.virtual_root / rel.parent
            ensure_dir(folder)
            self.children.setdefault(folder, [])
            self.children[folder].append((rel.name, False, item))

        # Sort each listing: folders first, then names
        for key in self.children:
            self.children[key].sort(key=lambda t: (not t[1], t[0].lower()))

    def _fill_sidebar(self) -> None:
        for child in self.side_list.winfo_children():
            child.destroy()
        top = self.children.get(self.virtual_root, [])
        ctk.CTkButton(
            self.side_list,
            text=f"📁 {self.dest_root.name or 'Destination'}",
            anchor="w",
            command=self.go_home,
        ).pack(fill="x", padx=2, pady=2)
        for name, is_dir, _ in top:
            if not is_dir:
                continue
            target = self.virtual_root / name
            ctk.CTkButton(
                self.side_list,
                text=f"📁 {name}",
                anchor="w",
                command=lambda p=target: self.open_folder(p),
            ).pack(fill="x", padx=2, pady=1)

    def _display_path(self) -> str:
        if self.cwd == self.virtual_root:
            return str(self.dest_root)
        rel = self.cwd.relative_to(self.virtual_root)
        return str(self.dest_root / rel)

    def refresh_listing(self) -> None:
        self.path_var.set(self._display_path())
        for child in self.main_list.winfo_children():
            child.destroy()
        entries = self.children.get(self.cwd, [])
        if not entries:
            ctk.CTkLabel(self.main_list, text="(empty folder in this plan)").pack(
                anchor="w", padx=8, pady=8
            )
            enable_copyable_text(self.main_list)
            return
        for name, is_dir, item in entries:
            if is_dir:
                target = self.cwd / name
                btn = ctk.CTkButton(
                    self.main_list,
                    text=f"📁  {name}",
                    anchor="w",
                    fg_color="transparent",
                    text_color=("gray10", "gray90"),
                    hover_color=("gray80", "gray30"),
                    command=lambda p=target: self.open_folder(p),
                )
                btn.pack(fill="x", padx=4, pady=1)
            else:
                src = item.source if item else "?"
                row = ctk.CTkFrame(self.main_list, fg_color="transparent")
                row.pack(fill="x", padx=4, pady=1)
                ctk.CTkLabel(row, text=f"📄  {name}", anchor="w").pack(side="left")
                ctk.CTkLabel(
                    row,
                    text=f"← {src}",
                    anchor="e",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray65"),
                ).pack(side="right", padx=4)
        enable_copyable_text(self.main_list)

    def open_folder(self, path: Path) -> None:
        self.cwd = path
        self.refresh_listing()

    def go_home(self) -> None:
        self.cwd = self.virtual_root
        self.refresh_listing()

    def go_up(self) -> None:
        if self.cwd == self.virtual_root:
            return
        self.cwd = self.cwd.parent
        self.refresh_listing()

    def go_path(self) -> None:
        typed = self.path_var.get().strip()
        try:
            typed_path = Path(typed).expanduser()
            dest = self.dest_root.expanduser()
            if typed_path == dest or str(typed_path) == str(dest):
                self.go_home()
                return
            rel = typed_path.relative_to(dest)
            target = self.virtual_root / rel
            if target in self.children or any(
                target == self.virtual_root / Path(*p.parts[1:i])
                for p in self.children
                for i in range(1, len(p.parts) + 1)
            ):
                if target in self.children:
                    self.open_folder(target)
                    return
            # Best effort: walk parts
            cur = self.virtual_root
            for part in rel.parts:
                nxt = cur / part
                if nxt not in self.children:
                    break
                cur = nxt
            self.open_folder(cur)
        except Exception:
            self.refresh_listing()
