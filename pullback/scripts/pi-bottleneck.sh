#!/bin/bash
# pi-bottleneck.sh — Monitor CPU, IO, network, and tuning state during sync.
#
# Usage:
#   pi-bottleneck.sh                     # run until Ctrl+C, log to file
#   pi-bottleneck.sh --runsec=300        # run for 5 minutes
#   pi-bottleneck.sh --daemon            # run in background, log only
#   pi-bottleneck.sh --report            # print summary from latest log
#   pi-bottleneck.sh --report=N          # print summary from last N minutes of log

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_FILE="${PROJECT_DIR}/state/bottleneck.log"

RUNSEC=0
DAEMON=false
REPORT=0
for arg in "$@"; do
    case "$arg" in
        --runsec=*) RUNSEC="${arg#*=}" ;;
        --daemon) DAEMON=true ;;
        --report) REPORT=5 ;;
        --report=*) REPORT="${arg#*=}" ;;
    esac
done

# ── Report mode: summarise from log ──

if [[ $REPORT -gt 0 ]]; then
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "No log file at ${LOG_FILE}" >&2
        exit 1
    fi

    CUTOFF=$(date -d "${REPORT} minutes ago" +%s 2>/dev/null || date -v-${REPORT}M +%s 2>/dev/null)

    COUNT=0
    SUM_NET=0; SUM_DISK=0; SUM_DIRTY=0; SUM_CPU=0
    MIN_NET=999999; MIN_DISK=999999; MIN_DIRTY=999999
    MAX_NET=0; MAX_DISK=0; MAX_DIRTY=0; MAX_CPU=0

    while IFS=',' read -r TS CPU DISK NET DIRTY TOP; do
        [[ "$TS" =~ ^[0-9]+$ ]] || continue
        [[ $TS -ge $CUTOFF ]] || continue
        COUNT=$((COUNT + 1))
        SUM_CPU=$((SUM_CPU + CPU))
        SUM_DISK=$((SUM_DISK + DISK))
        SUM_NET=$((SUM_NET + NET))
        SUM_DIRTY=$((SUM_DIRTY + DIRTY))
        [[ $NET -lt $MIN_NET ]] && MIN_NET=$NET
        [[ $DISK -lt $MIN_DISK ]] && MIN_DISK=$DISK
        [[ $DIRTY -lt $MIN_DIRTY ]] && MIN_DIRTY=$DIRTY
        [[ $NET -gt $MAX_NET ]] && MAX_NET=$NET
        [[ $DISK -gt $MAX_DISK ]] && MAX_DISK=$DISK
        [[ $DIRTY -gt $MAX_DIRTY ]] && MAX_DIRTY=$DIRTY
        [[ $CPU -gt $MAX_CPU ]] && MAX_CPU=$CPU
    done < "$LOG_FILE"

    if [[ $COUNT -eq 0 ]]; then
        echo "No samples in last ${REPORT} minutes"
        exit 0
    fi

    AVG_CPU=$((SUM_CPU / COUNT))
    AVG_NET=$((SUM_NET / COUNT))
    AVG_DISK=$((SUM_DISK / COUNT))
    AVG_DIRTY=$((SUM_DIRTY / COUNT))

    echo "=== Bottleneck Report (last ${REPORT} min, ${COUNT} samples) ==="
    echo ""
    echo "  ============================================"
    echo "  Net:    avg=${AVG_NET}  min=${MIN_NET}  max=${MAX_NET} MB/s   (target: ~55)"
    echo "  Disk:   avg=${AVG_DISK}  min=${MIN_DISK}  max=${MAX_DISK} MB/s   (target: ~55)"
    echo "  Dirty:  avg=${AVG_DIRTY}  min=${MIN_DIRTY}  max=${MAX_DIRTY} MB     (target: <80)"
    echo "  ============================================"
    echo ""
    echo "  CPU:    avg=${AVG_CPU}%  max=${MAX_CPU}%"
    echo ""

    # Tuning state
    echo "=== Tuning State ==="
    GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "n/a")
    echo "  Governor:    ${GOV}"
    RPS=$(cat /sys/class/net/eth0/queues/rx-0/rps_cpus 2>/dev/null || echo "n/a")
    echo "  RPS:         ${RPS}"
    EEE=$(ethtool --show-eee eth0 2>/dev/null | grep -i "eee status" | awk -F: '{print $2}' | xargs)
    echo "  EEE:         ${EEE:-n/a}"
    SOFTIRQ=$(grep NET_RX /proc/softirqs 2>/dev/null | awk '{printf "CPU0=%s CPU1=%s CPU2=%s CPU3=%s", $2, $3, $4, $5}')
    echo "  NET_RX:      ${SOFTIRQ:-n/a}"
    RXDROP=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo "n/a")
    echo "  RX dropped:  ${RXDROP}"
    DR=$(sysctl -n vm.dirty_ratio 2>/dev/null)
    DBR=$(sysctl -n vm.dirty_background_ratio 2>/dev/null)
    DB=$(sysctl -n vm.dirty_bytes 2>/dev/null)
    DBB=$(sysctl -n vm.dirty_background_bytes 2>/dev/null)
    if [[ "$DB" -gt 0 ]] 2>/dev/null; then
        echo "  Dirty cfg:   bytes=${DB} bg_bytes=${DBB}"
    else
        echo "  Dirty cfg:   ratio=${DR} bg_ratio=${DBR}"
    fi
    DISK_DEV=$(df /backup 2>/dev/null | tail -1 | awk '{print $1}' | xargs basename 2>/dev/null || echo "n/a")
    SCHED=$(cat /sys/block/${DISK_DEV}/queue/scheduler 2>/dev/null || echo "n/a")
    echo "  Scheduler:   ${SCHED}"
    RA=$(blockdev --getra /dev/${DISK_DEV} 2>/dev/null || echo "n/a")
    echo "  Read-ahead:  ${RA} sectors"
    exit 0
