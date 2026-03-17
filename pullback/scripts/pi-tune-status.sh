#!/bin/bash
# pi-tune-status.sh — Show all current tuning settings on the Pi.

DISK_DEV=$(df /backup 2>/dev/null | tail -1 | awk '{print $1}' | xargs basename 2>/dev/null | sed 's/[0-9]*$//' || echo "sda")

echo "=== Dirty Pages ==="
sysctl vm.dirty_ratio vm.dirty_background_ratio vm.dirty_bytes vm.dirty_background_bytes vm.dirty_expire_centisecs vm.dirty_writeback_centisecs
echo ""

echo "=== BDI ($DISK_DEV) ==="
echo "  strict_limit = $(cat /sys/block/${DISK_DEV}/bdi/strict_limit 2>/dev/null || echo 'n/a')"
echo "  max_bytes    = $(cat /sys/block/${DISK_DEV}/bdi/max_bytes 2>/dev/null || echo 'n/a')"
echo ""

echo "=== CPU ==="
echo "  governor = $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo 'n/a')"
echo ""

echo "=== Network ==="
echo "  RPS          = $(cat /sys/class/net/eth0/queues/rx-0/rps_cpus 2>/dev/null || echo 'n/a')"
echo "  RPS flow     = $(cat /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || echo 'n/a')"
EEE=$(ethtool --show-eee eth0 2>/dev/null | grep -i 'eee status' | awk -F: '{print $2}' | xargs)
echo "  EEE          = ${EEE:-n/a}"
echo ""

echo "=== Disk ($DISK_DEV) ==="
echo "  scheduler    = $(cat /sys/block/${DISK_DEV}/queue/scheduler 2>/dev/null || echo 'n/a')"
echo "  read-ahead   = $(blockdev --getra /dev/${DISK_DEV} 2>/dev/null || echo 'n/a') sectors"
echo "  nr_requests  = $(cat /sys/block/${DISK_DEV}/queue/nr_requests 2>/dev/null || echo 'n/a')"
echo "  max_sectors  = $(cat /sys/block/${DISK_DEV}/queue/max_sectors_kb 2>/dev/null || echo 'n/a') KB"
USB_DRIVER=$(lsusb -t 2>/dev/null | grep -i 'mass storage' | grep -oP 'Driver=\S+' || echo 'n/a')
echo "  USB protocol = ${USB_DRIVER}"
echo ""

echo "=== Mount ==="
mount | grep /backup || echo "  /backup not mounted"
echo ""

echo "=== Live ==="
grep -E 'Dirty:|Writeback:' /proc/meminfo | head -2
