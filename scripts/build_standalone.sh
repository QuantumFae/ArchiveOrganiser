#!/usr/bin/env bash
# Build a standalone folder with PyInstaller (optional packaging for other PCs).
# Requires: pip install pyinstaller
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is not installed."
  echo "Install it with:  pip install pyinstaller"
  exit 1
fi

OUT="$ROOT/dist/ArchiveOrganiser"
rm -rf "$ROOT/build/ArchiveOrganiser" "$OUT"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name ArchiveOrganiser \
  --paths "$ROOT" \
  --collect-all customtkinter \
  --collect-all PIL \
  "$ROOT/main.py"

echo
echo "Standalone build created under: $ROOT/dist/ArchiveOrganiser/"
echo "Copy that folder to another Linux PC and run ArchiveOrganiser."
echo "(AppImage wrapping can be added later if you want a single .AppImage file.)"
