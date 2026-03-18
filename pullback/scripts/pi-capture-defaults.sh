#!/bin/bash
# pi-capture-defaults.sh — Capture current system tuning defaults before any changes.
# Writes to docs/TUNEDEFAULT.local.md so you have a record of THIS host's values.
# Run ONCE on a fresh install, BEFORE applying any tuning.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"
OUTPUT="${PROJECT_DIR}/docs/TUNEDEFAULT.local.yaml"

if [[ ! -f "$VENV_PYTHON" ]]; then
    VENV_PYTHON="python3"
fi

if [[ -f "$OUTPUT" ]]; then
    echo "Error: ${OUTPUT} already exists — defaults already captured." >&2
    echo "  Delete it manually if you want to re-capture." >&2
    exit 1
fi

echo "[capture-defaults] Capturing system defaults to ${OUTPUT}"

cd "$PROJECT_DIR"

{
    echo "# Tuning defaults — captured from $(hostname) on $(date -Iseconds)"
    echo "# Run BEFORE applying any tuning. Used to revert to OS defaults."
    echo ""
    "$VENV_PYTHON" -c "from tuning import status_yaml; print(status_yaml())"
} > "$OUTPUT"

echo "[capture-defaults] Defaults captured to ${OUTPUT}"
echo "[capture-defaults] Review the file, then proceed with tuning one parameter at a time."
