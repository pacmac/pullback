#!/bin/bash
# pi-tune-status.sh — Show all current tuning settings on the Pi.
# Usage:
#   pi-tune-status.sh            # display to console
#   pi-tune-status.sh --save     # save to yaml (config.yaml tuning format)
#   pi-tune-status.sh --save=FILE  # save to specific file

SAVE=""
for arg in "$@"; do
    case "$arg" in
        --save) SAVE="/dev/stdout" ;;
        --save=*) SAVE="${arg#*=}" ;;
    esac
done

DISK_DEV=$(df /backup 2>/dev/null | tail -1 | awk '{print $1}' | xargs basename 2>/dev/null | sed 's/[0-9]*$//' || echo "sda")

# Read values
DIRTY_RATIO=$(sysctl -n vm.dirty_ratio 2>/dev/null)
DIRTY_BG_RATIO=$(sysctl -n vm.dirty_background_ratio 2>/dev/null)
DIRTY_BYTES=$(sysctl -n vm.dirty_bytes 2>/dev/null)
DIRTY_BG_BYTES=$(sysctl -n vm.dirty_background_bytes 2>/dev/null)
DIRTY_EXPIRE=$(sysctl -n vm.dirty_expire_centisecs 2>/dev/null)
DIRTY_WRITEBACK=$(sysctl -n vm.dirty_writeback_centisecs 2>/dev/null)
BDI_STRICT=$(cat /sys/block/${DISK_DEV}/bdi/strict_limit 2>/dev/null || echo 0)
BDI_MAX=$(cat /sys/block/${DISK_DEV}/bdi/max_bytes 2>/dev/null || echo 0)
GOVERNOR=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo 'n/a')
RPS=$(cat /sys/class/net/eth0/queues/rx-0/rps_cpus 2>/dev/null || echo 0)
RPS_FLOW=$(cat /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || echo 0)
EEE_STATUS=$(ethtool --show-eee eth0 2>/dev/null | grep -i 'eee status' | awk -F: '{print $2}' | xargs)
SCHEDULER=$(cat /sys/block/${DISK_DEV}/queue/scheduler 2>/dev/null || echo 'n/a')
READAHEAD=$(blockdev --getra /dev/${DISK_DEV} 2>/dev/null || echo 'n/a')
NR_REQ=$(cat /sys/block/${DISK_DEV}/queue/nr_requests 2>/dev/null || echo 'n/a')
MAX_SECTORS=$(cat /sys/block/${DISK_DEV}/queue/max_sectors_kb 2>/dev/null || echo 'n/a')
USB_DRIVER=$(lsusb -t 2>/dev/null | grep -i 'mass storage' | grep -oP 'Driver=\S+' || echo 'n/a')

# Derive booleans
RPS_ENABLED=false
[[ "$RPS" != "0" && "$RPS" != "00000000" ]] && RPS_ENABLED=true
EEE_OFF=false
[[ "$EEE_STATUS" == "disabled" ]] && EEE_OFF=true
BDI_BYTES=0
[[ "$BDI_STRICT" == "1" ]] && BDI_BYTES=$BDI_MAX

# ── Save mode ──

if [[ -n "$SAVE" ]]; then
    OUTPUT="tuning:
  dirty_ratio: ${DIRTY_RATIO}
  dirty_background_ratio: ${DIRTY_BG_RATIO}
  dirty_expire_centisecs: ${DIRTY_EXPIRE}
  dirty_writeback_centisecs: ${DIRTY_WRITEBACK}
  bdi_max_bytes: ${BDI_BYTES}
  rps_enabled: ${RPS_ENABLED}
  eee_off: ${EEE_OFF}
  cpu_governor: ${GOVERNOR}"

    if [[ "$SAVE" == "/dev/stdout" ]]; then
        echo "$OUTPUT"
    else
        echo "$OUTPUT" > "$SAVE"
        echo "Saved to ${SAVE}"
    fi
    exit 0
fi

# ── Display mode ──

echo "=== Dirty Pages ==="
echo "  dirty_ratio            = ${DIRTY_RATIO}"
echo "  dirty_background_ratio = ${DIRTY_BG_RATIO}"
echo "  dirty_bytes            = ${DIRTY_BYTES}"
echo "  dirty_background_bytes = ${DIRTY_BG_BYTES}"
echo "  dirty_expire_centisecs = ${DIRTY_EXPIRE}"
echo "  dirty_writeback_centisecs = ${DIRTY_WRITEBACK}"
echo ""

echo "=== BDI ($DISK_DEV) ==="
echo "  strict_limit = ${BDI_STRICT}"
echo "  max_bytes    = ${BDI_MAX}"
echo ""

echo "=== CPU ==="
echo "  governor = ${GOVERNOR}"
echo ""

echo "=== Network ==="
echo "  RPS          = ${RPS}"
echo "  RPS flow     = ${RPS_FLOW}"
echo "  EEE          = ${EEE_STATUS:-n/a}"
echo ""

echo "=== Disk ($DISK_DEV) ==="
echo "  scheduler    = ${SCHEDULER}"
echo "  read-ahead   = ${READAHEAD} sectors"
echo "  nr_requests  = ${NR_REQ}"
echo "  max_sectors  = ${MAX_SECTORS} KB"
echo "  USB protocol = ${USB_DRIVER}"
echo ""

echo "=== Mount ==="
mount | grep /backup || echo "  /backup not mounted"
echo ""

echo "=== Live ==="
grep -E 'Dirty:|Writeback:' /proc/meminfo | head -2
