#!/usr/bin/env python3
"""autotune.py — Automated per-layer tuning with measurement.

Tests tuning parameters one at a time in logical layer order:
  1. Network  (tcp buffers, backlog) — uses tmpfs, no sync needed
  2. Write    (RPS, EEE, governor, scheduler, dirty, BDI) — needs active sync
  3. rsync    (cipher, transport, flags) — needs active sync

Usage:
  autotune.py                        # run all layers
  autotune.py --layer=network        # run one layer only
  autotune.py --layer=network --dry-run
  autotune.py --sample=60            # 60s samples (default 120)
  autotune.py --drive-type=hdd       # override auto-detection
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
STATE_DIR = PROJECT_DIR / "state"
BOTTLENECK = SCRIPT_DIR / "pi-bottleneck.sh"
RESULTS_LOG = STATE_DIR / "autotune-v2.json"
MOUNT_POINT = "/backup"

# Set by main() from CLI args
_config = {
    "iperf_server": "192.168.0.1",
    "iperf_port": 42947,
    "source_host": "proxmox.home",
}

# ── Colours ──────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg, colour=""):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{colour}[{ts}] {msg}{RESET}")


def log_info(msg):
    log(msg, CYAN)


def log_ok(msg):
    log(msg, GREEN)


def log_warn(msg):
    log(msg, YELLOW)


def log_err(msg):
    log(msg, RED)


# ── Parameter definitions ────────────────────────────────


@dataclass
class Param:
    name: str
    layer: str  # network, cpu, disk, dirty
    description: str
    apply_cmd: str  # shell command to apply the test value
    revert_cmd: str  # shell command to revert to default
    read_cmd: str  # shell command to read current value
    test_values: list = field(default_factory=list)  # for sweep
    drive_type: str = "both"  # hdd, ssd, both
    requires_sync: bool = False
    metric: str = "net_avg"  # primary metric to judge improvement
    threshold_pct: float = 5.0  # minimum % improvement to keep


# Block device is detected at runtime — these use {dev} placeholder
PARAMS = [
    # ── Layer 1: Network (pure TCP, no disk interaction) ──
    # Confirmed default-ok at gigabit (117 MB/s to tmpfs, 2026-03-18)
    Param(
        name="tcp_slow_start_off",
        layer="network",
        description="Disable TCP slow start after idle",
        apply_cmd="sysctl -w net.ipv4.tcp_slow_start_after_idle=0 > /dev/null",
        revert_cmd="sysctl -w net.ipv4.tcp_slow_start_after_idle=1 > /dev/null",
        read_cmd="sysctl -n net.ipv4.tcp_slow_start_after_idle",
        metric="net_avg",
    ),
    Param(
        name="rmem_wmem_16m",
        layer="network",
        description="Increase socket buffer max to 16MB",
        apply_cmd="sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216 > /dev/null",
        revert_cmd="sysctl -w net.core.rmem_max=212992 net.core.wmem_max=212992 > /dev/null",
        read_cmd="sysctl -n net.core.rmem_max",
        metric="net_avg",
    ),
    Param(
        name="netdev_backlog_5000",
        layer="network",
        description="Increase netdev_max_backlog to 5000",
        apply_cmd="sysctl -w net.core.netdev_max_backlog=5000 > /dev/null",
        revert_cmd="sysctl -w net.core.netdev_max_backlog=1000 > /dev/null",
        read_cmd="sysctl -n net.core.netdev_max_backlog",
        metric="net_avg",
    ),
    # ── Layer 2: Write speed (all params that affect disk write throughput) ──
    # Needs active sync. Tests kernel tuning that affects how fast
    # data gets from network receive buffers onto the physical disk.
    Param(
        name="rps_cpus_0xc",
        layer="write",
        description="RPS distribute NET_RX to CPU2+3 (relieves CPU0 under disk+net load)",
        apply_cmd="echo c > /sys/class/net/eth0/queues/rx-0/rps_cpus && echo 32768 > /proc/sys/net/core/rps_sock_flow_entries",
        revert_cmd="echo 0 > /sys/class/net/eth0/queues/rx-0/rps_cpus && echo 0 > /proc/sys/net/core/rps_sock_flow_entries",
        read_cmd="cat /sys/class/net/eth0/queues/rx-0/rps_cpus",
        metric="disk_avg",
    ),
    Param(
        name="eee_off",
        layer="write",
        description="Disable EEE (bcmgenet bug causes packet drops under sustained load)",
        apply_cmd="ethtool --set-eee eth0 eee off 2>/dev/null",
        revert_cmd="ethtool --set-eee eth0 eee on 2>/dev/null",
        read_cmd="ethtool --show-eee eth0 2>/dev/null | grep -i 'eee status' | awk -F: '{print $2}' | xargs",
        metric="disk_avg",
    ),
    Param(
        name="governor_performance",
        layer="write",
        description="CPU governor: performance (max clock, no scaling latency)",
        apply_cmd="echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null",
        revert_cmd="echo ondemand | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null",
        read_cmd="cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
        metric="disk_avg",
        drive_type="hdd",  # no gain measured on SSD
    ),
    Param(
        name="scheduler_mq_deadline",
        layer="write",
        description="I/O scheduler: mq-deadline (deadline guarantees for rotational)",
        apply_cmd="echo mq-deadline > /sys/block/{dev}/queue/scheduler",
        revert_cmd="echo none > /sys/block/{dev}/queue/scheduler",
        read_cmd="cat /sys/block/{dev}/queue/scheduler",
        metric="disk_avg",
        drive_type="hdd",
    ),
    Param(
        name="dirty_ratio_5",
        layer="write",
        description="dirty_ratio=5, background=2 (force earlier flushes)",
        apply_cmd="sysctl -w vm.dirty_ratio=5 vm.dirty_background_ratio=2 > /dev/null",
        revert_cmd="sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=10 > /dev/null",
        read_cmd="sysctl -n vm.dirty_ratio",
        metric="dirty_avg",
    ),
    Param(
        name="dirty_expire_1000",
        layer="write",
        description="dirty_expire_centisecs=1000 (flush stale pages sooner)",
        apply_cmd="sysctl -w vm.dirty_expire_centisecs=1000 > /dev/null",
        revert_cmd="sysctl -w vm.dirty_expire_centisecs=3000 > /dev/null",
        read_cmd="sysctl -n vm.dirty_expire_centisecs",
        metric="dirty_avg",
    ),
    Param(
        name="bdi_80m",
        layer="write",
        description="BDI strict_limit=1 + max_bytes=80MB (per-device dirty cap)",
        apply_cmd="echo 1 > /sys/block/{dev}/bdi/strict_limit && echo 83886080 > /sys/block/{dev}/bdi/max_bytes",
        revert_cmd="echo 0 > /sys/block/{dev}/bdi/strict_limit && echo 0 > /sys/block/{dev}/bdi/max_bytes",
        read_cmd="cat /sys/block/{dev}/bdi/max_bytes",
        metric="dirty_avg",
        drive_type="hdd",
    ),
    # ── Layer 3: rsync optimisation (transport and flags) ──
    # Tests the rsync transfer itself — cipher, transport mode, flags.
    # These change HOW data moves, not how the kernel handles writes.
    Param(
        name="cipher_aes128_ctr",
        layer="rsync",
        description="SSH cipher aes128-ctr (faster in software on Pi, no AES-NI)",
        apply_cmd="true",  # applied via config, not sysctl — placeholder
        revert_cmd="true",
        read_cmd="echo 'requires config change'",
        requires_sync=True,
        metric="net_avg",
    ),
]


# ── System helpers ───────────────────────────────────────


def run(cmd: str, timeout: int = 10) -> str:
    """Run a shell command, return stdout."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def detect_block_device() -> Optional[str]:
    """Get block device name (e.g. 'sda') for /backup mount point."""
    try:
        line = run(f"df {MOUNT_POINT} | tail -1")
        dev = line.split()[0]  # e.g. /dev/sda1
        name = os.path.basename(dev).rstrip("0123456789")
        return name if name else None
    except (IndexError, subprocess.TimeoutExpired):
        return None


