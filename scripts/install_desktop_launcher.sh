#!/usr/bin/env bash
# Install a one-click desktop launcher for Archive Organiser (current user).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT/scripts/run_archive_organiser.sh"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS/ArchiveOrganiser.desktop"

chmod +x "$RUNNER"
mkdir -p "$APPS"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Archive Organiser
Comment=Private local duplicate finder and file organiser
Exec=$RUNNER
Path=$ROOT
Icon=folder
Terminal=false
Categories=Utility;FileTools;
StartupNotify=true
EOF

chmod +x "$DESKTOP"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

echo "Launcher installed:"
echo "  $DESKTOP"
echo "Open your app menu and search for “Archive Organiser”, or run:"
echo "  gtk-launch ArchiveOrganiser"
