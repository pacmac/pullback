#!/bin/bash
# self-backup.sh — Image the SD card to the backup volume.
# Creates a compressed dd image of /dev/mmcblk0 for disaster recovery.
# Restore: gunzip -c pullback-sd-YYYY-MM-DD.img.gz | dd of=/dev/mmcblk0 bs=4M
#
# Usage: self-backup.sh [--keep=N]  (default: keep 2 images)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${PROJECT_DIR}/config.yaml"
TAG="self-backup"

KEEP=2
for arg in "$@"; do
    case "$arg" in
        --keep=*) KEEP="${arg#*=}" ;;
    esac
done

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

# ── Check SD card exists ──

SD_DEV="/dev/mmcblk0"
if [[ ! -b "$SD_DEV" ]]; then
    log "ERROR: SD card not found at ${SD_DEV}"
    exit 1
fi

# ── Setup ──

BACKUP_DIR="${MOUNT_POINT}/.self-backup"
mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y-%m-%d)
IMAGE="${BACKUP_DIR}/pullback-sd-${DATE}.img.gz"

SD_SIZE=$(blockdev --getsize64 "$SD_DEV")
SD_SIZE_GB=$((SD_SIZE / 1024 / 1024 / 1024))

log "Starting SD card backup"
log "  Source:  ${SD_DEV} (${SD_SIZE_GB} GB)"
log "  Dest:    ${IMAGE}"

# ── Check disk space ──

FREE_BYTES=$(df --output=avail -B1 "$MOUNT_POINT" | tail -1)
# Compressed image is typically 30-50% of SD size
NEEDED=$((SD_SIZE / 3))
if [[ $FREE_BYTES -lt $NEEDED ]]; then
    FREE_GB=$((FREE_BYTES / 1024 / 1024 / 1024))
    NEED_GB=$((NEEDED / 1024 / 1024 / 1024))
    log "ERROR: not enough space. Need ~${NEED_GB} GB, have ${FREE_GB} GB free"
    exit 1
fi

# ── Create image ──

START=$(date +%s)
if command -v pigz &>/dev/null; then
    dd if="$SD_DEV" bs=4M status=none | pigz -1 > "$IMAGE"
else
    dd if="$SD_DEV" bs=4M status=none | gzip -1 > "$IMAGE"
fi
ELAPSED=$(( $(date +%s) - START ))

IMAGE_SIZE=$(stat -c%s "$IMAGE")
IMAGE_SIZE_MB=$((IMAGE_SIZE / 1024 / 1024))

log "Backup complete: ${IMAGE_SIZE_MB} MB in ${ELAPSED}s"

# ── Prune old images ──

COUNT=$(ls -1 "${BACKUP_DIR}"/pullback-sd-*.img.gz 2>/dev/null | wc -l)
if [[ $COUNT -gt $KEEP ]]; then
    REMOVE=$((COUNT - KEEP))
    ls -1t "${BACKUP_DIR}"/pullback-sd-*.img.gz | tail -${REMOVE} | while read -r old; do
        log "Pruning old image: $(basename "$old")"
        rm -f "$old"
    done
fi

# ── Summary ──

log "Images in ${BACKUP_DIR}:"
ls -lh "${BACKUP_DIR}"/pullback-sd-*.img.gz 2>/dev/null | while read -r line; do
    log "  $line"
done
