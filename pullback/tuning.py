"""Tuning SSOT — single authority for all tuning params.

Knows how to:
- Map config.yaml keys to sysctl/sysfs paths
- Read current live values from the system
- Apply values to the system
- Report status
- Generate boot scripts

All other code (autotune.py, pi-tune-install.sh, pi-tune-status.sh,
engine.py) calls this module. No hardcoded tuning values elsewhere.
"""

import logging
import os
import subprocess
from pathlib import Path

import yaml


log = logging.getLogger("pullback")

TUNE_FILE = ".pullback-tune.yaml"

# ── Param registry ──────────────────────────────────────
# Each param: config key → how to apply, read, and revert.
# {dev} is replaced at runtime with the block device name.

PARAM_REGISTRY = [
    {
        "key": "dirty_ratio",
        "description": "VM dirty page ratio (% of RAM)",
        "type": "sysctl",
        "sysctl": "vm.dirty_ratio",
        "default": 20,
        "unit": "int",
    },
    {
        "key": "dirty_background_ratio",
        "description": "VM dirty background ratio (% of RAM)",
        "type": "sysctl",
        "sysctl": "vm.dirty_background_ratio",
        "default": 10,
        "unit": "int",
    },
    {
        "key": "dirty_expire_centisecs",
        "description": "Age before dirty pages eligible for writeback",
        "type": "sysctl",
        "sysctl": "vm.dirty_expire_centisecs",
        "default": 3000,
        "unit": "int",
    },
    {
        "key": "dirty_writeback_centisecs",
        "description": "Flusher thread wakeup interval",
        "type": "sysctl",
        "sysctl": "vm.dirty_writeback_centisecs",
        "default": 500,
        "unit": "int",
    },
    {
        "key": "bdi_max_bytes",
        "description": "Per-device dirty page cap (0=off)",
        "type": "bdi",
        "sysfs_max": "/sys/block/{dev}/bdi/max_bytes",
        "sysfs_strict": "/sys/block/{dev}/bdi/strict_limit",
        "default": 0,
        "unit": "bytes",
    },
    {
        "key": "rps_enabled",
        "description": "Receive Packet Steering (distribute NET_RX)",
        "type": "rps",
        "sysfs_cpus": "/sys/class/net/{iface}/queues/rx-0/rps_cpus",
        "sysfs_flow": "/proc/sys/net/core/rps_sock_flow_entries",
        "default": False,
        "unit": "bool",
    },
    {
        "key": "eee_off",
        "description": "Disable Energy Efficient Ethernet",
        "type": "eee",
        "default": False,
        "unit": "bool",
    },
    {
        "key": "cpu_governor",
        "description": "CPU frequency scaling governor",
        "type": "governor",
        "sysfs_pattern": "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor",
        "default": "ondemand",
        "unit": "str",
        "options": ["ondemand", "performance", "conservative", "powersave"],
    },
    {
        "key": "scheduler",
        "description": "I/O scheduler for backup device",
        "type": "sysfs",
        "sysfs": "/sys/block/{dev}/queue/scheduler",
        "default": "none",
        "unit": "str",
        "options": ["none", "mq-deadline", "kyber", "bfq"],
    },
    {
        "key": "nr_requests",
        "description": "I/O queue depth",
        "type": "sysfs",
        "sysfs": "/sys/block/{dev}/queue/nr_requests",
        "default": 1,
        "unit": "int",
    },
    {
        "key": "max_sectors_kb",
        "description": "Max I/O request size in KB",
        "type": "sysfs",
        "sysfs": "/sys/block/{dev}/queue/max_sectors_kb",
        "default": 256,
        "unit": "int",
    },
    {
        "key": "read_ahead_kb",
        "description": "Block device read-ahead in KB",
        "type": "sysfs",
        "sysfs": "/sys/block/{dev}/queue/read_ahead_kb",
        "default": 128,
        "unit": "int",
    },
    {
        "key": "tcp_slow_start_after_idle",
        "description": "TCP slow start after idle",
        "type": "sysctl",
        "sysctl": "net.ipv4.tcp_slow_start_after_idle",
        "default": 1,
        "unit": "int",
    },
    {
        "key": "rmem_max",
        "description": "Socket receive buffer max",
        "type": "sysctl",
        "sysctl": "net.core.rmem_max",
        "default": 212992,
        "unit": "bytes",
    },
    {
        "key": "wmem_max",
        "description": "Socket send buffer max",
        "type": "sysctl",
        "sysctl": "net.core.wmem_max",
        "default": 212992,
        "unit": "bytes",
    },
    {
        "key": "netdev_max_backlog",
        "description": "Network device backlog queue",
        "type": "sysctl",
        "sysctl": "net.core.netdev_max_backlog",
        "default": 1000,
        "unit": "int",
    },
]


