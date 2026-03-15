#!/bin/bash
# autotune.sh — Automatically test each tuning parameter and keep what helps.
# Requires an active sync running during the test.
#
# Usage:
#   autotune.sh --dry-run     # show what would be tested, no changes
#   autotune.sh               # run all tests, apply winners
#
# Targets: Net ~55 MB/s, Disk ~55 MB/s, Dirty <80 MB
# Golden rule: one param at a time, revert if no improvement.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATS_SCRIPT="${SCRIPT_DIR}/pi-bottleneck.sh"
LOG_FILE="${PROJECT_DIR}/state/bottleneck.log"
RESULTS_FILE="${PROJECT_DIR}/state/autotune-results.log"

DRY_RUN=false
SAMPLE_SECS=120  # 2 minutes per test

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { echo "[autotune] $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

# ── Define tests ──
# Each test: name|apply_cmd|revert_cmd
# Order: biggest expected impact first

TESTS=(
    "dirty_ratio=5,bg=2|sysctl -w vm.dirty_ratio=5 vm.dirty_background_ratio=2|sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=10"
    "EEE_off|ethtool --set-eee eth0 eee off 2>/dev/null|ethtool --set-eee eth0 eee on 2>/dev/null"
    "RPS_CPU2+3|echo c > /sys/class/net/eth0/queues/rx-0/rps_cpus; echo 32768 > /proc/sys/net/core/rps_sock_flow_entries|echo 0 > /sys/class/net/eth0/queues/rx-0/rps_cpus; echo 0 > /proc/sys/net/core/rps_sock_flow_entries"
    "governor=performance|echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null|echo ondemand | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null"
    "dirty_expire=1000|sysctl -w vm.dirty_expire_centisecs=1000|sysctl -w vm.dirty_expire_centisecs=3000"
    "dirty_writeback=100|sysctl -w vm.dirty_writeback_centisecs=100|sysctl -w vm.dirty_writeback_centisecs=500"
)

# ── Collect a sample ──

collect() {
    local secs=$1
    # Clear old log
    > "$LOG_FILE"

    # Run collector in background
    bash "$STATS_SCRIPT" --runsec=$secs --daemon
    sleep $((secs + 2))

    # Parse results
    local count=0 sum_net=0 sum_disk=0 sum_dirty=0
    while IFS=',' read -r ts cpu disk net dirty top; do
        [[ "$ts" =~ ^[0-9]+$ ]] || continue
        count=$((count + 1))
        sum_net=$((sum_net + net))
        sum_disk=$((sum_disk + disk))
        sum_dirty=$((sum_dirty + dirty))
    done < "$LOG_FILE"

    if [[ $count -eq 0 ]]; then
        echo "0 0 0"
        return
    fi

    echo "$((sum_net / count)) $((sum_disk / count)) $((sum_dirty / count))"
}

# ── Dry run ──

if [[ "$DRY_RUN" == true ]]; then
    log "*** DRY-RUN — showing test plan, no changes ***"
    echo ""
    echo "Sample duration: ${SAMPLE_SECS}s per test"
    echo "Total estimated time: $(( (${#TESTS[@]} * 2 + 1) * SAMPLE_SECS / 60 )) minutes"
    echo ""
    echo "Tests in order:"
    for test in "${TESTS[@]}"; do
        IFS='|' read -r name apply revert <<< "$test"
        echo "  ${name}"
        echo "    Apply:  ${apply}"
        echo "    Revert: ${revert}"
        echo ""
    done
    echo "Each test: baseline → apply → measure → keep if Net or Disk improved AND Dirty <= 80"
    exit 0
fi

# ── Check sync is running ──

if ! pgrep -f "rsync" > /dev/null 2>&1; then
    echo "Error: no rsync process running. Start a sync first." >&2
    echo "  The autotune needs active sync traffic to measure throughput." >&2
    exit 1
fi

# ── Run tests ──

log "Starting autotune (${#TESTS[@]} params, ~$(( (${#TESTS[@]} * 2 + 1) * SAMPLE_SECS / 60 )) minutes)"
echo "" > "$RESULTS_FILE"

KEPT=()

for test in "${TESTS[@]}"; do
    IFS='|' read -r name apply revert <<< "$test"

    log "--- Testing: ${name} ---"

    # Baseline
    log "Collecting baseline (${SAMPLE_SECS}s)..."
    read base_net base_disk base_dirty <<< "$(collect $SAMPLE_SECS)"
    log "  Baseline: Net=${base_net} Disk=${base_disk} Dirty=${base_dirty}"

    # Check sync still running
    if ! pgrep -f "rsync" > /dev/null 2>&1; then
        log "WARNING: rsync stopped during test — skipping remaining params"
        break
    fi

    # Apply
    log "Applying: ${apply}"
    eval "$apply" 2>/dev/null || true

    # Measure
    log "Collecting after (${SAMPLE_SECS}s)..."
    read after_net after_disk after_dirty <<< "$(collect $SAMPLE_SECS)"
    log "  After:    Net=${after_net} Disk=${after_disk} Dirty=${after_dirty}"

    # Evaluate — keep if net OR disk improved and dirty didn't blow up
    improved=false
    if [[ $after_net -gt $base_net ]] || [[ $after_disk -gt $base_disk ]]; then
        if [[ $after_dirty -le 80 ]] || [[ $after_dirty -lt $base_dirty ]]; then
            improved=true
        fi
    fi
    # Also keep if dirty improved significantly and throughput didn't drop
    if [[ $after_dirty -lt $base_dirty ]] && [[ $((base_dirty - after_dirty)) -gt 20 ]]; then
        if [[ $after_net -ge $((base_net - 5)) ]]; then
            improved=true
        fi
    fi

    if [[ "$improved" == true ]]; then
        log "  >>> KEEP: ${name} (Net ${base_net}→${after_net}, Disk ${base_disk}→${after_disk}, Dirty ${base_dirty}→${after_dirty})"
        KEPT+=("$name")
        echo "KEEP|${name}|Net ${base_net}→${after_net}|Disk ${base_disk}→${after_disk}|Dirty ${base_dirty}→${after_dirty}" >> "$RESULTS_FILE"
    else
        log "  <<< REVERT: ${name} (no improvement)"
        eval "$revert" 2>/dev/null || true
        echo "REVERT|${name}|Net ${base_net}→${after_net}|Disk ${base_disk}→${after_disk}|Dirty ${base_dirty}→${after_dirty}" >> "$RESULTS_FILE"
    fi

    echo ""
done

# ── Summary ──

echo ""
log "============================================"
log "Autotune complete"
log "============================================"
echo ""

if [[ ${#KEPT[@]} -gt 0 ]]; then
    log "Kept params:"
    for k in "${KEPT[@]}"; do
        log "  ✓ ${k}"
    done
    echo ""
    log "To persist, update config.local.yaml and run pi-tune-install.sh"
else
    log "No params improved throughput. System is at default optimum."
fi

echo ""
log "Full results: ${RESULTS_FILE}"
cat "$RESULTS_FILE"
