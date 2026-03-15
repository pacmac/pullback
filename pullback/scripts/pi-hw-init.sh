#!/bin/bash
# hw-init.sh — Optimise Raspberry Pi 4 as a headless backup appliance
# Idempotent: safe to re-run. Requires reboot after first run.
# Run as root on the Pi itself.

set -euo pipefail

BOOT_CONFIG="/boot/firmware/config.txt"
SYSCTL_CONF="/etc/sysctl.d/99-backup.conf"
FSTAB="/etc/fstab"
LOG_TAG="hw-init"

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

log() { echo "[${LOG_TAG}] $*"; }

# Append a line to a file if it doesn't already exist
ensure_line() {
    local file="$1"
    local line="$2"
    if ! grep -qxF "$line" "$file" 2>/dev/null; then
        echo "$line" >> "$file"
        log "Added to ${file}: ${line}"
    else
        log "Already in ${file}: ${line}"
    fi
}

# Disable a systemd service if it exists
disable_service() {
    local svc="$1"
    if systemctl list-unit-files "${svc}" &>/dev/null; then
        systemctl disable --now "${svc}" 2>/dev/null && \
            log "Disabled: ${svc}" || \
            log "Already disabled or not running: ${svc}"
    else
        log "Service not found, skipping: ${svc}"
    fi
}

# ──────────────────────────────────────────────
# Preflight
# ──────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [[ ! -f "$BOOT_CONFIG" ]]; then
    # Older Pi OS uses /boot/config.txt
    if [[ -f /boot/config.txt ]]; then
        BOOT_CONFIG="/boot/config.txt"
        log "Using legacy boot config: ${BOOT_CONFIG}"
    else
        echo "Error: cannot find boot config.txt" >&2
        exit 1
    fi
fi

log "Starting hardware optimisation"

# ──────────────────────────────────────────────
# 1. Disable WiFi
# ──────────────────────────────────────────────

log "--- Disabling WiFi ---"
ensure_line "$BOOT_CONFIG" "dtoverlay=disable-wifi"
disable_service "wpa_supplicant.service"

# ──────────────────────────────────────────────
# 2. Disable Bluetooth
# ──────────────────────────────────────────────

log "--- Disabling Bluetooth ---"
ensure_line "$BOOT_CONFIG" "dtoverlay=disable-bt"
disable_service "hciuart.service"
disable_service "bluetooth.service"

# ──────────────────────────────────────────────
# 3. Disable Audio
# ──────────────────────────────────────────────

log "--- Disabling Audio ---"
# Replace dtparam=audio=on if present, otherwise add audio=off
if grep -q "^dtparam=audio=on" "$BOOT_CONFIG" 2>/dev/null; then
    sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$BOOT_CONFIG"
    log "Changed dtparam=audio=on to off in ${BOOT_CONFIG}"
else
    ensure_line "$BOOT_CONFIG" "dtparam=audio=off"
fi

# ──────────────────────────────────────────────
# 4. Minimise GPU memory (headless)
# ──────────────────────────────────────────────

log "--- Minimising GPU memory ---"
# Pi4 requires gpu_mem>=32 to boot (firmware needs it to initialise)
if grep -q "^gpu_mem=" "$BOOT_CONFIG" 2>/dev/null; then
    sed -i 's/^gpu_mem=.*/gpu_mem=32/' "$BOOT_CONFIG"
    log "Updated gpu_mem=32 in ${BOOT_CONFIG}"
else
    ensure_line "$BOOT_CONFIG" "gpu_mem=32"
fi

# ──────────────────────────────────────────────
# 5. Disable HDMI output
# ──────────────────────────────────────────────

log "--- Disabling HDMI ---"
# Disable HDMI at boot via config.txt
ensure_line "$BOOT_CONFIG" "hdmi_blanking=2"
# Also disable now if tvservice is available
if command -v tvservice &>/dev/null; then
    tvservice -o 2>/dev/null && log "HDMI disabled (tvservice)" || true
fi

# ──────────────────────────────────────────────
# 6. Enable hardware watchdog
# ──────────────────────────────────────────────

log "--- Enabling hardware watchdog ---"
ensure_line "$BOOT_CONFIG" "dtparam=watchdog=on"