def detect_drive_type(dev: str) -> str:
    """Detect HDD vs SSD from rotational flag."""
    try:
        val = run(f"cat /sys/block/{dev}/queue/rotational")
        return "hdd" if val == "1" else "ssd"
    except subprocess.TimeoutExpired:
        return "hdd"  # safe default


def sync_running() -> bool:
    """Check if rsync is running."""
    try:
        run("pgrep -f rsync")
        return True
    except subprocess.TimeoutExpired:
        return False


def resolve_cmd(cmd: str, dev: str) -> str:
    """Replace {dev} placeholder in commands."""
    return cmd.replace("{dev}", dev)


# ── Measurement ──────────────────────────────────────────


def _sample_rx_bytes(seconds: int) -> list:
    """Sample eth0 rx_bytes every 2s for the given duration. Returns list of MB/s."""
    samples = []
    prev_rx = int(run("cat /sys/class/net/eth0/statistics/rx_bytes"))
    prev_time = time.time()
    end_time = prev_time + seconds

    while time.time() < end_time:
        time.sleep(2)
        curr_rx = int(run("cat /sys/class/net/eth0/statistics/rx_bytes"))
        curr_time = time.time()
        dt = curr_time - prev_time
        if dt > 0:
            mb_s = (curr_rx - prev_rx) / (1024 * 1024) / dt
            samples.append(int(mb_s))
        prev_rx = curr_rx
        prev_time = curr_time

    return samples


