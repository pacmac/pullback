#!/bin/bash
# self-backup.sh — Back up the Pi root filesystem to the backup volume.
# Uses rsync to copy actual files (not empty space). Fast and incremental.
#
# Restore: write fresh Pi OS to SD card, boot, then:
#   rsync -aHAX /backup/.self-backup/rootfs/ /
#   rsync -aHAX /backup/.self-backup/boot/ /boot/firmware/
#   reboot
#
# Usage: self-backup.sh [--keep=N]  (default: keep latest only, no versioning)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${PROJECT_DIR}/config.yaml"
TAG="self-backup"

log() { echo "[${TAG}] $*"; logger -t "$TAG" "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

# ── Check backup volume is mounted ──

MOUNT_POINT=$(grep '^mount_point:' "$CONFIG" 2>/dev/null | awk '{print $2}')
: "${MOUNT_POINT:=/backup}"

if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    log "ERROR: backup volume not mounted at ${MOUNT_POINT}"
    exit 1
fi

# ── Setup ──

BACKUP_DIR="${MOUNT_POINT}/.self-backup"
ROOTFS_DIR="${BACKUP_DIR}/rootfs"
BOOT_DIR="${BACKUP_DIR}/boot"
mkdir -p "$ROOTFS_DIR" "$BOOT_DIR"

log "Starting self-backup"

# ── Backup root filesystem ──

START=$(date +%s)

rsync -aHAX --delete \
    --exclude='/backup' \
    --exclude='/proc' \
    --exclude='/sys' \
    --exclude='/dev' \
    --exclude='/tmp' \
    --exclude='/run' \
    --exclude='/mnt' \
    --exclude='/media' \
    --exclude='/lost+found' \
    --exclude='/var/cache/apt' \
    --exclude='/var/tmp' \
    --exclude='/swap*' \
    / "$ROOTFS_DIR/"

log "Root filesystem backed up"

# ── Backup boot partition ──

rsync -aHAX --delete /boot/firmware/ "$BOOT_DIR/"

log "Boot partition backed up"

# ── Summary ──

ELAPSED=$(( $(date +%s) - START ))
ROOTFS_SIZE=$(du -sh "$ROOTFS_DIR" | awk '{print $1}')
BOOT_SIZE=$(du -sh "$BOOT_DIR" | awk '{print $1}')

log "Complete: rootfs=${ROOTFS_SIZE} boot=${BOOT_SIZE} in ${ELAPSED}s"

# ── Timestamp ──

date -Iseconds > "${BACKUP_DIR}/last-backup"
