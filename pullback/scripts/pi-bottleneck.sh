#!/bin/bash
# pi-bottleneck.sh — Monitor CPU, IO, network, and tuning state during sync.
# Usage: pi-bottleneck.sh [--runsec=N]  (default: run until Ctrl+C)

RUNSEC=0
for arg in "$@"; do
    case "$arg" in
        --runsec=*) RUNSEC="${arg#*=}" ;;
    esac
done

# Detect backup disk device from /backup mount
DISK_DEV=$(findmnt -n -o SOURCE /backup 2>/dev/null | xargs basename 2>/dev/null || echo "sda")

SAMPLES=0
SUM_CPU=0
SUM_DISK=0
SUM_NET=0
SUM_DIRTY=0
MAX_CPU=0
MAX_DISK=0
MAX_NET=0
MAX_DIRTY=0
START=$(date +%s)

summary() {
    echo ""
    echo "=== Summary (${SAMPLES} samples, $(( $(date +%s) - START ))s) ==="
    if [[ $SAMPLES -gt 0 ]]; then
        AVG_CPU=$((SUM_CPU / SAMPLES))
        AVG_DISK=$((SUM_DISK / SAMPLES))
        AVG_NET=$((SUM_NET / SAMPLES))
        AVG_DIRTY=$((SUM_DIRTY / SAMPLES))
        # Targets: Net ~55 MB/s, Disk ~55 MB/s, Dirty < 80 MB
        echo ""
        echo "  ============================================"
        echo "  Net:    ${AVG_NET} MB/s   (target: ~55)"
        echo "  Disk:   ${AVG_DISK} MB/s   (target: ~55)"
        echo "  Dirty:  ${AVG_DIRTY} MB     (target: <80)"
        echo "  ============================================"
        echo ""
        echo "  CPU:    avg=${AVG_CPU}%  max=${MAX_CPU}%"
        echo "  Net:    avg=${AVG_NET} MB/s  max=${MAX_NET} MB/s"
        echo "  Disk:   avg=${AVG_DISK} MB/s  max=${MAX_DISK} MB/s"
        echo "  Dirty:  avg=${AVG_DIRTY} MB  max=${MAX_DIRTY} MB"

        # Tuning state snapshot
        echo ""
        echo "=== Tuning State ==="

        # Governor
        GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "n/a")
        echo "  Governor:    ${GOV}"

        # RPS
        RPS=$(cat /sys/class/net/eth0/queues/rx-0/rps_cpus 2>/dev/null || echo "n/a")
        echo "  RPS:         ${RPS}"

        # EEE
        EEE=$(ethtool --show-eee eth0 2>/dev/null | grep -i "eee status" | awk -F: '{print $2}' | xargs)
        echo "  EEE:         ${EEE:-n/a}"

        # NET_RX softirq distribution
        SOFTIRQ=$(grep NET_RX /proc/softirqs 2>/dev/null | awk '{printf "CPU0=%s CPU1=%s CPU2=%s CPU3=%s", $2, $3, $4, $5}')
        echo "  NET_RX:      ${SOFTIRQ:-n/a}"

        # RX dropped
        RXDROP=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo "n/a")
        echo "  RX dropped:  ${RXDROP}"

        # Dirty page sysctl
        DR=$(sysctl -n vm.dirty_ratio 2>/dev/null)
        DBR=$(sysctl -n vm.dirty_background_ratio 2>/dev/null)
        DB=$(sysctl -n vm.dirty_bytes 2>/dev/null)
        DBB=$(sysctl -n vm.dirty_background_bytes 2>/dev/null)
        if [[ "$DB" -gt 0 ]] 2>/dev/null; then
            echo "  Dirty cfg:   bytes=${DB} bg_bytes=${DBB}"
        else
            echo "  Dirty cfg:   ratio=${DR} bg_ratio=${DBR}"
        fi

        # I/O scheduler
        SCHED=$(cat /sys/block/${DISK_DEV}/queue/scheduler 2>/dev/null || echo "no ${DISK_DEV}")
        echo "  Scheduler:   ${SCHED}"

        # Read-ahead
        RA=$(blockdev --getra /dev/${DISK_DEV} 2>/dev/null || echo "n/a")
        echo "  Read-ahead:  ${RA} sectors"
    fi
    exit 0
}

