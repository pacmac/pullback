#!/bin/bash
# ssh-setup.sh — Generate SSH key and add config entry for the Pi backup host.
# Run on the dev server.

set -euo pipefail

SSH_DIR="$HOME/.ssh"
SSH_CONFIG="${SSH_DIR}/config"
KEY_FILE="${SSH_DIR}/id_piback"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Generate key if not present
if [[ ! -f "$KEY_FILE" ]]; then
    ssh-keygen -t ed25519 -N "" -f "$KEY_FILE"
    echo "Generated key: ${KEY_FILE}"
else
    echo "Key already exists: ${KEY_FILE}"
fi

# Remove old host key if present
ssh-keygen -R 192.168.0.94 2>/dev/null || true

# Add config entry if not present
if grep -q "Host piback" "$SSH_CONFIG" 2>/dev/null; then
    echo "piback entry already exists in ${SSH_CONFIG}"
else
    cat >> "$SSH_CONFIG" <<EOF

Host piback
    HostName 192.168.0.94
    User root
    IdentityFile ${KEY_FILE}
    StrictHostKeyChecking accept-new
EOF
    chmod 600 "$SSH_CONFIG"
    echo "Added piback entry to ${SSH_CONFIG}"
fi

# Copy key to Pi
echo "Copying key to Pi..."
ssh-copy-id -i "$KEY_FILE" root@192.168.0.94

echo "Done. Test: ssh piback 'hostname'"
