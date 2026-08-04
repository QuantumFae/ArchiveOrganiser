"""
Archive Organiser – main window.

Private by design: everything runs on your computer.
No files are uploaded anywhere.
"""

import json
import os
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from app_settings import last_scan_db_path, load_settings, save_settings
from duplicates import (
    DuplicateGroup,
    DuplicateReport,
    DuplicateSearchOptions,
    find_duplicate_matches,
    sort_groups_by_file_type,
)
from folder_picker import ask_folder
from helpers import format_bytes, format_duration, open_containing_folder
from copyable_text import enable_copyable_text, make_textbox_readonly_copyable
from models import FileInfo, ScanResult
from best_practices import build_inventory_text, organisation_advice
from custom_structure import DEFAULT_CUSTOM_TEMPLATE
from ctk_theme import (
    DANGER,
    DANGER_HOVER,
    LIST_BTN,
    LIST_BTN_TEXT,
    MUTED,
    PRIMARY,
    SELECT,
    SURFACE,
    WARNING,
    apply_theme,
    paned_bg,
    primary_button,
)
from organise_suggest import (
    OrganiseSuggestion,
    format_suggestion_summary,
    suggest_organise_options_auto,
)
from organiser import (
    ALL_CATEGORIES,
    CATEGORY_MODE_LABELS,
    CATEGORY_SUBFOLDER_MODES,
    LAYOUT_PRESETS,
    MEDIA_DATE_DEPTH_LABELS,
    MEDIA_DATE_DEPTHS,
    OrganiseOptions,
    apply_organise_plan,
    build_organise_plan,
    destination_conflicts_with_sources,
    get_layout,
    layout_combine_order_label,
    layout_combo_label,
    layout_tree_text,
    normalize_category_subfolders,
    normalize_media_date_depth,
    recommended_layout_core,
    recommended_layout_ids,
)
from plan_browser import PlanBrowserWindow
from preview import load_preview_for_info
from quarantine import (
    latest_quarantine_session,
    move_to_quarantine,
    permanently_delete,
    quarantine_root,
)
from scan_store import ScanStore
from scanner import ScanOptions, scan_paths


APP_TITLE = "Archive Organiser"
APP_SIZE = "1280x800"
_PROGRESS_FRACTION_RE = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)")
# Side-by-side paned compare works up to this many files; larger groups use a scrollable grid
_COMPARE_SIDE_BY_SIDE_MAX = 4
_COMPARE_GRID_CARD_MIN = 250
_COMPARE_GRID_THUMB = 220


class ArchiveOrganiserApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(900, 600)

        self._settings = load_settings()
        geometry = str(self._settings.get("window_geometry") or APP_SIZE)
        try:
            self.geometry(geometry)
        except tk.TclError:
            self.geometry(APP_SIZE)

        apply_theme()
        appearance = str(self._settings.get("appearance") or "System")
        ctk.set_appearance_mode(appearance)

        self.source_paths: list[str] = []
        self.scan_result: Optional[ScanResult] = None
        self.dup_report: Optional[DuplicateReport] = None
        self._cancel_flag = False
        self._busy = False
        self._selected_group_index: Optional[int] = None
        # IntVar (0/1) matches CTkCheckBox onvalue/offvalue more reliably than BooleanVar
        self._compare_check_vars: list[tk.IntVar] = []
        self._compare_checkboxes: list = []
        self._compare_file_infos: list[FileInfo] = []
        self._compare_is_keep: list[bool] = []
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
        self._compare_mode = "paned"  # "paned" (≤4) or "grid" (5+)
        self._compare_grid_scroll = None
        self._compare_grid_cards: list = []
        self._compare_grid_cols = 0
        self._busy_started = 0.0
        self._busy_detail = ""
        self._busy_tick_job: Optional[str] = None
        self._organise_preview_key: Optional[str] = None
        self._source_check_vars: dict[str, tk.BooleanVar] = {}
        self._group_list_shown = 0
        self._group_page_size = 80
        self._workflow_banner: Optional[ctk.CTkLabel] = None
        self._progress_mode = "indeterminate"
        # Paths marked across all duplicate groups (survive Prev/Next)
        self._dup_marked_paths: set[str] = set()
        self._last_quarantine_session = str(
            self._settings.get("last_quarantine_session") or ""
        )
        self._saved_layout_ids: list[str] = list(
            self._settings.get("layout_ids") or []
        )
        self._geometry_save_job: Optional[str] = None

        self._build_layout()
        self._enable_copyable_text()
        self._apply_saved_preferences()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.after(200, self._try_autoload_last_scan)
        self._set_status("Ready. Add folders or drives, then Scan.")
        self._refresh_workflow()

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
        self.appearance_var = tk.StringVar(value=str(self._settings.get("appearance") or "System"))
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
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=PRIMARY,
            fg_color=SURFACE,
            corner_radius=6,
            height=28,
            padx=10,
        )
        self.workflow_banner.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 2))
        self._workflow_banner = self.workflow_banner
        self._organise_preview_ready = False

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

        self.include_junk_var = tk.BooleanVar(value=bool(self._settings.get("include_junk")))
        self.scan_zips_var = tk.BooleanVar(value=bool(self._settings.get("scan_zips")))
        self.extract_zips_var = tk.BooleanVar(value=bool(self._settings.get("extract_zips")))
        self.delete_zip_after_extract_var = tk.BooleanVar(
            value=bool(self._settings.get("delete_zip_after_extract"))
        )
        self.delete_zip_if_low_space_var = tk.BooleanVar(
            value=bool(self._settings.get("delete_zip_if_low_space"))
        )
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
        ctk.CTkCheckBox(
            opts_frame,
            text=(
                "Unzip .zip files during scan "
                "(Vacation.zip → Vacation_unzipped/, then scan that folder)"
            ),
            variable=self.extract_zips_var,
            command=self._sync_unzip_delete_options,
        ).pack(anchor="w", padx=8, pady=2)
        self.delete_zip_after_extract_cb = ctk.CTkCheckBox(
            opts_frame,
            text=(
                "After a successful unzip, delete the original .zip "
                "(never deletes if unzip fails; scan uses the unzipped folder)"
            ),
            variable=self.delete_zip_after_extract_var,
            command=self._sync_unzip_delete_options,
        )
        self.delete_zip_after_extract_cb.pack(anchor="w", padx=28, pady=(0, 2))
        self.delete_zip_low_space_cb = ctk.CTkCheckBox(
            opts_frame,
            text=(
                "Only delete the .zip when the drive is low on space "
                "(after a successful unzip; ignored if “delete .zip” above is on)"
            ),
            variable=self.delete_zip_if_low_space_var,
        )
        self.delete_zip_low_space_cb.pack(anchor="w", padx=28, pady=(0, 2))
        self._sync_unzip_delete_options()

        buttons = ctk.CTkFrame(opts_frame, fg_color="transparent")
        buttons.pack(fill="x", padx=8, pady=(8, 2))
        primary_button(
            buttons, text="Add folder / drive", command=self.add_source, height=36, width=160
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Remove selected", command=self.remove_selected_sources, height=36
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Select all", width=90, height=36, command=self.select_all_sources
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Clear ticks", width=90, height=36, command=self.clear_source_ticks
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Remove all sources…", command=self.clear_sources, width=150, height=36
        ).pack(side="left", padx=(0, 8))

        actions = ctk.CTkFrame(opts_frame, fg_color="transparent")
        actions.pack(fill="x", padx=8, pady=(2, 8))
        ctk.CTkButton(
            actions, text="Reload last scan", command=self.reload_last_scan, width=140, height=36
        ).pack(side="left")
        primary_button(
            actions, text="Scan now", command=self.start_scan, height=36, width=120
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
            top,
            text="No scan yet.\nAdd folders on Sources, then Scan now — elapsed time appears here while working.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self.overview_summary.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        self.overview_box = ctk.CTkTextbox(bottom, font=ctk.CTkFont(family="monospace", size=12))
        self.overview_box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.overview_box.insert(
            "1.0",
            "Scan progress and a clear report will appear here.\n"
            "While scanning, the status bar and this summary show elapsed time.",
        )
        make_textbox_readonly_copyable(self.overview_box)

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
        self.compare_header_label = ctk.CTkLabel(
            compare_header,
            text="Side-by-side compare  ·  groups of 5+ open as a scrollable image grid",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.compare_header_label.grid(row=0, column=0, sticky="w")
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

        select_row = ctk.CTkFrame(tab, fg_color="transparent")
        select_row.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 2))
        ctk.CTkButton(
            select_row,
            text="Select extras only (this group)",
            command=self.select_extras_in_group,
            width=210,
            height=30,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            select_row, text="Select all", command=self.select_all_in_group, width=90, height=30
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            select_row,
            text="Clear selection",
            command=self.clear_compare_selection,
            width=120,
            height=30,
        ).pack(side="left", padx=(0, 6))
        primary_button(
            select_row,
            text="Mark all extras (every group)",
            command=self.mark_all_extras,
            width=210,
            height=30,
        ).pack(side="left", padx=(0, 6))
        self.dup_marked_label = ctk.CTkLabel(
            select_row,
            text="Marked: 0",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=PRIMARY,
        )
        self.dup_marked_label.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            select_row,
            text="← → groups · marks stay as you browse",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(side="left", padx=(8, 0))

        action_row = ctk.CTkFrame(tab, fg_color="transparent")
        action_row.grid(row=3, column=0, sticky="ew", padx=6, pady=(0, 4))
        ctk.CTkButton(
            action_row,
            text="Open last quarantine",
            command=self.open_last_quarantine,
            height=28,
            width=150,
        ).pack(side="left", padx=(0, 6))
        primary_button(
            action_row,
            text="Quarantine selected / marked",
            command=self.quarantine_selected_compare_files,
            height=28,
            width=200,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            action_row,
            text="Delete selected / marked",
            command=self.delete_selected_compare_files,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            height=28,
            width=170,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            action_row,
            text="Quarantine all extras",
            command=self.quarantine_extras,
            fg_color=WARNING,
            height=28,
            width=150,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            action_row,
            text="Delete all extras",
            command=self.delete_all_extras,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            height=28,
            width=130,
        ).pack(side="right", padx=(6, 0))

        # ← / → move between groups when the Duplicates tab is active
        self.bind_all("<Left>", self._on_duplicates_left_key, add="+")
        self.bind_all("<Right>", self._on_duplicates_right_key, add="+")
        self._show_duplicates_empty_state()

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
        self._compare_is_keep.clear()
        self._compare_image_refs.clear()
        self._compare_pil_images.clear()
        self._compare_img_labels.clear()
        self._selected_group_index = None
        self._compare_cards_paned = None
        self._compare_vpaned = None
        self._compare_hpaned_top = None
        self._compare_hpaned_bottom = None
        self._compare_mode = "paned"
        self._compare_grid_scroll = None
        self._compare_grid_cards = []
        self._compare_grid_cols = 0

    def _show_duplicates_empty_state(self) -> None:
        """Friendly placeholders before Find duplicates has been run."""
        self._clear_group_list()
        self._clear_compare_panel()
        tip = (
            "Run Find duplicates on Overview after a scan.\n"
            "Then click a group here to compare copies."
        )
        if self._has_scan_files():
            tip = (
                "Scan ready — open Overview and click Find duplicates.\n"
                "Groups will appear in this list."
            )
        ctk.CTkLabel(
            self.group_list,
            text=tip,
            justify="left",
            anchor="w",
            text_color=MUTED,
            wraplength=200,
        ).pack(anchor="w", padx=6, pady=8)
        ctk.CTkLabel(
            self.compare_frame,
            text="Compare view is empty until you open a duplicate group.\n"
            "Use ← Prev / Next → (or arrow keys) to move between groups.",
            justify="left",
            anchor="w",
            text_color=MUTED,
            wraplength=520,
        ).pack(anchor="w", padx=10, pady=12)

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
            self._enable_copyable_text(self.group_list)
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
            # Section header when category changes
            cat = group.primary_category
            prev_cat = groups[index - 1].primary_category if index > 0 else None
            if cat != prev_cat:
                hdr = ctk.CTkLabel(
                    self.group_list,
                    text=f"── {cat} ──",
                    anchor="w",
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=PRIMARY,
                )
                hdr.pack(fill="x", padx=4, pady=(8, 2))
            label = group.english_heading()
            marked_in_group = sum(
                1
                for f in group.files[1:]
                if not f.is_inside_archive and self._path_key(f) in self._dup_marked_paths
            )
            if marked_in_group:
                label = f"✓ {label}"
            btn = ctk.CTkButton(
                self.group_list,
                text=label,
                anchor="w",
                height=56,
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
        self._enable_copyable_text(self.group_list)

        for i, btn in enumerate(self._group_buttons):
            # Button i corresponds to group index i only for the first page;
            # after Load more, buttons are sequential from 0..shown-1 matching groups 0..shown-1
            group_index = i
            if group_index == index:
                btn.configure(fg_color=SELECT, text_color="white")
            else:
                btn.configure(fg_color=LIST_BTN, text_color=LIST_BTN_TEXT)

    def show_duplicate_group(self, index: int) -> None:
        """Show every file in one duplicate group (side-by-side or scrollable grid)."""
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
        if count > _COMPARE_SIDE_BY_SIDE_MAX:
            self._show_compare_grid(group)
        else:
            self._show_compare_paned(group)

        # Default: restore marks / auto-select extras; focus so ← → work
        self._restore_marks_to_checkboxes()
        try:
            self.focus_set()
        except Exception:
            pass

        marked_here = sum(1 for var in self._compare_check_vars if int(var.get()))
        layout_note = (
            "scroll grid"
            if self._compare_mode == "grid"
            else "side-by-side"
        )
        self._set_status(
            f"Viewing: {group.english_heading().splitlines()[0]} · "
            f"group {index + 1}/{len(self.dup_report.groups)} · "
            f"{count} files ({layout_note}) · "
            f"{marked_here} marked here · {len(self._dup_marked_paths)} marked total · "
            "← → Prev/Next"
        )
        self._refresh_marked_label()
        self._enable_copyable_text(self.compare_frame)

    def _show_compare_paned(self, group) -> None:
        """Classic side-by-side layout for small groups (≤4 files)."""
        self._compare_mode = "paned"
        count = len(group.files)
        if hasattr(self, "compare_header_label"):
            self.compare_header_label.configure(
                text="Side-by-side compare  ·  drag ↕ Preview/Info  ·  drag ↔ columns  ·  layout kept between groups"
            )

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
            role = self._duplicate_role_label(col)
            is_keep = col == 0
            preview_col = self._build_preview_column(
                h_top, info, role, is_keep=is_keep, total_cards=count
            )
            info_col = self._build_info_column(h_bottom, info, role, is_keep=is_keep)
            h_top.add(preview_col, minsize=160)
            h_bottom.add(info_col, minsize=160)

        vpaned.add(top_host, minsize=120)
        vpaned.add(bottom_host, minsize=120)

        for paned in (vpaned, h_top, h_bottom):
            paned.bind("<ButtonRelease-1>", self._on_compare_sash)
            paned.bind("<B1-Motion>", self._on_compare_sash)

        self.after(40, self._restore_compare_layout)
        self.after(200, self._restore_compare_layout)

    def _show_compare_grid(self, group) -> None:
        """Scrollable card grid so larger similar-file groups stay readable."""
        self._compare_mode = "grid"
        count = len(group.files)
        if hasattr(self, "compare_header_label"):
            self.compare_header_label.configure(
                text=(
                    f"Scrollable grid · {count} files · "
                    "images stay large — scroll to see every copy"
                )
            )

        tip = ctk.CTkLabel(
            self.compare_frame,
            text=(
                f"{count} files in this group. Scroll to review all images. "
                "Mark extras with the checkbox on each card."
            ),
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        tip.pack(fill="x", padx=8, pady=(4, 2))

        scroll = ctk.CTkScrollableFrame(self.compare_frame, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._compare_grid_scroll = scroll
        self._compare_grid_cards = []

        for col, info in enumerate(group.files):
            role = self._duplicate_role_label(col)
            is_keep = col == 0
            card = self._build_compare_grid_card(
                scroll, info, role, is_keep=is_keep, total_cards=count
            )
            self._compare_grid_cards.append(card)

        self.after(40, self._layout_compare_grid)
        self.after(200, self._layout_compare_grid)

    def _compare_grid_column_count(self) -> int:
        """How many cards fit across the compare pane at a comfortable width."""
        try:
            width = max(int(self.compare_frame.winfo_width()), 400)
        except Exception:
            width = 900
        cols = max(2, width // _COMPARE_GRID_CARD_MIN)
        return min(4, cols)

    def _layout_compare_grid(self) -> None:
        """Place grid cards in 2–4 columns based on current pane width."""
        self._compare_resize_job = None
        if self._compare_mode != "grid" or not self._compare_grid_cards:
            return
        scroll = self._compare_grid_scroll
        if scroll is None:
            return
        cols = self._compare_grid_column_count()
        self._compare_grid_cols = cols
        for i in range(8):
            try:
                scroll.grid_columnconfigure(i, weight=0, minsize=0)
            except Exception:
                pass
        for c in range(cols):
            scroll.grid_columnconfigure(c, weight=1, minsize=_COMPARE_GRID_CARD_MIN - 10)
        for i, card in enumerate(self._compare_grid_cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
        self._apply_compare_image_sizes()

    def _build_compare_grid_card(
        self,
        parent,
        info: FileInfo,
        role: str,
        is_keep: bool = False,
        total_cards: int = 5,
    ) -> ctk.CTkFrame:
        """One comfortable card: mark + image + short details (for large groups)."""
        card = ctk.CTkFrame(parent)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(3, weight=1)

        role_color = PRIMARY if is_keep else WARNING
        ctk.CTkLabel(
            card,
            text=role,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=role_color,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

        mark_row = ctk.CTkFrame(card, fg_color="transparent")
        mark_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        check_var = tk.IntVar(value=0)
        select_text = "Mark for quarantine/delete"
        if info.is_inside_archive:
            select_text = "Inside zip (can't quarantine alone)"
        elif is_keep:
            select_text = "Keep (not an extra)"
        checkbox = ctk.CTkCheckBox(
            mark_row,
            text=select_text,
            variable=check_var,
            checkbox_width=22,
            checkbox_height=22,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda i=info, v=check_var: self._on_mark_toggle(i, v),
        )
        checkbox.pack(side="left", fill="x", expand=True)
        if info.is_inside_archive:
            checkbox.configure(state="disabled")
            check_var.set(0)
        self._compare_check_vars.append(check_var)
        self._compare_checkboxes.append(checkbox)
        self._compare_file_infos.append(info)
        self._compare_is_keep.append(is_keep)

        ctk.CTkLabel(
            card,
            text=f"{info.category}  ·  {info.name}",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))

        preview_host = ctk.CTkFrame(card, fg_color="transparent")
        preview_host.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 4))
        preview_host.grid_columnconfigure(0, weight=1)
        preview_host.grid_rowconfigure(0, weight=1)

        preview = load_preview_for_info(info, role)
        show_image = preview.image is not None and preview.kind in ("image", "composite")
        show_text = bool(preview.text_content or preview.error) and preview.kind in (
            "text",
            "composite",
        )

        if show_image and not show_text:
            self._compare_pil_images.append(preview.image)
            max_side = _COMPARE_GRID_THUMB
            pil = preview.image.copy()
            pil.thumbnail((max_side, max_side))
            ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._compare_image_refs.append(ctk_image)
            img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
            img_label.grid(row=0, column=0, padx=4, pady=4, sticky="n")
            self._compare_img_labels.append(img_label)
        elif show_image and show_text:
            self._compare_pil_images.append(preview.image)
            max_side = max(140, _COMPARE_GRID_THUMB // 2)
            pil = preview.image.copy()
            pil.thumbnail((max_side, max_side))
            ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._compare_image_refs.append(ctk_image)
            img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
            img_label.grid(row=0, column=0, padx=4, pady=(4, 2), sticky="n")
            self._compare_img_labels.append(img_label)
            text_box = ctk.CTkTextbox(preview_host, height=90)
            text_box.grid(row=1, column=0, padx=2, pady=(0, 2), sticky="ew")
            text_box.insert("1.0", preview.text_content or preview.error)
            make_textbox_readonly_copyable(text_box)
        else:
            text_box = ctk.CTkTextbox(preview_host, height=120)
            text_box.grid(row=0, column=0, padx=2, pady=2, sticky="nsew")
            content = preview.text_content or preview.error or "No preview available"
            if preview.image is not None:
                self._compare_pil_images.append(preview.image)
                pil = preview.image.copy()
                pil.thumbnail((_COMPARE_GRID_THUMB, _COMPARE_GRID_THUMB))
                ctk_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                self._compare_image_refs.append(ctk_image)
                img_label = ctk.CTkLabel(preview_host, text="", image=ctk_image)
                img_label.grid(row=0, column=0, padx=4, pady=(4, 2), sticky="n")
                self._compare_img_labels.append(img_label)
                text_box.grid(row=1, column=0, padx=2, pady=(0, 2), sticky="ew")
            text_box.insert("1.0", content)
            make_textbox_readonly_copyable(text_box)

        # Compact path + open
        path_txt = str(info.path)
        if len(path_txt) > 64:
            path_txt = "…" + path_txt[-63:]
        ctk.CTkLabel(
            card,
            text=path_txt,
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 2))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", padx=8, pady=(2, 8))
        open_target = info.archive_container if info.is_inside_archive else info.path
        ctk.CTkButton(
            actions,
            text="Open folder",
            width=110,
            height=28,
            command=lambda p=open_target: self._open_folder(p),
        ).pack(side="right")
        return card

    @staticmethod
    def _duplicate_role_label(index: int) -> str:
        """Plain-English role for a file in a duplicate group (oldest is Keep)."""
        if index == 0:
            return "Keep (oldest)"
        if index == 1:
            return "Extra copy"
        return f"Extra copy {index}"

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
        self,
        parent,
        info: FileInfo,
        role: str,
        is_keep: bool = False,
        total_cards: int = 2,
    ) -> ctk.CTkFrame:
        """Top-row cell: mark checkbox (always visible) + role + preview."""
        col = ctk.CTkFrame(parent)
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(3, weight=1)

        role_color = PRIMARY if is_keep else WARNING
        ctk.CTkLabel(
            col,
            text=role,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=role_color,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

        # Mark checkbox lives in the TOP pane so Select extras is always visible
        mark_row = ctk.CTkFrame(col, fg_color="transparent")
        mark_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        check_var = tk.IntVar(value=0)
        select_text = "Mark for quarantine/delete"
        if info.is_inside_archive:
            select_text = "Inside zip (can't quarantine alone)"
        elif is_keep:
            select_text = "Keep (not an extra)"
        checkbox = ctk.CTkCheckBox(
            mark_row,
            text=select_text,
            variable=check_var,
            checkbox_width=22,
            checkbox_height=22,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda i=info, v=check_var: self._on_mark_toggle(i, v),
        )
        checkbox.pack(side="left", fill="x", expand=True)
        if info.is_inside_archive:
            checkbox.configure(state="disabled")
            check_var.set(0)
        self._compare_check_vars.append(check_var)
        self._compare_checkboxes.append(checkbox)
        self._compare_file_infos.append(info)
        self._compare_is_keep.append(is_keep)

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
        ).grid(row=2, column=0, sticky="w", padx=8, pady=(0, 2))

        preview_host = ctk.CTkFrame(col, fg_color=("gray90", "gray20"))
        preview_host.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 6))
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
            make_textbox_readonly_copyable(text_box)
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
            make_textbox_readonly_copyable(text_box)

        return col

    def _build_info_column(
        self, parent, info: FileInfo, role: str, is_keep: bool = False
    ) -> ctk.CTkFrame:
        """Bottom-row cell: bold category headings + full metadata + open folder."""
        col = ctk.CTkFrame(parent)
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(2, weight=1)

        role_color = PRIMARY if is_keep else WARNING
        ctk.CTkLabel(
            col,
            text=role,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=role_color,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        ctk.CTkLabel(
            col,
            text=f"{info.category}  ·  {info.name}",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))

        preview = load_preview_for_info(info, role)
        meta = ctk.CTkScrollableFrame(col)
        meta.grid(row=2, column=0, padx=6, pady=(0, 4), sticky="nsew")
        meta.grid_columnconfigure(0, weight=1)
        self._fill_file_metadata_panel(meta, preview.info_text)

        actions = ctk.CTkFrame(col, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 8))
        open_target = info.archive_container if info.is_inside_archive else info.path
        ctk.CTkButton(
            actions,
            text="Open folder",
            width=110,
            height=28,
            command=lambda p=open_target: self._open_folder(p),
        ).pack(side="right")
        return col

    def _fill_file_metadata_panel(self, parent, info_text: str) -> None:
        """Render metadata with bold section headings (ROLE, FILE, …)."""
        current_title = ""
        body_lines: list[str] = []

        def flush() -> None:
            nonlocal current_title, body_lines
            if not current_title and not body_lines:
                return
            if current_title:
                ctk.CTkLabel(
                    parent,
                    text=current_title,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=PRIMARY,
                    anchor="w",
                ).pack(anchor="w", padx=4, pady=(8, 0))
            text = "\n".join(body_lines).strip("\n")
            if text:
                ctk.CTkLabel(
                    parent,
                    text=text,
                    font=ctk.CTkFont(size=11),
                    anchor="w",
                    justify="left",
                    wraplength=420,
                ).pack(anchor="w", padx=10, pady=(0, 2))
            current_title = ""
            body_lines = []

        for raw in (info_text or "").splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            # Section headings from preview.build_info_text are ALL CAPS words
            compact = stripped.replace(" ", "").replace("&", "")
            if stripped and stripped == stripped.upper() and compact.isalpha():
                flush()
                current_title = stripped
                continue
            body_lines.append(line if line.startswith("  ") else f"  {line}" if line else "")
        flush()

    def _path_key(self, info: FileInfo) -> str:
        try:
            return str(info.path.resolve())
        except OSError:
            return str(info.path)

    def _refresh_marked_label(self) -> None:
        if hasattr(self, "dup_marked_label"):
            self.dup_marked_label.configure(text=f"Marked: {len(self._dup_marked_paths)}")

    def _refresh_group_mark_indicators(self) -> None:
        """Update ✓ badges on group list buttons after mark changes."""
        if not self.dup_report or not self._group_buttons:
            return
        for i, btn in enumerate(self._group_buttons):
            if i >= len(self.dup_report.groups):
                break
            group = self.dup_report.groups[i]
            label = group.english_heading()
            marked_in_group = sum(
                1
                for f in group.files[1:]
                if not f.is_inside_archive and self._path_key(f) in self._dup_marked_paths
            )
            if marked_in_group:
                label = f"✓ {label}"
            try:
                btn.configure(text=label)
            except Exception:
                pass
        if self._selected_group_index is not None:
            self._highlight_group_button(self._selected_group_index)

    def _on_mark_toggle(self, info: FileInfo, var: tk.Variable) -> None:
        if info.is_inside_archive:
            return
        key = self._path_key(info)
        if int(var.get()):
            self._dup_marked_paths.add(key)
        else:
            self._dup_marked_paths.discard(key)
        self._refresh_marked_label()
        self._refresh_group_mark_indicators()

    def _set_checkbox(self, checkbox, var: tk.Variable, want: bool) -> None:
        """Force CTkCheckBox visual + IntVar to match want (CTk can lag on var-only)."""
        # Cancel a previous delayed force on this widget so rapid clicks don't fight
        old_job = getattr(checkbox, "_ao_force_job", None)
        if old_job is not None:
            try:
                self.after_cancel(old_job)
            except Exception:
                pass
            checkbox._ao_force_job = None

        self._apply_checkbox_state(checkbox, var, want)
        checkbox._ao_force_job = self.after(
            20,
            lambda c=checkbox, v=var, w=want: self._force_checkbox_visual(c, v, w),
        )

    def _apply_checkbox_state(self, checkbox, var: tk.Variable, want: bool) -> None:
        want_i = 1 if want else 0
        try:
            checkbox._variable_callback_blocked = True
        except Exception:
            pass
        try:
            var.set(want_i)
            # Drive CTk's internal draw path directly — most reliable on Linux
            checkbox._check_state = bool(want)
            if getattr(checkbox, "_variable", None) is not None:
                on_v = getattr(checkbox, "_onvalue", 1)
                off_v = getattr(checkbox, "_offvalue", 0)
                checkbox._variable.set(on_v if want else off_v)
            checkbox._draw()
        except Exception:
            try:
                var.set(want_i)
                if want:
                    checkbox.select()
                else:
                    checkbox.deselect()
            except Exception:
                pass
        finally:
            try:
                checkbox._variable_callback_blocked = False
            except Exception:
                pass

    def _force_checkbox_visual(self, checkbox, var: tk.Variable, want: bool) -> None:
        try:
            if not checkbox.winfo_exists():
                return
        except Exception:
            return
        self._apply_checkbox_state(checkbox, var, want)
        try:
            checkbox._ao_force_job = None
        except Exception:
            pass

    def _restore_marks_to_checkboxes(self) -> None:
        """Apply persistent marks (or auto-select extras) into the open group."""
        if not self._compare_file_infos:
            return
        # If nothing marked yet for this report session, auto-mark extras here
        any_marked = any(
            self._path_key(info) in self._dup_marked_paths
            for info in self._compare_file_infos
            if not info.is_inside_archive
        )
        for i, (var, info, checkbox) in enumerate(
            zip(self._compare_check_vars, self._compare_file_infos, self._compare_checkboxes)
        ):
            is_keep = self._compare_is_keep[i] if i < len(self._compare_is_keep) else i == 0
            if info.is_inside_archive:
                self._set_checkbox(checkbox, var, False)
                continue
            key = self._path_key(info)
            if key in self._dup_marked_paths:
                want = True
            elif not any_marked and not is_keep:
                # First visit pattern: auto-select extras
                want = True
                self._dup_marked_paths.add(key)
            else:
                want = False
            self._set_checkbox(checkbox, var, want)
        self._refresh_marked_label()

    def select_extras_in_group(self) -> None:
        """Select every extra copy (not Keep); skip zip members. Marks persist."""
        if not self._compare_check_vars or not self._compare_file_infos:
            self._set_status("Open a duplicate group first, then use Select extras only.")
            messagebox.showinfo(
                APP_TITLE,
                "Open a duplicate group on the left first.\n\n"
                "Then click Select extras only (this group).\n"
                "Or use Mark all extras (every group) to mark everything at once.",
            )
            return
        selected = 0
        for i, (var, info, checkbox) in enumerate(
            zip(
                self._compare_check_vars,
                self._compare_file_infos,
                self._compare_checkboxes,
            )
        ):
            is_keep = self._compare_is_keep[i] if i < len(self._compare_is_keep) else i == 0
            want = (not is_keep) and (not info.is_inside_archive)
            self._set_checkbox(checkbox, var, want)
            key = self._path_key(info)
            if want:
                self._dup_marked_paths.add(key)
                selected += 1
            else:
                self._dup_marked_paths.discard(key)
        # Immediate second pass (no waiting for after()) so the tick is visible now
        for i, (var, info, checkbox) in enumerate(
            zip(
                self._compare_check_vars,
                self._compare_file_infos,
                self._compare_checkboxes,
            )
        ):
            is_keep = self._compare_is_keep[i] if i < len(self._compare_is_keep) else i == 0
            want = (not is_keep) and (not info.is_inside_archive)
            self._apply_checkbox_state(checkbox, var, want)
        self._refresh_marked_label()
        self._refresh_group_mark_indicators()
        # Verify end-to-end: checkbox visual + var + mark set agree
        mismatches = []
        for i, (var, info, checkbox) in enumerate(
            zip(
                self._compare_check_vars,
                self._compare_file_infos,
                self._compare_checkboxes,
            )
        ):
            is_keep = self._compare_is_keep[i] if i < len(self._compare_is_keep) else i == 0
            want = (not is_keep) and (not info.is_inside_archive)
            key = self._path_key(info)
            visual = bool(getattr(checkbox, "_check_state", int(var.get())))
            marked = key in self._dup_marked_paths
            if visual != want or bool(int(var.get())) != want or marked != want:
                mismatches.append(info.name)
        if mismatches:
            self._set_status(
                f"Select extras had trouble updating: {', '.join(mismatches[:3])}"
            )
        else:
            self._set_status(
                f"Selected {selected} extra file(s) in this group "
                f"(Keep left unchecked · total marked: {len(self._dup_marked_paths)})."
            )

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
                self._set_checkbox(checkbox, var, False)
                self._dup_marked_paths.discard(self._path_key(info))
                continue
            self._set_checkbox(checkbox, var, True)
            self._dup_marked_paths.add(self._path_key(info))
        self._refresh_marked_label()
        self._refresh_group_mark_indicators()
        self._set_status("Selected all removable files in this group.")

    def clear_compare_selection(self) -> None:
        if not self._compare_check_vars:
            return
        for var, info, checkbox in zip(
            self._compare_check_vars, self._compare_file_infos, self._compare_checkboxes
        ):
            self._set_checkbox(checkbox, var, False)
            self._dup_marked_paths.discard(self._path_key(info))
        self._refresh_marked_label()
        self._refresh_group_mark_indicators()
        self._set_status("Cleared selection in this group.")

    def mark_all_extras(self) -> None:
        """Mark every extra (non-Keep) on-disk file across all duplicate groups."""
        if not self.dup_report or not self.dup_report.groups:
            messagebox.showinfo(APP_TITLE, "Run Find duplicates first.")
            return
        self._dup_marked_paths.clear()
        count = 0
        for group in self.dup_report.groups:
            for info in group.files[1:]:
                if info.is_inside_archive:
                    continue
                self._dup_marked_paths.add(self._path_key(info))
                count += 1
        self._restore_marks_to_checkboxes()
        self._refresh_marked_label()
        # Refresh list ticks
        shown = self._group_list_shown
        self._group_list_shown = 0
        self._clear_group_list()
        while self._group_list_shown < shown:
            before = self._group_list_shown
            self._append_group_page()
            if self._group_list_shown == before:
                break
        if self._selected_group_index is not None:
            self._highlight_group_button(self._selected_group_index)
        self._set_status(f"Marked {count} extra duplicate(s) across all groups.")

    def _selected_compare_files(self) -> list[FileInfo]:
        """Files marked in the open group, plus any globally marked paths in this group."""
        chosen: list[FileInfo] = []
        seen: set[str] = set()
        for var, info in zip(self._compare_check_vars, self._compare_file_infos):
            if info.is_inside_archive:
                continue
            key = self._path_key(info)
            if int(var.get()) or key in self._dup_marked_paths:
                if key not in seen:
                    chosen.append(info)
                    seen.add(key)
        return chosen

    def _all_marked_files(self) -> list[FileInfo]:
        """Resolve marked paths against the current duplicate report."""
        if not self.dup_report:
            return []
        out: list[FileInfo] = []
        seen: set[str] = set()
        for group in self.dup_report.groups:
            for info in group.files:
                if info.is_inside_archive:
                    continue
                key = self._path_key(info)
                if key in self._dup_marked_paths and key not in seen:
                    out.append(info)
                    seen.add(key)
        return out

    def _compare_thumb_side(self, total_cards: int) -> int:
        """Pick a preview size from the current compare pane width."""
        if self._compare_mode == "grid":
            # Keep grid thumbs large; width is per-card, not shared across all files
            try:
                width = max(self.compare_frame.winfo_width(), 400)
            except Exception:
                width = 900
            cols = max(1, self._compare_grid_cols or self._compare_grid_column_count())
            per_card = max(_COMPARE_GRID_THUMB, int((width - 24) / cols) - 36)
            return max(_COMPARE_GRID_THUMB, min(per_card, 420))
        try:
            width = max(self.compare_frame.winfo_width(), 400)
        except Exception:
            width = 800
        per_card = max(180, int((width - 24) / max(total_cards, 1)) - 24)
        return max(180, min(per_card, 700))

    def _on_compare_resize(self, _event=None) -> None:
        if self._compare_mode == "grid" and self._compare_grid_cards:
            if self._compare_resize_job is not None:
                try:
                    self.after_cancel(self._compare_resize_job)
                except Exception:
                    pass
            self._compare_resize_job = self.after(120, self._layout_compare_grid)
            return
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

    def _remove_paths_from_reports(self, paths: set[Path]) -> None:
        """Drop removed files from the in-memory duplicate report and refresh the UI."""
        if not self.dup_report:
            return

        # Match by path strings built once — avoid resolve() on every survivor
        remove_keys: set[str] = set()
        for path in paths:
            remove_keys.add(str(path))
            try:
                remove_keys.add(str(path.resolve()))
            except OSError:
                pass

        def still_here(info: FileInfo) -> bool:
            return str(info.path) not in remove_keys

        remaining_groups: list[DuplicateGroup] = []
        for group in self.dup_report.groups:
            kept = [f for f in group.files if still_here(f)]
            if len(kept) >= 2:
                group.files = kept
                remaining_groups.append(group)
        self.dup_report.groups = remaining_groups

        if self.scan_result:
            store = getattr(self.scan_result, "store", None)
            if store is not None:
                try:
                    store.remove_paths(remove_keys)
                    self.scan_result.file_count = store.count()
                    self.scan_result.total_bytes = store.total_bytes()
                except Exception:
                    pass
                # Keep any already-loaded list in sync without forcing a full reload
                if self.scan_result.files:
                    self.scan_result.files = [
                        f for f in self.scan_result.files if still_here(f)
                    ]
            else:
                self.scan_result.files = [
                    f for f in self.scan_result.files if still_here(f)
                ]
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

    def _start_bulk_dup_cleanup(self, files: list[FileInfo], action: str) -> None:
        """
        Run quarantine or permanent delete off the UI thread with progress.
        action: "quarantine" or "delete"
        """
        if self._busy:
            messagebox.showinfo(APP_TITLE, "Wait for the current task to finish.")
            return
        if not files:
            return
        paths = [f.path for f in files]
        path_keys = [self._path_key(f) for f in files]
        self._set_busy(True)
        if action == "delete":
            self._set_status(f"Deleting {len(files)} file(s)…")
        else:
            self._set_status(f"Quarantining {len(files)} file(s)…")
        threading.Thread(
            target=self._bulk_dup_cleanup_worker,
            args=(action, paths, path_keys),
            daemon=True,
        ).start()

    def _bulk_dup_cleanup_worker(
        self,
        action: str,
        paths: list[Path],
        path_keys: list[str],
    ) -> None:
        started = time.monotonic()
        session: Optional[Path] = None
        log: list[str] = []
        try:
            if action == "delete":
                log = permanently_delete(
                    paths,
                    status_cb=self._worker_status,
                    should_cancel=self._should_cancel,
                )
            else:
                session, log = move_to_quarantine(
                    paths,
                    status_cb=self._worker_status,
                    should_cancel=self._should_cancel,
                )
        except Exception as exc:
            log = [f"[error] {exc}"]
        took = format_duration(time.monotonic() - started)
        self.after(
            0,
            self._bulk_dup_cleanup_done,
            action,
            paths,
            path_keys,
            log,
            session,
            took,
        )

    def _bulk_dup_cleanup_done(
        self,
        action: str,
        paths: list[Path],
        path_keys: list[str],
        log: list[str],
        session: Optional[Path],
        took: str,
    ) -> None:
        self._set_busy(False)
        for key in path_keys:
            self._dup_marked_paths.discard(key)
        self._remove_paths_from_reports(set(paths))
        self._refresh_marked_label()
        self._refresh_workflow()
        summary = next((line for line in reversed(log) if line.startswith("Summary:")), "")
        cancelled = any("[cancelled]" in line for line in log)
        count = len(paths)
        if action == "delete":
            title_bits = [f"Delete finished in {took}."]
            if summary:
                title_bits.append(summary)
            if cancelled:
                title_bits.append("Cancelled early — some files may remain.")
            messagebox.showinfo(
                APP_TITLE,
                "\n".join(title_bits) + "\n\n" + "\n".join(log[:12]),
            )
            self._set_status(
                f"Delete finished ({count} queued) in {took}."
                + (" Cancelled." if cancelled else "")
            )
        else:
            if session is not None:
                self._remember_quarantine_session(session)
            where = str(session) if session else "(see log)"
            title_bits = [f"Quarantine finished in {took}.", f"Folder:\n{where}"]
            if summary:
                title_bits.append(summary)
            if cancelled:
                title_bits.append("Cancelled early — some files may remain.")
            messagebox.showinfo(
                APP_TITLE,
                "\n\n".join(title_bits)
                + "\n\nA manifest.json file lists original locations.\n"
                "Use “Open last quarantine” anytime.\n\n"
                + "\n".join(log[:8]),
            )
            self._set_status(
                f"Quarantined toward {where} in {took}."
                + (" Cancelled." if cancelled else "")
            )

    def quarantine_selected_compare_files(self) -> None:
        # Prefer all marked files across groups when browsing with persistent marks
        chosen = self._all_marked_files()
        if not chosen:
            chosen = self._selected_compare_files()
        if not chosen:
            messagebox.showinfo(
                APP_TITLE,
                "Mark files with the checkbox (or Select extras / Mark all extras), then quarantine.",
            )
            return
        only_current = {
            self._path_key(i) for i in self._compare_file_infos if not i.is_inside_archive
        }
        spanning = any(self._path_key(c) not in only_current for c in chosen)
        scope = "across all groups" if spanning else "in this view"
        qpath = quarantine_root()
        ok = messagebox.askyesno(
            APP_TITLE,
            f"Move {len(chosen)} marked file(s) to quarantine ({scope})?\n\n{qpath}\n\n"
            "You can restore them later from that folder.",
        )
        if not ok:
            return
        self._start_bulk_dup_cleanup(chosen, "quarantine")

    def delete_selected_compare_files(self) -> None:
        chosen = self._all_marked_files() or self._selected_compare_files()
        if not chosen:
            messagebox.showinfo(
                APP_TITLE,
                "Mark files with the checkbox (or Select extras / Mark all extras), then delete.",
            )
            return
        names = "\n".join(str(c.path) for c in chosen[:10])
        extra = "" if len(chosen) <= 10 else f"\n… and {len(chosen) - 10} more"
        ok = messagebox.askyesno(
            APP_TITLE,
            "PERMANENT DELETE\n\n"
            f"This will erase {len(chosen)} marked file(s) from disk and cannot be undone "
            "by this app.\n\n"
            f"{names}{extra}\n\n"
            "Prefer quarantine if you might want them back.\n\n"
            "Delete permanently?",
        )
        if not ok:
            return
        ok2 = messagebox.askyesno(
            APP_TITLE,
            f"Final confirmation: permanently delete {len(chosen)} marked file(s)?",
        )
        if not ok2:
            return
        self._start_bulk_dup_cleanup(chosen, "delete")

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

        dest_panel = ctk.CTkFrame(tab, fg_color=("gray92", "gray17"), corner_radius=8)
        dest_panel.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 6))
        dest_panel.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            dest_panel,
            text="Destination folder",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 2))
        ctk.CTkLabel(
            dest_panel,
            text=(
                "Where tidy copies/moves go. An existing archive is fine — Apply adds into it "
                "(new folders only when needed; never removes destination files)."
            ),
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        ctk.CTkLabel(dest_panel, text="Path:", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=2, column=0, padx=(10, 8), pady=(0, 10), sticky="w"
        )
        self.dest_entry = ctk.CTkEntry(
            dest_panel,
            height=36,
            font=ctk.CTkFont(size=13),
            placeholder_text="Choose or type a destination folder…",
        )
        self.dest_entry.grid(row=2, column=1, sticky="ew", pady=(0, 10))
        saved_dest = str(self._settings.get("destination") or "")
        if saved_dest:
            self.dest_entry.insert(0, saved_dest)
        primary_button(
            dest_panel,
            text="Browse / add folder…",
            width=170,
            height=36,
            command=self.choose_dest,
        ).grid(row=2, column=2, padx=(8, 10), pady=(0, 10))

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
        hpaned.add(left, minsize=280)
        hpaned.add(right, minsize=260)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        org_tabs = ctk.CTkTabview(left)
        org_tabs.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.org_option_tabs = org_tabs
        layouts_tab = org_tabs.add("Layouts")
        advanced_tab = org_tabs.add("Advanced")
        layouts_tab.grid_columnconfigure(0, weight=1)
        layouts_tab.grid_rowconfigure(2, weight=1)
        advanced_tab.grid_columnconfigure(0, weight=1)
        advanced_tab.grid_rowconfigure(0, weight=1)

        self.layout_hint = ctk.CTkLabel(
            layouts_tab,
            text="Choose one layout, or tick several to nest folders. Scan first for recommendations.",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        )
        self.layout_hint.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 2))

        layout_tools = ctk.CTkFrame(layouts_tab, fg_color="transparent")
        layout_tools.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        ctk.CTkButton(
            layout_tools,
            text="Suggest layout for me",
            width=160,
            height=30,
            command=self._suggest_organise_layout,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            layout_tools,
            text="Use recommended",
            width=140,
            height=30,
            command=self._use_recommended_layouts,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            layout_tools,
            text="Clear to one",
            width=140,
            height=30,
            command=self._clear_layouts_to_one,
        ).pack(side="left")

        self.layout_vars: dict[str, tk.BooleanVar] = {}
        self.layout_check_frame = ctk.CTkScrollableFrame(layouts_tab)
        self.layout_check_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 2))
        self.layout_check_frame.grid_columnconfigure(0, weight=1)
        self._layout_check_widgets: list = []
        self._layout_recommended_ids: list[str] = ["type_date"]
        self._layout_recommended_core: list[str] = ["type_date"]

        self.layout_combine_label = ctk.CTkLabel(
            layouts_tab,
            text="Combine order: Type + date",
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        )
        self.layout_combine_label.grid(row=3, column=0, sticky="ew", padx=6, pady=(0, 2))

        safety = ctk.CTkFrame(layouts_tab, fg_color="transparent")
        safety.grid(row=4, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.copy_instead_var = tk.BooleanVar(
            value=bool(self._settings.get("copy_instead_of_move", True))
        )
        ctk.CTkCheckBox(
            safety,
            text="Copy files (safer) instead of moving",
            variable=self.copy_instead_var,
            command=self._update_layout_visual,
        ).pack(anchor="w")
        ctk.CTkLabel(
            safety,
            text=(
                "Apply merges into the destination without deleting what is already there. "
                "Move removes files from the source only (not from the destination)."
            ),
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=420,
        ).pack(anchor="w", pady=(2, 0))

        # Advanced options live on their own tab (full height — easier to see)
        opts = ctk.CTkScrollableFrame(advanced_tab)
        self.organise_advanced_frame = opts
        opts.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        opts.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            opts,
            text="Include categories",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))

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
            opts, text="Sub-folder options", font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=2, column=0, sticky="w", padx=6, pady=(10, 2))

        saved_depth = normalize_media_date_depth(
            self._settings.get("media_date_depth", self._settings.get("media_by_date", True))
        )
        self._media_depth_label_to_value = {
            MEDIA_DATE_DEPTH_LABELS[k]: k for k in MEDIA_DATE_DEPTHS
        }
        self._media_depth_value_to_label = dict(MEDIA_DATE_DEPTH_LABELS)
        self.media_date_depth_var = tk.StringVar(
            value=self._media_depth_value_to_label.get(saved_depth, MEDIA_DATE_DEPTH_LABELS["year_month"])
        )
        depth_row = ctk.CTkFrame(opts, fg_color="transparent")
        depth_row.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(depth_row, text="Media date folders:", font=ctk.CTkFont(size=12, weight="bold")).pack(
            side="left"
        )
        ctk.CTkOptionMenu(
            depth_row,
            values=[MEDIA_DATE_DEPTH_LABELS[k] for k in MEDIA_DATE_DEPTHS],
            variable=self.media_date_depth_var,
            width=160,
            height=30,
            command=lambda _v: self._update_layout_visual(),
        ).pack(side="left", padx=(8, 0))

        self.documents_by_ext_var = tk.BooleanVar(
            value=bool(self._settings.get("documents_by_ext", True))
        )
        self.separate_archives_var = tk.BooleanVar(
            value=bool(self._settings.get("separate_archives", True))
        )
        ctk.CTkCheckBox(
            opts,
            text="Documents: subfolder per extension (pdf, docx, …)",
            variable=self.documents_by_ext_var,
            command=self._update_layout_visual,
        ).grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkCheckBox(
            opts,
            text="Keep Archives in its own folder",
            variable=self.separate_archives_var,
            command=self._update_layout_visual,
        ).grid(row=5, column=0, sticky="w", padx=8, pady=(2, 6))

        ctk.CTkLabel(
            opts,
            text="Per-category folders",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=6, column=0, sticky="w", padx=6, pady=(10, 2))
        ctk.CTkLabel(
            opts,
            text="Override nesting for one category (optional). Follow layout = use the preset.",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            justify="left",
            anchor="w",
        ).grid(row=7, column=0, sticky="ew", padx=8, pady=(0, 4))

        saved_modes = normalize_category_subfolders(
            self._settings.get("category_subfolders") or {}
        )
        self._category_mode_label_to_value = {
            CATEGORY_MODE_LABELS[k]: k for k in CATEGORY_SUBFOLDER_MODES
        }
        self._category_mode_value_to_label = dict(CATEGORY_MODE_LABELS)
        self.category_mode_vars: dict[str, tk.StringVar] = {}
        cat_mode_frame = ctk.CTkFrame(opts, fg_color="transparent")
        cat_mode_frame.grid(row=8, column=0, sticky="ew", padx=6, pady=(0, 6))
        cat_mode_frame.grid_columnconfigure(1, weight=1)
        mode_labels = [CATEGORY_MODE_LABELS[k] for k in CATEGORY_SUBFOLDER_MODES]
        for row_i, cat in enumerate(ALL_CATEGORIES):
            mode_val = saved_modes.get(cat, "layout_default")
            var = tk.StringVar(
                value=self._category_mode_value_to_label.get(
                    mode_val, CATEGORY_MODE_LABELS["layout_default"]
                )
            )
            self.category_mode_vars[cat] = var
            ctk.CTkLabel(
                cat_mode_frame, text=f"{cat}:", width=90, anchor="w", font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=row_i, column=0, sticky="w", padx=(2, 6), pady=3)
            ctk.CTkOptionMenu(
                cat_mode_frame,
                values=mode_labels,
                variable=var,
                width=170,
                height=28,
                command=lambda _v: self._update_layout_visual(),
            ).grid(row=row_i, column=1, sticky="w", pady=3)

        ctk.CTkLabel(
            opts,
            text="Local AI (optional)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=9, column=0, sticky="w", padx=6, pady=(10, 2))
        ctk.CTkLabel(
            opts,
            text="Uses Ollama on this computer only. Off by default — Suggest still works with rules.",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            justify="left",
            anchor="w",
        ).grid(row=10, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.use_ollama_var = tk.BooleanVar(
            value=bool(self._settings.get("use_ollama_suggest", False))
        )
        ctk.CTkCheckBox(
            opts,
            text="Use local AI (Ollama)",
            variable=self.use_ollama_var,
            command=self._persist_settings,
        ).grid(row=11, column=0, sticky="w", padx=8, pady=(2, 8))

        ctk.CTkLabel(
            opts,
            text="Archive best practices",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=12, column=0, sticky="w", padx=6, pady=(10, 2))
        self.date_prefix_var = tk.BooleanVar(
            value=bool(self._settings.get("rename_with_date_prefix", False))
        )
        self.sanitize_names_var = tk.BooleanVar(
            value=bool(self._settings.get("sanitize_filenames", True))
        )
        self.readme_notes_var = tk.BooleanVar(
            value=bool(self._settings.get("add_readme_notes", True))
        )
        self.archive_days_var = tk.StringVar(
            value=str(self._settings.get("archive_older_than_days", 365))
        )
        ctk.CTkLabel(
            opts,
            text="(Copy vs move stays on the Layouts tab for safety)",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).grid(row=13, column=0, sticky="w", padx=8, pady=2)
        ctk.CTkCheckBox(
            opts,
            text="Add YYYY-MM-DD date prefix to filenames",
            variable=self.date_prefix_var,
            command=self._update_layout_visual,
        ).grid(row=14, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkCheckBox(
            opts,
            text="Sanitize filenames (safer characters)",
            variable=self.sanitize_names_var,
            command=self._update_layout_visual,
        ).grid(row=15, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkCheckBox(
            opts,
            text="Write README.txt notes in top folders (skip if already present)",
            variable=self.readme_notes_var,
            command=self._update_layout_visual,
        ).grid(row=16, column=0, sticky="w", padx=8, pady=4)
        age_row = ctk.CTkFrame(opts, fg_color="transparent")
        age_row.grid(row=17, column=0, sticky="ew", padx=8, pady=(2, 8))
        ctk.CTkLabel(age_row, text="Archive files older than (days):", font=ctk.CTkFont(size=12, weight="bold")).pack(
            side="left"
        )
        age_entry = ctk.CTkEntry(age_row, width=80, height=30, textvariable=self.archive_days_var)
        age_entry.pack(side="left", padx=8)
        age_entry.bind("<KeyRelease>", lambda _e: self._update_layout_visual())

        ctk.CTkLabel(
            opts,
            text="Custom structure (when layout = Custom)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=18, column=0, sticky="w", padx=6, pady=(10, 2))
        ctk.CTkLabel(
            opts,
            text="Tree lines + rules like: Photos = MyArchive/Photos/{year}/{month}",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
        ).grid(row=19, column=0, sticky="w", padx=8)
        self.custom_structure_box = ctk.CTkTextbox(
            opts, height=130, font=ctk.CTkFont(family="monospace", size=11)
        )
        self.custom_structure_box._allow_edit = True  # type: ignore[attr-defined]
        self.custom_structure_box.grid(row=20, column=0, sticky="ew", padx=8, pady=(2, 10))
        saved_custom = str(self._settings.get("custom_structure_text") or "").strip()
        self.custom_structure_box.insert(
            "1.0", saved_custom if saved_custom else DEFAULT_CUSTOM_TEMPLATE
        )
        self.custom_structure_box.bind("<KeyRelease>", lambda _e: self._update_layout_visual())

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Visual layout preview", font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            head, text="Refresh view", width=100, height=28, command=self._update_layout_visual
        ).grid(row=0, column=1, sticky="e")

        self.layout_tree_box = ctk.CTkTextbox(right, font=ctk.CTkFont(family="monospace", size=12))
        self.layout_tree_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.layout_tree_box.insert(
            "1.0",
            "Select a layout on the Layouts tab.\n"
            "Open Advanced for categories, date folders, and naming.\n"
            "Folder names use plain English (Personal, Media) — not numbered prefixes.",
        )
        make_textbox_readonly_copyable(self.layout_tree_box)

        self.org_box = ctk.CTkTextbox(bottom)
        self.org_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.org_box.insert(
            "1.0",
            "Organise plan appears here after Preview plan.\n"
            "Default is Copy (safer) — originals stay until you delete them.",
        )
        make_textbox_readonly_copyable(self.org_box)

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 6))
        self.btn_preview_org = ctk.CTkButton(
            row, text="Preview plan", height=34, command=self.preview_organise
        )
        self.btn_preview_org.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Browse dry-run…",
            width=140,
            height=34,
            command=self.open_plan_browser,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Save inventory…", width=130, height=34, command=self.save_inventory
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Show tips", width=100, height=34, command=self.show_organisation_tips
        ).pack(side="left", padx=(0, 8))
        self.btn_apply_org = primary_button(
            row,
            text="Apply organise",
            height=36,
            command=self.apply_organise,
        )
        self.btn_apply_org.pack(side="right")

        self.dry_run_var = tk.BooleanVar(value=bool(self._settings.get("dry_run", True)))
        ctk.CTkCheckBox(
            row,
            text="Dry run only (no file changes)",
            variable=self.dry_run_var,
            command=self._sync_organise_apply_button,
        ).pack(side="right", padx=16)

        self._last_organise_plan = None
        self._rebuild_layout_options(recommended=["type_date"])
        self._sync_organise_apply_button()

    def _sync_organise_apply_button(self) -> None:
        """Make Apply button wording match Dry run vs real copy/move."""
        if not hasattr(self, "btn_apply_org") or not hasattr(self, "dry_run_var"):
            return
        if self.dry_run_var.get():
            self.btn_apply_org.configure(text="Run dry run")
        else:
            self.btn_apply_org.configure(text="Apply organise")
        if self._organise_preview_ready:
            self._refresh_workflow()

    def _open_organise_advanced_tab(self) -> None:
        """Switch Organise left pane to the Advanced options tab."""
        tabs = getattr(self, "org_option_tabs", None)
        if tabs is None:
            return
        try:
            tabs.set("Advanced")
        except Exception:
            pass

    def _rebuild_layout_options(
        self,
        recommended: Optional[list[str]] = None,
        recommended_core: Optional[list[str]] = None,
    ) -> None:
        """Rebuild layout checkboxes; tick one or more to combine folder structures."""
        for child in self.layout_check_frame.winfo_children():
            child.destroy()
        self._layout_check_widgets.clear()

        order = recommended or [p.id for p in LAYOUT_PRESETS]
        self._layout_recommended_ids = list(order)
        if recommended_core:
            self._layout_recommended_core = [
                lid for lid in recommended_core if lid != "custom"
            ] or ["type_date"]
        else:
            # Without a scan core list, the top ordered id is the recommendation.
            self._layout_recommended_core = [order[0]] if order else ["type_date"]
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
        saved = {
            lid for lid in (self._saved_layout_ids or []) if lid in ordered_ids
        }

        for row_i, layout_id in enumerate(ordered_ids):
            preset = get_layout(layout_id)
            mark = "  (recommended)" if recommended and layout_id == recommended[0] else ""
            useful = ""
            if recommended and layout_id in recommended[:3] and layout_id != recommended[0]:
                useful = "  (fits your files)"
            title = f"{preset.name}{mark}{useful}"
            if any_prev:
                default_on = bool(previous.get(layout_id, False))
            elif saved:
                default_on = layout_id in saved
            else:
                default_on = layout_id == best
            var = tk.BooleanVar(value=default_on)
            self.layout_vars[layout_id] = var

            row = ctk.CTkFrame(self.layout_check_frame, fg_color="transparent")
            row.grid(row=row_i, column=0, sticky="ew", padx=2, pady=(2, 6))
            row.grid_columnconfigure(0, weight=1)
            box = ctk.CTkCheckBox(
                row,
                text=title,
                variable=var,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=self._on_layout_chosen,
            )
            box.grid(row=0, column=0, sticky="w")
            detail = f"{preset.description}\nExample: {preset.example}"
            ctk.CTkLabel(
                row,
                text=detail,
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=MUTED,
                wraplength=320,
            ).grid(row=1, column=0, sticky="ew", padx=(28, 4), pady=(0, 2))
            self._layout_check_widgets.append(box)

        if not any(var.get() for var in self.layout_vars.values()):
            self.layout_vars[best].set(True)
        self._on_layout_chosen()

    def _use_recommended_layouts(self) -> None:
        """Tick recommended layout ids only (best fit); clear other ticks."""
        core = [
            lid
            for lid in (getattr(self, "_layout_recommended_core", None) or ["type_date"])
            if lid in self.layout_vars and lid != "custom"
        ]
        # Beginner-friendly default: tick the single best layout, not a multi-layout nest.
        keep = core[0] if core else next(iter(self.layout_vars), "type_date")
        if keep not in self.layout_vars:
            keep = next(iter(self.layout_vars), "type_date")
        for lid, var in self.layout_vars.items():
            var.set(lid == keep)
        self._on_layout_chosen()

    def _suggest_organise_layout(self) -> None:
        """Propose layout + folder knobs (rules, or optional local Ollama)."""
        if not self._has_scan_files():
            messagebox.showinfo(
                APP_TITLE,
                "Scan a folder first so suggestions can look at your file types and paths.",
            )
            return
        if getattr(self, "_suggest_busy", False):
            return
        files = self._scan_files()
        settings = self._collect_settings()
        use_ollama = bool(
            getattr(self, "use_ollama_var", None) and self.use_ollama_var.get()
        )
        self._suggest_busy = True
        if use_ollama:
            self._set_status("Asking local AI (Ollama)… — stays on this computer.")
        else:
            self._set_status("Building offline suggestion…")
        threading.Thread(
            target=self._suggest_organise_worker,
            args=(files, settings, use_ollama),
            daemon=True,
        ).start()

    def _suggest_organise_worker(
        self,
        files: list,
        settings: dict,
        use_ollama: bool,
    ) -> None:
        suggestion = suggest_organise_options_auto(
            files,
            saved_settings=settings,
            use_ollama=use_ollama,
        )
        self.after(0, self._suggest_organise_done, suggestion)

    def _suggest_organise_done(self, suggestion: OrganiseSuggestion) -> None:
        self._suggest_busy = False
        if suggestion.source == "ollama":
            self._set_status("Local AI suggestion ready — review, then Apply if you like it.")
        elif suggestion.source == "rules_fallback":
            self._set_status("Local AI unavailable — showing offline rules suggestion.")
        else:
            self._set_status("Offline suggestion ready — review, then Apply if you like it.")
        self._show_organise_suggestion_dialog(suggestion)

    def _show_organise_suggestion_dialog(self, suggestion: OrganiseSuggestion) -> None:
        """Show a summary dialog; Apply fills existing Organise widgets only."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Suggest layout for me")
        dialog.geometry("560x480")
        dialog.minsize(420, 360)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        if suggestion.source == "ollama":
            title = "Local AI suggestion (Ollama)"
        elif suggestion.source == "rules_fallback":
            title = "Offline suggestion (AI fallback)"
        else:
            title = "Offline suggestion (rules only)"

        ctk.CTkLabel(
            dialog,
            text=title,
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))

        body = ctk.CTkTextbox(dialog, font=ctk.CTkFont(size=12))
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=4)
        lines = suggestion.summary_lines or format_suggestion_summary(suggestion)
        body.insert("1.0", "\n".join(lines))
        make_textbox_readonly_copyable(body)
        enable_copyable_text(dialog, on_copied=self._on_text_copied)

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 14))

        def _close() -> None:
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        def _apply() -> None:
            self._apply_organise_suggestion(suggestion)
            _close()

        ctk.CTkButton(buttons, text="Cancel", width=110, height=34, command=_close).pack(
            side="right", padx=(8, 0)
        )
        primary_button(
            buttons,
            text="Apply suggestion",
            width=160,
            height=34,
            command=_apply,
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", _close)
        dialog.focus_set()

    def _apply_organise_suggestion(self, suggestion: OrganiseSuggestion) -> None:
        """Fill Organise widgets from a suggestion; user still Preview → Apply."""
        want = set(suggestion.layout_ids)
        if not want:
            want = {"type_date"}
        # Rebuild list order so suggested layouts appear first
        ranked = list(suggestion.layout_ids) + [
            lid for lid in (self._layout_recommended_ids or []) if lid not in want
        ]
        self._saved_layout_ids = list(suggestion.layout_ids)
        self.layout_vars = {}
        self._rebuild_layout_options(recommended=ranked or ["type_date"])
        for lid, var in self.layout_vars.items():
            var.set(lid in want)
        if not any(var.get() for var in self.layout_vars.values()):
            fallback = next(iter(want), "type_date")
            if fallback in self.layout_vars:
                self.layout_vars[fallback].set(True)

        depth = suggestion.media_date_depth
        if hasattr(self, "media_date_depth_var"):
            label = self._media_depth_value_to_label.get(
                depth, MEDIA_DATE_DEPTH_LABELS.get(depth, MEDIA_DATE_DEPTH_LABELS["year_month"])
            )
            self.media_date_depth_var.set(label)

        if hasattr(self, "documents_by_ext_var"):
            self.documents_by_ext_var.set(bool(suggestion.documents_by_ext))

        modes = suggestion.category_subfolders or {}
        for cat, var in getattr(self, "category_mode_vars", {}).items():
            mode = modes.get(cat, "layout_default")
            label = self._category_mode_value_to_label.get(
                mode, CATEGORY_MODE_LABELS["layout_default"]
            )
            var.set(label)

        self._organise_preview_ready = False
        self._organise_preview_key = None
        self._on_layout_chosen()
        self._persist_settings()
        self._sync_organise_apply_button()
        self._open_organise_advanced_tab()
        self._set_status(
            "Suggestion applied to Organise controls — Preview plan when ready "
            "(nothing copied or moved yet)."
        )
        messagebox.showinfo(
            APP_TITLE,
            "Suggestion applied to the Organise controls.\n\n"
            "Next: check the visual preview, then click Preview plan.\n"
            "Apply organise still waits for you — nothing was moved.",
        )

    def _clear_layouts_to_one(self) -> None:
        """Keep the first selected layout only (or recommended if none)."""
        selected = [lid for lid, var in self.layout_vars.items() if var.get()]
        if selected:
            keep = selected[0]
        else:
            keep = (self._layout_recommended_ids or ["type_date"])[0]
        if keep not in self.layout_vars:
            keep = next(iter(self.layout_vars), "type_date")
        for lid, var in self.layout_vars.items():
            var.set(lid == keep)
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
        if hasattr(self, "layout_combine_label"):
            self.layout_combine_label.configure(
                text=layout_combine_order_label(self._selected_layout_ids())
            )
        self._update_layout_visual()
        self._persist_settings()

    def _selected_media_date_depth(self) -> str:
        label = self.media_date_depth_var.get() if hasattr(self, "media_date_depth_var") else ""
        return normalize_media_date_depth(
            getattr(self, "_media_depth_label_to_value", {}).get(label, label)
        )

    def _selected_category_subfolders(self) -> dict[str, str]:
        raw: dict[str, str] = {}
        mapping = getattr(self, "_category_mode_label_to_value", {})
        for cat, var in getattr(self, "category_mode_vars", {}).items():
            label = var.get()
            raw[cat] = mapping.get(label, "layout_default")
        return normalize_category_subfolders(raw)

    def _current_organise_options(self) -> OrganiseOptions:
        selected = {cat for cat, var in self.category_vars.items() if var.get()}
        try:
            days = max(1, int(self.archive_days_var.get().strip() or "365"))
        except ValueError:
            days = 365
        return OrganiseOptions(
            categories=selected if selected else set(ALL_CATEGORIES),
            media_date_depth=self._selected_media_date_depth(),
            documents_by_ext=self.documents_by_ext_var.get(),
            separate_archives=self.separate_archives_var.get(),
            category_subfolders=self._selected_category_subfolders(),
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
        overrides = [
            f"{cat}:{mode}"
            for cat, mode in sorted((options.category_subfolders or {}).items())
            if mode and mode != "layout_default"
        ]
        override_txt = ", ".join(overrides) if overrides else "none"
        flags = (
            f"Folders: media date={options.media_date_depth} · "
            f"docs by ext={options.documents_by_ext} · "
            f"archives separate={options.separate_archives} · "
            f"category overrides={override_txt}\n"
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
        make_textbox_readonly_copyable(self.layout_tree_box)

    def _refresh_layout_options_from_scan(self) -> None:
        if not self._has_scan_files():
            self.layout_hint.configure(
                text="Scan a folder first — recommended layouts will appear here."
            )
            for cat, var in self.category_vars.items():
                var.set(True)
            self._rebuild_layout_options(
                recommended=["type_date"], recommended_core=["type_date"]
            )
            return
        files = self._scan_files()
        counts: dict[str, int] = {}
        for info in files:
            counts[info.category] = counts.get(info.category, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        core = recommended_layout_core(files)
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
        self._rebuild_layout_options(recommended=recommended, recommended_core=core)

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
   • Your sources, destination, layouts, and window size are remembered next launch.
   • Leave “Scan inside .zip” and junk/system folders off for huge drives (both slow scans).
   • Optional: “Unzip .zip files during scan” creates Vacation_unzipped/ next to Vacation.zip
     (writes UNZIPPED.txt; then scans that folder; keeps the .zip by default).
     Optional: “After a successful unzip, delete the original .zip”
     (never deletes if unzip fails). Or only delete when the drive is low on space.
     When unzip-on-scan is on, the scan uses the extracted files and does not also list zip members.
   • Click Scan now. Fast folder walk + on-disk SQLite index; use Reload last scan to avoid re-scanning.
   • One-click start: run scripts/install_desktop_launcher.sh once.

2. Overview tab
   • While scanning, elapsed time updates in the summary and status bar.
   • After a scan: clear sections for counts, categories, samples, and timing.
   • Tick Exact (recommended). Similar photos/docs are optional and capped.
   • Click Find duplicates — then you are taken to the Duplicates tab.

3. Duplicates tab
   • Groups are listed in plain English and grouped by file type (Photos, Documents, …).
   • Extras are auto-marked; marks stay as you browse with ← → / Prev / Next.
   • Select extras only (this group) ticks extras and unticks Keep — checkboxes are in the top preview row.
   • Mark all extras (every group) / Clear selection for fine control.
   • File info shows ROLE, FILE, SIZE & DATE, LOCATION, and fingerprint sections.
   • Prefer Quarantine selected/marked. Confirmations show count + size + “oldest kept”.
   • Open last quarantine jumps to the newest quarantine session folder.

4. Organise tab
   • Destination panel: Browse / add folder… (same friendly picker as Sources).
   • Existing archive roots are OK — Apply adds into them (creates missing folders only;
     never deletes or overwrites destination files; name clashes become name_1.ext).
   • Left pane has two tabs: Layouts (choose structure) and Advanced (full-height options).
   • Each layout shows a short description + example. Use recommended ticks the best fit;
     Clear to one keeps a single layout. Combine order under the list shows nesting (A → B).
   • Useful extras: Keep source folders (DriveName/…/file) and Shallow by type (category only).
   • Advanced → Media date folders: none / year only / year + month.
   • Advanced → Per-category folders: follow layout, flat, by year, by year+month, or by extension
     (overrides nesting for that category without writing custom rule text).
   • Suggest layout for me: offline rules (or optional local Ollama) fill layouts + folder options.
   • Advanced → Use local AI (Ollama) is optional and off by default; falls back to rules if needed.
   • Life-area folders use plain names (Personal, Media, Finance) — not 01_Personal.
   • Keep Copy + Dry run on at first. Preview plan, then Run dry run / Apply organise.
   • Copy keeps sources; Move removes from the source only — destination content is never wiped.
   • Custom structure cannot mix with other layouts (Custom alone).

LARGE DRIVES (1TB+)
• Exact duplicates: size buckets → partial CRC → full CRC only on collisions.
• Stay on one mount (default). Zip listing is capped per archive and overall.
• Cancel stays available; status and progress show n/total when known (hash / organise).

PRIVACY & SAFETY
• Nothing is uploaded. Prefer quarantine. Test on a small folder first.
""",
        )
        make_textbox_readonly_copyable(help_box)

    # ---------- helpers ----------

    def _on_text_copied(self, text: str) -> None:
        preview = text.replace("\n", " ").strip()
        if len(preview) > 60:
            preview = preview[:57] + "…"
        self._set_status(f"Copied: {preview}" if preview else "Copied.")

    def _enable_copyable_text(self, root=None) -> None:
        """Allow selecting/copying text across labels and read-only boxes."""
        editable = set()
        if hasattr(self, "custom_structure_box"):
            editable.add(self.custom_structure_box)
        enable_copyable_text(
            root or self,
            editable_textboxes=editable,
            on_copied=self._on_text_copied,
        )

    def _set_status(self, text: str) -> None:
        """
        Update the status bar.
        While a long task is running, the GUI owns the live clock so the
        time keeps advancing even when the worker is busy inside a big zip.
        Also upgrades the progress bar when the message includes n/total.
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
            self._maybe_update_progress_from_text(detail)
        else:
            self.status_label.configure(text=text)

    def _worker_status(self, msg: str) -> None:
        """Thread-safe status update used by background workers."""
        self.after(0, self._set_status, msg)

    def _worker_progress(self, current: int, total: int) -> None:
        """Thread-safe determinate progress (e.g. scan sources 1/3)."""
        self.after(0, self._set_progress_counts, current, total)

    def _maybe_update_progress_from_text(self, text: str) -> None:
        match = _PROGRESS_FRACTION_RE.search(text or "")
        if not match:
            return
        current = int(match.group(1))
        total = int(match.group(2))
        self._set_progress_counts(current, total)

    def _set_progress_counts(self, current: int, total: int) -> None:
        if total <= 0:
            return
        fraction = max(0.0, min(1.0, float(current) / float(total)))
        if self._progress_mode != "determinate":
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress.configure(mode="determinate")
            self._progress_mode = "determinate"
        self.progress.set(fraction)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._cancel_flag = False
            self._busy_started = time.monotonic()
            self._busy_detail = "Working…"
            self.cancel_btn.configure(state="normal")
            self._progress_mode = "indeterminate"
            self.progress.configure(mode="indeterminate")
            self.progress.set(0)
            self.progress.start()
            self._schedule_busy_tick()
        else:
            self._cancel_busy_tick()
            self.cancel_btn.configure(state="disabled")
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress.configure(mode="determinate")
            self._progress_mode = "determinate"
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
        # Keep Overview readable while a long scan/hash runs
        if hasattr(self, "overview_summary"):
            try:
                self.overview_summary.configure(
                    text=(
                        f"In progress — elapsed {elapsed}\n"
                        f"{detail}\n"
                        "Cancel stays available in the status bar."
                    )
                )
            except Exception:
                pass
        self._schedule_busy_tick()

    def _request_cancel(self) -> None:
        self._cancel_flag = True
        self._set_status("Cancelling…")

    def _should_cancel(self) -> bool:
        return self._cancel_flag

    def _refresh_workflow(self) -> None:
        """Clear next-step tip in the banner based on current stage."""
        if self._organise_preview_ready and self._has_scan_files():
            if hasattr(self, "dry_run_var") and self.dry_run_var.get():
                text = (
                    "Next: review the Organise plan, then untick Dry run and click Apply organise."
                )
            else:
                text = "Next: click Apply organise to copy/move files (Preview plan must match)."
        elif self.dup_report and self.dup_report.groups:
            text = (
                "Next: compare groups, then Quarantine extras "
                "(keeps the oldest file in each group)."
            )
        elif self._has_scan_files():
            text = (
                "Next: open Overview and click Find duplicates "
                "(Exact is safest for huge drives)."
            )
        elif self.source_paths:
            text = "Next: click Scan now on Sources (or Reload last scan if you scanned before)."
        else:
            text = "Start on Sources: add a folder or drive, then Scan now."
        self.workflow_banner.configure(text=text)

    def _apply_saved_preferences(self) -> None:
        """Restore sources and related choices after the UI widgets exist."""
        saved_sources = [
            str(p) for p in (self._settings.get("source_paths") or []) if str(p).strip()
        ]
        self.source_paths = saved_sources
        self._refresh_source_list()
        if self._saved_layout_ids and getattr(self, "layout_vars", None):
            for lid, var in self.layout_vars.items():
                var.set(lid in self._saved_layout_ids)

    def _collect_settings(self) -> dict:
        layout_ids = []
        if getattr(self, "layout_vars", None):
            layout_ids = [lid for lid, var in self.layout_vars.items() if var.get()]
        try:
            archive_days = max(1, int(self.archive_days_var.get().strip() or "365"))
        except (ValueError, AttributeError):
            archive_days = 365
        custom_text = ""
        if hasattr(self, "custom_structure_box"):
            custom_text = self.custom_structure_box.get("1.0", "end").strip()
        return {
            "source_paths": list(self.source_paths),
            "destination": self.dest_entry.get().strip() if hasattr(self, "dest_entry") else "",
            "layout_ids": layout_ids,
            "window_geometry": self.geometry(),
            "appearance": self.appearance_var.get() if hasattr(self, "appearance_var") else "System",
            "include_junk": bool(self.include_junk_var.get()) if hasattr(self, "include_junk_var") else False,
            "scan_zips": bool(self.scan_zips_var.get()) if hasattr(self, "scan_zips_var") else False,
            "extract_zips": bool(self.extract_zips_var.get()) if hasattr(self, "extract_zips_var") else False,
            "delete_zip_after_extract": bool(self.delete_zip_after_extract_var.get())
            if hasattr(self, "delete_zip_after_extract_var")
            else False,
            "delete_zip_if_low_space": bool(self.delete_zip_if_low_space_var.get())
            if hasattr(self, "delete_zip_if_low_space_var")
            else False,
            "copy_instead_of_move": bool(self.copy_instead_var.get())
            if hasattr(self, "copy_instead_var")
            else True,
            "dry_run": bool(self.dry_run_var.get()) if hasattr(self, "dry_run_var") else True,
            "media_date_depth": self._selected_media_date_depth()
            if hasattr(self, "media_date_depth_var")
            else "year_month",
            "documents_by_ext": bool(self.documents_by_ext_var.get())
            if hasattr(self, "documents_by_ext_var")
            else True,
            "separate_archives": bool(self.separate_archives_var.get())
            if hasattr(self, "separate_archives_var")
            else True,
            "category_subfolders": self._selected_category_subfolders()
            if hasattr(self, "category_mode_vars")
            else {},
            "rename_with_date_prefix": bool(self.date_prefix_var.get())
            if hasattr(self, "date_prefix_var")
            else False,
            "sanitize_filenames": bool(self.sanitize_names_var.get())
            if hasattr(self, "sanitize_names_var")
            else True,
            "add_readme_notes": bool(self.readme_notes_var.get())
            if hasattr(self, "readme_notes_var")
            else True,
            "archive_older_than_days": archive_days,
            "custom_structure_text": custom_text,
            "use_ollama_suggest": bool(self.use_ollama_var.get())
            if hasattr(self, "use_ollama_var")
            else False,
            "last_quarantine_session": self._last_quarantine_session or "",
            "last_scan_db": str(last_scan_db_path()),
        }

    def _sync_unzip_delete_options(self) -> None:
        """Enable zip-delete options only when Unzip-on-scan is ticked."""
        extract_on = (
            bool(self.extract_zips_var.get()) if hasattr(self, "extract_zips_var") else False
        )
        always_delete = (
            bool(self.delete_zip_after_extract_var.get())
            if hasattr(self, "delete_zip_after_extract_var")
            else False
        )
        if hasattr(self, "delete_zip_after_extract_cb"):
            try:
                self.delete_zip_after_extract_cb.configure(
                    state="normal" if extract_on else "disabled"
                )
            except tk.TclError:
                pass
        if hasattr(self, "delete_zip_low_space_cb"):
            # Low-space delete is an alternative to always-delete
            low_space_on = extract_on and not always_delete
            try:
                self.delete_zip_low_space_cb.configure(
                    state="normal" if low_space_on else "disabled"
                )
            except tk.TclError:
                pass
            if always_delete and hasattr(self, "delete_zip_if_low_space_var"):
                self.delete_zip_if_low_space_var.set(False)

    def _persist_settings(self) -> None:
        try:
            save_settings(self._collect_settings())
        except OSError:
            pass

    def _on_window_configure(self, _event=None) -> None:
        if self._geometry_save_job is not None:
            try:
                self.after_cancel(self._geometry_save_job)
            except Exception:
                pass
        self._geometry_save_job = self.after(800, self._persist_settings)

    def _on_close(self) -> None:
        self._persist_settings()
        # Keep the on-disk scan DB; only drop the connection.
        store = self.scan_result.store if self.scan_result else None
        if store is not None:
            try:
                store.flush()
                store.commit()
            except Exception:
                pass
        self.destroy()

    def _close_current_store(self) -> None:
        if not self.scan_result or self.scan_result.store is None:
            return
        try:
            self.scan_result.store.close()
        except Exception:
            pass
        self.scan_result.store = None

    def _remember_quarantine_session(self, session: Path) -> None:
        self._last_quarantine_session = str(session)
        self._persist_settings()

    def open_last_quarantine(self) -> None:
        session: Optional[Path] = None
        if self._last_quarantine_session:
            candidate = Path(self._last_quarantine_session)
            if candidate.exists():
                session = candidate
        if session is None:
            session = latest_quarantine_session()
        if session is None or not session.exists():
            messagebox.showinfo(
                APP_TITLE,
                "No quarantine session found yet.\n\n"
                f"Quarantine folder:\n{quarantine_root()}",
            )
            return
        err = open_containing_folder(session)
        if err:
            messagebox.showerror(APP_TITLE, err)
            return
        self._set_status(f"Opened quarantine session: {session}")

    def _extras_summary_text(self, extras: list[FileInfo], action: str) -> str:
        count = len(extras)
        reclaim = sum(e.size for e in extras)
        groups = len(self.dup_report.groups) if self.dup_report else 0
        return (
            f"{action}\n\n"
            f"Extra copies to remove: {count}\n"
            f"Total size: ~{format_bytes(reclaim)}\n"
            f"Duplicate groups involved: {groups}\n"
            f"Kept in each group: the oldest file (KEEP)\n"
            "Zip members are skipped (cannot remove inside a zip alone).\n"
        )

    def _try_autoload_last_scan(self) -> None:
        """Quietly reload the last SQLite scan if it still exists."""
        if self._busy or self._has_scan_files():
            return
        db = last_scan_db_path()
        if not db.exists():
            self._refresh_workflow()
            return
        try:
            self._load_scan_from_db(db, announce=True)
        except Exception as exc:
            self._set_status(f"Could not reload last scan: {exc}")
            self._refresh_workflow()

    def reload_last_scan(self) -> None:
        if self._busy:
            return
        db = last_scan_db_path()
        if not db.exists():
            messagebox.showinfo(
                APP_TITLE,
                "No saved scan found yet.\n\n"
                "Run Scan now once — results are kept for the next launch.",
            )
            return
        try:
            self._load_scan_from_db(db, announce=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not reload last scan:\n{exc}")

    def _load_scan_from_db(self, db_path: Path, announce: bool = False) -> None:
        self._close_current_store()
        store = ScanStore(db_path)
        file_count = store.count()
        if file_count <= 0:
            store.close()
            raise RuntimeError("Saved scan database is empty.")

        sources_raw = store.get_meta("sources_json", "[]")
        try:
            sources = json.loads(sources_raw)
            if isinstance(sources, list) and sources:
                self.source_paths = [str(p) for p in sources]
                self._refresh_source_list()
        except json.JSONDecodeError:
            pass

        include_junk = store.get_meta("include_junk", "0") == "1"
        scan_zips = store.get_meta("scan_zips", "0") == "1"
        extract_zips = store.get_meta("extract_zips", "0") == "1"
        delete_zip_after_extract = store.get_meta("delete_zip_after_extract", "0") == "1"
        delete_zip_if_low_space = store.get_meta("delete_zip_if_low_space", "0") == "1"
        if hasattr(self, "include_junk_var"):
            self.include_junk_var.set(include_junk)
        if hasattr(self, "scan_zips_var"):
            self.scan_zips_var.set(scan_zips)
        if hasattr(self, "extract_zips_var"):
            self.extract_zips_var.set(extract_zips)
        if hasattr(self, "delete_zip_after_extract_var"):
            self.delete_zip_after_extract_var.set(delete_zip_after_extract)
        if hasattr(self, "delete_zip_if_low_space_var"):
            self.delete_zip_if_low_space_var.set(delete_zip_if_low_space)
        self._sync_unzip_delete_options()

        duration = 0.0
        try:
            duration = float(store.get_meta("duration_seconds", "0") or "0")
        except ValueError:
            duration = 0.0
        archive_members = 0
        try:
            archive_members = int(store.get_meta("archive_members", "0") or "0")
        except ValueError:
            archive_members = 0

        result = ScanResult(
            files=[],
            errors=[],
            skipped=0,
            archive_members=archive_members,
            duration_seconds=duration,
            total_bytes=store.total_bytes(),
            file_count=file_count,
            store=store,
        )
        self.scan_result = result
        self.dup_report = None
        self._organise_preview_ready = False
        self._update_overview()
        self._show_duplicates_empty_state()
        self.dup_summary.configure(
            text="Loaded last scan. Click Find duplicates on Overview (Exact is safest for huge drives)."
        )
        self._refresh_layout_options_from_scan()
        self.tabs.set("Overview")
        self._refresh_workflow()
        if announce:
            self._set_status(
                f"Loaded last scan: {file_count} files from {db_path.name} "
                "(no re-scan needed)."
            )
        self._persist_settings()

    def _refresh_source_list(self) -> None:
        """Rebuild the selectable source checklist."""
        for child in self.source_list_frame.winfo_children():
            child.destroy()
        self._source_check_vars.clear()

        if not self.source_paths:
            ctk.CTkLabel(
                self.source_list_frame,
                text="No folders or drives yet",
                font=ctk.CTkFont(size=14, weight="bold"),
                justify="left",
                anchor="w",
            ).pack(anchor="w", padx=8, pady=(10, 4))
            ctk.CTkLabel(
                self.source_list_frame,
                text=(
                    "1) Click Add folder / drive (green button below)\n"
                    "2) Pick a place from the sidebar or browse folders\n"
                    "3) Click Scan now\n"
                    "Or use Reload last scan if you scanned before."
                ),
                text_color=("gray40", "gray70"),
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=8, pady=(0, 10))
            self._enable_copyable_text(self.source_list_frame)
            return

        for path in self.source_paths:
            row = ctk.CTkFrame(self.source_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            var = tk.BooleanVar(value=False)
            self._source_check_vars[path] = var
            ctk.CTkCheckBox(row, text=path, variable=var).pack(
                side="left", fill="x", expand=True
            )
            ctk.CTkButton(
                row,
                text="Open",
                width=60,
                height=26,
                command=lambda p=path: self._open_folder(Path(p)),
            ).pack(side="right", padx=(4, 0))
        self._enable_copyable_text(self.source_list_frame)

    def _write_box(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        make_textbox_readonly_copyable(box)

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
        self._refresh_workflow()
        self._persist_settings()

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
        self._refresh_workflow()
        self._persist_settings()

    def select_all_sources(self) -> None:
        for var in self._source_check_vars.values():
            var.set(True)

    def clear_source_ticks(self) -> None:
        for var in self._source_check_vars.values():
            var.set(False)

    def clear_sources(self) -> None:
        if not self.source_paths:
            return
        ok = messagebox.askyesno(
            APP_TITLE,
            f"Remove all {len(self.source_paths)} source folder(s) from the list?\n\n"
            "This does not delete files on disk — it only clears the scan list.",
        )
        if not ok:
            return
        self.source_paths.clear()
        self._refresh_source_list()
        self._set_status("Cleared source list.")
        self._refresh_workflow()
        self._persist_settings()

    def _on_appearance_change(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        # Refresh paned sash colors on next rebuild; banner stays readable
        try:
            if getattr(self, "dup_paned", None) is not None:
                self.dup_paned.configure(bg=paned_bg())
        except Exception:
            pass
        self._persist_settings()

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
        self._persist_settings()
        self._close_current_store()
        self.scan_result = None
        self._set_busy(True)
        self._set_status("Scanning…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        options = ScanOptions(
            include_junk_system=self.include_junk_var.get(),
            scan_zip_contents=self.scan_zips_var.get(),
            extract_zips=self.extract_zips_var.get(),
            delete_zip_after_extract=bool(self.delete_zip_after_extract_var.get())
            and bool(self.extract_zips_var.get()),
            delete_zip_if_low_space=bool(self.delete_zip_if_low_space_var.get())
            and bool(self.extract_zips_var.get())
            and not bool(self.delete_zip_after_extract_var.get()),
        )
        db_path = last_scan_db_path()
        result = scan_paths(
            self.source_paths,
            status_cb=self._worker_status,
            should_cancel=self._should_cancel,
            options=options,
            store_path=db_path,
            progress_cb=self._worker_progress,
        )
        store = result.store
        if store is not None:
            try:
                store.set_meta("sources_json", json.dumps(self.source_paths))
                store.set_meta("include_junk", "1" if options.include_junk_system else "0")
                store.set_meta("scan_zips", "1" if options.scan_zip_contents else "0")
                store.set_meta("extract_zips", "1" if options.extract_zips else "0")
                store.set_meta(
                    "delete_zip_after_extract",
                    "1" if options.delete_zip_after_extract else "0",
                )
                store.set_meta(
                    "delete_zip_if_low_space",
                    "1" if options.delete_zip_if_low_space else "0",
                )
                store.set_meta("duration_seconds", str(result.duration_seconds))
                store.set_meta("archive_members", str(result.archive_members))
                store.set_meta("zips_extracted", str(result.zips_extracted))
                store.set_meta(
                    "zips_deleted_low_space", str(result.zips_deleted_low_space)
                )
                store.set_meta("total_bytes", str(result.total_bytes))
            except Exception:
                pass
        self.after(0, self._scan_done, result)

    def _scan_done(self, result: ScanResult) -> None:
        self._set_busy(False)
        self.scan_result = result
        self.dup_report = None
        self._organise_preview_ready = False
        self._update_overview()
        self._show_duplicates_empty_state()
        self.dup_summary.configure(
            text="Scan ready. Click Find duplicates on Overview (Exact is safest for huge drives)."
        )
        self._refresh_layout_options_from_scan()
        self.tabs.set("Overview")
        self.workflow_banner.configure(
            text="Next: click Find duplicates on Overview (Exact). Then open Duplicates to compare."
        )
        took = format_duration(result.duration_seconds)
        n = result.file_count or len(result.files)
        self._set_status(f"Scan finished: {n} files in {took}.")
        self._persist_settings()

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
            "══ SCAN COMPLETE ══",
            f"Duration: {took}",
            f"Files found: {file_count}",
            f"  · On disk: {disk_files}",
            f"  · Inside zips: {result.archive_members}",
            f"Total size: {format_bytes(total_size)}",
            f"Skipped / unreadable: {result.skipped}",
            f"Errors: {len(result.errors)}",
        ]
        if result.zips_extracted:
            lines.insert(
                5,
                f"  · Zips extracted to disk (*_unzipped folders): {result.zips_extracted}",
            )
        if result.zips_deleted_low_space:
            lines.insert(
                6 if result.zips_extracted else 5,
                f"  · Zips deleted after unzip: {result.zips_deleted_low_space}",
            )
        if result.cross_device_skipped:
            lines.append(f"Other-mount folders skipped: {result.cross_device_skipped}")
        if result.zip_members_capped:
            lines.append(f"Zip listing caps hit: {result.zip_members_capped}")
        if store is not None:
            lines.append("Index: SQLite (large-drive mode)")
        if readonly_sources:
            lines.append("")
            lines.append("── READ-ONLY SOURCES ──")
            lines.append(
                "(quarantine / delete / move will fail until remounted read-write)"
            )
            for src in readonly_sources:
                lines.append(f"  • {src}")
        lines.append("")
        lines.append("── BY CATEGORY ──")
        for cat in sorted(by_cat):
            lines.append(f"  • {cat}: {by_cat[cat]}")

        if result.errors:
            lines.append("")
            lines.append("── ERRORS (first 20) ──")
            for err in result.errors[:20]:
                lines.append(f"  - {err}")

        lines.append("")
        lines.append("── SAMPLE PATHS (first 40) ──")
        for info in sample:
            junk = " [junk]" if info.is_junk_location else ""
            lines.append(f"  [{info.category}]{junk} {info.display_path}")

        lines.append("")
        lines.append("── TIMING ──")
        lines.append(
            f"This scan took {took}. "
            "Duplicate searches append their own timing below when you run them."
        )

        cat_bits = ", ".join(f"{k}: {v}" for k, v in sorted(by_cat.items())) or "no categories"
        summary = (
            f"Completed in {took}\n"
            f"{file_count} files · {format_bytes(total_size)}\n"
            f"{cat_bits}"
        )
        if result.archive_members:
            summary += f"\n{result.archive_members} files inside zips"
        if result.zips_extracted:
            summary += f"\n{result.zips_extracted} zip(s) extracted to *_unzipped folders"
        if result.zips_deleted_low_space:
            summary += f"\n{result.zips_deleted_low_space} zip(s) deleted after unzip"
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
            status_cb=self._worker_status,
            should_cancel=self._should_cancel,
            store=store,
        )
        self.after(0, self._dup_done, report)

    def _dup_done(self, report: DuplicateReport) -> None:
        self._set_busy(False)
        report.groups = sort_groups_by_file_type(report.groups)
        self.dup_report = report
        # Auto-mark every extra copy so quarantine/delete is ready while browsing
        self._dup_marked_paths.clear()
        for group in report.groups:
            for info in group.files[1:]:
                if not info.is_inside_archive:
                    self._dup_marked_paths.add(self._path_key(info))
        self._refresh_marked_label()
        took = format_duration(report.duration_seconds)
        note_txt = ""
        if report.notes:
            note_txt = " · " + "; ".join(report.notes[:2])
        self.dup_summary.configure(
            text=(
                f"{len(report.groups)} groups (by file type) · "
                f"{report.duplicate_file_count} extras · "
                f"~{format_bytes(report.wasted_bytes)} reclaimable · "
                f"took {took}"
                f"{note_txt}  ·  Extras auto-marked · ← → browse"
            )
        )
        # Append timing to Overview report
        if self.scan_result:
            self._append_overview_timing_block(report)

        self.tabs.set("Duplicates")
        self._organise_preview_ready = False
        self.workflow_banner.configure(
            text="Next: compare groups, then Quarantine extras (oldest file is kept). Prefer quarantine over delete."
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
        qpath = quarantine_root()
        summary = self._extras_summary_text(extras, "QUARANTINE ALL DUPLICATE EXTRAS")
        ok = messagebox.askyesno(
            APP_TITLE,
            summary
            + f"\nDestination:\n{qpath}\n\n"
            "Files are moved (not permanently deleted). Continue?",
        )
        if not ok:
            return
        self._start_bulk_dup_cleanup(extras, "quarantine")

    def delete_all_extras(self) -> None:
        """Permanently delete every extra copy across all groups (keeps oldest KEEP)."""
        extras = self._all_disk_extras()
        if extras is None:
            return
        count = len(extras)
        summary = self._extras_summary_text(extras, "DELETE ALL DUPLICATE EXTRAS")
        ok = messagebox.askyesno(
            APP_TITLE,
            summary
            + "\nThis cannot be undone by this app.\n"
            "Prefer “Quarantine all extras” if you might want files back.\n\n"
            "Continue?",
        )
        if not ok:
            return
        ok2 = messagebox.askyesno(
            APP_TITLE,
            f"Final confirmation: permanently delete {count} extra duplicate(s)?",
        )
        if not ok2:
            return
        self._start_bulk_dup_cleanup(extras, "delete")

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
        self._persist_settings()
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
            f"Media date folders: {options.media_date_depth} · Docs by extension: {options.documents_by_ext} · Separate archives: {options.separate_archives}",
            f"Category overrides: {options.category_subfolders or {}}",
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
        self._organise_preview_ready = True
        self._set_status(f"Organise preview ({label}): {len(plan.items)} actions planned.")
        self._refresh_workflow()
        self._persist_settings()

    def _organise_plan_key(self, dest: str, layout_ids, options: OrganiseOptions) -> str:
        cats = ",".join(sorted(options.categories or []))
        layouts = "+".join(layout_ids) if isinstance(layout_ids, list) else str(layout_ids)
        modes = ",".join(
            f"{k}={v}"
            for k, v in sorted((options.category_subfolders or {}).items())
        )
        return (
            f"{dest}|{layouts}|{cats}|{options.media_date_depth}|"
            f"{options.documents_by_ext}|{options.separate_archives}|{modes}|"
            f"{options.copy_instead_of_move}|"
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
        self._enable_copyable_text(win)
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
                "Adds into the destination — existing archive files/folders are left alone.\n"
                "Name clashes get a unique name (e.g. photo_1.jpg); nothing is overwritten.\n\n"
                + (
                    "Copy keeps originals on the source until you delete them later (safer).\n"
                    if options.copy_instead_of_move
                    else (
                        "MOVE removes files from the source only (destination is not wiped).\n"
                        "Prefer Copy if unsure.\n"
                    )
                )
                + "Continue?",
            )
            if not ok:
                return
            # Extra confirm for destructive move (source side only)
            if not options.copy_instead_of_move:
                ok2 = messagebox.askyesno(
                    APP_TITLE,
                    f"Final confirm: MOVE {len(plan.items)} files?\n\n"
                    "Files leave the source location (not undone automatically).\n"
                    "Destination content already there is not deleted.\n"
                    "Quarantine is not used for organise.",
                )
                if not ok2:
                    return

        self._last_organise_plan = plan
        self._set_busy(True)
        mode_label = "Dry run" if dry else ("Copy" if options.copy_instead_of_move else "Move")
        self._set_status(f"{mode_label} starting ({label})…")
        threading.Thread(
            target=self._organise_worker,
            args=(plan, dry, options, dest, label),
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
            status_cb=self._worker_status,
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
        if dry:
            self._organise_preview_ready = True
            self.workflow_banner.configure(
                text="Dry run finished — untick Dry run, then click Apply organise to make real changes."
            )
        else:
            self._organise_preview_ready = False
            self._refresh_workflow()
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
