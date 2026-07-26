"""
Archive Organiser – main window.

Private by design: everything runs on your computer.
No files are uploaded anywhere.
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from duplicates import DuplicateGroup, DuplicateReport, find_duplicates
from models import FileInfo, ScanResult
from organiser import apply_organise_plan, build_organise_plan
from preview import load_preview
from quarantine import format_bytes, move_to_quarantine, permanently_delete, quarantine_root
from scanner import scan_paths


APP_TITLE = "Archive Organiser"
APP_SIZE = "1280x800"


class ArchiveOrganiserApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(900, 600)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.source_paths: list[str] = []
        self.scan_result: Optional[ScanResult] = None
        self.dup_report: Optional[DuplicateReport] = None
        self._cancel_flag = False
        self._busy = False
        self._selected_group_index: Optional[int] = None
        self._compare_check_vars: list[tk.BooleanVar] = []
        self._compare_file_infos: list[FileInfo] = []
        self._compare_image_refs: list[object] = []  # keep CTkImage alive
        self._group_buttons: list[ctk.CTkButton] = []

        self._build_layout()
        self._set_status("Ready. Add folders or drives, then Scan.")

    # ---------- layout ----------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=APP_TITLE,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Private · Local only · Safe quarantine (no permanent delete by default)",
            font=ctk.CTkFont(size=13),
            text_color=("gray40", "gray70"),
        ).grid(row=1, column=0, sticky="w")

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
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

        footer = ctk.CTkFrame(self)
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        footer.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(footer, text="", anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", padx=8, pady=4)

        self.progress = ctk.CTkProgressBar(footer, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.progress.set(0)

        self.cancel_btn = ctk.CTkButton(
            footer, text="Cancel", width=100, command=self._request_cancel, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, rowspan=2, padx=8, pady=4)

    def _build_sources_tab(self) -> None:
        tab = self.tabs.tab("Sources")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Add external HDDs, USBs, SD cards, or folders to include in the scan.",
            wraplength=800,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        self.source_list = ctk.CTkTextbox(tab, height=280)
        self.source_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.source_list.configure(state="disabled")

        buttons = ctk.CTkFrame(tab, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(buttons, text="Add folder / drive", command=self.add_source).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(buttons, text="Remove selected line", command=self.remove_selected_source).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(buttons, text="Clear all", command=self.clear_sources).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            buttons, text="Scan now", command=self.start_scan, fg_color="#2a7a4b"
        ).pack(side="right")

    def _build_overview_tab(self) -> None:
        tab = self.tabs.tab("Overview")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self.overview_summary = ctk.CTkLabel(
            tab, text="No scan yet.", justify="left", anchor="w"
        )
        self.overview_summary.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        self.overview_box = ctk.CTkTextbox(tab)
        self.overview_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.overview_box.insert("1.0", "Scan results and a short report will appear here.")
        self.overview_box.configure(state="disabled")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(row, text="Save report…", command=self.save_report).pack(side="left")
        ctk.CTkButton(
            row, text="Find duplicates", command=self.start_duplicate_search, fg_color="#2a7a4b"
        ).pack(side="right")

    def _build_duplicates_tab(self) -> None:
        tab = self.tabs.tab("Duplicates")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=3)
        tab.grid_rowconfigure(1, weight=1)

        self.dup_summary = ctk.CTkLabel(
            tab,
            text="Run a scan, then click Find duplicates on the Overview tab.",
            justify="left",
            anchor="w",
        )
        self.dup_summary.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))

        # Left: full list of duplicate groups
        left = ctk.CTkFrame(tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="Duplicate groups", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        self.group_list = ctk.CTkScrollableFrame(left, width=280)
        self.group_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.group_list.grid_columnconfigure(0, weight=1)

        # Right: side-by-side compare for the selected group
        right = ctk.CTkFrame(tab)
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            right,
            text="Side-by-side compare (select a group on the left)",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.compare_frame = ctk.CTkScrollableFrame(right, orientation="horizontal")
        self.compare_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # Actions under the compare view
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        ctk.CTkButton(
            row,
            text="Select extras only",
            command=self.select_extras_in_group,
            width=140,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Clear selection",
            command=self.clear_compare_selection,
            width=120,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Quarantine all extras (every group)",
            command=self.quarantine_extras,
            fg_color="#a65d00",
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            row,
            text="Permanently delete selected",
            command=self.delete_selected_compare_files,
            fg_color="#8b1e1e",
            hover_color="#6e1515",
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            row,
            text="Quarantine selected",
            command=self.quarantine_selected_compare_files,
            fg_color="#2a7a4b",
        ).pack(side="right", padx=(8, 0))

    def _clear_group_list(self) -> None:
        for child in self.group_list.winfo_children():
            child.destroy()
        self._group_buttons.clear()

    def _clear_compare_panel(self) -> None:
        for child in self.compare_frame.winfo_children():
            child.destroy()
        self._compare_check_vars.clear()
        self._compare_file_infos.clear()
        self._compare_image_refs.clear()
        self._selected_group_index = None

    def _populate_group_list(self) -> None:
        self._clear_group_list()
        self._clear_compare_panel()
        if not self.dup_report or not self.dup_report.groups:
            ctk.CTkLabel(self.group_list, text="No duplicate groups found.").grid(
                row=0, column=0, sticky="w", padx=4, pady=4
            )
            return

        for index, group in enumerate(self.dup_report.groups):
            label = (
                f"Group {index + 1} · {len(group.files)} copies\n"
                f"{format_bytes(group.size)} · waste ~{format_bytes(group.wasted_bytes)}\n"
                f"{group.files[0].path.name}"
            )
            btn = ctk.CTkButton(
                self.group_list,
                text=label,
                anchor="w",
                justify="left",
                height=70,
                fg_color=("gray75", "gray35"),
                text_color=("gray10", "gray90"),
                command=lambda i=index: self.show_duplicate_group(i),
            )
            btn.grid(row=index, column=0, sticky="ew", padx=2, pady=3)
            self._group_buttons.append(btn)

        # Auto-open the first group so the visual compare is ready immediately
        self.show_duplicate_group(0)

    def _highlight_group_button(self, index: int) -> None:
        for i, btn in enumerate(self._group_buttons):
            if i == index:
                btn.configure(fg_color=("#3a7ebf", "#1f538d"), text_color="white")
            else:
                btn.configure(
                    fg_color=("gray75", "gray35"),
                    text_color=("gray10", "gray90"),
                )

    def show_duplicate_group(self, index: int) -> None:
        """Show every file in one duplicate group side by side."""
        if not self.dup_report or index < 0 or index >= len(self.dup_report.groups):
            return
        group = self.dup_report.groups[index]
        self._clear_compare_panel()
        self._selected_group_index = index
        self._highlight_group_button(index)

        for col, info in enumerate(group.files):
            role = "KEEP (oldest)" if col == 0 else f"Copy #{col + 1}"
            self._add_compare_card(col, info, role)

        self._set_status(
            f"Viewing group {index + 1}/{len(self.dup_report.groups)} · "
            f"{len(group.files)} files · {format_bytes(group.size)} each"
        )

    def _add_compare_card(self, col: int, info: FileInfo, role: str) -> None:
        card = ctk.CTkFrame(self.compare_frame, width=360, height=520)
        card.grid(row=0, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(card, text=role, font=ctk.CTkFont(size=14, weight="bold"))
        title.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        preview = load_preview(info.path, info.size, info.modified, info.category, role)

        if preview.kind == "image" and preview.image is not None:
            # Fit preview into the card without stretching oddly
            w, h = preview.image.size
            ctk_image = ctk.CTkImage(light_image=preview.image, dark_image=preview.image, size=(w, h))
            self._compare_image_refs.append(ctk_image)
            img_label = ctk.CTkLabel(card, text="", image=ctk_image)
            img_label.grid(row=1, column=0, padx=10, pady=4)
        elif preview.kind == "text":
            text_box = ctk.CTkTextbox(card, width=320, height=180)
            text_box.grid(row=1, column=0, padx=10, pady=4, sticky="ew")
            text_box.insert("1.0", preview.text_content)
            text_box.configure(state="disabled")
        else:
            msg = preview.error or "No visual preview"
            ctk.CTkLabel(card, text=msg, wraplength=320, justify="left").grid(
                row=1, column=0, padx=10, pady=8, sticky="w"
            )

        info_box = ctk.CTkTextbox(card, width=320, height=150)
        info_box.grid(row=2, column=0, padx=10, pady=4, sticky="ew")
        info_box.insert("1.0", preview.info_text)
        info_box.configure(state="disabled")

        check_var = tk.BooleanVar(value=(role != "KEEP (oldest)"))
        checkbox = ctk.CTkCheckBox(card, text="Select for remove/delete", variable=check_var)
        checkbox.grid(row=3, column=0, sticky="w", padx=10, pady=(4, 12))

        self._compare_check_vars.append(check_var)
        self._compare_file_infos.append(info)

    def select_extras_in_group(self) -> None:
        for i, var in enumerate(self._compare_check_vars):
            var.set(i > 0)

    def clear_compare_selection(self) -> None:
        for var in self._compare_check_vars:
            var.set(False)

    def _selected_compare_files(self) -> list[FileInfo]:
        chosen: list[FileInfo] = []
        for var, info in zip(self._compare_check_vars, self._compare_file_infos):
            if var.get():
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
            self.scan_result.files = [f for f in self.scan_result.files if still_here(f)]

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
            text=(
                "Choose a destination folder. The app suggests a tidy layout:\n"
                "Photos/YYYY/MM · Videos/YYYY/MM · Documents/<type> · Audio · Archives · Other"
            ),
            justify="left",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        dest_row = ctk.CTkFrame(tab, fg_color="transparent")
        dest_row.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        dest_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(dest_row, text="Destination:").grid(row=0, column=0, padx=(0, 8))
        self.dest_entry = ctk.CTkEntry(dest_row, placeholder_text="Choose a tidy destination folder")
        self.dest_entry.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(dest_row, text="Browse…", width=100, command=self.choose_dest).grid(
            row=0, column=2, padx=(8, 0)
        )

        self.org_box = ctk.CTkTextbox(tab)
        self.org_box.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        self.org_box.insert("1.0", "Preview of planned moves will appear here.")
        self.org_box.configure(state="disabled")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(row, text="Preview plan", command=self.preview_organise).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            row,
            text="Apply moves",
            command=self.apply_organise,
            fg_color="#2a7a4b",
        ).pack(side="right")

        self.dry_run_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            row,
            text="Dry run only (preview, do not move)",
            variable=self.dry_run_var,
        ).pack(side="right", padx=16)

    def _build_help_tab(self) -> None:
        tab = self.tabs.tab("Help")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        help_box = ctk.CTkTextbox(tab)
        help_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        help_box.insert(
            "1.0",
            """HOW TO USE (first draft)