fi

# ── Collector mode ──

# Detect backup disk device
DISK_DEV=$(df /backup 2>/dev/null | tail -1 | awk '{print $1}' | xargs basename 2>/dev/null || echo "sda")

# Ensure state dir exists
mkdir -p "$(dirname "$LOG_FILE")"

START=$(date +%s)

sample() {
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

    # CPU %
    IDLE_DIFF=$((CPU_IDLE2 - CPU_IDLE1))
    TOTAL_DIFF=$((CPU_TOTAL2 - CPU_TOTAL1))
    if [[ $TOTAL_DIFF -gt 0 ]]; then
        CPU_PCT=$(( 100 - (IDLE_DIFF * 100 / TOTAL_DIFF) ))
    else
        CPU_PCT=0
    fi

    # Disk MB/s
    DISK_MB=$(( (DISK_SECTORS2 - DISK_SECTORS1) * 512 / 1024 / 1024 / 2 ))

    # Net MB/s
    NET_MB=$(( (RX2 - RX1 + TX2 - TX1) / 1024 / 1024 / 2 ))

    # Dirty pages (MB)
    DIRTY_KB=$(awk '/^Dirty:/{print $2}' /proc/meminfo)
    DIRTY_MB=$((DIRTY_KB / 1024))

    # Top process
    TOP_PROC=$(ps -eo pcpu,comm --sort=-pcpu --no-headers | head -1 | awk '{printf "%s%% %s", $1, $2}')

    # Log: timestamp,cpu,disk,net,dirty,top
    echo "$(date +%s),${CPU_PCT},${DISK_MB},${NET_MB},${DIRTY_MB},${TOP_PROC}" >> "$LOG_FILE"

    # Console output (unless daemon)
    if [[ "$DAEMON" == false ]]; then
        printf "\r%-5s %-11s %-11s %-9s %-20s" "${CPU_PCT}%" "${DISK_MB}" "${NET_MB}" "${DIRTY_MB}" "$TOP_PROC"
    fi
}

cleanup() {
    if [[ "$DAEMON" == false ]]; then
        echo ""
        echo "Log: ${LOG_FILE}"
        echo "Report: bash $0 --report"
    fi
    exit 0
}

trap cleanup INT

if [[ "$DAEMON" == false ]]; then
    printf "%-5s %-11s %-11s %-9s %-20s\n" "CPU%" "DISK(MB/s)" "NET(MB/s)" "DIRTY(MB)" "top_process"
fi

while true; do
    sample

    if [[ $RUNSEC -gt 0 ]]; then
        NOW=$(date +%s)
        if [[ $((NOW - START)) -ge $RUNSEC ]]; then
            cleanup
        fi
    fi
done
