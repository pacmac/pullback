#!/bin/bash
# pi-tune-revert.sh — Revert all tuning to OS defaults.
# Reads defaults from docs/TUNEDEFAULT.local.md if available,
# otherwise uses known Debian/Pi OS defaults.
# Run as root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() { echo "[tune-revert] $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

log "Reverting all tuning to OS defaults"

# ── VM / Dirty Pages ──

sysctl -w \
    vm.dirty_bytes=0 \
    vm.dirty_background_bytes=0 \
    vm.dirty_ratio=20 \
    vm.dirty_background_ratio=10 \
    vm.dirty_expire_centisecs=3000 \
    vm.dirty_writeback_centisecs=500

log "Dirty pages: reverted"

# ── Network buffers ──

sysctl -w \
    net.core.rmem_max=212992 \
    net.core.wmem_max=212992 \
    net.core.netdev_max_backlog=1000 \
    net.ipv4.tcp_slow_start_after_idle=1

log "Network buffers: reverted"

# ── CPU governor ──

for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [[ -f "$gov" ]] && echo ondemand > "$gov"
done
log "CPU governor: ondemand"

# ── RPS ──

if [[ -f /sys/class/net/eth0/queues/rx-0/rps_cpus ]]; then
    echo 0 > /sys/class/net/eth0/queues/rx-0/rps_cpus
fi
if [[ -f /proc/sys/net/core/rps_sock_flow_entries ]]; then
    echo 0 > /proc/sys/net/core/rps_sock_flow_entries
fi
log "RPS: disabled"

# ── EEE ──

if command -v ethtool &>/dev/null; then
    ethtool --set-eee eth0 eee on 2>/dev/null && log "EEE: enabled" || log "EEE: not supported"
fi

# ── Read-ahead ──

if [[ -b /dev/sda ]]; then
    blockdev --setra 256 /dev/sda
    log "Read-ahead: 256 sectors"
fi

# ── Remove pullback sysctl override ──

if [[ -f /etc/sysctl.d/99-pullback.conf ]]; then
    rm -f /etc/sysctl.d/99-pullback.conf
    log "Removed /etc/sysctl.d/99-pullback.conf"
fi

# ── Disable pullback-tune service ──

if systemctl is-enabled pullback-tune &>/dev/null; then
    systemctl disable pullback-tune
    rm -f /etc/systemd/system/pullback-tune.service
    systemctl daemon-reload
    log "Disabled pullback-tune service"
fi

echo ""
log "============================================"
log "All tuning reverted to OS defaults."
log "============================================"
echo ""
log "Verify with: bash ${SCRIPT_DIR}/pi-bottleneck.sh --runsec=10"