def _net_result(samples: list) -> dict:
    """Build result dict from a list of MB/s samples."""
    if not samples:
        return {"net_avg": 0, "net_min": 0, "net_max": 0, "samples": 0}
    return {
        "net_avg": sum(samples) // len(samples),
        "net_min": min(samples),
        "net_max": max(samples),
        "samples": len(samples),
    }


def measure_network_iperf3(seconds: int, server: str = "192.168.0.1",
                           port: int = 42947) -> dict:
    """Measure pure network throughput using iperf3.

    Runs iperf3 client against the specified server (default: OPNsense router).
    -R (reverse) = server sends to us, measuring download throughput.
    Returns dict with net_avg, net_min, net_max in MB/s.
    """
    log(f"    iperf3 → {server}:{port} for {seconds}s")

    # iperf3 -c <server> -p <port> -t <seconds> -R (reverse = server sends to us)
    proc = subprocess.Popen(
        f"iperf3 -c {server} -p {port} -t {seconds} -R >/dev/null 2>&1",
        shell=True,
    )
    time.sleep(2)  # let iperf3 connect

    samples = _sample_rx_bytes(seconds)

    proc.wait(timeout=seconds + 10)

    return _net_result(samples)


def measure_network_tmpfs(seconds: int, source_host: str = "proxmox.home") -> dict:
    """Measure network throughput using rsync to /dev/shm (tmpfs).

    Eliminates disk as a variable. Fallback when iperf3 is unavailable.
    Returns dict with net_avg, net_min, net_max in MB/s.
    """
    tmpfs_dir = "/dev/shm/autotune_net_test"
    os.makedirs(tmpfs_dir, exist_ok=True)

    log(f"    rsync → tmpfs from {source_host} for {seconds}s")

    # Start rsync to tmpfs in background
    # Try rsync daemon first (no encryption overhead)
    rsync_cmd = (
        f"rsync --archive --numeric-ids --partial "
        f"{source_host}::backup/shares/prox-backups/dump/ "
        f"{tmpfs_dir}/ "
        f">/dev/null 2>&1"
    )
    proc = subprocess.Popen(rsync_cmd, shell=True)
    time.sleep(3)

    # Check if daemon mode worked
    if proc.poll() is not None:
        # Daemon failed, fall back to SSH
        log(f"    rsync daemon unavailable, falling back to SSH")
        shutil.rmtree(tmpfs_dir, ignore_errors=True)
        os.makedirs(tmpfs_dir, exist_ok=True)
        rsync_cmd = (
            f"rsync --archive --numeric-ids --partial "
            f"-e 'ssh -i /usr/share/pac/pullback/keys/id_ed25519 -c aes128-ctr' "
            f"root@{source_host}:/ssd8704t/shares/prox-backups/dump/ "
            f"{tmpfs_dir}/ "
            f">/dev/null 2>&1"
        )
        proc = subprocess.Popen(rsync_cmd, shell=True)
        time.sleep(3)

    samples = _sample_rx_bytes(seconds)

    # Kill rsync and clean up
    proc.kill()
    run("pkill -f 'rsync.*autotune_net_test' 2>/dev/null || true")
    shutil.rmtree(tmpfs_dir, ignore_errors=True)

    return _net_result(samples)


