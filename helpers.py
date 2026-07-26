"""Small helpers that do not belong to one feature alone."""

import os
import subprocess
import sys
import time
from pathlib import Path


def format_duration(seconds: float) -> str:
    """Human-readable elapsed time, e.g. 0.4s, 12s, 3m 05s, 1h 02m 03s."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 1:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_bytes(num: int) -> str:
    """Human-readable size, e.g. 12.3 MB."""
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < step or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{num} B"


class ElapsedTimer:
    """Track how long a task has been running; used in status messages."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def seconds(self) -> float:
        return time.monotonic() - self.started

    def label(self) -> str:
        return format_duration(self.seconds())

    def stamp(self, message: str) -> str:
        return f"[{self.label()}] {message}"


def open_containing_folder(path: Path) -> str:
    """
    Open the folder that contains this file in the system file manager.
    When possible, also highlight/select the file.
    Returns an empty string on success, or an error message.
    """
    try:
        path = Path(path).expanduser()
        if path.exists():
            path = path.resolve()
    except OSError as exc:
        return f"Could not resolve path: {exc}"

    folder = path if path.is_dir() else path.parent
    if not folder.exists():
        return f"Folder not found: {folder}"

    try:
        if sys.platform.startswith("linux"):
            # Prefer selecting the file when a known file manager is available
            if path.is_file():
                for cmd in (
                    ["nautilus", "--select", str(path)],
                    ["dolphin", "--select", str(path)],
                ):
                    try:
                        # Don't wait — open and return; if the binary is missing, try next
                        subprocess.Popen(
                            cmd,
                            start_new_session=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return ""
                    except FileNotFoundError:
                        continue
            subprocess.Popen(
                ["xdg-open", str(folder)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            if path.is_file():
                subprocess.Popen(["open", "-R", str(path)], start_new_session=True)
            else:
                subprocess.Popen(["open", str(folder)], start_new_session=True)
        elif sys.platform.startswith("win"):
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)], start_new_session=True)
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            return f"Open folder is not supported on this system ({sys.platform})."
    except OSError as exc:
        return f"Could not open folder: {exc}"
    return ""
