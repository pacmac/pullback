#!/bin/bash
# udev-mount.sh — Auto-mount known pullback USB backup drives.
# Called by udev rule. Runs in restricted environment — no interactive prompts.
# Logs to syslog via logger.
#
# Known drive (UUID in fstab + flag file present): mount it.
# Unknown drive: log and REFUSE. Use hd-init.sh --format to prepare new drives.
#
# NEVER auto-formats. Formatting destroys data and must be explicitly requested.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${PROJECT_DIR}/config.yaml"
TAG="pullback-udev"

log() { logger -t "$TAG" "$*"; }

die() { log "ERROR: $*"; exit 1; }

# ── Read config values ──

[[ -f "$CONFIG" ]] || die "config.yaml not found at ${CONFIG}"

MOUNT_POINT=$(grep '^mount_point:' "$CONFIG" | awk '{print $2}')
FLAG_FILE=$(grep '^\s*flag_file:' "$CONFIG" | awk '{print $2}')

[[ -n "$MOUNT_POINT" && -n "$FLAG_FILE" ]] || die "could not read mount_point or flag_file from config"

# ── Get device from udev environment ──

DEVICE="${DEVNAME:-}"
[[ -n "$DEVICE" ]] || die "no DEVNAME in environment"
[[ -b "$DEVICE" ]] || die "${DEVICE} is not a block device"

# ── Already mounted? ──

if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    log "${MOUNT_POINT} already mounted, skipping"
    exit 0
fi

# ── Get UUID ──

UUID=$(blkid -s UUID -o value "$DEVICE" 2>/dev/null || true)

# ── Known drive: UUID in fstab ──

if [[ -n "$UUID" ]] && grep -q "UUID=${UUID}" /etc/fstab 2>/dev/null; then
    log "Known volume ${DEVICE} (UUID=${UUID}), mounting"
    mkdir -p "$MOUNT_POINT"
    systemctl daemon-reload
    sleep 2
    mount "$DEVICE" "$MOUNT_POINT" || die "failed to mount known volume ${DEVICE}"

    if [[ ! -f "${MOUNT_POINT}/${FLAG_FILE}" ]]; then
        log "WARNING: ${FLAG_FILE} missing on known volume, unmounting"
        umount "$MOUNT_POINT" 2>/dev/null || true
        exit 1
    fi

    log "Mounted pullback volume at ${MOUNT_POINT}"
    exit 0
fi

# ── Unknown drive: refuse to mount ──

log "Unknown USB drive detected: ${DEVICE} (UUID=${UUID:-none}) — NOT mounting"
log "To prepare this drive as a pullback volume, run:"
log "  bash ${SCRIPT_DIR}/hd-init.sh ${DEVICE} --format"
exit 0
