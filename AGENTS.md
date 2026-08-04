# Archive Organiser

A local, private desktop GUI app (Python + CustomTkinter/Tk) that scans folders,
finds exact duplicate files, and suggests a tidy folder layout. See `README.md`
for the full user-facing guide and `main.py` for the entry point.

## Cursor Cloud specific instructions

This is a **desktop GUI application**, not a web service. It needs an X display.

### Running the app
- Use the project virtualenv and the VM's VNC X server (display `:1`):
  `source .venv/bin/activate && DISPLAY=:1 python3 main.py`
- The system package `python3-tk` is required for `tkinter` and is baked into the
  VM snapshot (it is NOT installed by the startup update script). If `import
  tkinter` fails, reinstall it with `sudo apt-get install -y python3-tk`.

### Scanning gotcha (important, non-obvious)
- The scanner default `ScanOptions.stay_on_device=True` (`scanner.py`) skips any
  file whose `st_dev` differs from the scanned root folder's `st_dev`. On the
  VM's **overlay** root filesystem (`/`, and therefore `/workspace`, `/tmp`,
  `/home`), regular files report a *different* device number than their parent
  directory, so a scan there finds **0 files** with default options.
- To test scanning / duplicate detection, put test folders on a **tmpfs** mount
  such as `/dev/shm` (small, ~64 MB), where files and directories share the same
  device number. This is only a VM/overlayfs quirk; on a normal Linux desktop
  drive the default works fine.

### Lint / test / build
- There is no automated test suite and no linter config. Treat
  `python -m compileall .` (syntax check) plus importing every module as the
  effective "lint". There is no build step — it runs directly from source.

### Where state is stored
- Settings/config: `~/.config/ArchiveOrganiser/`; data (SQLite scan index):
  `~/.local/share/ArchiveOrganiser/` (see `app_settings.py`). Both fall back to
  in-project dot-folders if those locations are not writable.
