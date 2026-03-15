#!/bin/bash
# pi-setup.sh — Install pullback on Raspberry Pi with Pi-specific tuning.
# Runs general setup first, then applies Pi 4 performance optimisations.
# Run as root on the Pi.
#
# Usage: pi-setup.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { echo "[pi-setup] $*"; }

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

log "Installing pullback with Pi-specific tuning"
[[ "$DRY_RUN" == true ]] && log "*** DRY-RUN MODE — no changes will be made ***"

# ── Pi prerequisites ──

MISSING=()
command -v ethtool &>/dev/null || MISSING+=("ethtool")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Error: missing Pi packages: ${MISSING[*]}" >&2
    echo "Install with: apt install ${MISSING[*]}" >&2
    exit 1
fi

# ── General setup ──

log "--- Running general setup ---"
if [[ "$DRY_RUN" == true ]]; then
    run bash "${SCRIPT_DIR}/setup.sh" --dry-run
else
    bash "${SCRIPT_DIR}/setup.sh"
fi

# ── Pi tuning ──

log "--- Pi performance tuning ---"
run bash "${SCRIPT_DIR}/pi-tune-install.sh"

# ── Done ──

echo ""
log "============================================"
log "Pi setup complete."
log "============================================"
echo ""
log "Pi-specific tuning applied:"
log "  - Dirty page limits (sysctl)"
log "  - RPS network softirq distribution"
log "  - EEE disabled"
log "  - CPU governor: performance"
log "  - UAS check (reboot required if enabled)"
echo ""

# Check if a USB drive is connected but not initialised
MOUNT_POINT=$(grep '^mount_point:' "${PROJECT_DIR}/config.yaml" 2>/dev/null | awk '{print $2}')
: "${MOUNT_POINT:=/backup}"

if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    log "No backup volume mounted at ${MOUNT_POINT}."
    log "To initialise a USB drive:"
    log "  bash ${SCRIPT_DIR}/hd-init.sh /dev/sda1 --format"
fi
