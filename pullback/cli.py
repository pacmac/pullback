"""pullback CLI — sync, status, cancel, config, tune."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from config import load_config
from state import load_state, get_progress, request_cancel
import tuning

# ── Colours ──────────────────────────────────────────────

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"

PROJECT_DIR = Path(__file__).resolve().parent


def _log(msg, colour=""):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{colour}[{ts}] {msg}{RESET}")


def _log_info(msg): _log(msg, CYAN)
def _log_ok(msg): _log(msg, GREEN)
def _log_warn(msg): _log(msg, YELLOW)


def _require_root():
    if os.geteuid() != 0:
        print("Error: must run as root", file=sys.stderr)
        sys.exit(1)


def _run(cmd, timeout=10):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def cmd_sync(args):
    """Run sync with live console progress."""
    from engine import run_all, _setup_logging

    cfg = load_config(args.config)
    _setup_logging(cfg)

    if args.folder and not args.source:
        print("Error: --folder requires --source", file=sys.stderr)
        sys.exit(1)

    ok = run_all(cfg, source_filter=args.source, folder_filter=args.folder)
    sys.exit(0 if ok else 1)


def cmd_status(args):
    """Show current state and progress."""
    cfg = load_config(args.config)

    sources = [args.source] if args.source else list(cfg["sources"].keys())

    for name in sources:
        state = load_state(name)
        progress = get_progress(name)

        print(f"=== {name} ===")
        print(f"  Last run:      {state.get('last_run_started_at', 'never')}")
        print(f"  Success:       {state.get('last_run_success', 'n/a')}")
        print(f"  Duration:      {state.get('last_sync_duration', 0)}s")
        print(f"  Last error:    {state.get('last_error', 'none')}")

        if progress:
            print(f"  ** RUNNING: {progress.get('step', '?')} "
                  f"{progress.get('overall_pct', 0)}% "
                  f"{progress.get('speed', '')} "
                  f"ETA {progress.get('eta', '?')}")

        folders = state.get("folders", {})
        if folders:
            print(f"  Folders:")
            for fpath, fstate in folders.items():
                ok = "OK" if fstate.get("success") else "FAIL"
                ts = fstate.get("last_synced_at", "?")
                err = fstate.get("error", "")
                line = f"    {fpath}: {ok} ({ts})"
                if err:
                    line += f" — {err}"
                print(line)
        print()


def cmd_cancel(args):
    """Request cancellation of a running sync."""
    if not args.source:
        print("Error: --source is required", file=sys.stderr)
        sys.exit(1)

    request_cancel(args.source)
    print(f"Cancel requested for '{args.source}'")


# ── Tune commands ────────────────────────────────────────


def cmd_tune(args):
    """Route tune subcommands."""
    tune_cmds = {
        "status": cmd_tune_status,
        "apply": cmd_tune_apply,
        "defaults": cmd_tune_defaults,
        "install": cmd_tune_install,
        "capture": cmd_tune_capture,
        "autotune": cmd_tune_autotune,
    }
    if args.tune_command in tune_cmds:
        tune_cmds[args.tune_command](args)
    else:
        print("Usage: pullback tune {status|apply|defaults|install|capture|autotune}")
        sys.exit(1)


def cmd_tune_status(args):
    print(tuning.status_yaml())


def cmd_tune_apply(args):
    _require_root()
    cfg = load_config(args.config)
    tuning.apply_tuning(cfg.get("mount_point", "/backup"), cfg)
    print(tuning.status_yaml())


def cmd_tune_defaults(args):
    _require_root()
    applied = tuning.apply_defaults()
    _log_ok(f"Reset {len(applied)} params to OS defaults")
    print(tuning.status_yaml())


def cmd_tune_install(args):
    _require_root()
    cfg = load_config(args.config)
    t = cfg.get("tuning", {})

    scripts_dir = PROJECT_DIR / "scripts"
    sysctl_dst = Path("/etc/sysctl.d/99-pullback.conf")
    tune_script = scripts_dir / "pi-tune-boot.sh"
    service_name = "pullback-tune"
    service_dst = Path(f"/etc/systemd/system/{service_name}.service")

    print("\n=== Config (merged) ===")
    print(tuning.status_yaml())
    print()

    # sysctl.conf
    sysctl_dst.write_text(tuning.generate_sysctl_conf(t))
    subprocess.run(["sysctl", "--load", str(sysctl_dst)],
                   capture_output=True, timeout=5)
    print(f"Installed: {sysctl_dst}")

    # Boot script
    tune_script.write_text(tuning.generate_boot_script(t))
    tune_script.chmod(0o755)
    print(f"Generated: {tune_script}")

    # UAS
    if cfg.get("usb", {}).get("uas"):
        _install_uas()

    # systemd service
    service_dst.write_text(f"""[Unit]