def _iperf3_reachable(server: str, port: int) -> bool:
    """Quick check if iperf3 server is reachable (3s connect test)."""
    try:
        r = subprocess.run(
            f"iperf3 -c {server} -p {port} -t 1 -R",
            shell=True, capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _measure_network(seconds: int) -> dict:
    """Pick best available network measurement: iperf3 > rsync-to-tmpfs."""
    if shutil.which("iperf3") and _iperf3_reachable(_config["iperf_server"], _config["iperf_port"]):
        return measure_network_iperf3(seconds, _config["iperf_server"], _config["iperf_port"])
    else:
        if shutil.which("iperf3"):
            log_warn("    iperf3 server unreachable, falling back to rsync-to-tmpfs")
        return measure_network_tmpfs(seconds, _config["source_host"])


def measure_write_speed(seconds: int) -> dict:
    """Measure raw disk write speed using dd to the backup volume.

    Writes a large file with dd, samples /proc/diskstats and /proc/meminfo
    every 2s for the duration. Returns disk_avg, disk_min, disk_max (MB/s)
    and dirty_avg, dirty_max (MB).
    """
    test_file = f"{MOUNT_POINT}/.autotune_write_test"
    dev = detect_block_device() or "sda"

    # Calculate dd count — write ~1GB per 10s to keep the disk busy
    # bs=1M, so count = seconds * 100 (100 MB/s target feed rate)
    count = seconds * 100

    log(f"    dd if=/dev/zero of={test_file} bs=1M count={count}")

    # Start dd in background
    dd_proc = subprocess.Popen(
        f"dd if=/dev/zero of={test_file} bs=1M count={count} conv=fdatasync 2>/dev/null",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    time.sleep(1)  # let dd start

    # Sample disk write rate and dirty pages
    samples_disk = []
    samples_dirty = []

    end_time = time.time() + seconds

    # Read initial diskstats
    prev_sectors = _read_disk_sectors(dev)
    prev_time = time.time()

    while time.time() < end_time and dd_proc.poll() is None:
        time.sleep(2)

        curr_sectors = _read_disk_sectors(dev)
        curr_time = time.time()
        dt = curr_time - prev_time

        if dt > 0 and curr_sectors is not None and prev_sectors is not None:
            # sectors are 512 bytes
            mb_s = ((curr_sectors - prev_sectors) * 512) / (1024 * 1024) / dt
            samples_disk.append(int(mb_s))

        # Dirty pages
        dirty_mb = _read_dirty_mb()
        if dirty_mb is not None:
            samples_dirty.append(dirty_mb)

        prev_sectors = curr_sectors
        prev_time = curr_time

    # Wait for dd to finish, then clean up
    try:
        dd_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        dd_proc.kill()

    # Remove test file
    try:
        os.remove(test_file)
    except OSError:
        pass

    metrics = {}
    if samples_disk:
        metrics["disk_avg"] = sum(samples_disk) // len(samples_disk)
        metrics["disk_min"] = min(samples_disk)
        metrics["disk_max"] = max(samples_disk)
    if samples_dirty:
        metrics["dirty_avg"] = sum(samples_dirty) // len(samples_dirty)
        metrics["dirty_min"] = min(samples_dirty)
        metrics["dirty_max"] = max(samples_dirty)
    metrics["samples"] = len(samples_disk)

    if not samples_disk:
        log_err("No disk samples collected — dd may not have started")

    return metrics


def _read_disk_sectors(dev: str) -> Optional[int]:
    """Read total sectors written from /proc/diskstats for device."""
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 10 and parts[2] == dev:
                    # Field 10 (index 9) = sectors written
                    return int(parts[9])
    except (IOError, IndexError, ValueError):
        pass
    return None


def _read_dirty_mb() -> Optional[int]:
    """Read current dirty pages in MB from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Dirty:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except (IOError, ValueError):
        pass
    return None


def _get_measure_fn(layer: str):
    """Return the measurement function for a given layer."""
    if layer == "network":
        return _measure_network
    elif layer == "write":
        return measure_write_speed
    else:
        # rsync layer — will need its own measurement (TODO)
        return measure_write_speed


# ── Test logic ───────────────────────────────────────────


def test_param(param: Param, dev: str, sample_secs: int, dry_run: bool = False) -> dict:
    """Test a single parameter. Returns result dict."""
    apply_cmd = resolve_cmd(param.apply_cmd, dev)
    revert_cmd = resolve_cmd(param.revert_cmd, dev)
    read_cmd = resolve_cmd(param.read_cmd, dev)

    result = {
        "name": param.name,
        "layer": param.layer,
        "description": param.description,
        "timestamp": datetime.now().isoformat(),
        "sample_secs": sample_secs,
    }

    log_info(f"━━━ Testing: {param.name} ━━━")
    log(f"    {param.description}")

    # Read current value
    current_val = run(read_cmd)
    result["original_value"] = current_val
    log(f"    Current: {current_val}")

    if dry_run:
        log(f"    Apply:  {apply_cmd}")
        log(f"    Revert: {revert_cmd}")
        result["action"] = "dry-run"
        return result

    # Choose measurement method by layer
    measure = _get_measure_fn(param.layer)

    # Measure baseline
    log_info(f"    Measuring baseline ({sample_secs}s)...")
    before = measure(sample_secs)
    result["before"] = before
    _print_metrics(before, "BEFORE")

    # Apply change
    log_info(f"    Applying: {param.name}")
    run(apply_cmd)
    new_val = run(read_cmd)
    log(f"    Value now: {new_val}")

    # Settle — network changes are instant, others need time
    settle = 3 if param.layer == "network" else 30
    log(f"    Settling ({settle}s)...")
    time.sleep(settle)

    # Measure after
    log_info(f"    Measuring after ({sample_secs}s)...")
    after = measure(sample_secs)
    result["after"] = after
    _print_metrics(after, "AFTER")

    # Evaluate
    metric = param.metric
    before_val = before.get(metric, 0)
    after_val = after.get(metric, 0)

    # For dirty metrics, lower is better
    if "dirty" in metric:
        if before_val > 0:
            change_pct = ((before_val - after_val) / before_val) * 100
        else:
            change_pct = 0
        improved = change_pct >= param.threshold_pct
    else:
        if before_val > 0:
            change_pct = ((after_val - before_val) / before_val) * 100
        else:
            change_pct = 100 if after_val > 0 else 0
        improved = change_pct >= param.threshold_pct

    result["metric"] = metric
    result["before_value"] = before_val
    result["after_value"] = after_val
    result["change_pct"] = round(change_pct, 1)
    result["improved"] = improved

    if improved:
        log_ok(f"    ✓ KEEP: {metric} {before_val} → {after_val} ({change_pct:+.1f}%)")
        result["action"] = "keep"
    else:
        log_warn(f"    ✗ REVERT: {metric} {before_val} → {after_val} ({change_pct:+.1f}%)")
        run(revert_cmd)
        reverted_val = run(read_cmd)
        log(f"    Reverted to: {reverted_val}")
        result["action"] = "revert"

    return result


def _print_metrics(m: dict, label: str):
    """Print a compact metrics line."""
    parts = []
    if "net_avg" in m:
        parts.append(f"Net avg={m['net_avg']} min={m.get('net_min','?')} max={m.get('net_max','?')}")
    if "disk_avg" in m:
        parts.append(f"Disk avg={m['disk_avg']}")
    if "dirty_avg" in m:
        parts.append(f"Dirty avg={m['dirty_avg']} max={m.get('dirty_max','?')}")
    if "cpu_avg" in m:
        parts.append(f"CPU avg={m['cpu_avg']}%")
    if "samples" in m:
        parts.append(f"({m['samples']} samples)")
    log(f"    {label}: {', '.join(parts)}")


# ── Orchestration ────────────────────────────────────────


def run_layer(
    layer: str,
    dev: str,
    drive_type: str,
    sample_secs: int,
    dry_run: bool = False,
) -> list:
    """Run all params for a given layer. Returns list of result dicts."""
    params = [p for p in PARAMS if p.layer == layer]

    # Filter by drive type
    params = [p for p in params if p.drive_type in (drive_type, "both")]

    if not params:
        log_warn(f"No parameters for layer '{layer}' on {drive_type}")
        return []

    log_info(f"╔══ Layer: {layer.upper()} ({len(params)} params) ══╗")

    # Check sync requirement
    needs_sync = any(p.requires_sync for p in params)
    if needs_sync and not dry_run:
        if not sync_running():
            log_err("Sync not running — required for this layer. Skipping.")
            return []

    results = []
    for param in params:
        result = test_param(param, dev, sample_secs, dry_run)
        results.append(result)
        print()

    # Layer summary
    kept = [r for r in results if r.get("action") == "keep"]
    reverted = [r for r in results if r.get("action") == "revert"]
    log_info(f"╚══ Layer {layer.upper()} done: {len(kept)} kept, {len(reverted)} reverted ══╝")
    print()

    return results


def main():
    parser = argparse.ArgumentParser(description="Automated per-layer tuning")
    parser.add_argument("--layer", choices=["network", "write", "rsync"],
                        help="Run a specific layer only")
    parser.add_argument("--sample", type=int, default=120,
                        help="Sample duration in seconds (default: 120)")
    parser.add_argument("--drive-type", choices=["hdd", "ssd"],
                        help="Override drive type detection")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without making changes")
    parser.add_argument("--source-host", default="proxmox.home",
                        help="Source host for rsync network tests (default: proxmox.home)")
    parser.add_argument("--iperf-server", default="192.168.0.1",
                        help="iperf3 server for network tests (default: 192.168.0.1)")
    parser.add_argument("--iperf-port", type=int, default=42947,
                        help="iperf3 server port (default: 42947)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log_err("Must run as root")
        sys.exit(1)

    _config["iperf_server"] = args.iperf_server
    _config["iperf_port"] = args.iperf_port
    _config["source_host"] = args.source_host

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Detect block device and drive type
    dev = detect_block_device()
    if dev:
        drive_type = args.drive_type or detect_drive_type(dev)
        log_info(f"Block device: {dev} ({drive_type})")
    else:
        drive_type = args.drive_type or "hdd"
        dev = "sda"
        log_warn(f"Could not detect block device, assuming {dev} ({drive_type})")

    # Determine layers to run
    if args.layer:
        layers = [args.layer]
    else:
        layers = ["network", "write", "rsync"]

    log_info(f"Layers: {', '.join(layers)}")
    log_info(f"Sample: {args.sample}s per test")
    log_info(f"Drive:  {drive_type}")
    if args.dry_run:
        log_warn("DRY RUN — no changes will be made")
    print()

    all_results = []

    for layer in layers:
        results = run_layer(layer, dev, drive_type, args.sample, args.dry_run)
        all_results.extend(results)

    # Save results
    if not args.dry_run and all_results:
        _save_results(all_results)
        _print_summary(all_results)


def _save_results(results: list):
    """Append results to JSON log."""
    existing = []
    if RESULTS_LOG.exists():
        try:
            existing = json.loads(RESULTS_LOG.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    run_entry = {
        "timestamp": datetime.now().isoformat(),
        "results": results,
    }
    existing.append(run_entry)
    RESULTS_LOG.write_text(json.dumps(existing, indent=2))
    log_info(f"Results saved to {RESULTS_LOG}")


def _print_summary(results: list):
    """Print final summary table."""
    print()
    log_info("═══ SUMMARY ═══")
    print(f"  {'Parameter':<30} {'Metric':<12} {'Before':>8} {'After':>8} {'Change':>8} {'Action':<8}")
    print(f"  {'─'*30} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
    for r in results:
        if r.get("action") == "dry-run":
            continue
        name = r["name"][:30]
        metric = r.get("metric", "?")
        bv = r.get("before_value", "?")
        av = r.get("after_value", "?")
        ch = r.get("change_pct", 0)
        action = r.get("action", "?")
        colour = GREEN if action == "keep" else RED
        print(f"  {name:<30} {metric:<12} {bv:>8} {av:>8} {ch:>+7.1f}% {colour}{action:<8}{RESET}")
    print()


if __name__ == "__main__":
    main()
