#!/bin/bash
# bottleneck.sh — One-line monitor for CPU, IO, network during sync.
# Usage: bottleneck.sh [--runsec=N]  (default: run until Ctrl+C)

RUNSEC=0
for arg in "$@"; do
    case "$arg" in
        --runsec=*) RUNSEC="${arg#*=}" ;;
    esac
done

SAMPLES=0
SUM_CPU=0
SUM_DISK=0
SUM_NET=0
MAX_CPU=0
MAX_DISK=0
MAX_NET=0
START=$(date +%s)

summary() {
    echo ""
    echo "=== Summary (${SAMPLES} samples) ==="
    if [[ $SAMPLES -gt 0 ]]; then
        AVG_CPU=$((SUM_CPU / SAMPLES))
        AVG_DISK=$((SUM_DISK / SAMPLES))
        AVG_NET=$((SUM_NET / SAMPLES))
        echo "  CPU%%:  avg=${AVG_CPU}  max=${MAX_CPU}"
        echo "  DISK:  avg=${AVG_DISK} MB/s  max=${MAX_DISK} MB/s"
        echo "  NET:   avg=${AVG_NET} MB/s  max=${MAX_NET} MB/s"
        if [[ $MAX_CPU -gt 90 ]]; then
            echo "  >> BOTTLENECK: CPU (likely SSH encryption)"
        elif [[ $MAX_NET -lt 10 ]]; then
            echo "  >> BOTTLENECK: Network"
        elif [[ $MAX_DISK -lt 20 ]]; then
            echo "  >> BOTTLENECK: Disk IO"
        else
            echo "  >> No clear bottleneck"
        fi
    fi
    exit 0
}

trap summary INT

echo "CPU%  DISK(MB/s)  NET(MB/s)  top_process"

while true; do
    # Snapshot 1
    read CPU_IDLE1 < <(awk '/^cpu /{print $5}' /proc/stat)
    read CPU_TOTAL1 < <(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
    DISK_SECTORS1=$(awk '/sda /{print $6+$10}' /proc/diskstats 2>/dev/null) || DISK_SECTORS1=0
    RX1=$(cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null) || RX1=0
    TX1=$(cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null) || TX1=0

    sleep 2

    # Snapshot 2
    read CPU_IDLE2 < <(awk '/^cpu /{print $5}' /proc/stat)
    read CPU_TOTAL2 < <(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
    DISK_SECTORS2=$(awk '/sda /{print $6+$10}' /proc/diskstats 2>/dev/null) || DISK_SECTORS2=0
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

    # Top CPU-consuming process
    TOP_PROC=$(ps -eo pcpu,comm --sort=-pcpu --no-headers | head -1 | awk '{printf "%s%% %s", $1, $2}')

    printf "\r%3s%%  %4s        %4s       %-20s" "$CPU_PCT" "$DISK_MB" "$NET_MB" "$TOP_PROC"

    # Track stats
    SAMPLES=$((SAMPLES + 1))
    SUM_CPU=$((SUM_CPU + CPU_PCT))
    SUM_DISK=$((SUM_DISK + DISK_MB))
    SUM_NET=$((SUM_NET + NET_MB))
    [[ $CPU_PCT -gt $MAX_CPU ]] && MAX_CPU=$CPU_PCT
    [[ $DISK_MB -gt $MAX_DISK ]] && MAX_DISK=$DISK_MB
    [[ $NET_MB -gt $MAX_NET ]] && MAX_NET=$NET_MB

    # Check runsec
    if [[ $RUNSEC -gt 0 ]]; then
        NOW=$(date +%s)
        if [[ $((NOW - START)) -ge $RUNSEC ]]; then
            summary
        fi
    fi
done
