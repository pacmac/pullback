#!/bin/bash
# pi-capture-defaults.sh — Capture current system tuning defaults before any changes.
# Writes to docs/TUNEDEFAULT.local.md so you have a record of THIS host's values.
# Run ONCE on a fresh install, BEFORE applying any tuning.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT="${PROJECT_DIR}/docs/TUNEDEFAULT.local.md"

log() { echo "[capture-defaults] $*"; }

if [[ -f "$OUTPUT" ]]; then
    echo "Error: ${OUTPUT} already exists — defaults already captured." >&2
    echo "  Delete it manually if you want to re-capture." >&2
    exit 1
fi

log "Capturing system defaults to ${OUTPUT}"

cat > "$OUTPUT" <<EOF
# Tuning Defaults — Captured from $(hostname) on $(date -Iseconds)

These are the ACTUAL values from this host before any pullback tuning.
Use these to revert any change that doesn't show measurable improvement.

## VM / Dirty Pages

| Parameter | Value |
|-----------|-------|
| vm.dirty_ratio | $(sysctl -n vm.dirty_ratio) |
| vm.dirty_background_ratio | $(sysctl -n vm.dirty_background_ratio) |
| vm.dirty_expire_centisecs | $(sysctl -n vm.dirty_expire_centisecs) |
| vm.dirty_writeback_centisecs | $(sysctl -n vm.dirty_writeback_centisecs) |
| vm.dirty_bytes | $(sysctl -n vm.dirty_bytes) |
| vm.dirty_background_bytes | $(sysctl -n vm.dirty_background_bytes) |

## Network Buffers

| Parameter | Value |
|-----------|-------|
| net.core.rmem_max | $(sysctl -n net.core.rmem_max) |
| net.core.wmem_max | $(sysctl -n net.core.wmem_max) |
| net.ipv4.tcp_rmem | $(sysctl -n net.ipv4.tcp_rmem) |
| net.ipv4.tcp_wmem | $(sysctl -n net.ipv4.tcp_wmem) |
| net.core.netdev_max_backlog | $(sysctl -n net.core.netdev_max_backlog) |
| net.ipv4.tcp_slow_start_after_idle | $(sysctl -n net.ipv4.tcp_slow_start_after_idle) |

## TCP

| Parameter | Value |
|-----------|-------|
| net.ipv4.tcp_congestion_control | $(sysctl -n net.ipv4.tcp_congestion_control) |
| net.core.default_qdisc | $(sysctl -n net.core.default_qdisc) |

## CPU

| Parameter | Value |
|-----------|-------|
| CPU governor | $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "n/a") |

## Network Hardware

| Parameter | Value |
|-----------|-------|
| RPS (rps_cpus) | $(cat /sys/class/net/eth0/queues/rx-0/rps_cpus 2>/dev/null || echo "n/a") |
| RPS (rps_sock_flow_entries) | $(cat /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || echo "n/a") |
| EEE | $(ethtool --show-eee eth0 2>/dev/null | grep -i "eee status" || echo "n/a") |

## Disk I/O

| Parameter | Value |
|-----------|-------|
| I/O scheduler | $(BDEV=$(findmnt -n -o SOURCE /backup 2>/dev/null | xargs basename 2>/dev/null); cat /sys/block/${BDEV:-sda}/queue/scheduler 2>/dev/null || echo "not mounted") |
| Read-ahead | $(BDEV=$(findmnt -n -o SOURCE /backup 2>/dev/null); blockdev --getra ${BDEV:-/dev/sda} 2>/dev/null || echo "not mounted") sectors |

## Existing sysctl overrides

$(ls /etc/sysctl.d/ 2>/dev/null || echo "none")

EOF

log "Defaults captured to ${OUTPUT}"
log "Review the file, then proceed with tuning one parameter at a time."