Description=pullback performance tuning
After=network.target

[Service]
Type=oneshot
ExecStart={tune_script}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
""")
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
    subprocess.run(["systemctl", "enable", service_name], capture_output=True, timeout=10)
    subprocess.run(["systemctl", "restart", service_name], capture_output=True, timeout=10)
    print(f"Installed: {service_dst}")

    print("\n=== Applied ===")
    print(tuning.status_yaml())


def _install_uas():
    """Force UAS for backup USB drive if supported."""
    for cmdline_path in ["/boot/firmware/cmdline.txt", "/boot/cmdline.txt"]:
        if os.path.exists(cmdline_path):
            break
    else:
        print("UAS: cmdline.txt not found")
        return

    try:
        lsusb = _run("lsusb")
    except subprocess.TimeoutExpired:
        return

    usb_id = ""
    for pattern in [r"mass storage|external|canvio|seagate|wd|toshiba|hitachi|backup", r"Bus 002"]:
        for line in lsusb.splitlines():
            if re.search(pattern, line, re.IGNORECASE) and "root hub" not in line:
                m = re.search(r"\b([0-9a-f]{4}:[0-9a-f]{4})\b", line)
                if m:
                    usb_id = m.group(1)
                    break
        if usb_id:
            break

    if not usb_id:
        print("UAS: no USB storage device detected")
        return

    try:
        detail = _run(f"lsusb -v -d {usb_id} 2>/dev/null")
    except subprocess.TimeoutExpired:
        return

    proto = ""
    for line in detail.splitlines():
        if "bInterfaceProtocol" in line:
            proto = line.strip().split()[-1]
            break

    cmdline = Path(cmdline_path).read_text()
    cmdline = re.sub(r" usb-storage\.quirks=[0-9a-f:]+:u", "", cmdline)

    if proto == "Bulk-Only":
        print(f"UAS: {usb_id} Bulk-Only — not supported")
        Path(cmdline_path).write_text(cmdline)
    else:
        quirk = f"usb-storage.quirks={usb_id}:u"
        cmdline = cmdline.replace("rootwait", f"rootwait {quirk}")
        Path(cmdline_path).write_text(cmdline)
        print(f"UAS: enabled for {usb_id} — REBOOT REQUIRED")


def cmd_tune_capture(args):
    output = PROJECT_DIR / "docs" / "TUNEDEFAULT.local.yaml"

    if output.exists() and not getattr(args, "force", False):
        print(f"Error: {output} already exists — use --force to overwrite", file=sys.stderr)
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    hostname = _run("hostname") or "unknown"

    content = (
        f"# Tuning defaults — captured from {hostname} on {datetime.now().isoformat()}\n"
        f"# Run BEFORE applying any tuning. Used to revert to OS defaults.\n\n"
        f"{tuning.status_yaml()}\n"
    )
    output.write_text(content)
    _log_ok(f"Defaults captured to {output}")


# ── Autotune sweep definitions ───────────────────────────
# Order: BDI first, dirty ratios, flush timing, scheduler last.

_WRITE_SWEEP = [
    {
        "name": "bdi_max_bytes",
        "description": "Per-device dirty page cap",
        "values": [41943040, 62914560, 83886080, 104857600, 125829120],
        "default": 0,
        "apply": lambda v, dev: (
            _run(f"echo 1 > /sys/block/{dev}/bdi/strict_limit") if v > 0
            else _run(f"echo 0 > /sys/block/{dev}/bdi/strict_limit"),
            _run(f"echo {v} > /sys/block/{dev}/bdi/max_bytes"),
        ),
        "drive_type": "hdd",
    },
    {
        "name": "dirty_ratio/bg_ratio",
        "description": "Dirty page ratio pair",
        "values": [(5, 2), (10, 3), (15, 5), (20, 5)],
        "default": (20, 10),
        "apply": lambda v, dev: _run(
            f"sysctl -w vm.dirty_ratio={v[0]} vm.dirty_background_ratio={v[1]} > /dev/null"
        ),
    },
    {
        "name": "dirty_expire_centisecs",
        "description": "Age before dirty pages eligible for writeback",
        "values": [500, 1000, 1500, 2000],
        "default": 3000,
        "apply": lambda v, dev: _run(f"sysctl -w vm.dirty_expire_centisecs={v} > /dev/null"),
    },
    {
        "name": "dirty_writeback_centisecs",
        "description": "Flusher thread wakeup interval",
        "values": [100, 200, 300],
        "default": 500,
        "apply": lambda v, dev: _run(f"sysctl -w vm.dirty_writeback_centisecs={v} > /dev/null"),
    },
    {
        "name": "scheduler",
        "description": "I/O scheduler",
        "values": ["mq-deadline", "bfq"],
        "default": "none",
        "apply": lambda v, dev: _run(f"echo {v} > /sys/block/{dev}/queue/scheduler"),
        "drive_type": "hdd",
    },
]


def _read_dirty_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Dirty:"):
                    return int(line.split()[1]) // 1024
    except (IOError, ValueError):
        pass
    return None


def _dd_measure(mount_point):
    test_file = f"{mount_point}/.autotune_write_test"
    try:
        os.remove(test_file)
    except OSError:
        pass

    dd_proc = subprocess.Popen(
        f"dd if=/dev/zero of={test_file} bs=1M count=2048 conv=fdatasync",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )

    dirty_samples = []
    while dd_proc.poll() is None:
        d = _read_dirty_mb()
        if d is not None:
            dirty_samples.append(d)
        time.sleep(1)

    dd_stderr = dd_proc.stderr.read()
    speed = 0
    m = re.search(r"([\d.]+)\s+MB/s", dd_stderr)
    if m:
        speed = int(float(m.group(1)))

    try:
        os.remove(test_file)
    except OSError:
        pass

    result = {"disk_avg": speed}
    if dirty_samples:
        result["dirty_avg"] = sum(dirty_samples) // len(dirty_samples)
        result["dirty_max"] = max(dirty_samples)
    return result


def _val_str(val):
    if isinstance(val, tuple):
        return f"ratio={val[0]}/bg={val[1]}"
    elif isinstance(val, int) and val > 10000:
        return f"{val // (1024*1024)}MB"
    return str(val)


def cmd_tune_autotune(args):
    _require_root()
    cfg = load_config(args.config)
    mount_point = cfg.get("mount_point", "/backup")

    dev = tuning.block_device(mount_point)
    if not dev:
        print("Error: cannot detect block device", file=sys.stderr)
        sys.exit(1)

    rot = tuning._read_sysfs(f"/sys/block/{dev}/queue/rotational")
    drive_type = "hdd" if rot == "1" else "ssd"
    _log_info(f"Block device: {dev} ({drive_type})")

    params = [p for p in _WRITE_SWEEP if p.get("drive_type", "both") in (drive_type, "both")]
    total = sum(len(p["values"]) for p in params)
    _log_info(f"╔══ AUTOTUNE: {len(params)} params, {total} values ══╗")

    if getattr(args, "dry_run", False):
        for p in params:
            _log(f"  {p['name']}: {[_val_str(v) for v in p['values']]} (default={_val_str(p['default'])})")
        _log_info("╚══ DRY RUN ══╝")
        return

    # Reset all
    _log_info("Resetting all write params to defaults...")
    for p in params:
        p["apply"](p["default"], dev)
    _log_ok("All defaults applied")
    print()

    # Baseline
    _log_info("━━━ Baseline ━━━")
    bl = _dd_measure(mount_point)
    current_speed = bl.get("disk_avg", 0)
    _log(f"    {current_speed} MB/s, dirty avg={bl.get('dirty_avg','?')} max={bl.get('dirty_max','?')}")
    print()

    results = []
    for p in params:
        name = p["name"]
        _log_info(f"━━━ Sweeping: {name} ━━━")
        _log(f"    {p['description']}")

        best_speed = current_speed
        best_value = p["default"]
        best_da = best_dm = None

        for val in p["values"]:
            p["apply"](val, dev)
            m = _dd_measure(mount_point)
            spd = m.get("disk_avg", 0)
            da = m.get("dirty_avg", "?")
            dm = m.get("dirty_max", "?")
            marker = ""
            if spd > best_speed:
                best_speed = spd
                best_value = val
                best_da = da
                best_dm = dm
                marker = f" {GREEN}◀ best{RESET}"
            _log(f"    {_val_str(val):>20} → {spd:>4} MB/s  dirty avg={da} max={dm}{marker}")

        p["apply"](best_value, dev)
        if best_value != p["default"]:
            _log_ok(f"    ✓ BEST: {_val_str(best_value)} ({best_speed} MB/s)")
        else:
            _log_warn(f"    ✗ DEFAULT kept ({best_speed} MB/s)")

        current_speed = best_speed
        results.append({"name": name, "best": _val_str(best_value), "speed": best_speed,
                        "dirty_max": best_dm, "kept": best_value != p["default"]})
        print()

    # Final
    _log_info("━━━ Final confirmation ━━━")
    f = _dd_measure(mount_point)
    _log_ok(f"    FINAL: {f.get('disk_avg','?')} MB/s, dirty avg={f.get('dirty_avg','?')} max={f.get('dirty_max','?')} MB")
    dm = f.get("dirty_max")
    if isinstance(dm, int) and dm < 80:
        _log_ok(f"    ✓ dirty max {dm} MB < 80 MB target")
    elif isinstance(dm, int):
        _log_warn(f"    ✗ dirty max {dm} MB >= 80 MB target")

    print()
    _log_info("═══ RESULTS ═══")
    print(f"  {'Param':<30} {'Best':<20} {'Speed':>8} {'Dirty':>10} {'Kept':<6}")
    print(f"  {'─'*30} {'─'*20} {'─'*8} {'─'*10} {'─'*6}")
    for r in results:
        c = GREEN if r["kept"] else YELLOW
        print(f"  {r['name']:<30} {r['best']:<20} {r['speed']:>7} {r['dirty_max'] or '?':>10} {c}{'yes' if r['kept'] else 'no':<6}{RESET}")
    print()
    print(tuning.status_yaml())


# ── config command ───────────────────────────────────────


def cmd_config(args):
    """Validate and display config."""
    try:
        cfg = load_config(args.config)
        if getattr(args, "dump", False):
            print(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
        else:
            print(json.dumps(cfg, indent=2))
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="pullback backup CLI")
    parser.add_argument("--config", default=None, help="Path to config.yaml")

    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Run sync")
    p_sync.add_argument("--source", default=None)
    p_sync.add_argument("--folder", default=None)

    p_status = sub.add_parser("status", help="Show status")
    p_status.add_argument("--source", default=None)

    p_cancel = sub.add_parser("cancel", help="Cancel running sync")
    p_cancel.add_argument("--source", required=True)

    p_config = sub.add_parser("config", help="Show loaded config")
    p_config.add_argument("--dump", action="store_true", help="Output as YAML")

    # Tune subcommands
    p_tune = sub.add_parser("tune", help="Tuning commands")
    tune_sub = p_tune.add_subparsers(dest="tune_command")
    tune_sub.add_parser("status", help="Show current tuning as YAML")
    tune_sub.add_parser("apply", help="Apply config tuning to system")
    tune_sub.add_parser("defaults", help="Revert all to OS defaults")
    tune_sub.add_parser("install", help="Persist tuning to sysctl + systemd")
    tc = tune_sub.add_parser("capture", help="Capture OS defaults to file")
    tc.add_argument("--force", action="store_true")
    ta = tune_sub.add_parser("autotune", help="Sweep write params")
    ta.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "sync": cmd_sync,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "config": cmd_config,
        "tune": cmd_tune,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
