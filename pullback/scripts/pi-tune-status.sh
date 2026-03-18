#!/bin/bash
# pi-tune-status.sh — Show current tuning settings as YAML.
# Usage:
#   pi-tune-status.sh              # display to console
#   pi-tune-status.sh --save=FILE  # save to file

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"

if [[ ! -f "$VENV_PYTHON" ]]; then
    VENV_PYTHON="python3"
fi

SAVE=""
for arg in "$@"; do
    case "$arg" in
        --save=*) SAVE="${arg#*=}" ;;
    esac
done

cd "$PROJECT_DIR"

if [[ -n "$SAVE" ]]; then
    "$VENV_PYTHON" -c "from tuning import status_yaml; print(status_yaml())" > "$SAVE"
    echo "Saved to ${SAVE}"
else
    "$VENV_PYTHON" -c "from tuning import status_yaml; print(status_yaml())"
fi