trap summary INT

printf "%-5s %-11s %-11s %-9s %-20s\n" "CPU%" "DISK(MB/s)" "NET(MB/s)" "DIRTY(MB)" "top_process"

while true; do
    # Snapshot 1
    read CPU_IDLE1 < <(awk '/^cpu /{print $5}' /proc/stat)
    read CPU_TOTAL1 < <(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
    DISK_SECTORS1=$(awk "/${DISK_DEV} /"'{print $6+$10}' /proc/diskstats 2>/dev/null) || DISK_SECTORS1=0
    RX1=$(cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null) || RX1=0
    TX1=$(cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null) || TX1=0

    sleep 2

    # Snapshot 2
    read CPU_IDLE2 < <(awk '/^cpu /{print $5}' /proc/stat)
    read CPU_TOTAL2 < <(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
    DISK_SECTORS2=$(awk "/${DISK_DEV} /"'{print $6+$10}' /proc/diskstats 2>/dev/null) || DISK_SECTORS2=0
    RX2=$(cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null) || RX2=0
    TX2=$(cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null) || TX2=0

    # CPU % (all cores combined)
    IDLE_DIFF=$((CPU_IDLE2 - CPU_IDLE1))
    TOTAL_DIFF=$((CPU_TOTAL2 - CPU_TOTAL1))
    if [[ $TOTAL_DIFF -gt 0 ]]; then
        CPU_PCT=$(( 100 - (IDLE_DIFF * 100 / TOTAL_DIFF) ))
    else
        CPU_PCT=0
    fi

    # Disk MB/s (512 byte sectors, over 2 seconds)
    DISK_MB=$(( (DISK_SECTORS2 - DISK_SECTORS1) * 512 / 1024 / 1024 / 2 ))

    # Net MB/s (over 2 seconds)
    NET_MB=$(( (RX2 - RX1 + TX2 - TX1) / 1024 / 1024 / 2 ))

    # Dirty pages (MB)
    DIRTY_KB=$(awk '/^Dirty:/{print $2}' /proc/meminfo)
    DIRTY_MB=$((DIRTY_KB / 1024))

    # Top CPU-consuming process
    TOP_PROC=$(ps -eo pcpu,comm --sort=-pcpu --no-headers | head -1 | awk '{printf "%s%% %s", $1, $2}')

    printf "\r%-5s %-11s %-11s %-9s %-20s" "${CPU_PCT}%" "${DISK_MB}" "${NET_MB}" "${DIRTY_MB}" "$TOP_PROC"

    # Track stats
    SAMPLES=$((SAMPLES + 1))
    SUM_CPU=$((SUM_CPU + CPU_PCT))
    SUM_DISK=$((SUM_DISK + DISK_MB))
    SUM_NET=$((SUM_NET + NET_MB))
    SUM_DIRTY=$((SUM_DIRTY + DIRTY_MB))
    [[ $CPU_PCT -gt $MAX_CPU ]] && MAX_CPU=$CPU_PCT
    [[ $DISK_MB -gt $MAX_DISK ]] && MAX_DISK=$DISK_MB
    [[ $NET_MB -gt $MAX_NET ]] && MAX_NET=$NET_MB
    [[ $DIRTY_MB -gt $MAX_DIRTY ]] && MAX_DIRTY=$DIRTY_MB

    # Check runsec
    if [[ $RUNSEC -gt 0 ]]; then
        NOW=$(date +%s)
        if [[ $((NOW - START)) -ge $RUNSEC ]]; then
            summary
        fi
    fi
done
