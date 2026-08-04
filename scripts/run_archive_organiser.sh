#!/usr/bin/env bash
# Start Archive Organiser with the project virtualenv when present.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
exec python3 "$ROOT/main.py" "$@"