1. Sources tab
   • Click “Add folder / drive” for each messy HDD, USB, SD, or folder.
   • Click “Scan now”. Wait until the status says the scan is complete.

2. Overview tab
   • Read the summary (counts by type, total size).
   • Optionally save a text report.
   • Click “Find duplicates”.

3. Duplicates tab
   • Left: full list of every duplicate group found.
   • Click a group to open a side-by-side visual compare on the right.
   • Each card shows a preview (image/text when possible), file info, and a checkbox.
   • Extras are pre-ticked; the oldest “KEEP” file is not.
   • “Quarantine selected” moves ticked files to ArchiveOrganiser_Quarantine/ (safe).
   • “Permanently delete selected” erases ticked files after two confirmations.
   • “Quarantine all extras (every group)” still clears extras across the whole list.

4. Organise tab
   • Choose a destination folder (ideally an empty or new tidy drive/folder).
   • Click “Preview plan” with Dry run checked.
   • When happy, untick Dry run and click Apply to move files into:
       Photos/YYYY/MM, Videos/YYYY/MM, Documents/<ext>, Audio, Archives, Other

PRIVACY & SAFETY
• This app does not upload your files or talk to the internet for organising.
• Scanning and hashing happen only on your machine.
• Prefer quarantine over permanent delete. You can restore quarantined files.
• Start with a small test folder before pointing at your whole archive.