def get_registry():
    """Return the param registry list."""
    return PARAM_REGISTRY


def get_param(key):
    """Look up a single param definition by config key."""
    for p in PARAM_REGISTRY:
        if p["key"] == key:
            return p
    return None


# ── Block device detection ──────────────────────────────


def block_device(mount_point="/backup"):
    """Get block device name (e.g. 'sda') for a mount point."""
    try:
        result = subprocess.run(
            ["df", mount_point], capture_output=True, text=True, timeout=5,
        )
        line = result.stdout.strip().split("\n")[-1]
        dev = line.split()[0]  # e.g. /dev/sda1
        name = os.path.basename(dev).rstrip("0123456789")
        return name if name else None
    except (subprocess.TimeoutExpired, IndexError):
        return None


# ── Drive tuning file ───────────────────────────────────


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


# ── Read live values ────────────────────────────────────


def read_live(mount_point="/backup", tuning_cfg=None):
    """Read all current tuning values from the system. Returns dict.

    tuning_cfg provides net_interface and other config-driven values.
    """
    dev = block_device(mount_point)
    iface = (tuning_cfg or {}).get("net_interface", "eth0")
    values = {}

    for p in PARAM_REGISTRY:
        key = p["key"]
        ptype = p["type"]

        if ptype == "sysctl":
            values[key] = _sysctl_get(p["sysctl"])
        elif ptype == "sysfs":
            path = p["sysfs"].replace("{dev}", dev or "sda")
            raw = _read_sysfs(path)
            if key == "scheduler" and raw and "[" in raw:
                import re
                m = re.search(r"\[(\S+)\]", raw)
                raw = m.group(1) if m else raw
            values[key] = raw
        elif ptype == "bdi":
            if dev:
                strict = _read_sysfs(p["sysfs_strict"].replace("{dev}", dev))
                max_b = _read_sysfs(p["sysfs_max"].replace("{dev}", dev))
                if strict == "1" or strict == 1:
                    values[key] = int(max_b) if max_b else 0
                else:
                    values[key] = 0
            else:
                values[key] = 0
        elif ptype == "rps":
            cpus_path = p["sysfs_cpus"].replace("{iface}", iface)
            cpus = _read_sysfs(cpus_path)
            values[key] = cpus not in ("0", "00000000", "0000", None)
        elif ptype == "eee":
            values[key] = _eee_is_off(iface)
        elif ptype == "governor":
            gov_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
            values[key] = _read_sysfs(gov_path)

    return values


# ── Apply values ────────────────────────────────────────


def apply_tuning(mount_point, cfg):
    """Apply tuning from config to the system.

    Called at sync start when /backup is mounted.
    Config already includes drive overrides (merged by config.py).
    """
    tuning = cfg.get("tuning", {})
    applied = apply_values(tuning, mount_point)

    if applied:
        log.info(f"Tuning applied: {', '.join(applied)}")

    return tuning


def apply_values(tuning, mount_point="/backup"):
    """Apply a tuning dict to the system. Returns list of applied descriptions.

    Reads net_interface, rps_cpus, rps_flow_entries from the tuning dict.
    """
    dev = block_device(mount_point)
    iface = tuning.get("net_interface", "eth0")
    rps_cpus = tuning.get("rps_cpus", "c")
    rps_flow = str(tuning.get("rps_flow_entries", 32768))
    applied = []

    for p in PARAM_REGISTRY:
        key = p["key"]
        val = tuning.get(key)
        if val is None:
            continue

        ptype = p["type"]

        if ptype == "sysctl":
            _sysctl_set(p["sysctl"], str(val))
            applied.append(f"{p['sysctl']}={val}")

        elif ptype == "sysfs" and dev:
            path = p["sysfs"].replace("{dev}", dev)
            _write_sysfs(path, str(val))
            applied.append(f"{key}={val}")

        elif ptype == "bdi" and dev:
            strict_path = p["sysfs_strict"].replace("{dev}", dev)
            max_path = p["sysfs_max"].replace("{dev}", dev)
            if val and int(val) > 0:
                _write_sysfs(strict_path, "1")
                _write_sysfs(max_path, str(val))
                applied.append(f"BDI strict_limit=1 max_bytes={val} on {dev}")
            else:
                _write_sysfs(strict_path, "0")
                _write_sysfs(max_path, "0")
                applied.append(f"BDI off on {dev}")

        elif ptype == "rps":
            cpus_path = p["sysfs_cpus"].replace("{iface}", iface)
            flow_path = p["sysfs_flow"]
            if val:
                _write_sysfs(cpus_path, rps_cpus)
                _write_sysfs(flow_path, rps_flow)
                applied.append(f"RPS={rps_cpus}")
            else:
                _write_sysfs(cpus_path, "0")
                _write_sysfs(flow_path, "0")
                applied.append("RPS=off")

        elif ptype == "eee":
            if val:
                _ethtool_eee("off", iface)
                applied.append("EEE=off")
            else:
                _ethtool_eee("on", iface)
                applied.append("EEE=on")

        elif ptype == "governor":
            for gov_path in Path("/sys/devices/system/cpu").glob(
                "cpu*/cpufreq/scaling_governor"
            ):
                _write_sysfs(str(gov_path), str(val))
            applied.append(f"governor={val}")

    return applied


