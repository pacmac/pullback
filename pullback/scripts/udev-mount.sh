#!/bin/bash
# udev-mount.sh — Auto-mount pullback USB backup drives.
# Called by udev rule. Runs in restricted environment — no interactive prompts.
# Logs to syslog via logger.
#
# 1. Already mounted? Skip.
# 2. Try to mount the device, check for flag file:
#    - Flag found: keep mounted.
#    - No flag: unmount, refuse. Use hd-init.sh --format for new drives.
#
# Does NOT touch fstab. Does NOT auto-format. Mounts directly by device.

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

# ── Try to mount and check for flag file ──

log "USB drive detected: ${DEVICE} — checking for flag file"

mkdir -p "$MOUNT_POINT"
if ! mount -o noatime,commit=60 "$DEVICE" "$MOUNT_POINT" 2>/dev/null; then
    log "Cannot mount ${DEVICE} — not a valid filesystem, ignoring"
    exit 0
fi

if [[ -f "${MOUNT_POINT}/${FLAG_FILE}" ]]; then
    log "Mounted pullback volume ${DEVICE} at ${MOUNT_POINT}"
    exit 0
fi

# No flag file — not a pullback volume
umount "$MOUNT_POINT" 2>/dev/null || true
log "No flag file on ${DEVICE} — NOT a pullback volume, ignoring"
log "To prepare this drive, run: bash ${SCRIPT_DIR}/hd-init.sh ${DEVICE} --format"
exit 0
