#!/bin/bash
# udev-mount.sh — Auto-mount or auto-init USB backup drives.
# Called by udev rule. Runs in restricted environment — no interactive prompts.
# Logs to syslog via logger.
#
# Known drive (has flag file in fstab): mount it.
# New drive (not in fstab): format, create flag file, add fstab entry, mount.
# This Pi is a dedicated backup appliance — any USB drive is a backup drive.

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
FILESYSTEM=$(grep '^\s*filesystem:' "$CONFIG" | awk '{print $2}')
RESERVED_PCT=$(grep '^\s*reserved_pct:' "$CONFIG" | awk '{print $2}')

[[ -n "$MOUNT_POINT" && -n "$FLAG_FILE" ]] || die "could not read mount_point or flag_file from config"
: "${FILESYSTEM:=ext4}"
: "${RESERVED_PCT:=1}"

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

# ── New drive: format, flag, fstab, mount ──

log "New USB drive detected: ${DEVICE} — auto-initialising"

# Format
log "Formatting ${DEVICE} as ${FILESYSTEM} (reserved: ${RESERVED_PCT}%)"
if ! mkfs."${FILESYSTEM}" -L pullback -m "${RESERVED_PCT}" "$DEVICE" >>/tmp/pullback-mkfs.log 2>&1; then
    die "mkfs failed on ${DEVICE} — see /tmp/pullback-mkfs.log"
fi
log "Format complete"

# Re-read so kernel drops old partition table
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

# Get UUID after format
UUID=$(blkid -s UUID -o value "$DEVICE" 2>/dev/null || true)
[[ -n "$UUID" ]] || die "cannot determine UUID after formatting ${DEVICE}"

# Add fstab entry
if ! grep -q "UUID=${UUID}" /etc/fstab 2>/dev/null; then
    echo "UUID=${UUID} ${MOUNT_POINT} ${FILESYSTEM} noatime,commit=60,nofail 0 2" >> /etc/fstab
    log "Added fstab entry for UUID=${UUID}"
fi
systemctl daemon-reload

# Mount
mkdir -p "$MOUNT_POINT"
if ! mount "$MOUNT_POINT"; then
    # Retry with direct device in case fstab/UUID not yet resolved
    log "fstab mount failed, trying direct mount"
    if ! mount "$DEVICE" "$MOUNT_POINT"; then
        die "failed to mount ${DEVICE} at ${MOUNT_POINT}"
    fi
fi

# Create flag file
echo "$(date -Iseconds)" > "${MOUNT_POINT}/${FLAG_FILE}"
log "Created flag file: ${MOUNT_POINT}/${FLAG_FILE}"

log "New pullback volume initialised: ${DEVICE} (UUID=${UUID}) at ${MOUNT_POINT}"