TIPS
• Plug in external drives and make sure they appear in your file manager first.
• On Linux, drives often appear under /media/YOURNAME/ or /mnt/.
• Re-scan after plugging in a new drive if you add more sources.
• Large archives take time to fingerprint — leave the window open while it works.
""",
        )
        help_box.configure(state="disabled")

    # ---------- helpers ----------

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._cancel_flag = False
            self.cancel_btn.configure(state="normal")
            self.progress.start()
        else:
            self.cancel_btn.configure(state="disabled")
            self.progress.stop()
            self.progress.set(0)

    def _request_cancel(self) -> None:
        self._cancel_flag = True
        self._set_status("Cancelling…")

    def _should_cancel(self) -> bool:
        return self._cancel_flag

    def _refresh_source_list(self) -> None:
        self.source_list.configure(state="normal")
        self.source_list.delete("1.0", "end")
        if not self.source_paths:
            self.source_list.insert("1.0", "(no folders added yet)")
        else:
            self.source_list.insert("1.0", "\n".join(self.source_paths))
        self.source_list.configure(state="disabled")

    def _write_box(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    # ---------- sources ----------

    def add_source(self) -> None:
        path = filedialog.askdirectory(title="Choose a folder or mounted drive")
        if not path:
            return
        if path in self.source_paths:
            messagebox.showinfo(APP_TITLE, "That folder is already in the list.")
            return
        self.source_paths.append(path)
        self._refresh_source_list()
        self._set_status(f"Added: {path}")

    def remove_selected_source(self) -> None:
        # Simple approach: remove the last item (beginner-friendly)
        if not self.source_paths:
            return
        removed = self.source_paths.pop()
        self._refresh_source_list()
        self._set_status(f"Removed: {removed}")

    def clear_sources(self) -> None:
        self.source_paths.clear()
        self._refresh_source_list()
        self._set_status("Cleared source list.")

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
        result = scan_paths(
            self.source_paths,
            status_cb=lambda msg: self.after(0, self._set_status, msg),
            should_cancel=self._should_cancel,
        )
        self.after(0, self._scan_done, result)

    def _scan_done(self, result: ScanResult) -> None:
        self._set_busy(False)
        self.scan_result = result
        self.dup_report = None
        self._update_overview()
        self._clear_group_list()
        self._clear_compare_panel()
        self.dup_summary.configure(text="Duplicates not searched yet. Use Find duplicates on Overview.")
        self.tabs.set("Overview")
        self._set_status(f"Scan finished: {len(result.files)} files.")

    def _update_overview(self) -> None:
        if not self.scan_result:
            return
        files = self.scan_result.files
        total_size = sum(f.size for f in files)
        by_cat: dict[str, int] = {}
        for f in files:
            by_cat[f.category] = by_cat.get(f.category, 0) + 1

        lines = [
            f"Files found: {len(files)}",
            f"Total size: {format_bytes(total_size)}",
            f"Skipped / unreadable: {self.scan_result.skipped}",
            f"Errors: {len(self.scan_result.errors)}",
            "",
            "By category:",
        ]
        for cat in sorted(by_cat):
            lines.append(f"  • {cat}: {by_cat[cat]}")

        if self.scan_result.errors:
            lines.append("")
            lines.append("Errors (first 20):")
            for err in self.scan_result.errors[:20]:
                lines.append(f"  - {err}")

        lines.append("")
        lines.append("Sample paths (first 40):")
        for info in files[:40]:
            lines.append(f"  [{info.category}] {info.path}")

        summary = (
            f"{len(files)} files · {format_bytes(total_size)} · "
            + ", ".join(f"{k}: {v}" for k, v in sorted(by_cat.items()))
        )
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
        if not self.scan_result or not self.scan_result.files:
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        self._set_busy(True)
        self._set_status("Searching for duplicates…")
        threading.Thread(target=self._dup_worker, daemon=True).start()

    def _dup_worker(self) -> None:
        report = find_duplicates(
            self.scan_result.files if self.scan_result else [],
            status_cb=lambda msg: self.after(0, self._set_status, msg),
            should_cancel=self._should_cancel,
        )
        self.after(0, self._dup_done, report)

    def _dup_done(self, report: DuplicateReport) -> None:
        self._set_busy(False)
        self.dup_report = report
        self.dup_summary.configure(
            text=(
                f"{len(report.groups)} duplicate groups · "
                f"{report.duplicate_file_count} extra files · "
                f"~{format_bytes(report.wasted_bytes)} reclaimable  ·  "
                "Click a group to compare side by side"
            )
        )
        self._populate_group_list()
        self.tabs.set("Duplicates")
        self._set_status("Duplicate search finished.")

    def quarantine_extras(self) -> None:
        if not self.dup_report or not self.dup_report.groups:
            messagebox.showinfo(APP_TITLE, "No duplicate extras to quarantine.")
            return
        extras = []
        for group in self.dup_report.groups:
            extras.extend(group.files[1:])  # keep index 0
        count = len(extras)
        if count == 0:
            messagebox.showinfo(APP_TITLE, "No extra copies found.")
            return
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

    # ---------- organise ----------

    def choose_dest(self) -> None:
        path = filedialog.askdirectory(title="Choose destination folder")
        if not path:
            return
        self.dest_entry.delete(0, "end")
        self.dest_entry.insert(0, path)

    def preview_organise(self) -> None:
        if not self.scan_result or not self.scan_result.files:
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        dest = self.dest_entry.get().strip()
        if not dest:
            messagebox.showwarning(APP_TITLE, "Choose a destination folder.")
            return
        plan = build_organise_plan(self.scan_result.files, dest)
        lines = [
            f"Planned moves: {len(plan.items)}",
            f"Skipped: {len(plan.skipped)}",
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
        self._set_status(f"Organise preview: {len(plan.items)} moves planned.")

    def apply_organise(self) -> None:
        if not self.scan_result or not self.scan_result.files:
            messagebox.showwarning(APP_TITLE, "Run a scan first.")
            return
        dest = self.dest_entry.get().strip()
        if not dest:
            messagebox.showwarning(APP_TITLE, "Choose a destination folder.")
            return
        dry = self.dry_run_var.get()
        plan = build_organise_plan(self.scan_result.files, dest)
        if not plan.items:
            messagebox.showinfo(APP_TITLE, "Nothing to move.")
            return
        if not dry:
            ok = messagebox.askyesno(
                APP_TITLE,
                f"Move {len(plan.items)} files into:\n{dest}\n\n"
                "This changes where files live. Dry run is safer for a first try.\n"
                "Continue?",
            )
            if not ok:
                return
        log = apply_organise_plan(
            plan,
            dry_run=dry,
            status_cb=lambda msg: self.after(0, self._set_status, msg),
        )
        self._write_box(self.org_box, "\n".join(log[:500]))
        mode = "Dry run" if dry else "Moves"
        self._set_status(f"{mode} finished: {len(plan.items)} items.")
        if not dry:
            messagebox.showinfo(APP_TITLE, "Moves complete. Consider scanning again.")
            self.scan_result = None
            self.dup_report = None
