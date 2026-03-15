#!/bin/bash
# setup.sh — Install pullback on any Linux host.
# Creates venv, installs SSH keys, sets up udev auto-mount, and web dashboard.
# Run as root on the target backup host.
#
# Usage: setup.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { echo "[setup] $*"; }

run() {
    if [[ "$DRY_RUN" == true ]]; then
        log "DRY-RUN: $*"
    else
        "$@"
    fi
}

# ── Preflight ──

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

log "Installing pullback from ${PROJECT_DIR}"
[[ "$DRY_RUN" == true ]] && log "*** DRY-RUN MODE — no changes will be made ***"

# ── Prerequisites ──

log "--- Checking prerequisites ---"
MISSING=()

command -v python3 &>/dev/null || MISSING+=("python3")
python3 -c "import venv" 2>/dev/null || MISSING+=("python3-venv")
command -v rsync &>/dev/null || MISSING+=("rsync")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Error: missing packages: ${MISSING[*]}" >&2
    echo "Install with: apt install ${MISSING[*]}" >&2
    exit 1
fi

log "Prerequisites OK: python3, python3-venv, rsync"

# ── Step 1: Python venv ──

log "--- Step 1: Python venv ---"
run bash "${SCRIPT_DIR}/pyenv-setup.sh"

# ── Step 2: SSH keys ──

log "--- Step 2: SSH keys ---"
KEY_DIR="${PROJECT_DIR}/keys"
KEY_FILE="${KEY_DIR}/id_ed25519"
mkdir -p "$KEY_DIR"
if [[ -f "$KEY_FILE" ]]; then
    log "SSH key already exists: ${KEY_FILE}"
else
    run ssh-keygen -t ed25519 -N "" -f "$KEY_FILE" -C "pullback@$(hostname)"
    log "Generated SSH key: ${KEY_FILE}"
    log "  >>> Copy pubkey to your remote host(s): <<<"
    log "  ssh-copy-id -i ${KEY_FILE} root@YOUR_HOST"
fi

# ── Step 3: Config check ──

log "--- Step 3: Local config ---"
if [[ -f "${PROJECT_DIR}/config.local.yaml" ]]; then
    log "config.local.yaml already exists"
else
    run cp "${PROJECT_DIR}/config.local.yaml.example" "${PROJECT_DIR}/config.local.yaml"
    log "Created config.local.yaml from example"
    log "  >>> Edit ${PROJECT_DIR}/config.local.yaml with your SMTP credentials <<<"
fi

# ── Step 4: udev auto-mount ──

log "--- Step 4: udev auto-mount ---"
run bash "${SCRIPT_DIR}/udev-install.sh"

# ── Step 5: Web dashboard service ──

log "--- Step 5: Web dashboard ---"
run bash "${SCRIPT_DIR}/web-install.sh"

# ── Done ──

echo ""
log "============================================"
log "pullback setup complete."
log "============================================"
echo ""
log "Next steps:"
log "  1. Edit ${PROJECT_DIR}/config.local.yaml with SMTP credentials"
log "  2. Edit config.yaml with your sources"
log "  3. Copy SSH pubkey to remote host(s):"
log "     ssh-copy-id -i ${PROJECT_DIR}/keys/id_ed25519 root@YOUR_HOST"
log "  4. Connect a USB drive (auto-formatted on first plug-in)"
log "  5. Test: ${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/cli.py sync"
log "  6. Dashboard: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080/"
