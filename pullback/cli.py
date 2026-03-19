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
from monitor import Monitor
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

    # Reset live values
    applied = tuning.apply_defaults()
    _log_ok(f"Reset {len(applied)} params to OS defaults")

    # Remove persisted tuning so defaults survive reboot
    sysctl_conf = Path("/etc/sysctl.d/99-pullback.conf")
    service_name = "pullback-tune"
    service_dst = Path(f"/etc/systemd/system/{service_name}.service")
    boot_script = PROJECT_DIR / "scripts" / "pi-tune-boot.sh"

    if sysctl_conf.exists():
        sysctl_conf.unlink()
        _log_ok(f"Removed {sysctl_conf}")

    if service_dst.exists():
        subprocess.run(["systemctl", "disable", service_name], capture_output=True, timeout=10)
        service_dst.unlink()
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
        _log_ok(f"Disabled and removed {service_dst}")

    if boot_script.exists():
        boot_script.unlink()
        _log_ok(f"Removed {boot_script}")

    print(tuning.status_yaml())


def cmd_tune_install(args):
    """Remove boot-time tuning (sysctl.d, systemd service). Tuning is applied at sync start only."""
    _require_root()

    sysctl_conf = Path("/etc/sysctl.d/99-pullback.conf")
    service_name = "pullback-tune"
    service_dst = Path(f"/etc/systemd/system/{service_name}.service")
    boot_script = PROJECT_DIR / "scripts" / "pi-tune-boot.sh"

    removed = []
    if sysctl_conf.exists():
        sysctl_conf.unlink()
        removed.append(str(sysctl_conf))

    if service_dst.exists():
        subprocess.run(["systemctl", "stop", service_name], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "disable", service_name], capture_output=True, timeout=10)
        service_dst.unlink()
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
        removed.append(str(service_dst))

    if boot_script.exists():
        boot_script.unlink()
        removed.append(str(boot_script))

    if removed:
        print(f"Removed boot-time tuning:")
        for r in removed:
            print(f"  {r}")
        print("\nTuning will be applied at sync start from config.yaml only.")
    else:
        print("No boot-time tuning found.")

    # UAS (kernel cmdline, not runtime — keep this)
    cfg = load_config(args.config)
    if cfg.get("usb", {}).get("uas"):
        _install_uas()


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


# ── Autotune ─────────────────────────────────────────────
# All sweep ranges come from cfg["autotune"]["disk"|"network"|"rsync"].
# Apply/revert uses tuning.apply_values(). No hardcoded values.

# Sweep order for disk layer — params tested in this sequence.
# Each entry maps a config key to how it applies. dirty_ratio_pairs
# is special (applies two params at once).
_DISK_SWEEP_ORDER = [
    "bdi_max_bytes",
    "dirty_ratio_pairs",
    "dirty_expire_centisecs",
    "dirty_writeback_centisecs",
    "scheduler",
    "nr_requests",
    "max_sectors_kb",
    "read_ahead_kb",
]


def _apply_sweep_value(key, val, dev, mount_point, tuning_cfg):
    """Apply a single sweep value using tuning.apply_values."""
    if key == "dirty_ratio_pairs":
        tuning.apply_values({"dirty_ratio": val[0], "dirty_background_ratio": val[1]}, mount_point)
    else:
        tuning.apply_values({key: val}, mount_point)


