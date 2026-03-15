#!/bin/bash
# autotune.sh — Automatically test and optimise tuning parameters.
# Requires an active sync running during the test.
#
# Usage:
#   autotune.sh --dry-run       # show what would be tested, no changes
#   autotune.sh                 # run binary tests, apply winners
#   autotune.sh --sweep         # sweep sysctl params to find optimal values
#   autotune.sh --sweep --dry-run
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
SWEEP=false
SAMPLE_SECS=120  # 2 minutes per test

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --sweep) SWEEP=true ;;
        --sample=*) SAMPLE_SECS="${arg#*=}" ;;
    esac
done

log() { echo "[autotune] $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

# ── Collect a sample — returns "net disk dirty" ──

collect() {
    local secs=$1
    > "$LOG_FILE"

    bash "$STATS_SCRIPT" --runsec=$secs --daemon
    sleep $((secs + 2))

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

# ── Check sync is running ──

check_sync() {
    if ! pgrep -f "rsync" > /dev/null 2>&1; then
        log "ERROR: rsync not running. Start a sync first."
        return 1
    fi
    return 0
}

# ── Define binary tests ──

TESTS=(
    "dirty_ratio=5,bg=2|sysctl -w vm.dirty_ratio=5 vm.dirty_background_ratio=2|sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=10"
    "EEE_off|ethtool --set-eee eth0 eee off 2>/dev/null|ethtool --set-eee eth0 eee on 2>/dev/null"
    "RPS_CPU2+3|echo c > /sys/class/net/eth0/queues/rx-0/rps_cpus; echo 32768 > /proc/sys/net/core/rps_sock_flow_entries|echo 0 > /sys/class/net/eth0/queues/rx-0/rps_cpus; echo 0 > /proc/sys/net/core/rps_sock_flow_entries"
    "governor=performance|echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null|echo ondemand | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null"
    "dirty_expire=1000|sysctl -w vm.dirty_expire_centisecs=1000|sysctl -w vm.dirty_expire_centisecs=3000"
    "dirty_writeback=100|sysctl -w vm.dirty_writeback_centisecs=100|sysctl -w vm.dirty_writeback_centisecs=500"
)

# ── Define sweep params ──
# Each: name|sysctl_key|values_to_test|current_value_cmd

SWEEPS=(
    "dirty_ratio|vm.dirty_ratio|2 3 4 5 6 7 8|sysctl -n vm.dirty_ratio"
    "dirty_background_ratio|vm.dirty_background_ratio|1 2 3 4|sysctl -n vm.dirty_background_ratio"
    "dirty_expire|vm.dirty_expire_centisecs|500 1000 1500 2000 3000|sysctl -n vm.dirty_expire_centisecs"
    "dirty_writeback|vm.dirty_writeback_centisecs|100 200 300 500 1000|sysctl -n vm.dirty_writeback_centisecs"
)

# ── Dry run ──

if [[ "$DRY_RUN" == true ]]; then
    log "*** DRY-RUN — showing test plan, no changes ***"
    echo ""
    echo "Sample duration: ${SAMPLE_SECS}s per value"

    if [[ "$SWEEP" == true ]]; then
        echo "Mode: SWEEP (find optimal value per param)"
        echo ""
        local_total=0
        for sweep in "${SWEEPS[@]}"; do
            IFS='|' read -r name key values get_cmd <<< "$sweep"
            count=$(echo $values | wc -w)
            echo "  ${name} (${key}): test values [${values}]  (${count} tests)"
            local_total=$((local_total + count))
        done
        echo ""
        echo "Total: ${local_total} tests, ~$((local_total * SAMPLE_SECS / 60)) minutes"
    else
        echo "Mode: BINARY (on/off per param)"
        echo "Total: ${#TESTS[@]} tests, ~$(( (${#TESTS[@]} * 2 + 1) * SAMPLE_SECS / 60 )) minutes"
        echo ""
        for test in "${TESTS[@]}"; do
            IFS='|' read -r name apply revert <<< "$test"
            echo "  ${name}"
        done
    fi
    exit 0
fi

# ── Check sync ──

check_sync || exit 1

# ── Ensure state dir ──

mkdir -p "$(dirname "$LOG_FILE")"
echo "" > "$RESULTS_FILE"

# ═══════════════════════════════════
# SWEEP MODE
# ═══════════════════════════════════

if [[ "$SWEEP" == true ]]; then
    log "Starting sweep mode"
    echo ""

    for sweep in "${SWEEPS[@]}"; do
        IFS='|' read -r name key values get_cmd <<< "$sweep"

        original=$(eval "$get_cmd")
        log "=== Sweeping ${name} (${key}) ==="
        log "  Current value: ${original}"
        log "  Testing: ${values}"

        best_val=$original
        best_net=0
        best_disk=0
        best_dirty=999

        for val in $values; do
            check_sync || break

            log "  Testing ${key}=${val}..."
            sysctl -w ${key}=${val} > /dev/null 2>&1
            sleep 5  # settle

            read net disk dirty <<< "$(collect $SAMPLE_SECS)"
            log "    Result: Net=${net} Disk=${disk} Dirty=${dirty}"
            echo "SWEEP|${name}=${val}|Net=${net}|Disk=${disk}|Dirty=${dirty}" >> "$RESULTS_FILE"

            # Score: prioritise net throughput, penalise dirty >80
            score=$net
            if [[ $dirty -gt 80 ]]; then
                score=$((score - (dirty - 80)))
            fi

            best_score=$best_net
            if [[ $best_dirty -gt 80 ]]; then
                best_score=$((best_score - (best_dirty - 80)))
            fi

            if [[ $score -gt $best_score ]]; then
                best_val=$val
                best_net=$net
                best_disk=$disk
                best_dirty=$dirty
                log "    ^ New best: ${key}=${val} (score=${score})"
            fi
        done

        # Apply best value
        sysctl -w ${key}=${best_val} > /dev/null 2>&1
        log "  >>> Best: ${key}=${best_val} (Net=${best_net} Disk=${best_disk} Dirty=${best_dirty})"
        echo "BEST|${name}=${best_val}|Net=${best_net}|Disk=${best_disk}|Dirty=${best_dirty}" >> "$RESULTS_FILE"
        echo ""
    done

    echo ""
    log "============================================"
    log "Sweep complete"
    log "============================================"
    echo ""
    log "Best values found:"
    grep "^BEST" "$RESULTS_FILE" | while IFS='|' read -r tag param net disk dirty; do
        log "  ${param}  ${net} ${disk} ${dirty}"
    done
    echo ""
    log "All results:"
    cat "$RESULTS_FILE"
    echo ""
    log "To persist, update config.local.yaml and run pi-tune-install.sh"
    exit 0
fi

# ═══════════════════════════════════
# BINARY MODE (original)
# ═══════════════════════════════════

log "Starting binary test mode (${#TESTS[@]} params, ~$(( (${#TESTS[@]} * 2 + 1) * SAMPLE_SECS / 60 )) minutes)"

KEPT=()

for test in "${TESTS[@]}"; do
    IFS='|' read -r name apply revert <<< "$test"

    log "--- Testing: ${name} ---"

    # Baseline
    log "Collecting baseline (${SAMPLE_SECS}s)..."
    read base_net base_disk base_dirty <<< "$(collect $SAMPLE_SECS)"
    log "  Baseline: Net=${base_net} Disk=${base_disk} Dirty=${base_dirty}"

    check_sync || break

    # Apply
    log "Applying: ${apply}"
    eval "$apply" 2>/dev/null || true

    # Measure
    log "Collecting after (${SAMPLE_SECS}s)..."
    read after_net after_disk after_dirty <<< "$(collect $SAMPLE_SECS)"
    log "  After:    Net=${after_net} Disk=${after_disk} Dirty=${after_dirty}"

    # Evaluate
    improved=false
    if [[ $after_net -gt $base_net ]] || [[ $after_disk -gt $base_disk ]]; then
        if [[ $after_dirty -le 80 ]] || [[ $after_dirty -lt $base_dirty ]]; then
            improved=true
        fi
    fi
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
        log "  ${k}"
    done
    echo ""
    log "To persist, update config.local.yaml and run pi-tune-install.sh"
else
    log "No params improved throughput. System is at default optimum."
fi

echo ""
log "Full results: ${RESULTS_FILE}"
cat "$RESULTS_FILE"
