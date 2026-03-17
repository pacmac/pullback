"""Apply per-drive tuning from .pullback-tune.yaml on the backup volume."""

import logging
import os
import subprocess
from pathlib import Path

import yaml


log = logging.getLogger("pullback")

TUNE_FILE = ".pullback-tune.yaml"


def load_drive_tuning(mount_point):
    """Read .pullback-tune.yaml from the backup volume.

    Returns the tuning dict, or empty dict if file doesn't exist.
    """
    tune_path = Path(mount_point) / TUNE_FILE
    if not tune_path.exists():
        return {}

    with open(tune_path) as f:
        data = yaml.safe_load(f)

    if not data or "tuning" not in data:
        return {}

    return data["tuning"]


def merge_tuning(cfg_tuning, drive_tuning):
    """Merge drive tuning over config.yaml defaults. Drive wins."""
    merged = dict(cfg_tuning)
    merged.update(drive_tuning)
    return merged


def apply_tuning(mount_point, cfg):
    """Read drive tuning, merge with config defaults, apply to system.

    Called at sync start when /backup is mounted.
    """
    cfg_tuning = cfg.get("tuning", {})
    drive_tuning = load_drive_tuning(mount_point)

    if drive_tuning:
        log.info(f"Drive tuning loaded from {mount_point}/{TUNE_FILE}")
        tuning = merge_tuning(cfg_tuning, drive_tuning)
    else:
        log.info("No drive tuning file, using config.yaml defaults")
        tuning = cfg_tuning

    applied = []

    # BDI per-device dirty limit
    bdi_max = tuning.get("bdi_max_bytes", 0)
    if bdi_max and bdi_max > 0:
        dev = _block_device(mount_point)
        if dev:
            strict_path = f"/sys/block/{dev}/bdi/strict_limit"
            max_path = f"/sys/block/{dev}/bdi/max_bytes"
            if os.path.exists(strict_path):
                _write_sysfs(strict_path, "1")
                _write_sysfs(max_path, str(bdi_max))
                applied.append(f"BDI strict_limit=1 max_bytes={bdi_max} on {dev}")

    # Sysctl dirty page settings
    sysctl_map = {
        "dirty_ratio": "vm.dirty_ratio",
        "dirty_background_ratio": "vm.dirty_background_ratio",
        "dirty_expire_centisecs": "vm.dirty_expire_centisecs",
        "dirty_writeback_centisecs": "vm.dirty_writeback_centisecs",
    }
    for key, sysctl_key in sysctl_map.items():
        val = tuning.get(key)
        if val is not None:
            _sysctl_set(sysctl_key, str(val))
            applied.append(f"{sysctl_key}={val}")

    # CPU governor
    governor = tuning.get("cpu_governor")
    if governor:
        for gov_path in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor"):
            _write_sysfs(str(gov_path), governor)
        applied.append(f"governor={governor}")

    # RPS
    rps = tuning.get("rps_enabled")
    if rps:
        _write_sysfs("/sys/class/net/eth0/queues/rx-0/rps_cpus", "c")
        _write_sysfs("/proc/sys/net/core/rps_sock_flow_entries", "32768")
        applied.append("RPS=CPU2+3")
    elif rps is False:
        _write_sysfs("/sys/class/net/eth0/queues/rx-0/rps_cpus", "0")
        _write_sysfs("/proc/sys/net/core/rps_sock_flow_entries", "0")
        applied.append("RPS=off")

    # EEE
    eee_off = tuning.get("eee_off")
    if eee_off:
        try:
            subprocess.run(
                ["ethtool", "--set-eee", "eth0", "eee", "off"],
                capture_output=True, timeout=5,
            )
            applied.append("EEE=off")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if applied:
        log.info(f"Tuning applied: {', '.join(applied)}")

    return tuning


def _block_device(mount_point):
    """Get block device name (e.g. 'sda') for a mount point."""
    try:
        result = subprocess.run(
            ["df", mount_point], capture_output=True, text=True, timeout=5,
        )
        line = result.stdout.strip().split("\n")[-1]
        dev = line.split()[0]  # e.g. /dev/sda
        name = os.path.basename(dev).rstrip("0123456789")
        return name if name else None
    except (subprocess.TimeoutExpired, IndexError):
        return None


def _write_sysfs(path, value):
    """Write a value to a sysfs/proc file. Silently ignore errors."""
    try:
        with open(path, "w") as f:
            f.write(value)
    except (OSError, IOError):
        pass


def _sysctl_set(key, value):
    """Set a sysctl value."""
    try:
        subprocess.run(
            ["sysctl", "-w", f"{key}={value}"],
            capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