def _val_str(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return f"ratio={val[0]}/bg={val[1]}"
    elif isinstance(val, int) and val >= 1048576:  # >= 1MB
        return f"{val // (1024*1024)}MB"
    return str(val)


def _net_measure(cfg, seconds=15):
    """Measure network receive throughput by rsyncing from source to tmpfs.

    Pulls data from the configured source host into /dev/shm to eliminate
    disk as a variable. Measures rx_bytes over the transfer.
    """
    tmpfs_dir = "/dev/shm/autotune_net_test"
    os.makedirs(tmpfs_dir, exist_ok=True)

    # Get source from config
    sources = cfg.get("sources", {})
    if not sources:
        _log_warn("No sources configured")
        return {"net_avg": 0}

    source_name = list(sources.keys())[0]
    source = sources[source_name]
    host = source.get("host", "")
    remote_root = source.get("remote_root", "/")
    transport = source.get("transport", "ssh")
    folders = source.get("folders", [])
    folder_path = folders[0]["path"] if folders else ""

    # Build rsync command to tmpfs
    if transport == "rsync":
        module = source.get("rsync_module", "backup")
        rsync_cmd = f"rsync --archive --numeric-ids --partial {host}::{module}/{folder_path}/ {tmpfs_dir}/ >/dev/null 2>&1"
    else:
        ssh_cfg = cfg.get("ssh", {})
        key = ssh_cfg.get("key", "")
        cipher = ssh_cfg.get("cipher", "aes128-gcm@openssh.com")
        rsync_cmd = (
            f"rsync --archive --numeric-ids --partial "
            f"-e 'ssh -i {key} -c {cipher}' "
            f"root@{host}:{remote_root}{folder_path}/ {tmpfs_dir}/ >/dev/null 2>&1"
        )

    # Start rsync in background
    proc = subprocess.Popen(rsync_cmd, shell=True)
    time.sleep(2)

    # Sample rx_bytes
    iface = cfg.get("tuning", {}).get("net_interface", "eth0")
    rx_path = f"/sys/class/net/{iface}/statistics/rx_bytes"
    samples = []
    prev_rx = int(tuning._read_sysfs(rx_path) or 0)
    prev_time = time.time()
    end_time = prev_time + seconds

    while time.time() < end_time and proc.poll() is None:
        time.sleep(2)
        curr_rx = int(tuning._read_sysfs(rx_path) or 0)
        curr_time = time.time()
        dt = curr_time - prev_time
        if dt > 0:
            mb_s = (curr_rx - prev_rx) / (1024 * 1024) / dt
            samples.append(int(mb_s))
        prev_rx = curr_rx
        prev_time = curr_time

    # Kill rsync and clean up
    proc.kill()
    proc.wait()
    import shutil
    shutil.rmtree(tmpfs_dir, ignore_errors=True)

    if not samples:
        return {"net_avg": 0, "disk_avg": 0}

    return {
        "net_avg": sum(samples) // len(samples),
        "disk_avg": sum(samples) // len(samples),  # use same key so sweep logic works
        "dirty_avg": 0,
        "dirty_max": 0,
    }


def _dd_measure(mount_point, dd_size_mb=2048):
    """Run dd, return {disk_avg, dirty_avg, dirty_max}."""
    test_file = f"{mount_point}/.autotune_write_test"
    try:
        os.remove(test_file)
    except OSError:
        pass

    # Wait for dirty pages to flush before starting
    waited = 0
    for _ in range(60):  # max 60 seconds
        dirty = tuning._read_meminfo("Dirty")
        if dirty is not None and dirty < 2048:  # < 2 MB
            break
        subprocess.run(["sync"], timeout=30)
        time.sleep(2)
        waited += 2
    if waited > 0:
        _log(f"    (waited {waited}s for dirty flush)")

    dd_proc = subprocess.Popen(
        f"dd if=/dev/zero of={test_file} bs=1M count={dd_size_mb} conv=fdatasync",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )

    dirty_samples = []
    while dd_proc.poll() is None:
        d = tuning._read_meminfo("Dirty")
        if d is not None:
            dirty_samples.append(d // 1024)  # KB to MB
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


def cmd_tune_autotune(args):
    _require_root()
    cfg = load_config(args.config)
    mount_point = cfg.get("mount_point", "/backup")
    autotune_cfg = cfg.get("autotune", {})
    tuning_cfg = cfg.get("tuning", {})
    dd_size = autotune_cfg.get("dd_size_mb", 2048)

    layer = getattr(args, "layer", "disk")

    dev = tuning.block_device(mount_point)
    if not dev:
        print("Error: cannot detect block device", file=sys.stderr)
        sys.exit(1)

    rot = tuning._read_sysfs(f"/sys/block/{dev}/queue/rotational")
    drive_type = "hdd" if rot == "1" else "ssd"
    _log_info(f"Block device: {dev} ({drive_type})")

    # Get sweep ranges from config
    layer_ranges = autotune_cfg.get(layer, {})
    if not layer_ranges:
        _log_warn(f"No autotune ranges defined for layer '{layer}' in config.yaml")
        return

    # Build sweep list from config ranges + registry defaults
    if layer == "disk":
        sweep_keys = [k for k in _DISK_SWEEP_ORDER if k in layer_ranges]
    else:
        sweep_keys = list(layer_ranges.keys())

    sweeps = []
    for key in sweep_keys:
        values = layer_ranges[key]
        # Convert dirty_ratio_pairs lists to tuples
        if key == "dirty_ratio_pairs":
            values = [tuple(v) for v in values]

        # Get default from registry
        p = tuning.get_param(key)
        if p:
            default = p["default"]
        elif key == "dirty_ratio_pairs":
            default = (20, 10)
        else:
            default = None

        desc = p["description"] if p else key
        sweeps.append({"key": key, "description": desc, "values": values, "default": default})

    total = sum(len(s["values"]) for s in sweeps)
    _log_info(f"╔══ AUTOTUNE {layer.upper()}: {len(sweeps)} params, {total} values ══╗")

    if getattr(args, "dry_run", False):
        for s in sweeps:
            _log(f"  {s['key']}: {[_val_str(v) for v in s['values']]} (default={_val_str(s['default'])})")
        _log_info("╚══ DRY RUN ══╝")
        return

    # Pick measurement function by layer
    if layer == "network":
        measure = lambda: _net_measure(cfg)
    else:
        measure = lambda: _dd_measure(mount_point, dd_size)

    # Reset all sweep params to defaults
    _log_info("Resetting sweep params to defaults...")
    for s in sweeps:
        _apply_sweep_value(s["key"], s["default"], dev, mount_point, tuning_cfg)

    # Verify reset worked
    live = tuning.read_live(mount_point, tuning_cfg)
    reset_ok = True
    for s in sweeps:
        key = s["key"]
        expected = s["default"]
        if key == "dirty_ratio_pairs":
            actual_r = live.get("dirty_ratio")
            actual_b = live.get("dirty_background_ratio")
            exp_str = f"ratio={expected[0]}/bg={expected[1]}"
            act_str = f"ratio={actual_r}/bg={actual_b}"
            if str(actual_r) != str(expected[0]) or str(actual_b) != str(expected[1]):
                _log_warn(f"    RESET FAILED: {key} expected {exp_str} got {act_str}")
                reset_ok = False
            else:
                _log(f"    {key}: {act_str} ✓")
        else:
            actual = live.get(key)
            if str(actual) != str(expected):
                _log_warn(f"    RESET FAILED: {key} expected {expected} got {actual}")
                reset_ok = False
            else:
                _log(f"    {key}: {actual} ✓")

    if reset_ok:
        _log_ok("All defaults verified")
    else:
        _log_warn("Some params did not reset — results may be unreliable")
    print()

    # Baseline
    _log_info("━━━ Baseline ━━━")
    bl = measure()
    current_speed = bl.get("disk_avg", 0)
    _log(f"    {current_speed} MB/s, dirty avg={bl.get('dirty_avg','?')} max={bl.get('dirty_max','?')}")
    print()

    results = []
    for s in sweeps:
        key = s["key"]
        _log_info(f"━━━ Sweeping: {key} ━━━")
        _log(f"    {s['description']}")

        best_speed = current_speed
        best_value = s["default"]
        best_da = best_dm = None

        for val in s["values"]:
            _apply_sweep_value(key, val, dev, mount_point, tuning_cfg)
            m = measure()
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

        _apply_sweep_value(key, best_value, dev, mount_point, tuning_cfg)
        if best_value != s["default"]:
            _log_ok(f"    ✓ BEST: {_val_str(best_value)} ({best_speed} MB/s)")
        else:
            _log_warn(f"    ✗ DEFAULT kept ({best_speed} MB/s)")

        current_speed = best_speed
        results.append({"key": key, "best_value": best_value, "best_str": _val_str(best_value),
                        "speed": best_speed, "dirty_max": best_dm,
                        "kept": best_value != s["default"]})
        print()

    # Final confirmation
    _log_info("━━━ Final confirmation ━━━")
    f = measure()
    _log_ok(f"    FINAL: {f.get('disk_avg','?')} MB/s, dirty avg={f.get('dirty_avg','?')} max={f.get('dirty_max','?')} MB")
    dm = f.get("dirty_max")
    if isinstance(dm, int) and dm < 80:
        _log_ok(f"    ✓ dirty max {dm} MB < 80 MB target")
    elif isinstance(dm, int):
        _log_warn(f"    ✗ dirty max {dm} MB >= 80 MB target")

    # Results table
    print()
    _log_info("═══ RESULTS ═══")
    print(f"  {'Param':<30} {'Best':<20} {'Speed':>8} {'Dirty':>10} {'Kept':<6}")
    print(f"  {'─'*30} {'─'*20} {'─'*8} {'─'*10} {'─'*6}")
    for r in results:
        c = GREEN if r["kept"] else YELLOW
        print(f"  {r['key']:<30} {r['best_str']:<20} {r['speed']:>7} {r['dirty_max'] or '?':>10} {c}{'yes' if r['kept'] else 'no':<6}{RESET}")

    # Changed params as YAML
    changed = [r for r in results if r["kept"]]
    if changed:
        print()
        _log_info("═══ CHANGED (YAML) ═══")
        print("tuning:")
        for r in changed:
            key = r["key"]
            val = r["best_value"]
            if key == "dirty_ratio_pairs":
                print(f"  dirty_ratio: {val[0]}")
                print(f"  dirty_background_ratio: {val[1]}")
            else:
                print(f"  {key}: {val}")

    print()
    _log_info("═══ FULL STATUS ═══")
    print(tuning.status_yaml(mount_point, tuning_cfg))


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


# ── Watch — interactive terminal dashboard ───────────


def cmd_watch(args):
    """Live terminal dashboard with keyboard controls."""
    import select, termios, tty

    cfg = load_config(args.config)
    mount_point = cfg.get("mount_point", "/backup")
    iface = cfg.get("tuning", {}).get("net_interface", "eth0")
    mon = Monitor(mount_point, iface)

    W = 56  # box width

    def _speed_col(mbs):
        if mbs < 50: return "\033[31m"
        elif mbs <= 80: return "\033[33m"
        else: return "\033[32m"

    def _status_col(s):
        if s == "RUNNING": return "\033[32m"
        elif s == "FAILED": return "\033[31m"
        elif s == "OK": return "\033[32m"
        return "\033[37m"

    def _bar(pct, width=25):
        filled = int(width * pct / 100)
        return "█" * filled + "░" * (width - filled)

    def _trunc(s, maxlen):
        return s[:maxlen-1] + "…" if len(s) > maxlen else s

    def _fmt_bytes(b):
        if b >= 1024**3: return f"{b/1024**3:.1f} GB"
        elif b >= 1024**2: return f"{b/1024**2:.0f} MB"
        elif b >= 1024: return f"{b/1024:.0f} KB"
        return f"{b} B"

    def _fmt_dur(secs):
        if not secs: return "--:--"
        h, m = divmod(int(secs), 3600)
        m, s = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _box_line(content, w=W):
        # Remove ANSI codes for length calculation
        import re
        clean = re.sub(r'\033\[[0-9;]*m', '', content)
        pad = w - 4 - len(clean)  # w minus │ + space + space + │
        if pad < 0: pad = 0
        return f"│ {content}{' ' * pad} │"

    def _box_top(title="", w=W):
        if title:
            return f"┌─ {title} " + "─" * (w - 4 - len(title)) + "┐"
        return "┌" + "─" * (w - 2) + "┐"

    def _box_mid(w=W):
        return "├" + "─" * (w - 2) + "┤"

    def _box_bot(w=W):
        return "└" + "─" * (w - 2) + "┘"

    R = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        # Hide cursor
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        while True:
            # Check for keypress
            if select.select([sys.stdin], [], [], 2)[0]:
                key = sys.stdin.read(1)
                if key == "q" or key == "\x03":  # q or Ctrl+C
                    break
                elif key == "r":
                    # Restore terminal for subprocess
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    _run_sync_bg(cfg)
                    tty.setraw(fd)
                elif key == "c":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    for name in cfg["sources"]:
                        request_cancel(name)
                    tty.setraw(fd)

            # Sample stats
            s = mon.sample()
            a = mon.averages()

            # Read progress
            lines = []
            lines.append(_box_top("pullback"))

            for src_name, src_cfg in cfg["sources"].items():
                state = load_state(src_name)
                progress = get_progress(src_name)
                is_running = progress and progress.get("source")

                # Status
                if is_running:
                    status = "RUNNING"
                elif state.get("last_run_success") is True:
                    status = "OK"
                elif state.get("last_run_success") is False:
                    status = "FAILED"
                else:
                    status = "IDLE"

                sc = _status_col(status)
                host = src_cfg.get("host", "")
                lines.append(_box_line(f"{BOLD}{src_name}{R} ({host}){' ' * 20}{sc}{status}{R}"))

                if is_running:
                    pct = progress.get("overall_pct", 0)
                    eta = progress.get("eta", "--")
                    bar = _bar(pct)
                    lines.append(_box_line(f"{bar}  {pct}%  ETA {eta}"))

                    cur_file = progress.get("current_file", "")
                    step = progress.get("step", "")
                    lines.append(_box_line(f"{DIM}{_trunc(cur_file or step, W-4)}{R}"))

                    transferred = progress.get("bytes_transferred", 0)
                    elapsed = progress.get("elapsed", 0)
                    lines.append(_box_line(
                        f"{_fmt_bytes(transferred)} transferred  •  {_fmt_dur(elapsed)} elapsed"
                    ))
                else:
                    last = state.get("last_success_at") or state.get("last_run_started_at")
                    dur = state.get("last_sync_duration", 0)
                    if last:
                        last_short = last[:19].replace("T", " ")
                        lines.append(_box_line(f"{DIM}Last: {last_short}  •  {_fmt_dur(dur)}{R}"))
                    err = state.get("last_error")
                    if err:
                        lines.append(_box_line(f"\033[31m{_trunc(err, W-4)}{R}"))

            # Stats section
            lines.append(_box_mid())

            nc = _speed_col(s["net_mbs"])
            dc = _speed_col(s["disk_mbs"])
            nac = _speed_col(a["net_avg"])
            dac = _speed_col(a["disk_avg"])

            lines.append(_box_line(
                f"Net   {nac}avg {a['net_avg']:>3}{R} MB/s  {nc}now {s['net_mbs']:>3}{R} MB/s"
            ))
            lines.append(_box_line(
                f"Disk  {dac}avg {a['disk_avg']:>3}{R} MB/s  {dc}now {s['disk_mbs']:>3}{R} MB/s"
            ))
            lines.append(_box_line(
                f"Dirty {s['dirty_mb']}MB  •  Writeback {s['writeback_mb']}MB"
            ))

            # Volume info
            try:
                st = os.statvfs(mount_point)
                total_gb = st.f_blocks * st.f_frsize / 1024**3
                free_gb = st.f_bavail * st.f_frsize / 1024**3
                if total_gb >= 1024:
                    lines.append(_box_line(f"Volume: {free_gb/1024:.1f} TB free / {total_gb/1024:.1f} TB"))
                else:
                    lines.append(_box_line(f"Volume: {free_gb:.0f} GB free / {total_gb:.0f} GB"))
            except OSError:
                lines.append(_box_line("Volume: not mounted"))

            # Controls
            lines.append(_box_mid())
            lines.append(_box_line(f"{DIM}r=Run  c=Cancel  t=Tune  q=Quit{R}"))
            lines.append(_box_bot())

            # Render — cursor home + overwrite
            frame = "\033[H\033[J" + "\r\n".join(lines) + "\r\n"
            sys.stdout.write(frame)
            sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        # Show cursor, restore terminal
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print()


def _run_sync_bg(cfg):
    """Launch sync in background."""
    venv_python = str(PROJECT_DIR / "venv" / "bin" / "python3")
    engine = str(PROJECT_DIR / "engine.py")
    cmd = ["systemd-run", "--scope", "--quiet", venv_python, engine]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

    sub.add_parser("watch", help="Live terminal dashboard")

    p_config = sub.add_parser("config", help="Show loaded config")
    p_config.add_argument("--dump", action="store_true", help="Output as YAML")

    # Tune subcommands
    p_tune = sub.add_parser("tune", help="Tuning commands")
    tune_sub = p_tune.add_subparsers(dest="tune_command")
    tune_sub.add_parser("status", help="Show current tuning as YAML")
    tune_sub.add_parser("apply", help="Apply config tuning to system")
    tune_sub.add_parser("defaults", help="Revert all to OS defaults")
    tune_sub.add_parser("install", help="Remove boot-time tuning (tuning applied at sync start only)")
    tc = tune_sub.add_parser("capture", help="Capture OS defaults to file")
    tc.add_argument("--force", action="store_true")
    ta = tune_sub.add_parser("autotune", help="Sweep tuning params")
    ta.add_argument("--layer", default="disk",
                    choices=["disk", "network", "rsync"],
                    help="Layer to autotune (default: disk)")
    ta.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "sync": cmd_sync,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "watch": cmd_watch,
        "config": cmd_config,
        "tune": cmd_tune,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