# Configure systemd watchdog
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
ShutdownWatchdogSec=2min
EOF
log "Configured systemd watchdog (15s runtime, 2min shutdown)"

# ──────────────────────────────────────────────
# 7. Disable unneeded services
# ──────────────────────────────────────────────

log "--- Disabling unneeded services ---"
disable_service "triggerhappy.service"
disable_service "ModemManager.service"
disable_service "alsa-restore.service"
disable_service "alsa-state.service"

# ──────────────────────────────────────────────
# 8. Sysctl tuning
# ──────────────────────────────────────────────

log "--- Writing sysctl tuning ---"
cat > "$SYSCTL_CONF" <<'EOF'
# Backup appliance tuning

# Reduce swap usage (preserve SD card, 4GB RAM is sufficient)
vm.swappiness = 10

# Reserve memory for system stability
vm.min_free_kbytes = 4096

# Optimise for large sequential writes (rsync backup workload)
# Allow more dirty pages before flushing — reduces write amplification
vm.dirty_ratio = 40
vm.dirty_background_ratio = 10

# Extend dirty page expiry — batch larger writes to HDD
vm.dirty_expire_centisecs = 6000
vm.dirty_writeback_centisecs = 1500
EOF

sysctl --load="$SYSCTL_CONF" >/dev/null 2>&1 && \
    log "Applied sysctl settings" || \
    log "Sysctl written (will apply on reboot)"

# ──────────────────────────────────────────────
# 9. Mount /tmp as tmpfs (reduce SD writes)
# ──────────────────────────────────────────────

log "--- Configuring tmpfs for /tmp ---"
if ! grep -q "^tmpfs.*/tmp" "$FSTAB" 2>/dev/null; then
    echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,size=128M 0 0" >> "$FSTAB"
    log "Added /tmp tmpfs to fstab"
else
    log "/tmp tmpfs already in fstab"
fi

# ──────────────────────────────────────────────
# 10. I/O scheduler for USB HDD
# ──────────────────────────────────────────────

log "--- Configuring I/O scheduler ---"
UDEV_RULES="/etc/udev/rules.d/60-backup-iosched.rules"
cat > "$UDEV_RULES" <<'EOF'
# Set mq-deadline scheduler for USB disks (better for sequential backup writes)
ACTION=="add|change", KERNEL=="sd[a-z]", SUBSYSTEM=="block", ATTR{queue/scheduler}="mq-deadline"
EOF
log "Created udev rule for mq-deadline scheduler: ${UDEV_RULES}"

# Apply immediately to any existing sd* devices
for dev in /sys/block/sd*/queue/scheduler; do
    if [[ -f "$dev" ]]; then
        echo "mq-deadline" > "$dev" 2>/dev/null && \
            log "Set mq-deadline on $(dirname $(dirname $dev) | xargs basename)" || true
    fi
done

# ──────────────────────────────────────────────
# 11. Disable swap partition (optional, rely on RAM)
# ──────────────────────────────────────────────

log "--- Reducing swap usage ---"
if systemctl is-active dphys-swapfile &>/dev/null; then
    dphys-swapfile swapoff 2>/dev/null || true
    systemctl disable dphys-swapfile.service 2>/dev/null || true
    log "Disabled dphys-swapfile (SD card swap)"
else
    log "dphys-swapfile not active"
fi

# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────

echo ""
log "============================================"
log "Hardware optimisation complete."
log "============================================"
echo ""
log "Changes applied:"
log "  - WiFi, Bluetooth, Audio: disabled"
log "  - GPU memory: 32MB"
log "  - HDMI: disabled"
log "  - Hardware watchdog: enabled"
log "  - Unneeded services: disabled"
log "  - Sysctl: tuned for backup workload"
log "  - /tmp: tmpfs"
log "  - I/O scheduler: mq-deadline for USB disks"
log "  - SD swap: disabled"
echo ""
log "A reboot is required for all changes to take effect."
read -rp "[hw-init] Reboot now? [y/N] " answer
if [[ "${answer,,}" == "y" ]]; then
    log "Rebooting..."
    reboot
else
    log "Skipped reboot. Remember to reboot later."
fi
