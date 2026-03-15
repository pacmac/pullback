#!/bin/bash

SUBNET="192.168.0.0/24"
EXPORTS_FILE="/etc/exports"

if [ -z "$1" ]; then
    echo "Usage: $0 /path/to/folder"
    exit 1
fi

SHARE_PATH="$1"

# Install nfs if not present
if ! command -v exportfs &>/dev/null; then
    echo "Installing NFS..."
    apt update && apt install -y nfs-common nfs-kernel-server
fi

# Create folder if it doesn't exist
if [ ! -d "$SHARE_PATH" ]; then
    echo "Creating directory: $SHARE_PATH"
    mkdir -p "$SHARE_PATH"
    chown nobody:nogroup "$SHARE_PATH"
    chmod 755 "$SHARE_PATH"
fi

# Check if already in exports
if grep -q "^$SHARE_PATH " "$EXPORTS_FILE" 2>/dev/null; then
    echo "Share already exists in $EXPORTS_FILE: $SHARE_PATH"
    exit 0
fi

# Append to exports
echo "$SHARE_PATH    $SUBNET(rw,sync,no_subtree_check,no_root_squash)" >> "$EXPORTS_FILE"
echo "Added share: $SHARE_PATH"

# Apply
systemctl enable --now nfs-kernel-server
exportfs -ra
echo "Exports reloaded."
exportfs -v | grep "$SHARE_PATH"
