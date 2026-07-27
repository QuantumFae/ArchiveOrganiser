"""
Archive Organiser – main window.

Private by design: everything runs on your computer.
No files are uploaded anywhere.
"""

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from duplicates import (
    DuplicateGroup,
    DuplicateReport,
    DuplicateSearchOptions,
    find_duplicate_matches,
)
from folder_picker import ask_folder
from helpers import format_duration, open_containing_folder
import os
from models import FileInfo, ScanResult
from best_practices import build_inventory_text, organisation_advice
from custom_structure import DEFAULT_CUSTOM_TEMPLATE
from ctk_theme import (
    DANGER,
    DANGER_HOVER,
    LIST_BTN,
    LIST_BTN_TEXT,
    MUTED,
    SELECT,
    WARNING,
    apply_theme,
    paned_bg,
    primary_button,
)
from organiser import (
    ALL_CATEGORIES,
    LAYOUT_PRESETS,
    OrganiseOptions,
    apply_organise_plan,
    build_organise_plan,
    destination_conflicts_with_sources,
    get_layout,
    layout_combo_label,
    layout_tree_text,
    recommended_layout_ids,
)
from plan_browser import PlanBrowserWindow
from preview import load_preview_for_info
from quarantine import format_bytes, move_to_quarantine, permanently_delete, quarantine_root
from scanner import ScanOptions, scan_paths


APP_TITLE = "Archive Organiser"
APP_SIZE = "1280x800"


class ArchiveOrganiserApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(900, 600)

        apply_theme()
        ctk.set_appearance_mode("System")

        self.source_paths: list[str] = []
        self.scan_result: Optional[ScanResult] = None
        self.dup_report: Optional[DuplicateReport] = None
        self._cancel_flag = False
        self._busy = False
        self._selected_group_index: Optional[int] = None
        self._compare_check_vars: list[tk.BooleanVar] = []
        self._compare_checkboxes: list = []
        self._compare_file_infos: list[FileInfo] = []
        self._compare_image_refs: list[object] = []  # keep CTkImage alive
        self._compare_pil_images: list[object] = []
        self._compare_img_labels: list[ctk.CTkLabel] = []
        self._compare_resize_job: Optional[str] = None
        self._group_buttons: list[ctk.CTkButton] = []
        self._compare_cards_paned = None
        self._compare_vpaned = None
        self._compare_hpaned_top = None
        self._compare_hpaned_bottom = None
        self._sash_sync_lock = False
        # Remembered sash layout (kept when switching duplicate groups)
        self._compare_layout_v_frac = 0.58
        self._compare_layout_h_fracs: Optional[list[float]] = None
        self._busy_started = 0.0
        self._busy_detail = ""
        self._busy_tick_job: Optional[str] = None
        self._organise_preview_key: Optional[str] = None
        self._source_check_vars: dict[str, tk.BooleanVar] = {}
        self._group_list_shown = 0
        self._group_page_size = 80
        self._workflow_banner: Optional[ctk.CTkLabel] = None

        self._build_layout()
        self._set_status("Ready. Add folders or drives, then Scan.")

    # ---------- layout ----------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Top bar — brand + appearance
        header = ctk.CTkFrame(self, fg_color="transparent", height=28)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text=APP_TITLE,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Private · Local only · Safe quarantine",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(12, 8))
        self.appearance_var = tk.StringVar(value="System")
        ctk.CTkOptionMenu(
            header,
            values=["System", "Light", "Dark"],
            variable=self.appearance_var,
            width=100,
            height=26,
            command=self._on_appearance_change,
        ).grid(row=0, column=2, sticky="e")

        self.workflow_banner = ctk.CTkLabel(
            self,
            text="Start on Sources: add a folder or drive, then Scan now.",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.workflow_banner.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 0))

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=8, pady=(2, 2))
        self.tabs.add("Sources")
        self.tabs.add("Overview")
        self.tabs.add("Duplicates")
        self.tabs.add("Organise")
        self.tabs.add("Help")

        self._build_sources_tab()
        self._build_overview_tab()
        self._build_duplicates_tab()
        self._build_organise_tab()
        self._build_help_tab()

        footer = ctk.CTkFrame(self, height=40)
        footer.grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 6))
        footer.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(footer, text="", anchor="w", font=ctk.CTkFont(size=12))
        self.status_label.grid(row=0, column=0, sticky="ew", padx=6, pady=2)

        self.progress = ctk.CTkProgressBar(footer, mode="indeterminate", height=8)
        self.progress.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        self.progress.set(0)

        self.cancel_btn = ctk.CTkButton(
            footer, text="Cancel", width=80, height=28, command=self._request_cancel, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, rowspan=2, padx=6, pady=2)

    def _build_sources_tab(self) -> None:
        tab = self.tabs.tab("Sources")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Add external HDDs, USBs, SD cards, or folders. Tick rows to select them, then Remove selected.",
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        paned = tk.PanedWindow(tab, orient=tk.VERTICAL, sashwidth=8, sashrelief=tk.RAISED)
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

        list_frame = ctk.CTkFrame(paned)
        opts_frame = ctk.CTkFrame(paned)
        paned.add(list_frame, minsize=140)
        paned.add(opts_frame, minsize=100)
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            list_frame, text="Scan sources", font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))
        self.source_list_frame = ctk.CTkScrollableFrame(list_frame)
        self.source_list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.source_list_frame.grid_columnconfigure(0, weight=1)
        self._source_check_vars: dict[str, tk.BooleanVar] = {}
        self._source_empty_label: Optional[ctk.CTkLabel] = None

        self.include_junk_var = tk.BooleanVar(value=False)
        self.scan_zips_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts_frame,
            text="Include junk / system folders (.Trash, System Volume Information, dot-folders, …)",
            variable=self.include_junk_var,
        ).pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkCheckBox(
            opts_frame,
            text="Scan inside .zip archives (slower / more RAM on huge libraries — capped)",
            variable=self.scan_zips_var,
        ).pack(anchor="w", padx=8, pady=2)

        buttons = ctk.CTkFrame(opts_frame, fg_color="transparent")
        buttons.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(buttons, text="Add folder / drive", command=self.add_source).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            buttons, text="Remove selected", command=self.remove_selected_sources
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Select all", width=90, command=self.select_all_sources
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Clear ticks", width=90, command=self.clear_source_ticks
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Clear all", command=self.clear_sources).pack(
            side="left", padx=(0, 8)
        )
        primary_button(
            buttons, text="Scan now", command=self.start_scan
        ).pack(side="right")

        self._refresh_source_list()

    def _build_overview_tab(self) -> None:
        tab = self.tabs.tab("Overview")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        paned = tk.PanedWindow(tab, orient=tk.VERTICAL, sashwidth=8, sashrelief=tk.RAISED)
        paned.grid(row=0, column=0, sticky="nsew", padx=8, pady=4)

        top = ctk.CTkFrame(paned)
        bottom = ctk.CTkFrame(paned)
        paned.add(top, minsize=60)
        paned.add(bottom, minsize=160)
        top.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(0, weight=1)

        self.overview_summary = ctk.CTkLabel(
            top, text="No scan yet.", justify="left", anchor="w"
        )
        self.overview_summary.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        self.overview_box = ctk.CTkTextbox(bottom)
        self.overview_box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.overview_box.insert("1.0", "Scan results and a short report will appear here.")
        self.overview_box.configure(state="disabled")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        self.btn_save_report = ctk.CTkButton(row, text="Save report…", command=self.save_report)
        self.btn_save_report.pack(side="left")

        dup_opts = ctk.CTkFrame(row, fg_color="transparent")
        dup_opts.pack(side="right", padx=(0, 12))
        self.dup_exact_var = tk.BooleanVar(value=True)
        # Similar modes off by default — Exact is enough for whole-drive first pass
        self.dup_photos_var = tk.BooleanVar(value=False)
        self.dup_docs_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(dup_opts, text="Exact", variable=self.dup_exact_var, width=70).pack(
            side="left", padx=2
        )
        ctk.CTkCheckBox(
            dup_opts, text="Similar photos", variable=self.dup_photos_var, width=120
        ).pack(side="left", padx=2)
        ctk.CTkCheckBox(
            dup_opts, text="Similar docs", variable=self.dup_docs_var, width=110
        ).pack(side="left", padx=2)

        self.btn_find_dups = primary_button(
            row, text="Find duplicates", command=self.start_duplicate_search
        )
        self.btn_find_dups.pack(side="right")

    def _build_duplicates_tab(self) -> None:
        tab = self.tabs.tab("Duplicates")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self.dup_summary = ctk.CTkLabel(
            tab,
            text="1) Scan on Sources  ·  2) Find duplicates on Overview  ·  3) Compare here (← → keys = Prev/Next). Extras start selected.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.dup_summary.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 2))

        # Resizable split: drag the sash between group list and compare pane
        paned = tk.PanedWindow(
            tab, orient=tk.HORIZONTAL, sashwidth=8, sashrelief=tk.RAISED, bg=paned_bg()
        )
        paned.grid(row=1, column=0, sticky="nsew", padx=6, pady=4)
        self.dup_paned = paned

        left = ctk.CTkFrame(paned, width=240)
        right = ctk.CTkFrame(paned)
        paned.add(left, minsize=160)
        paned.add(right, minsize=320)

        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="Duplicate groups", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=6, pady=(6, 2)
        )
        self.group_list = ctk.CTkScrollableFrame(left, width=220)
        self.group_list.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.group_list.grid_columnconfigure(0, weight=1)

        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        compare_header = ctk.CTkFrame(right, fg_color="transparent")
        compare_header.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        compare_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            compare_header,
            text="Side-by-side compare  ·  one ↕ sash (Preview/Info) · one ↔ sash (columns) · layout kept between groups",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        nav = ctk.CTkFrame(compare_header, fg_color="transparent")
        nav.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(
            nav, text="← Prev", width=70, height=26, command=self.prev_duplicate_group
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            nav, text="Next →", width=70, height=26, command=self.next_duplicate_group
        ).pack(side="left", padx=2)

        self.compare_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.compare_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.compare_frame.grid_rowconfigure(0, weight=1)
        self.compare_frame.bind("<Configure>", self._on_compare_resize)

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 4))
        ctk.CTkButton(
            row, text="Select extras only", command=self.select_extras_in_group, width=130, height=28
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            row, text="Select all", command=self.select_all_in_group, width=90, height=28
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            row, text="Clear selection", command=self.clear_compare_selection, width=110, height=28
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            row,
            text="Delete all extras (every group)",
            command=self.delete_all_extras,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            height=28,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            row,
            text="Quarantine all extras (every group)",
            command=self.quarantine_extras,
            fg_color=WARNING,
            height=28,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            row,
            text="Permanently delete selected",
            command=self.delete_selected_compare_files,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            height=28,
        ).pack(side="right", padx=(6, 0))
        primary_button(
            row,
            text="Quarantine selected",
            command=self.quarantine_selected_compare_files,
            height=28,
        ).pack(side="right", padx=(6, 0))

        # ← / → move between groups when the Duplicates tab is active
        self.bind_all("<Left>", self._on_duplicates_left_key, add="+")
        self.bind_all("<Right>", self._on_duplicates_right_key, add="+")

    def _clear_group_list(self) -> None:
        for child in self.group_list.winfo_children():
            child.destroy()
        self._group_buttons.clear()

    def _clear_compare_panel(self) -> None:
        for child in self.compare_frame.winfo_children():
            child.destroy()
        self._compare_check_vars.clear()
        self._compare_checkboxes.clear()
        self._compare_file_infos.clear()
        self._compare_image_refs.clear()
        self._compare_pil_images.clear()
        self._compare_img_labels.clear()
        self._selected_group_index = None
        self._compare_cards_paned = None
        self._compare_vpaned = None
        self._compare_hpaned_top = None
        self._compare_hpaned_bottom = None

    def _remember_compare_layout(self) -> None:
        """Store sash positions as fractions so they survive rebuilding the panel."""
        vpaned = self._compare_vpaned
        if vpaned is not None:
            try:
                height = max(vpaned.winfo_height(), 1)
                if height > 40 and len(vpaned.panes()) >= 2:
                    _x, y = vpaned.sash_coord(0)
                    self._compare_layout_v_frac = min(0.85, max(0.15, y / height))
            except Exception:
                pass

        hpaned = self._compare_hpaned_top or self._compare_hpaned_bottom
        if hpaned is not None:
            try:
                width = max(hpaned.winfo_width(), 1)
                if width > 40 and len(hpaned.panes()) >= 2:
                    fracs = []
                    for i in range(len(hpaned.panes()) - 1):
                        x, _y = hpaned.sash_coord(i)
                        fracs.append(min(0.9, max(0.1, x / width)))
                    if fracs:
                        self._compare_layout_h_fracs = fracs
            except Exception:
                pass

    def _restore_compare_layout(self) -> None:
        """Apply remembered sash fractions after the new group UI is built."""
        vpaned = self._compare_vpaned
        if vpaned is not None and len(vpaned.panes()) >= 2:
            try:
                height = max(vpaned.winfo_height(), 1)
                y = int(height * self._compare_layout_v_frac)
                vpaned.sash_place(0, 0, y)
            except Exception:
                pass

        fracs = self._compare_layout_h_fracs
        for hpaned in (self._compare_hpaned_top, self._compare_hpaned_bottom):
            if hpaned is None or len(hpaned.panes()) < 2:
                continue
            try:
                width = max(hpaned.winfo_width(), 1)
                n_sash = len(hpaned.panes()) - 1
                if fracs and len(fracs) == n_sash:
                    for i, frac in enumerate(fracs):
                        hpaned.sash_place(i, int(width * frac), 0)
                else:
                    for i in range(n_sash):
                        hpaned.sash_place(i, int(width * (i + 1) / (n_sash + 1)), 0)
            except Exception:
                pass

    def _sync_horizontal_sashes(self, source) -> None:
        """Keep Preview-row and File-info-row column sashes lined up as one red line."""
        if self._sash_sync_lock:
            return
        top = self._compare_hpaned_top
        bottom = self._compare_hpaned_bottom
        if top is None or bottom is None:
            return
        other = bottom if source is top else top
        self._sash_sync_lock = True
        try:
            n = min(len(source.panes()), len(other.panes())) - 1
            for i in range(max(0, n)):
                x, y = source.sash_coord(i)
                other.sash_place(i, x, y)
        except Exception:
            pass
        finally:
            self._sash_sync_lock = False

    def _on_compare_sash(self, event=None) -> None:
        widget = event.widget if event is not None else None
        if widget in (self._compare_hpaned_top, self._compare_hpaned_bottom):
            self._sync_horizontal_sashes(widget)
        self._remember_compare_layout()

    def _populate_group_list(self) -> None:
        self._clear_group_list()
        self._clear_compare_panel()
        self._group_list_shown = 0
        if not self.dup_report or not self.dup_report.groups:
            ctk.CTkLabel(
                self.group_list,
                text="No duplicate groups found.\nTry Exact on Overview after a scan.",
                justify="left",
                text_color=MUTED,
            ).pack(anchor="w", padx=4, pady=4)
            return
        self._append_group_page()
        self.show_duplicate_group(0)

    def _append_group_page(self) -> None:
        """Add the next page of group buttons (virtualized for 10k+ groups)."""
        if not self.dup_report:
            return
        # Remove previous "Load more" control if present
        for child in list(self.group_list.winfo_children()):
            if getattr(child, "_is_load_more", False):
                child.destroy()

        groups = self.dup_report.groups
        start = self._group_list_shown
        end = min(len(groups), start + self._group_page_size)
        for index in range(start, end):
            group = groups[index]
            name = group.files[0].name
            if len(name) > 28:
                name = name[:25] + "..."
            kind_tag = {
                "exact": "Exact",
                "similar_photo": "Photo≈",
                "similar_document": "Doc≈",
            }.get(group.kind, group.kind)
            label = (
                f"#{index + 1} {kind_tag} · {len(group.files)} · {format_bytes(group.size)}\n"
                f"{name}"
            )
            btn = ctk.CTkButton(
                self.group_list,
                text=label,
                anchor="w",
                height=42,
                font=ctk.CTkFont(size=12),
                fg_color=LIST_BTN,
                text_color=LIST_BTN_TEXT,
                command=lambda i=index: self.show_duplicate_group(i),
            )
            btn.pack(fill="x", padx=1, pady=1)
            self._group_buttons.append(btn)

        self._group_list_shown = end
        if end < len(groups):
            more = ctk.CTkButton(
                self.group_list,
                text=f"Load more groups ({end} / {len(groups)})",
                height=32,
                command=self._append_group_page,
            )
            more._is_load_more = True  # type: ignore[attr-defined]
            more.pack(fill="x", padx=1, pady=6)

    def _highlight_group_button(self, index: int) -> None:
        for i, btn in enumerate(self._group_buttons):
            # Button i corresponds to group index i only for the first page;
            # after Load more, buttons are sequential from 0..shown-1 matching groups 0..shown-1
            group_index = i
            if group_index == index:
                btn.configure(fg_color=SELECT, text_color="white")
            else:
                btn.configure(fg_color=LIST_BTN, text_color=LIST_BTN_TEXT)

    def show_duplicate_group(self, index: int) -> None:
        """Show every file in one duplicate group side by side (fills the pane)."""
        if not self.dup_report or index < 0 or index >= len(self.dup_report.groups):
            return
        # Ensure the group button exists in the virtualized list
        while self._group_list_shown <= index:
            before = self._group_list_shown
            self._append_group_page()
            if self._group_list_shown == before:
                break
        # Keep the user's sash layout when flipping between groups
        self._remember_compare_layout()
        group = self.dup_report.groups[index]
        self._clear_compare_panel()
        self._selected_group_index = index
        self._highlight_group_button(index)

        count = len(group.files)

        # GREEN: one vertical sash across the full width (Preview row ↔ File info row)
        vpaned = tk.PanedWindow(
            self.compare_frame,
            orient=tk.VERTICAL,
            sashwidth=10,
            sashrelief=tk.RAISED,
            bg=paned_bg(),
        )
        vpaned.pack(fill="both", expand=True, padx=2, pady=2)
        self._compare_vpaned = vpaned

        top_host = ctk.CTkFrame(vpaned, fg_color="transparent")
        bottom_host = ctk.CTkFrame(vpaned, fg_color="transparent")
        top_host.grid_columnconfigure(0, weight=1)
        top_host.grid_rowconfigure(0, weight=1)
        bottom_host.grid_columnconfigure(0, weight=1)
        bottom_host.grid_rowconfigure(0, weight=1)

        # RED: horizontal sashes in Preview row and File-info row, kept in sync as one line
        h_top = tk.PanedWindow(
            top_host,
            orient=tk.HORIZONTAL,
            sashwidth=10,
            sashrelief=tk.RAISED,
            bg=paned_bg(),
        )
        h_bottom = tk.PanedWindow(
            bottom_host,
            orient=tk.HORIZONTAL,
            sashwidth=10,
            sashrelief=tk.RAISED,
            bg=paned_bg(),
        )
        h_top.grid(row=0, column=0, sticky="nsew")
        h_bottom.grid(row=0, column=0, sticky="nsew")
        self._compare_hpaned_top = h_top
        self._compare_hpaned_bottom = h_bottom
        self._compare_cards_paned = h_top

        for col, info in enumerate(group.files):
            role = "KEEP (oldest)" if col == 0 else f"Copy #{col + 1}"
            preview_col = self._build_preview_column(h_top, info, role, total_cards=count)
            info_col = self._build_info_column(h_bottom, info, role)
            h_top.add(preview_col, minsize=160)
            h_bottom.add(info_col, minsize=160)

        vpaned.add(top_host, minsize=120)
        vpaned.add(bottom_host, minsize=120)

        for paned in (vpaned, h_top, h_bottom):
            paned.bind("<ButtonRelease-1>", self._on_compare_sash)
            paned.bind("<B1-Motion>", self._on_compare_sash)

        self.after(40, self._restore_compare_layout)
        self.after(200, self._restore_compare_layout)

        # Default: all extras selected (KEEP left unticked); focus so ← → work
        self.select_extras_in_group()
        try:
            self.focus_set()
        except Exception:
            pass

        self._set_status(
            f"Viewing group {index + 1}/{len(self.dup_report.groups)} · "
            f"{group.label} · {count} files · {format_bytes(group.size)} · "
            "← → for Prev/Next"
        )

    def prev_duplicate_group(self) -> None:
        if not self.dup_report or not self.dup_report.groups:
            return
        current = self._selected_group_index if self._selected_group_index is not None else 0
        self.show_duplicate_group(max(0, current - 1))

    def next_duplicate_group(self) -> None:
        if not self.dup_report or not self.dup_report.groups:
            return
        current = self._selected_group_index if self._selected_group_index is not None else 0
        last = len(self.dup_report.groups) - 1
        self.show_duplicate_group(min(last, current + 1))

    def _duplicates_tab_active(self) -> bool:
        try:
            return self.tabs.get() == "Duplicates"
        except Exception:
            return False

    def _focus_is_text_input(self, widget) -> bool:
        """True when the user is typing in an entry/textbox (don't steal arrow keys)."""
        w = widget
        for _ in range(12):
            if w is None:
                break
            name = w.__class__.__name__
            if name in ("CTkEntry", "CTkTextbox", "Entry", "Text", "TEntry"):
                return True
            try:
                w = w.master
            except Exception:
                break
        return False

    def _on_duplicates_left_key(self, event) -> Optional[str]:
        if not self._duplicates_tab_active():
            return None
        if self._focus_is_text_input(event.widget):
            return None
        self.prev_duplicate_group()
        return "break"

    def _on_duplicates_right_key(self, event) -> Optional[str]:
        if not self._duplicates_tab_active():
            return None
        if self._focus_is_text_input(event.widget):
            return None
        self.next_duplicate_group()
        return "break"

    def _build_preview_column(
        self, parent, info: FileInfo, role: str, total_cards: int = 2
    ) -> ctk.CTkFrame:
        """Top-row cell: role title + Preview content (always something visual)."""
        col = ctk.CTkFrame(parent)
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(col, text=role, font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 2)
        )
        preview = load_preview_for_info(info, role)
        kind_label = {
            "image": "Preview",
            "text": "Text extract / binary sample",
            "composite": "Preview + binary sample",
        }.get(preview.kind, "Preview")
        ctk.CTkLabel(
            col,
            text=kind_label,
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))

        preview_host = ctk.CTkFrame(col, fg_color=("gray90", "gray20"))
        preview_host.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 6))
        preview_host.grid_columnconfigure(0, weight=1)
        preview_host.grid_rowconfigure(0, weight=1)

        show_image = preview.image is not None and preview.kind in ("image", "composite")
        show_text = bool(preview.text_content or preview.error) and preview.kind in (
            "text",
            "composite",
        )
        # Always show something: prefer image; if both, stack image then text
        if show_image and not show_text:
            self._compare_pil_images.append(preview.image)
            max_side = self._compare_thumb_side(total_cards)
            pil = preview.image.copy()
            pil.thumbnail((max_side, max_side))
            ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._compare_image_refs.append(ctk_image)
            img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
            img_label.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
            self._compare_img_labels.append(img_label)
        elif show_image and show_text:
            preview_host.grid_rowconfigure(1, weight=1)
            self._compare_pil_images.append(preview.image)
            max_side = max(140, self._compare_thumb_side(total_cards) // 2)
            pil = preview.image.copy()
            pil.thumbnail((max_side, max_side))
            ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._compare_image_refs.append(ctk_image)
            img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
            img_label.grid(row=0, column=0, padx=6, pady=(6, 2), sticky="n")
            self._compare_img_labels.append(img_label)
            text_box = ctk.CTkTextbox(preview_host, height=120)
            text_box.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
            text_box.insert("1.0", preview.text_content or preview.error)
            text_box.configure(state="disabled")
        else:
            text_box = ctk.CTkTextbox(preview_host)
            text_box.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
            content = preview.text_content or preview.error or "No preview available"
            # If we have a type-card image but kind was text-only, still show the card
            if preview.image is not None:
                self._compare_pil_images.append(preview.image)
                max_side = self._compare_thumb_side(total_cards)
                pil = preview.image.copy()
                pil.thumbnail((max_side, max_side))
                ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                self._compare_image_refs.append(ctk_image)
                img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
                img_label.grid(row=0, column=0, padx=6, pady=(6, 2), sticky="n")
                self._compare_img_labels.append(img_label)
                preview_host.grid_rowconfigure(1, weight=1)
                text_box.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
            text_box.insert("1.0", content)
            text_box.configure(state="disabled")

        return col

    def _build_info_column(self, parent, info: FileInfo, role: str) -> ctk.CTkFrame:
        """Bottom-row cell: File info + actions."""
        col = ctk.CTkFrame(parent)
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            col,
            text="File info",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        preview = load_preview_for_info(info, role)
        info_box = ctk.CTkTextbox(col)
        info_box.grid(row=1, column=0, padx=8, pady=(0, 4), sticky="nsew")
        info_box.insert("1.0", preview.info_text)
        info_box.configure(state="disabled")

        actions = ctk.CTkFrame(col, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 8))

        check_var = tk.BooleanVar(
            value=(role != "KEEP (oldest)" and not info.is_inside_archive)
        )
        select_text = "Select for remove/delete"
        if info.is_inside_archive:
            select_text = "Inside zip (can't quarantine alone)"
            check_var.set(False)
        checkbox = ctk.CTkCheckBox(actions, text=select_text, variable=check_var)
        checkbox.pack(side="left")
        if info.is_inside_archive:
            checkbox.configure(state="disabled")
        open_target = info.archive_container if info.is_inside_archive else info.path
        ctk.CTkButton(
            actions,
            text="Open folder",
            width=100,
            height=26,
            command=lambda p=open_target: self._open_folder(p),
        ).pack(side="right")

        self._compare_check_vars.append(check_var)
        self._compare_checkboxes.append(checkbox)
        self._compare_file_infos.append(info)
        return col

    def _compare_thumb_side(self, total_cards: int) -> int:
        """Pick a preview size from the current compare pane width."""
        try:
            width = max(self.compare_frame.winfo_width(), 400)
        except Exception:
            width = 800
        per_card = max(180, int((width - 24) / max(total_cards, 1)) - 24)
        return max(180, min(per_card, 700))

    def _on_compare_resize(self, _event=None) -> None:
        if not self._compare_pil_images or not self._compare_img_labels:
            return
        if self._compare_resize_job is not None:
            try:
                self.after_cancel(self._compare_resize_job)
            except Exception:
                pass
        self._compare_resize_job = self.after(120, self._apply_compare_image_sizes)

    def _apply_compare_image_sizes(self) -> None:
        self._compare_resize_job = None
        total = max(len(self._compare_file_infos), 1)
        max_side = self._compare_thumb_side(total)
        new_refs: list[object] = []
        img_i = 0
        for pil in self._compare_pil_images:
            if img_i >= len(self._compare_img_labels):
                break
            scaled = pil.copy()
            scaled.thumbnail((max_side, max_side))
            ctk_image = ctk.CTkImage(light_image=scaled, dark_image=scaled, size=scaled.size)
            new_refs.append(ctk_image)
            self._compare_img_labels[img_i].configure(image=ctk_image)
            img_i += 1
        self._compare_image_refs = new_refs

    def _open_folder(self, path: Path) -> None:
        err = open_containing_folder(path)
        if err:
            messagebox.showwarning(APP_TITLE, err)
        else:
            self._set_status(f"Opened folder for: {path.name}")

    def select_extras_in_group(self) -> None:
        """Select every extra copy (not KEEP); skip zip members."""
        if not self._compare_check_vars:
            self._set_status("Open a duplicate group first, then use Select extras only.")
            return
        selected = 0
        for i, (var, info, checkbox) in enumerate(
            zip(
                self._compare_check_vars,
                self._compare_file_infos,
                self._compare_checkboxes,
            )
        ):
            want = i > 0 and not info.is_inside_archive
            var.set(want)
            # CTkCheckBox often ignores BooleanVar.set() alone — force the widget
            try:
                if want:
                    checkbox.select()
                else:
                    checkbox.deselect()
            except Exception:
                pass
            if want:
                selected += 1
        self._set_status(f"Selected {selected} extra file(s) in this group.")

    def select_all_in_group(self) -> None:
        if not self._compare_check_vars:
            self._set_status("Open a duplicate group first.")
            return
        for var, info, checkbox in zip(
            self._compare_check_vars,
            self._compare_file_infos,
            self._compare_checkboxes,
        ):
            if info.is_inside_archive:
                var.set(False)
                try:
                    checkbox.deselect()
                except Exception:
                    pass
                continue
            var.set(True)
            try:
                checkbox.select()
            except Exception:
                pass
        self._set_status("Selected all removable files in this group.")

    def clear_compare_selection(self) -> None:
        if not self._compare_check_vars:
            return
        for var, checkbox in zip(self._compare_check_vars, self._compare_checkboxes):
            var.set(False)
            try:
                checkbox.deselect()
            except Exception:
                pass
        self._set_status("Cleared selection in this group.")

    def _selected_compare_files(self) -> list[FileInfo]:
        chosen: list[FileInfo] = []
        for var, info in zip(self._compare_check_vars, self._compare_file_infos):
            if var.get() and not info.is_inside_archive:
                chosen.append(info)
        return chosen

    def _remove_paths_from_reports(self, paths: set[Path]) -> None:
        """Drop removed files from the in-memory duplicate report and refresh the UI."""
        if not self.dup_report:
            return

        resolved: set[Path] = set()
        for path in paths:
            resolved.add(path)
            try:
                resolved.add(path.resolve())
            except OSError:
                pass

        def still_here(info: FileInfo) -> bool:
            if info.path in resolved:
                return False
            try:
                return info.path.resolve() not in resolved
            except OSError:
                return True

        remaining_groups: list[DuplicateGroup] = []
        for group in self.dup_report.groups:
            kept = [f for f in group.files if still_here(f)]
            if len(kept) >= 2:
                group.files = kept
                remaining_groups.append(group)
        self.dup_report.groups = remaining_groups

        if self.scan_result:
            self.scan_result.ensure_files_loaded()
            self.scan_result.files = [f for f in self.scan_result.files if still_here(f)]
            self.scan_result.file_count = len(self.scan_result.files)

        self.dup_summary.configure(
            text=(
                f"{len(self.dup_report.groups)} duplicate groups · "
                f"{self.dup_report.duplicate_file_count} extra files · "
                f"~{format_bytes(self.dup_report.wasted_bytes)} reclaimable  ·  "
                "Click a group to compare side by side"
            )
        )
        self._populate_group_list()

    def quarantine_selected_compare_files(self) -> None:
        chosen = self._selected_compare_files()
        if not chosen:
            messagebox.showinfo(APP_TITLE, "Tick at least one file in the compare view.")
            return
        if len(chosen) >= len(self._compare_file_infos) and len(self._compare_file_infos) > 1:
            ok_all = messagebox.askyesno(
                APP_TITLE,
                "You selected every file in this group.\n"
                "That removes all copies of this content from the scanned locations.\n\nContinue?",
            )
            if not ok_all:
                return
        qpath = quarantine_root()
        ok = messagebox.askyesno(
            APP_TITLE,
            f"Move {len(chosen)} selected file(s) to quarantine?\n\n{qpath}\n\n"
            "You can restore them later from that folder.",
        )
        if not ok:
            return
        session, log = move_to_quarantine([c.path for c in chosen])
        paths = {c.path.resolve() for c in chosen}
        # Also match by original Path objects used in the report
        paths |= {c.path for c in chosen}
        self._remove_paths_from_reports(paths)
        messagebox.showinfo(APP_TITLE, f"Quarantined to:\n{session}\n\n" + "\n".join(log[:8]))
        self._set_status(f"Quarantined {len(chosen)} selected file(s).")

    def delete_selected_compare_files(self) -> None:
        chosen = self._selected_compare_files()
        if not chosen:
            messagebox.showinfo(APP_TITLE, "Tick at least one file in the compare view.")
            return
        names = "\n".join(str(c.path) for c in chosen[:10])
        extra = "" if len(chosen) <= 10 else f"\n… and {len(chosen) - 10} more"
        ok = messagebox.askyesno(
            APP_TITLE,
            "PERMANENT DELETE\n\n"
            f"This will erase {len(chosen)} file(s) from disk and cannot be undone "
            "by this app.\n\n"
            f"{names}{extra}\n\n"
            "Prefer “Quarantine selected” if you might want them back.\n\n"
            "Delete permanently?",
        )
        if not ok:
            return
        ok2 = messagebox.askyesno(
            APP_TITLE,
            "Final confirmation: permanently delete the selected file(s)?",
        )
        if not ok2:
            return
        log = permanently_delete([c.path for c in chosen])
        paths = {c.path for c in chosen}
        self._remove_paths_from_reports(paths)
        messagebox.showinfo(APP_TITLE, "Delete finished.\n\n" + "\n".join(log[:12]))
        self._set_status(f"Permanently deleted {len(chosen)} selected file(s).")

    def _build_organise_tab(self) -> None:
        tab = self.tabs.tab("Organise")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            tab,
            text="Pick a layout (or define your own), preview the tree, then browse the dry-run like a file manager.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))

        dest_row = ctk.CTkFrame(tab, fg_color="transparent")
        dest_row.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        dest_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(dest_row, text="Destination:").grid(row=0, column=0, padx=(0, 8))
        self.dest_entry = ctk.CTkEntry(dest_row, placeholder_text="Choose a tidy destination folder")
        self.dest_entry.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(dest_row, text="Browse…", width=100, command=self.choose_dest).grid(
            row=0, column=2, padx=(8, 0)
        )

        # Vertical sash: options/preview on top, plan text on bottom (drag to resize)
        vpaned = tk.PanedWindow(tab, orient=tk.VERTICAL, sashwidth=8, sashrelief=tk.RAISED)
        vpaned.grid(row=2, column=0, sticky="nsew", padx=6, pady=4)
        self.org_vpaned = vpaned

        top = ctk.CTkFrame(vpaned, fg_color="transparent")
        bottom = ctk.CTkFrame(vpaned, fg_color="transparent")
        vpaned.add(top, minsize=200)
        vpaned.add(bottom, minsize=120)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(0, weight=1)
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(0, weight=1)

        # Horizontal sash: layouts/options | visual tree
        hpaned = tk.PanedWindow(top, orient=tk.HORIZONTAL, sashwidth=8, sashrelief=tk.RAISED)
        hpaned.grid(row=0, column=0, sticky="nsew")
        self.org_hpaned = hpaned

        left = ctk.CTkFrame(hpaned)
        right = ctk.CTkFrame(hpaned)
        hpaned.add(left, minsize=260)
        hpaned.add(right, minsize=260)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(left, text="Folder layout", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(6, 2)
        )
        self.layout_hint = ctk.CTkLabel(
            left,
            text="Tick one or more layouts to combine. Scan first for recommendations.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        )
        self.layout_hint.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        self.layout_vars: dict[str, tk.BooleanVar] = {}
        self.layout_check_frame = ctk.CTkScrollableFrame(left, height=140)
        self.layout_check_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 4))
        self.layout_check_frame.grid_columnconfigure(0, weight=1)
        self._layout_check_widgets: list[ctk.CTkCheckBox] = []

        safety = ctk.CTkFrame(left, fg_color="transparent")
        safety.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.copy_instead_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            safety,
            text="Copy files (safer) instead of moving",
            variable=self.copy_instead_var,
            command=self._update_layout_visual,
        ).pack(anchor="w")
        self.show_advanced_org_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            safety,
            text="Show advanced options (categories, naming, custom)",
            variable=self.show_advanced_org_var,
            command=self._toggle_organise_advanced,
        ).pack(anchor="w", pady=(4, 0))

        # Category include + sub-folder options (collapsed by default)
        opts = ctk.CTkScrollableFrame(left, height=220)
        self.organise_advanced_frame = opts
        opts.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            opts, text="Include categories", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 2))

        self.category_vars: dict[str, tk.BooleanVar] = {}
        cat_row = ctk.CTkFrame(opts, fg_color="transparent")
        cat_row.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        for i, cat in enumerate(ALL_CATEGORIES):
            var = tk.BooleanVar(value=True)
            self.category_vars[cat] = var
            ctk.CTkCheckBox(
                cat_row,
                text=cat,
                variable=var,
                width=90,
                command=self._update_layout_visual,
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)

        ctk.CTkLabel(
            opts, text="Sub-folder options", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=0, sticky="w", padx=6, pady=(8, 2))
        self.media_by_date_var = tk.BooleanVar(value=True)
        self.documents_by_ext_var = tk.BooleanVar(value=True)
        self.separate_archives_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts,
            text="Media: year / month subfolders",
            variable=self.media_by_date_var,
            command=self._update_layout_visual,
        ).grid(row=3, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Documents: subfolder per extension (pdf, docx, …)",
            variable=self.documents_by_ext_var,
            command=self._update_layout_visual,
        ).grid(row=4, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Keep Archives in its own folder",
            variable=self.separate_archives_var,
            command=self._update_layout_visual,
        ).grid(row=5, column=0, sticky="w", padx=8, pady=(2, 4))

        ctk.CTkLabel(
            opts,
            text="Archive best practices",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=6, column=0, sticky="w", padx=6, pady=(8, 2))
        self.date_prefix_var = tk.BooleanVar(value=False)
        self.sanitize_names_var = tk.BooleanVar(value=True)
        self.readme_notes_var = tk.BooleanVar(value=True)
        self.archive_days_var = tk.StringVar(value="365")
        ctk.CTkLabel(
            opts,
            text="(Copy vs move is above — kept visible for safety)",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).grid(row=7, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Add YYYY-MM-DD date prefix to filenames",
            variable=self.date_prefix_var,
            command=self._update_layout_visual,
        ).grid(row=8, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Sanitize filenames (safer characters)",
            variable=self.sanitize_names_var,
            command=self._update_layout_visual,
        ).grid(row=9, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Write README.txt notes in top folders",
            variable=self.readme_notes_var,
            command=self._update_layout_visual,
        ).grid(row=10, column=0, sticky="w", padx=8, pady=2)
        age_row = ctk.CTkFrame(opts, fg_color="transparent")
        age_row.grid(row=11, column=0, sticky="ew", padx=8, pady=(2, 6))
        ctk.CTkLabel(age_row, text="Archive files older than (days):").pack(side="left")
        age_entry = ctk.CTkEntry(age_row, width=70, textvariable=self.archive_days_var)
        age_entry.pack(side="left", padx=6)
        age_entry.bind("<KeyRelease>", lambda _e: self._update_layout_visual())

        ctk.CTkLabel(
            opts,
            text="Custom structure (when layout = Custom)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=12, column=0, sticky="w", padx=6, pady=(8, 2))
        ctk.CTkLabel(
            opts,
            text="Tree lines + rules like: Photos = MyArchive/Photos/{year}/{month}",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        ).grid(row=13, column=0, sticky="w", padx=8)
        self.custom_structure_box = ctk.CTkTextbox(
            opts, height=110, font=ctk.CTkFont(family="monospace", size=11)
        )
        self.custom_structure_box.grid(row=14, column=0, sticky="ew", padx=8, pady=(2, 8))
        self.custom_structure_box.insert("1.0", DEFAULT_CUSTOM_TEMPLATE)
        self.custom_structure_box.bind("<KeyRelease>", lambda _e: self._update_layout_visual())

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Visual layout preview", font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            head, text="Refresh view", width=100, height=26, command=self._update_layout_visual
        ).grid(row=0, column=1, sticky="e")

        self.layout_tree_box = ctk.CTkTextbox(right, font=ctk.CTkFont(family="monospace", size=12))
        self.layout_tree_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.layout_tree_box.insert(
            "1.0",
            "Select a layout on the left.\nAfter a scan, this shows the folder tree that will be created.\nDrag the sashes to resize panes.",
        )
        self.layout_tree_box.configure(state="disabled")

        self.org_box = ctk.CTkTextbox(bottom)
        self.org_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.org_box.insert("1.0", "File move plan appears here after Preview plan.")
        self.org_box.configure(state="disabled")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 6))
        self.btn_preview_org = ctk.CTkButton(
            row, text="Preview plan", command=self.preview_organise
        )
        self.btn_preview_org.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Browse dry-run…",
            width=130,
            command=self.open_plan_browser,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Save inventory…", width=130, command=self.save_inventory
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Show tips", width=100, command=self.show_organisation_tips
        ).pack(side="left", padx=(0, 8))
        self.btn_apply_org = primary_button(
            row,
            text="Apply organise",
            command=self.apply_organise,
        )
        self.btn_apply_org.pack(side="right")

        self.dry_run_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            row,
            text="Dry run only (preview, do not change files)",
            variable=self.dry_run_var,
        ).pack(side="right", padx=16)

        self._last_organise_plan = None
        self._rebuild_layout_options(recommended=["type_date"])
        self._toggle_organise_advanced()

    def _toggle_organise_advanced(self) -> None:
        frame = getattr(self, "organise_advanced_frame", None)
        if frame is None:
            return
        if self.show_advanced_org_var.get():
            frame.grid(row=4, column=0, sticky="nsew", padx=6, pady=(0, 6))
            try:
                frame.master.grid_rowconfigure(4, weight=1)
            except Exception:
                pass
        else:
            frame.grid_remove()

    def _rebuild_layout_options(self, recommended: Optional[list[str]] = None) -> None:
        """Rebuild layout checkboxes; tick one or more to combine folder structures."""
        for child in self.layout_check_frame.winfo_children():
            child.destroy()
        self._layout_check_widgets.clear()

        order = recommended or [p.id for p in LAYOUT_PRESETS]
        seen: set[str] = set()
        ordered_ids: list[str] = []
        for layout_id in order:
            if layout_id not in seen:
                ordered_ids.append(layout_id)
                seen.add(layout_id)
        for preset in LAYOUT_PRESETS:
            if preset.id not in seen:
                ordered_ids.append(preset.id)

        previous = {
            lid: var.get() for lid, var in self.layout_vars.items()
        } if self.layout_vars else {}
        self.layout_vars = {}

        best = ordered_ids[0] if ordered_ids else "type_date"
        any_prev = any(previous.values())

        for row_i, layout_id in enumerate(ordered_ids):
            preset = get_layout(layout_id)
            mark = "  (recommended)" if recommended and layout_id == recommended[0] else ""
            useful = ""
            if recommended and layout_id in recommended[:3] and layout_id != recommended[0]:
                useful = "  (fits your files)"
            text = f"{preset.name}{mark}{useful}"
            if any_prev:
                default_on = bool(previous.get(layout_id, False))
            else:
                default_on = layout_id == best
            var = tk.BooleanVar(value=default_on)
            self.layout_vars[layout_id] = var
            box = ctk.CTkCheckBox(
                self.layout_check_frame,
                text=text,
                variable=var,
                command=self._on_layout_chosen,
            )
            box.grid(row=row_i, column=0, sticky="w", padx=4, pady=2)
            self._layout_check_widgets.append(box)

        if not any(var.get() for var in self.layout_vars.values()):
            self.layout_vars[best].set(True)
        self._on_layout_chosen()

    def _selected_layout_ids(self) -> list[str]:
        ids = [lid for lid, var in self.layout_vars.items() if var.get()]
        if not ids:
            return ["type_date"]
        # Custom cannot mix with other layouts
        if "custom" in ids and len(ids) > 1:
            for lid, var in self.layout_vars.items():
                var.set(lid == "custom")
            return ["custom"]
        return ids

    def _on_layout_chosen(self) -> None:
        # If user ticks Custom with others, keep Custom only
        if self.layout_vars.get("custom") and self.layout_vars["custom"].get():
            others = [lid for lid, var in self.layout_vars.items() if lid != "custom" and var.get()]
            if others:
                for lid in others:
                    self.layout_vars[lid].set(False)
        if not any(var.get() for var in self.layout_vars.values()):
            # Always keep at least one layout
            fallback = "type_date" if "type_date" in self.layout_vars else next(iter(self.layout_vars))
            self.layout_vars[fallback].set(True)
        self._update_layout_visual()

    def _current_organise_options(self) -> OrganiseOptions:
        selected = {cat for cat, var in self.category_vars.items() if var.get()}
        try:
            days = max(1, int(self.archive_days_var.get().strip() or "365"))
        except ValueError:
            days = 365
        return OrganiseOptions(
            categories=selected if selected else set(ALL_CATEGORIES),
            media_by_date=self.media_by_date_var.get(),
            documents_by_ext=self.documents_by_ext_var.get(),
            separate_archives=self.separate_archives_var.get(),
            copy_instead_of_move=self.copy_instead_var.get(),
            rename_with_date_prefix=self.date_prefix_var.get(),
            sanitize_filenames=self.sanitize_names_var.get(),
            add_readme_notes=self.readme_notes_var.get(),
            archive_older_than_days=days,
            custom_structure_text=self.custom_structure_box.get("1.0", "end").strip(),
        )

    def _update_layout_visual(self) -> None:
        """Refresh the visual folder-tree for the selected layout(s) and options."""
        files = self._scan_files() if self.scan_result else []
        dest = self.dest_entry.get().strip() if hasattr(self, "dest_entry") else ""
        layout_ids = self._selected_layout_ids()
        options = self._current_organise_options()
        tree = layout_tree_text(
            files, dest, layout_ids=layout_ids, options=options
        )
        label = layout_combo_label(layout_ids)
        flags = (
            f"Practices: copy={options.copy_instead_of_move} · "
            f"date prefix={options.rename_with_date_prefix} · "
            f"sanitize={options.sanitize_filenames} · "
            f"README={options.add_readme_notes} · "
            f"archive>{options.archive_older_than_days}d"
        )
        combo_note = ""
        if len(layout_ids) > 1:
            combo_note = (
                "\nCombining layouts: folder parts are nested "
                "(duplicate folder names skipped).\n"
            )
        text = f"{label}{combo_note}\n{flags}\n\n{tree}"
        self.layout_tree_box.configure(state="normal")
        self.layout_tree_box.delete("1.0", "end")
        self.layout_tree_box.insert("1.0", text)
        self.layout_tree_box.configure(state="disabled")

    def _refresh_layout_options_from_scan(self) -> None:
        if not self._has_scan_files():
            self.layout_hint.configure(
                text="Scan a folder first — recommended layouts will appear here."
            )
            for cat, var in self.category_vars.items():
                var.set(True)
            self._rebuild_layout_options(recommended=["type_date"])
            return
        files = self._scan_files()
        counts: dict[str, int] = {}
        for info in files:
            counts[info.category] = counts.get(info.category, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        recommended = recommended_layout_ids(files)
        best = get_layout(recommended[0])
        self.layout_hint.configure(
            text=(
                f"Based on your scan ({summary}). Suggested: {best.name}. "
                "Tick extra layouts to combine folders."
            )
        )
        for cat, var in self.category_vars.items():
            var.set(counts.get(cat, 0) > 0)
        # Fresh recommendation tick (rebuild defaults to top recommended)
        self.layout_vars = {}
        self._rebuild_layout_options(recommended=recommended)

    def _build_help_tab(self) -> None:
        tab = self.tabs.tab("Help")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        help_box = ctk.CTkTextbox(tab)
        help_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        help_box.insert(
            "1.0",
            """HOW TO USE

1. Sources tab
   • Add folder / drive. Tick rows to select; Remove selected / Open as needed.
   • Leave “Scan inside .zip” off for huge drives (you can turn it on for small libraries).
   • Click Scan now. Large libraries use an on-disk SQLite index so RAM stays sane.

2. Overview tab
   • Read counts, size, and timing. Save a report if you like.
   • Tick Exact (recommended). Similar photos/docs are optional and capped.
   • Click Find duplicates — then you are taken to the Duplicates tab.

3. Duplicates tab
   • Left: duplicate groups (loads in pages — use Load more on huge results).
   • Click a group for side-by-side compare. Drag sashes to resize panes.
   • Every file shows a best-effort Preview (image/page/frame/waveform/text) or a
     type card + binary sample — not a full proprietary document renderer.
   • Prefer Quarantine selected. Permanent delete needs two confirmations.
   • Zip members can be compared; quarantine applies to real disk files only.

4. Organise tab
   • Destination must be outside your scan sources for real copy/move.
   • Tick one or more folder layouts to combine (folders nest together).
   • Keep Copy + Dry run on at first. Preview plan, then Apply.
   • Advanced / custom options can stay collapsed until you need them.
   • Custom structure cannot mix with other layouts (Custom alone).

LARGE DRIVES (1TB+)
• Exact duplicates: size buckets → partial CRC → full CRC only on collisions.
• Stay on one mount (default). Zip listing is capped per archive and overall.
• Cancel stays available; status shows file counts and sizes while working.

PRIVACY & SAFETY
• Nothing is uploaded. Prefer quarantine. Test on a small folder first.
""",
        )
        help_box.configure(state="disabled")

    # ---------- helpers ----------

    def _set_status(self, text: str) -> None:
        """
        Update the status bar.
        While a long task is running, the GUI owns the live clock so the
        time keeps advancing even when the worker is busy inside a big zip.
        """
        if self._busy:
            detail = text.strip()
            if detail.startswith("["):
                bracket = detail.find("]")
                if bracket != -1:
                    detail = detail[bracket + 1 :].strip() or self._busy_detail
            if detail:
                self._busy_detail = detail
            elapsed = format_duration(time.monotonic() - self._busy_started)
            self.status_label.configure(text=f"[{elapsed}] {self._busy_detail}")
        else:
            self.status_label.configure(text=text)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._cancel_flag = False
            self._busy_started = time.monotonic()
            self._busy_detail = "Working…"
            self.cancel_btn.configure(state="normal")
            self.progress.start()
            self._schedule_busy_tick()
        else:
            self._cancel_busy_tick()
            self.cancel_btn.configure(state="disabled")
            self.progress.stop()
            self.progress.set(0)

    def _schedule_busy_tick(self) -> None:
        self._cancel_busy_tick()
        self._busy_tick_job = self.after(500, self._busy_tick)

    def _cancel_busy_tick(self) -> None:
        if self._busy_tick_job is not None:
            try:
                self.after_cancel(self._busy_tick_job)
            except Exception:
                pass
            self._busy_tick_job = None

    def _busy_tick(self) -> None:
        """Refresh the elapsed clock twice a second while busy."""
        self._busy_tick_job = None
        if not self._busy:
            return
        elapsed = format_duration(time.monotonic() - self._busy_started)
        detail = self._busy_detail or "Working…"
        self.status_label.configure(text=f"[{elapsed}] {detail}")
        self._schedule_busy_tick()

    def _request_cancel(self) -> None:
        self._cancel_flag = True
        self._set_status("Cancelling…")

    def _should_cancel(self) -> bool:
        return self._cancel_flag

    def _refresh_source_list(self) -> None:
        """Rebuild the selectable source checklist."""
        for child in self.source_list_frame.winfo_children():
            child.destroy()
        self._source_check_vars.clear()

        if not self.source_paths:
            ctk.CTkLabel(
                self.source_list_frame,
                text="(no folders added yet — click Add folder / drive)",
                text_color=("gray40", "gray70"),
            ).pack(anchor="w", padx=6, pady=8)
            return

        for path in self.source_paths:
            row = ctk.CTkFrame(self.source_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            var = tk.BooleanVar(value=False)
            self._source_check_vars[path] = var
            ctk.CTkCheckBox(row, text="", variable=var, width=28).pack(side="left")
            ctk.CTkLabel(row, text=path, anchor="w").pack(
                side="left", fill="x", expand=True, padx=(4, 0)
            )
            ctk.CTkButton(
                row,
                text="Open",
                width=60,
                height=26,
                command=lambda p=path: self._open_folder(Path(p)),
            ).pack(side="right", padx=(4, 0))

    def _write_box(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    # ---------- sources ----------

    def add_source(self) -> None:
        initial = self.source_paths[-1] if self.source_paths else str(Path.home())
        path = ask_folder(
            self,
            title="Add folder or drive to scan",
            initialdir=initial,
        )
        if not path:
            return
        if path in self.source_paths:
            messagebox.showinfo(APP_TITLE, "That folder is already in the list.")
            return
        self.source_paths.append(path)
        self._refresh_source_list()
        self._set_status(f"Added: {path}")

    def remove_selected_sources(self) -> None:
        selected = [p for p, var in self._source_check_vars.items() if var.get()]
        if not selected:
            messagebox.showinfo(
                APP_TITLE,
                "Tick one or more sources in the list, then click Remove selected.",
            )
            return
        for path in selected:
            if path in self.source_paths:
                self.source_paths.remove(path)
        self._refresh_source_list()
        self._set_status(f"Removed {len(selected)} source(s).")

    def select_all_sources(self) -> None:
        for var in self._source_check_vars.values():
            var.set(True)

    def clear_source_ticks(self) -> None:
        for var in self._source_check_vars.values():
            var.set(False)

    def clear_sources(self) -> None:
        self.source_paths.clear()
        self._refresh_source_list()
        self._set_status("Cleared source list.")

    def _on_appearance_change(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        # Refresh paned sash colors on next rebuild; banner stays readable
        try:
            if getattr(self, "dup_paned", None) is not None:
                self.dup_paned.configure(bg=paned_bg())
        except Exception:
            pass

    def _has_scan_files(self) -> bool:
        if not self.scan_result:
            return False
        return self.scan_result.file_count > 0 or bool(self.scan_result.files)

    def _scan_files(self) -> list[FileInfo]:
        if not self.scan_result:
            return []
        return self.scan_result.ensure_files_loaded()

    # ---------- scan ----------

    def start_scan(self) -> None:
        if self._busy:
            return
        if not self.source_paths:
            messagebox.showwarning(APP_TITLE, "Add at least one folder or drive first.")
            return
        self._set_busy(True)
        self._set_status("Scanning…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        options = ScanOptions(
            include_junk_system=self.include_junk_var.get(),
            scan_zip_contents=self.scan_zips_var.get(),
        )
        result = scan_paths(
            self.source_paths,
            status_cb=lambda msg: self.after(0, self._set_status, msg),
            should_cancel=self._should_cancel,
            options=options,
        )
        self.after(0, self._scan_done, result)

    def _scan_done(self, result: ScanResult) -> None:
        self._set_busy(False)
        self.scan_result = result
        self.dup_report = None
        self._update_overview()
        self._clear_group_list()
        self._clear_compare_panel()
        self.dup_summary.configure(
            text="Scan ready. Click Find duplicates on Overview (Exact is safest for huge drives)."
        )
        self._refresh_layout_options_from_scan()
        self.tabs.set("Overview")
        self.workflow_banner.configure(
            text="Next: review Overview, then Find duplicates (Exact). Open Duplicates to compare."
        )
        took = format_duration(result.duration_seconds)
        n = result.file_count or len(result.files)
        self._set_status(f"Scan finished: {n} files in {took}.")

    def _update_overview(self) -> None:
        if not self.scan_result:
            return
        result = self.scan_result
        store = result.store
        if store is not None:
            total_size = result.total_bytes or store.total_bytes()
            by_cat = store.category_counts()
            file_count = result.file_count or store.count()
            disk_files = store.disk_file_count()
            sample = store.sample_paths(40)
        else:
            files = result.files
            total_size = sum(f.size for f in files) or result.total_bytes
            by_cat = {}
            for f in files:
                by_cat[f.category] = by_cat.get(f.category, 0) + 1
            file_count = len(files)
            disk_files = sum(1 for f in files if not f.is_inside_archive)
            sample = files[:40]

        took = format_duration(result.duration_seconds)

        readonly_sources = []
        for src in self.source_paths:
            try:
                if not os.access(src, os.W_OK):
                    readonly_sources.append(src)
            except OSError:
                pass

        lines = [
            f"Files found: {file_count}",
            f"  · On disk: {disk_files}",
            f"  · Inside zips: {result.archive_members}",
            f"Total size: {format_bytes(total_size)}",
            f"Scan duration: {took}",
            f"Skipped / unreadable: {result.skipped}",
            f"Errors: {len(result.errors)}",
        ]
        if result.cross_device_skipped:
            lines.append(f"Other-mount folders skipped: {result.cross_device_skipped}")
        if result.zip_members_capped:
            lines.append(f"Zip listing caps hit: {result.zip_members_capped}")
        if store is not None:
            lines.append("Index: SQLite (large-drive mode)")
        if readonly_sources:
            lines.append("")
            lines.append(
                "Read-only sources (quarantine / delete / move will fail until remounted read-write):"
            )
            for src in readonly_sources:
                lines.append(f"  • {src}")
        lines.append("")
        lines.append("By category:")
        for cat in sorted(by_cat):
            lines.append(f"  • {cat}: {by_cat[cat]}")

        if result.errors:
            lines.append("")
            lines.append("Errors (first 20):")
            for err in result.errors[:20]:
                lines.append(f"  - {err}")

        lines.append("")
        lines.append("Sample paths (first 40):")
        for info in sample:
            junk = " [junk]" if info.is_junk_location else ""
            lines.append(f"  [{info.category}]{junk} {info.display_path}")

        lines.append("")
        lines.append(
            f"Timing: this scan took {took}. "
            "Duplicate searches also record how long they take."
        )

        summary = (
            f"{file_count} files · {format_bytes(total_size)} · "
            f"scan {took} · "
            + ", ".join(f"{k}: {v}" for k, v in sorted(by_cat.items()))
        )
        if result.archive_members:
            summary += f" · {result.archive_members} inside zips"
        self.overview_summary.configure(text=summary)
        self._write_box(self.overview_box, "\n".join(lines))

    def save_report(self) -> None:
        if not self.scan_result:
            messagebox.showinfo(APP_TITLE, "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        content = self.overview_box.get("1.0", "end")
        Path(path).write_text(content, encoding="utf-8")
        self._set_status(f"Report saved: {path}")

    # ---------- duplicates ----------

    def start_duplicate_search(self) -> None:
        if self._busy:
            return
        if not self.scan_result or (
            self.scan_result.file_count <= 0 and not self.scan_result.files
        ):
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        store = self.scan_result.store
        if store is not None:
            cats = store.category_counts()
            docs = cats.get("Documents", 0)
            photos = cats.get("Photos", 0)
        else:
            docs = sum(
                1
                for f in self.scan_result.files
                if f.category == "Documents" and not f.is_inside_archive
            )
            photos = sum(
                1
                for f in self.scan_result.files
                if f.category == "Photos" and not f.is_inside_archive
            )
        tip = ""
        if self.dup_docs_var.get() and docs > 5000:
            tip += (
                f"\n\nSimilar docs will scan up to 12,000 of {docs} on-disk documents "
                "(zip members skipped for speed). Exact still checks everything."
            )
        if self.dup_photos_var.get() and photos > 3000:
            tip += (
                f"\n\nSimilar photos will scan up to 8,000 of {photos} on-disk photos."
            )
        if tip:
            ok = messagebox.askyesno(
                APP_TITLE,
                "Large library detected." + tip + "\n\nContinue?",
            )
            if not ok:
                return
        self._set_busy(True)
        self._set_status("Searching for duplicates…")
        threading.Thread(target=self._dup_worker, daemon=True).start()

    def _dup_worker(self) -> None:
        options = DuplicateSearchOptions(
            exact=self.dup_exact_var.get(),
            similar_photos=self.dup_photos_var.get(),
            similar_documents=self.dup_docs_var.get(),
        )
        if not (options.exact or options.similar_photos or options.similar_documents):
            options.exact = True
        store = self.scan_result.store if self.scan_result else None
        files = self._scan_files() if self.scan_result else []
        report = find_duplicate_matches(
            files,
            options=options,
            status_cb=lambda msg: self.after(0, self._set_status, msg),
            should_cancel=self._should_cancel,
            store=store,
        )
        self.after(0, self._dup_done, report)

    def _dup_done(self, report: DuplicateReport) -> None:
        self._set_busy(False)
        self.dup_report = report
        took = format_duration(report.duration_seconds)
        note_txt = ""
        if report.notes:
            note_txt = " · " + "; ".join(report.notes[:2])
        self.dup_summary.configure(
            text=(
                f"{len(report.groups)} groups · "
                f"{report.duplicate_file_count} extras · "
                f"~{format_bytes(report.wasted_bytes)} reclaimable · "
                f"took {took}"
                f"{note_txt}  ·  Click a group to compare"
            )
        )
        # Append timing to Overview report
        if self.scan_result:
            self._append_overview_timing_block(report)

        self.tabs.set("Duplicates")
        self.workflow_banner.configure(
            text="Duplicates ready — click a group to compare. Prefer Quarantine over permanent delete."
        )
        self.update_idletasks()
        try:
            self._populate_group_list()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            for child in self.group_list.winfo_children():
                child.destroy()
            ctk.CTkLabel(
                self.group_list,
                text=f"Could not show groups:\n{exc}",
                wraplength=240,
                justify="left",
            ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
            self._set_status(f"Error showing duplicates: {exc}")
            return
        self._set_status(
            f"Duplicate search finished in {took}. Click a group to compare."
        )

    def _append_overview_timing_block(self, report: DuplicateReport) -> None:
        """Add duplicate-search timing into the Overview report text."""
        if not self.scan_result:
            return
        # Rebuild overview then append dup timing so Save report includes both
        self._update_overview()
        extra = [
            "",
            "── Duplicate search ──",
            f"Duration: {format_duration(report.duration_seconds)}",
            f"Groups: {len(report.groups)}",
            f"Extra files: {report.duplicate_file_count}",
            f"Reclaimable (approx): {format_bytes(report.wasted_bytes)}",
        ]
        for note in report.notes:
            extra.append(f"Note: {note}")
        current = self.overview_box.get("1.0", "end").rstrip()
        self._write_box(self.overview_box, current + "\n" + "\n".join(extra) + "\n")

    def quarantine_extras(self) -> None:
        extras = self._all_disk_extras()
        if extras is None:
            return
        count = len(extras)
        qpath = quarantine_root()
        ok = messagebox.askyesno(
            APP_TITLE,
            f"Move {count} extra copies (from every group) to quarantine?\n\n"
            f"Destination:\n{qpath}\n\n"
            "The oldest file in each group is kept. Files are moved, not permanently deleted.",
        )
        if not ok:
            return
        session, log = move_to_quarantine([e.path for e in extras])
        paths = {e.path for e in extras}
        self._remove_paths_from_reports(paths)
        messagebox.showinfo(
            APP_TITLE,
            f"Quarantine session created:\n{session}\n\n"
            "A manifest.json file lists original locations.\n\n"
            + "\n".join(log[:6]),
        )
        self._set_status(f"Quarantined {count} files → {session}")

    def delete_all_extras(self) -> None:
        """Permanently delete every extra copy across all groups (keeps oldest KEEP)."""
        extras = self._all_disk_extras()
        if extras is None:
            return
        count = len(extras)
        reclaim = sum(e.size for e in extras)
        ok = messagebox.askyesno(
            APP_TITLE,
            "DELETE ALL DUPLICATE EXTRAS\n\n"
            f"This permanently erases {count} extra file(s) "
            f"(~{format_bytes(reclaim)}) across every duplicate group.\n\n"
            "The oldest file in each group is kept.\n"
            "Zip members are skipped (cannot delete inside a zip alone).\n\n"
            "This cannot be undone by this app.\n"
            "Prefer “Quarantine all extras” if you might want files back.\n\n"
            "Continue?",
        )
        if not ok:
            return
        ok2 = messagebox.askyesno(
            APP_TITLE,
            f"Final confirmation: permanently delete all {count} extra duplicate(s)?",
        )
        if not ok2:
            return
        log = permanently_delete([e.path for e in extras])
        paths = {e.path for e in extras}
        self._remove_paths_from_reports(paths)
        messagebox.showinfo(
            APP_TITLE,
            f"Deleted {count} extra duplicate(s).\n\n" + "\n".join(log[:12]),
        )
        self._set_status(f"Permanently deleted {count} extra duplicate(s).")

    def _all_disk_extras(self) -> Optional[list[FileInfo]]:
        """Extra (non-KEEP) on-disk files from every group, or None if nothing to do."""
        if not self.dup_report or not self.dup_report.groups:
            messagebox.showinfo(APP_TITLE, "No duplicate extras found. Run Find duplicates first.")
            return None
        extras: list[FileInfo] = []
        for group in self.dup_report.groups:
            extras.extend(f for f in group.files[1:] if not f.is_inside_archive)
        if not extras:
            messagebox.showinfo(
                APP_TITLE,
                "No extra copies on disk "
                "(zip members cannot be removed alone).",
            )
            return None
        return extras

    # ---------- organise ----------

    def choose_dest(self) -> None:
        current = self.dest_entry.get().strip()
        initial = current if current else (self.source_paths[-1] if self.source_paths else str(Path.home()))
        path = ask_folder(
            self,
            title="Choose destination folder",
            initialdir=initial,
        )
        if not path:
            return
        self.dest_entry.delete(0, "end")
        self.dest_entry.insert(0, path)
        warn = destination_conflicts_with_sources(path, self.source_paths)
        if warn:
            messagebox.showwarning(APP_TITLE, warn)

    def preview_organise(self) -> None:
        if not self._has_scan_files():
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        dest = self.dest_entry.get().strip()
        if not dest:
            messagebox.showwarning(APP_TITLE, "Choose a destination folder.")
            return
        warn = destination_conflicts_with_sources(dest, self.source_paths)
        if warn and not messagebox.askyesno(APP_TITLE, warn + "\n\nPreview anyway?"):
            return
        layout_ids = self._selected_layout_ids()
        label = layout_combo_label(layout_ids)
        options = self._current_organise_options()
        plan = build_organise_plan(
            self._scan_files(), dest, layout_ids=layout_ids, options=options
        )
        self._last_organise_plan = plan
        self._update_layout_visual()
        lines = [
            f"Layout: {label}",
            f"Categories: {', '.join(sorted(options.categories or []))}",
            f"Media by date: {options.media_by_date} · Docs by extension: {options.documents_by_ext} · Separate archives: {options.separate_archives}",
            f"Copy (safer): {options.copy_instead_of_move} · Date prefix: {options.rename_with_date_prefix} · Sanitize names: {options.sanitize_filenames}",
            f"README notes: {options.add_readme_notes} · Archive older than: {options.archive_older_than_days} days",
            f"Planned actions: {len(plan.items)}",
            f"Skipped: {len(plan.skipped)}",
            "",
            "Tip: click Browse dry-run… to open a file-manager view of this plan.",
            "",
        ]
        for item in plan.items[:200]:
            lines.append(f"{item.category}: {item.source}  →  {item.destination}")
        if len(plan.items) > 200:
            lines.append(f"… and {len(plan.items) - 200} more")
        if plan.skipped:
            lines.append("")
            lines.append("Skipped (first 30):")
            for s in plan.skipped[:30]:
                lines.append(f"  - {s}")
        self._write_box(self.org_box, "\n".join(lines))
        self._organise_preview_key = self._organise_plan_key(dest, layout_ids, options)
        self._last_organise_plan = plan
        self._set_status(f"Organise preview ({label}): {len(plan.items)} actions planned.")

    def _organise_plan_key(self, dest: str, layout_ids, options: OrganiseOptions) -> str:
        cats = ",".join(sorted(options.categories or []))
        layouts = "+".join(layout_ids) if isinstance(layout_ids, list) else str(layout_ids)
        return (
            f"{dest}|{layouts}|{cats}|{options.copy_instead_of_move}|"
            f"{options.rename_with_date_prefix}|{options.sanitize_filenames}|"
            f"{options.archive_older_than_days}|{options.custom_structure_text}"
        )

    def open_plan_browser(self) -> None:
        """Open a Dolphin-style browser of the last dry-run / preview plan."""
        if not self._has_scan_files():
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        dest = self.dest_entry.get().strip()
        if not dest:
            messagebox.showwarning(APP_TITLE, "Choose a destination folder.")
            return
        layout_ids = self._selected_layout_ids()
        options = self._current_organise_options()
        plan = self._last_organise_plan
        if plan is None or not plan.items:
            plan = build_organise_plan(
                self._scan_files(), dest, layout_ids=layout_ids, options=options
            )
            self._last_organise_plan = plan
        if not plan.items:
            messagebox.showinfo(APP_TITLE, "Nothing to show — preview a plan with files first.")
            return
        win = PlanBrowserWindow(
            self,
            plan,
            dest,
            title=f"Dry-run browser — {layout_combo_label(layout_ids)}",
        )
        win.focus()

    def apply_organise(self) -> None:
        if self._busy:
            return
        if not self._has_scan_files():
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        dest = self.dest_entry.get().strip()
        if not dest:
            messagebox.showwarning(APP_TITLE, "Choose a destination folder.")
            return

        dest_path = Path(dest).expanduser()
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Cannot create destination folder:\n{exc}")
            return
        if not os.access(dest_path, os.W_OK):
            messagebox.showerror(
                APP_TITLE,
                f"Destination is not writable:\n{dest_path}\n\n"
                "Remount the drive as read-write, or choose another folder.",
            )
            return

        warn = destination_conflicts_with_sources(dest, self.source_paths)
        dry = self.dry_run_var.get()
        layout_ids = self._selected_layout_ids()
        label = layout_combo_label(layout_ids)
        options = self._current_organise_options()
        plan_key = self._organise_plan_key(dest, layout_ids, options)

        # Safer: refuse risky destination for real copy/move (preview still allowed with confirm)
        if warn and not dry:
            messagebox.showerror(
                APP_TITLE,
                warn
                + "\n\nReal organise is blocked for safety.\n"
                "Choose a destination outside your scan sources.",
            )
            return
        if warn and dry and not messagebox.askyesno(APP_TITLE, warn + "\n\nPreview anyway?"):
            return

        # Safer: require Preview plan before a real change
        if not dry and self._organise_preview_key != plan_key:
            messagebox.showwarning(
                APP_TITLE,
                "Safer organise: click Preview plan first with the same options,\n"
                "check the plan, then untick Dry run and Apply.",
            )
            return

        plan = build_organise_plan(
            self._scan_files(), dest, layout_ids=layout_ids, options=options
        )
        if not plan.items:
            messagebox.showinfo(APP_TITLE, "Nothing to organise with the current options.")
            return

        if not dry:
            action = "COPY" if options.copy_instead_of_move else "MOVE"
            ok = messagebox.askyesno(
                APP_TITLE,
                f"Layout: {label}\n"
                f"Action: {action} {len(plan.items)} files into:\n{dest}\n\n"
                + (
                    "Copy keeps originals until you delete them later (safer).\n"
                    if options.copy_instead_of_move
                    else "MOVE changes where files live. Prefer Copy if unsure.\n"
                )
                + "Continue?",
            )
            if not ok:
                return
            # Extra confirm for destructive move
            if not options.copy_instead_of_move:
                ok2 = messagebox.askyesno(
                    APP_TITLE,
                    f"Final confirm: MOVE {len(plan.items)} files?\n\n"
                    "This cannot be undone automatically.\n"
                    "Quarantine is not used for organise.",
                )
                if not ok2:
                    return

        self._last_organise_plan = plan
        self._set_busy(True)
        mode_label = "Dry run" if dry else ("Copy" if options.copy_instead_of_move else "Move")
        self._set_status(f"{mode_label} starting ({preset.name})…")
        threading.Thread(
            target=self._organise_worker,
            args=(plan, dry, options, dest, preset.name),
            daemon=True,
        ).start()

    def _organise_worker(
        self,
        plan,
        dry: bool,
        options: OrganiseOptions,
        dest: str,
        layout_name: str,
    ) -> None:
        started = time.monotonic()
        log = apply_organise_plan(
            plan,
            dry_run=dry,
            status_cb=lambda msg: self.after(0, self._set_status, msg),
            should_cancel=self._should_cancel,
            options=options,
            dest_root=dest,
        )
        took = format_duration(time.monotonic() - started)
        self.after(
            0,
            self._organise_done,
            log,
            dry,
            options.copy_instead_of_move,
            layout_name,
            len(plan.items),
            took,
        )

    def _organise_done(
        self,
        log: list[str],
        dry: bool,
        copied: bool,
        layout_name: str,
        count: int,
        took: str,
    ) -> None:
        self._set_busy(False)
        mode = "Dry run" if dry else ("Copy" if copied else "Move")
        header = [
            f"Layout: {layout_name}",
            f"Duration: {took}",
            f"Mode: {mode.lower()}",
            "",
        ]
        self._write_box(self.org_box, "\n".join(header + log[:500]))
        self._set_status(f"{mode} finished ({layout_name}): {count} items in {took}.")
        if not dry:
            tip = (
                "Done. Spot-check a few files in the destination, "
                "then optionally delete originals only if you used Copy."
                if copied
                else "Done. Consider scanning again and saving an inventory."
            )
            messagebox.showinfo(APP_TITLE, tip)
            if not copied:
                self.scan_result = None
                self.dup_report = None
            self._refresh_layout_options_from_scan()

    def save_inventory(self) -> None:
        if not self._has_scan_files():
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save file inventory",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        text = build_inventory_text(self._scan_files(), title="Archive Organiser inventory")
        Path(path).write_text(text, encoding="utf-8")
        self._set_status(f"Inventory saved: {path}")

    def show_organisation_tips(self) -> None:
        files = self._scan_files() if self.scan_result else []
        tips = organisation_advice(files)
        messagebox.showinfo(APP_TITLE, "Organisation tips\n\n" + "\n\n".join(f"• {t}" for t in tips))
