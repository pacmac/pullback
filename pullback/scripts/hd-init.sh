#!/bin/bash
# hd-init.sh — Prepare a USB drive as a pullback backup volume.
# Formats (with --format), mounts, and creates flag file.
# Idempotent for mount, destructive format requires --format flag.
# Run as root on the Pi.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${PROJECT_DIR}/config.yaml"
LOG_TAG="hd-init"

# ── Helpers ──

log() { echo "[${LOG_TAG}] $*"; }

usage() {
    echo "Usage: $0 <device> [--format]"
    echo ""
    echo "  <device>    Block device partition (e.g. /dev/sda1)"
    echo "  --format    Format the device (DESTRUCTIVE — required on first run)"
    echo ""
    echo "Examples:"
    echo "  $0 /dev/sda1 --format    # First time: format, mount, create flag"
    echo "  $0 /dev/sda1             # Re-run: mount and verify flag only"
    exit 1
}

# ── Read config ──

if [[ ! -f "$CONFIG" ]]; then
    echo "Error: config.yaml not found at ${CONFIG}" >&2
    exit 1
fi

MOUNT_POINT=$(grep '^mount_point:' "$CONFIG" | awk '{print $2}')
FLAG_FILE=$(grep '^\s*flag_file:' "$CONFIG" | awk '{print $2}')
FILESYSTEM=$(grep '^\s*filesystem:' "$CONFIG" | awk '{print $2}')
RESERVED_PCT=$(grep '^\s*reserved_pct:' "$CONFIG" | awk '{print $2}')

if [[ -z "$MOUNT_POINT" ]]; then echo "Error: mount_point not in config" >&2; exit 1; fi
if [[ -z "$FLAG_FILE" ]]; then echo "Error: usb.flag_file not in config" >&2; exit 1; fi
if [[ -z "$FILESYSTEM" ]]; then FILESYSTEM="ext4"; fi
if [[ -z "$RESERVED_PCT" ]]; then RESERVED_PCT="1"; fi

# ── Args ──

DEVICE="${1:-}"
DO_FORMAT=false

if [[ -z "$DEVICE" ]]; then
    usage
fi

shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --format) DO_FORMAT=true ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
    shift
done

# ── Preflight ──

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [[ ! -b "$DEVICE" ]]; then
    echo "Error: ${DEVICE} is not a block device" >&2
    exit 1
fi

# ── Check if already mounted ──

if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    MOUNTED_DEV=$(findmnt -n -o SOURCE "$MOUNT_POINT")
    if [[ "$DO_FORMAT" == true ]]; then
        echo "Error: ${MOUNT_POINT} is already mounted (${MOUNTED_DEV}). Unmount first to format." >&2
        exit 1
    fi
    log "${MOUNT_POINT} already mounted from ${MOUNTED_DEV}"

    # Verify flag file
    if [[ ! -f "${MOUNT_POINT}/${FLAG_FILE}" ]]; then
        echo "Error: ${FLAG_FILE} not found — this is not a pullback volume" >&2
        exit 1
    fi
    log "Flag file verified: ${MOUNT_POINT}/${FLAG_FILE}"
else
    # ── Format (only with --format) ──

    if [[ "$DO_FORMAT" == true ]]; then
        log "--- Formatting ${DEVICE} ---"
        echo ""
        echo "WARNING: This will DESTROY all data on ${DEVICE}"
        read -rp "[hd-init] Type YES to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            log "Aborted."
            exit 1
        fi
        mkfs."${FILESYSTEM}" -L pullback -m "${RESERVED_PCT}" "$DEVICE"
        log "Formatted ${DEVICE} as ${FILESYSTEM} (label: pullback, reserved: ${RESERVED_PCT}%)"
    fi

    # ── Mount ──

    log "--- Setting up mount ---"
    mkdir -p "$MOUNT_POINT"

    UUID=$(blkid -s UUID -o value "$DEVICE")
    if [[ -z "$UUID" ]]; then
        echo "Error: cannot determine UUID for ${DEVICE}. Was it formatted?" >&2
        exit 1
    fi
    log "Device UUID: ${UUID}"

    # Add fstab entry if not present
    if ! grep -q "UUID=${UUID}" /etc/fstab 2>/dev/null; then
        echo "UUID=${UUID} ${MOUNT_POINT} ${FILESYSTEM} noatime,commit=60,nofail 0 2" >> /etc/fstab
        log "Added fstab entry"
    else
        log "fstab entry already exists"
    fi

    mount "$MOUNT_POINT"
    log "Mounted ${DEVICE} at ${MOUNT_POINT}"

    # ── Create flag file on format ──

    if [[ "$DO_FORMAT" == true ]]; then
        echo "$(date -Iseconds)" > "${MOUNT_POINT}/${FLAG_FILE}"
        log "Created flag file: ${MOUNT_POINT}/${FLAG_FILE}"
    else
        # Verify flag file exists
        if [[ ! -f "${MOUNT_POINT}/${FLAG_FILE}" ]]; then
            echo "Error: ${FLAG_FILE} not found — this is not a pullback volume" >&2
            umount "$MOUNT_POINT" 2>/dev/null || true
            exit 1
        fi
        log "Flag file verified: ${MOUNT_POINT}/${FLAG_FILE}"
    fi
fi

# ── Summary ──

echo ""
log "============================================"
log "Backup volume ready."
log "============================================"
echo ""
log "Device:     ${DEVICE}"
log "Mount:      ${MOUNT_POINT}"
log "Filesystem: ${FILESYSTEM}"
log "Flag file:  ${FLAG_FILE}"
log "Size:       $(df -h "$MOUNT_POINT" | awk 'NR==2{print $2}')"
log "Available:  $(df -h "$MOUNT_POINT" | awk 'NR==2{print $4}')"
