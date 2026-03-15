#!/bin/bash
# pi-setup.sh — Install pullback on Raspberry Pi.
# Runs general setup, then captures system defaults for tuning baseline.
# Does NOT apply tuning — that must be done manually, one param at a time.
# See docs/TUNING.md for the tuning procedure.
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

log "Installing pullback on Pi"
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
    bash "${SCRIPT_DIR}/setup.sh" --dry-run
else
    bash "${SCRIPT_DIR}/setup.sh"
fi

# ── Capture defaults ──

log "--- Capturing system defaults ---"
run bash "${SCRIPT_DIR}/pi-capture-defaults.sh"

# ── Done ──

echo ""
log "============================================"
log "Pi base setup complete."
log "============================================"
echo ""
log "Tuning has NOT been applied."
log "System defaults have been captured to docs/TUNEDEFAULT.local.md"
echo ""
log "To apply tuning, follow the procedure in docs/TUNING.md:"
log "  1. Review docs/TUNEDEFAULT.local.md (your baseline)"
log "  2. Start a sync to get baseline throughput numbers"
log "  3. Apply ONE parameter at a time, measure, keep or revert"
log "  4. When satisfied, run pi-tune-install.sh to make changes permanent"
echo ""

# Check if a USB drive is connected
MOUNT_POINT=$(grep '^mount_point:' "${PROJECT_DIR}/config.yaml" 2>/dev/null | awk '{print $2}')
: "${MOUNT_POINT:=/backup}"

if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    log "No backup volume mounted at ${MOUNT_POINT}."
    log "To initialise a USB drive:"
    log "  bash ${SCRIPT_DIR}/hd-init.sh /dev/sda1 --format"
fi