def apply_defaults(mount_point="/backup"):
    """Reset all params to OS defaults."""
    defaults = {p["key"]: p["default"] for p in PARAM_REGISTRY}
    return apply_values(defaults, mount_point)


# ── Status report ───────────────────────────────────────


def status_report(mount_point="/backup", tuning_cfg=None):
    """Return a formatted status string of all tuning params."""
    dev = block_device(mount_point) or "sda"
    live = read_live(mount_point, tuning_cfg)

    lines = []
    lines.append("=== Tuning Status ===")
    for p in PARAM_REGISTRY:
        key = p["key"]
        val = live.get(key, "?")
        default = p["default"]
        marker = "" if str(val) == str(default) else " *"
        lines.append(f"  {key:<30} = {val}{marker}")

    dirty_kb = _read_meminfo("Dirty")
    wb_kb = _read_meminfo("Writeback")
    lines.append("")
    lines.append("=== Live ===")
    lines.append(f"  Dirty:     {dirty_kb // 1024 if dirty_kb else '?'} MB")
    lines.append(f"  Writeback: {wb_kb // 1024 if wb_kb else '?'} MB")

    return "\n".join(lines)


def status_yaml(mount_point="/backup", tuning_cfg=None):
    """Return current live values as YAML (config.yaml tuning format)."""
    live = read_live(mount_point, tuning_cfg)
    lines = ["tuning:"]
    for p in PARAM_REGISTRY:
        key = p["key"]
        val = live.get(key)
        if val is None:
            val = p["default"]
        if isinstance(val, bool):
            val = "true" if val else "false"
        lines.append(f"  {key}: {val}")
    return "\n".join(lines)


# ── Boot script generation ──────────────────────────────


def generate_sysctl_conf(tuning):
    """Generate /etc/sysctl.d/99-pullback.conf content from tuning dict."""
    lines = ["# pullback performance tuning — generated by tuning.py"]
    for p in PARAM_REGISTRY:
        if p["type"] == "sysctl":
            val = tuning.get(p["key"])
            if val is not None:
                lines.append(f"{p['sysctl']} = {val}")
    return "\n".join(lines) + "\n"


