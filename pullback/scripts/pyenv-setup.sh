#!/bin/bash
# Create local venv and install dependencies.
# Must be run ON the Pi (not the dev server).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/venv"

if [ -d "$VENV_DIR" ]; then
    echo "venv already exists at ${VENV_DIR}"
else
    echo "Creating venv at ${VENV_DIR}"
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies..."
"${VENV_DIR}/bin/pip" install --quiet pyyaml

echo "Done. Test:"
"${VENV_DIR}/bin/python" -c "import yaml; print(f'  pyyaml {yaml.__version__} OK')"
