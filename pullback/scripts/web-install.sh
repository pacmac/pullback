#!/bin/bash
# web-install.sh — Install systemd service for pullback web dashboard.
# Detects paths automatically from where it's run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"
WEB_PY="${PROJECT_DIR}/web.py"

SERVICE_NAME="pullback-web"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Error: venv not found at ${VENV_PYTHON} — run pyenv-setup.sh first" >&2
    exit 1
fi

cat > "$SERVICE_DST" <<EOF
[Unit]
Description=pullback web dashboard
After=network.target

[Service]
Type=simple
ExecStart=${VENV_PYTHON} ${WEB_PY}
WorkingDirectory=${PROJECT_DIR}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Installed: ${SERVICE_DST}"
echo "  ExecStart=${VENV_PYTHON} ${WEB_PY}"
echo "  WorkingDirectory=${PROJECT_DIR}"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "Service ${SERVICE_NAME} started and enabled on boot."
echo "Status: systemctl status ${SERVICE_NAME}"
