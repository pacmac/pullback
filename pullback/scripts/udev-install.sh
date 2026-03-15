#!/bin/bash
# udev-install.sh — Install pullback udev rule and systemd service for USB auto-mount.
# Detects paths automatically from where it's run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MOUNT_SCRIPT="${SCRIPT_DIR}/udev-mount.sh"

RULES_SRC="${PROJECT_DIR}/udev/99-pullback-usb.rules"
RULES_DST="/etc/udev/rules.d/99-pullback-usb.rules"
SERVICE_DST="/etc/systemd/system/pullback-mount@.service"

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [[ ! -f "$RULES_SRC" ]]; then
    echo "Error: rule file not found at ${RULES_SRC}" >&2
    exit 1
fi

if [[ ! -f "$MOUNT_SCRIPT" ]]; then
    echo "Error: mount script not found at ${MOUNT_SCRIPT}" >&2
    exit 1
fi

# Make mount script executable
chmod +x "$MOUNT_SCRIPT"

# Copy udev rule
cp "$RULES_SRC" "$RULES_DST"
echo "Installed: ${RULES_DST}"

# Generate systemd service with detected path
cat > "$SERVICE_DST" <<EOF
[Unit]
Description=pullback USB backup drive mount (%i)
After=dev-%i.device
Requires=dev-%i.device

[Service]
Type=oneshot
ExecStart=${MOUNT_SCRIPT} %i
Environment=DEVNAME=/dev/%i
RemainAfterExit=no
EOF
echo "Installed: ${SERVICE_DST} (ExecStart=${MOUNT_SCRIPT})"

# Reload
udevadm control --reload-rules
udevadm trigger
systemctl daemon-reload

echo "Done. Pullback USB auto-mount is active."