def generate_boot_script(tuning):
    """Generate pi-tune-boot.sh content from tuning dict."""
    lines = [
        "#!/bin/bash",
        "# pullback-tune boot script — generated by tuning.py",
        "# Applied on every boot via systemd.",
        "",
        'TAG="pullback-tune"',
        'log() { logger -t "$TAG" "$*"; }',
        "",
    ]

    # RPS
    iface = tuning.get("net_interface", "eth0")
    rps_cpus = tuning.get("rps_cpus", "c")
    rps_flow = tuning.get("rps_flow_entries", 32768)

    if tuning.get("rps_enabled"):
        lines.extend([
            "# RPS: distribute network softirqs",
            f"if [[ -f /sys/class/net/{iface}/queues/rx-0/rps_cpus ]]; then",
            f"    echo {rps_cpus} > /sys/class/net/{iface}/queues/rx-0/rps_cpus",
            f'    log "RPS enabled (cpus={rps_cpus})"',
            "fi",
            "if [[ -f /proc/sys/net/core/rps_sock_flow_entries ]]; then",
            f"    echo {rps_flow} > /proc/sys/net/core/rps_sock_flow_entries",
            '    log "RPS flow entries set"',
            "fi",
        ])

    # EEE
    if tuning.get("eee_off"):
        lines.extend([
            "# Disable EEE (Energy Efficient Ethernet)",
            "if command -v ethtool &>/dev/null; then",
            f"    ethtool --set-eee {iface} eee off 2>/dev/null && \\",
            '        log "EEE disabled" || \\',
            '        log "EEE not supported (ok)"',
            "fi",
        ])

    # Governor
    governor = tuning.get("cpu_governor")
    if governor:
        lines.extend([
            "# CPU governor",
            "for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do",
            '    if [[ -f "$gov" ]]; then',
            f'        echo {governor} > "$gov"',
            "    fi",
            "done",
            f'log "CPU governor set to {governor}"',
        ])

    # BDI
    bdi = tuning.get("bdi_max_bytes", 0)
    if bdi and int(bdi) > 0:
        lines.extend([
            "# BDI: per-device dirty page limit for backup drive",
            'MOUNT_POINT=$(awk \'/\\/backup/{print $1}\' /proc/mounts | head -1)',
            'if [[ -n "$MOUNT_POINT" ]]; then',
            '    BDI_DEV=$(basename "$MOUNT_POINT" | sed \'s/[0-9]*$//\')',
            '    if [[ -f "/sys/block/${BDI_DEV}/bdi/strict_limit" ]]; then',
            "        echo 1 > /sys/block/${BDI_DEV}/bdi/strict_limit",
            f"        echo {bdi} > /sys/block/${{BDI_DEV}}/bdi/max_bytes",
            f'        log "BDI strict_limit=1 max_bytes={bdi} on ${{BDI_DEV}}"',
            "    else",
            '        log "BDI sysfs not available for ${BDI_DEV}"',
            "    fi",
            "else",
            '    log "BDI: /backup not mounted, skipping"',
            "fi",
        ])

    # Scheduler
    scheduler = tuning.get("scheduler")
    if scheduler and scheduler != "none":
        lines.extend([
            "# I/O scheduler for backup device",
            'MOUNT_POINT=$(awk \'/\\/backup/{print $1}\' /proc/mounts | head -1)',
            'if [[ -n "$MOUNT_POINT" ]]; then',
            '    SCHED_DEV=$(basename "$MOUNT_POINT" | sed \'s/[0-9]*$//\')',
            f'    echo {scheduler} > /sys/block/${{SCHED_DEV}}/queue/scheduler 2>/dev/null',
            f'    log "Scheduler set to {scheduler} on ${{SCHED_DEV}}"',
            "fi",
        ])

    # nr_requests
    nr = tuning.get("nr_requests")
    if nr and int(nr) > 2:
        lines.extend([
            "# I/O queue depth",
            'MOUNT_POINT=$(awk \'/\\/backup/{print $1}\' /proc/mounts | head -1)',
            'if [[ -n "$MOUNT_POINT" ]]; then',
            '    NR_DEV=$(basename "$MOUNT_POINT" | sed \'s/[0-9]*$//\')',
            f'    echo {nr} > /sys/block/${{NR_DEV}}/queue/nr_requests 2>/dev/null',
            f'    log "nr_requests set to {nr} on ${{NR_DEV}}"',
            "fi",
        ])

    # max_sectors_kb
    max_sec = tuning.get("max_sectors_kb")
    if max_sec and int(max_sec) > 256:
        lines.extend([
            "# Max I/O request size",
            'MOUNT_POINT=$(awk \'/\\/backup/{print $1}\' /proc/mounts | head -1)',
            'if [[ -n "$MOUNT_POINT" ]]; then',
            '    MS_DEV=$(basename "$MOUNT_POINT" | sed \'s/[0-9]*$//\')',
            f'    echo {max_sec} > /sys/block/${{MS_DEV}}/queue/max_sectors_kb 2>/dev/null',
            f'    log "max_sectors_kb set to {max_sec} on ${{MS_DEV}}"',
            "fi",
        ])

    return "\n".join(lines) + "\n"


# ── Low-level helpers ───────────────────────────────────


def _write_sysfs(path, value):
    """Write a value to a sysfs/proc file. Silently ignore errors."""
    try:
        with open(path, "w") as f:
            f.write(value)
    except (OSError, IOError):
        pass


def _read_sysfs(path):
    """Read a sysfs/proc file. Returns string or None."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _sysctl_set(key, value):
    """Set a sysctl value."""
    try:
        subprocess.run(
            ["sysctl", "-w", f"{key}={value}"],
            capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _sysctl_get(key):
    """Read a sysctl value."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _ethtool_eee(state, iface="eth0"):
    """Set EEE on or off."""
    try:
        subprocess.run(
            ["ethtool", "--set-eee", iface, "eee", state],
            capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _eee_is_off(iface="eth0"):
    """Check if EEE is disabled."""
    try:
        r = subprocess.run(
            ["ethtool", "--show-eee", iface],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            if "EEE status:" in line:
                return "disabled" in line.lower()
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _read_meminfo(field):
    """Read a field from /proc/meminfo in KB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(f"{field}:"):
                    return int(line.split()[1])
    except (IOError, ValueError):
        pass
    return None


# ── CLI ─────────────────────────────────────────────────


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        print(status_report())
    elif cmd == "yaml":
        print(status_yaml())
    elif cmd == "apply":
        from config import load_config
        cfg = load_config()
        applied = apply_tuning("/backup", cfg)
        print(f"Applied {len(applied)} params")
    elif cmd == "defaults":
        applied = apply_defaults()
        print(f"Reset {len(applied)} params to defaults")
    else:
        print(f"Usage: {sys.argv[0]} [status|yaml|apply|defaults]")
        sys.exit(1)
