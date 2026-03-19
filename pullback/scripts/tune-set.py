#!/usr/bin/env python3
"""tune-set.py — Tuning parameter editor with interactive and CLI modes.

Interactive:  tune-set.py
CLI:          tune-set.py set <key> <value>
              tune-set.py get <key>
              tune-set.py defaults
              tune-set.py list
              tune-set.py monitor
              tune-set.py save
              tune-set.py save-drive
              tune-set.py load <file>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning
from monitor import Monitor

_MB = 1024 * 1024


def _speed_colour(mbs):
    if mbs < 50:
        return "\033[31m"
    elif mbs <= 80:
        return "\033[33m"
    else:
        return "\033[32m"


def _fmt(val, unit):
    if unit == "bytes":
        if isinstance(val, (int, float)) and val > 0:
            return f"{int(val) // _MB}MB"
        if isinstance(val, str) and val.isdigit() and int(val) > 0:
            return f"{int(val) // _MB}MB"
        return "0MB" if val == 0 or val == "0" else str(val)
    elif unit == "bool":
        if isinstance(val, bool):
            return "on" if val else "off"
        if isinstance(val, str):
            return "on" if val.lower() in ("true", "1") else "off"
        return str(val)
    return str(val)


def _parse_value(val_str, unit):
    """Parse a string value according to unit type. Returns native value."""
    if unit == "bytes":
        return int(float(val_str)) * _MB
    elif unit == "bool":
        return val_str.lower() in ("true", "1", "yes", "on")
    elif unit == "int":
        return int(val_str)
    return val_str


def cmd_list(mount_point="/backup"):
    """List all params with current and default values."""
    live = tuning.read_live(mount_point)
    registry = tuning.get_registry()

    print(f"{'Parameter':<32} {'Current':<20} {'Default':<20}")
    print("─" * 72)
    for p in registry:
        key = p["key"]
        unit = p.get("unit", "str")
        val = live.get(key, "?")
        default = p["default"]
        disp_val = _fmt(val, unit)
        disp_def = _fmt(default, unit)
        changed = str(val) != str(default)
        if changed:
            print(f"\033[33m{key:<32} {disp_val:<20} {disp_def:<20} *\033[0m")
        else:
            print(f"{key:<32} {disp_val:<20} {disp_def:<20}")

    dirty_kb = tuning._read_meminfo("Dirty") or 0
    wb_kb = tuning._read_meminfo("Writeback") or 0
    print()
    print(f"Dirty: {dirty_kb//1024}MB   Writeback: {wb_kb//1024}MB")


def cmd_get(key, mount_point="/backup"):
    """Get a single param value."""
    p = tuning.get_param(key)
    if not p:
        print(f"Unknown param: {key}", file=sys.stderr)
        sys.exit(1)
    live = tuning.read_live(mount_point)
    unit = p.get("unit", "str")
    val = live.get(key, "?")
    print(_fmt(val, unit))


def cmd_set(key, val_str, mount_point="/backup"):
    """Set a single param value."""
    p = tuning.get_param(key)
    if not p:
        print(f"Unknown param: {key}", file=sys.stderr)
        sys.exit(1)
    unit = p.get("unit", "str")

    if val_str.lower() == "default":
        val = p["default"]
    else:
        val = _parse_value(val_str, unit)

    applied = tuning.apply_values({key: val}, mount_point)
    if applied:
        # Re-read to confirm
        live = tuning.read_live(mount_point)
        actual = live.get(key, "?")
        print(f"{key}: {_fmt(actual, unit)}")
    else:
        print(f"Failed to apply {key}={val}", file=sys.stderr)
        sys.exit(1)


def cmd_defaults(mount_point="/backup"):
    """Reset all to OS defaults."""
    applied = tuning.apply_defaults(mount_point)
    print(f"Reset {len(applied)} params to defaults")
    cmd_list(mount_point)


def cmd_monitor(mount_point="/backup", duration=0):
    """Monitor live stats. Runs until Ctrl+C or duration seconds."""
    mon = Monitor(mount_point)

    print(f"{'':>8} {'Dirty':>8} {'WB':>8} {'Net MB/s':>10} {'Disk MB/s':>10}")
    print(f"{'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

    start_t = time.time()
    R = "\033[0m"

    try:
        while True:
            time.sleep(2)
            s = mon.sample()
            a = mon.averages()

            anc = _speed_colour(a["net_avg"])
            adc = _speed_colour(a["disk_avg"])
            cnc = _speed_colour(s["net_mbs"])
            cdc = _speed_colour(s["disk_mbs"])

            print(f"  avg  {a['dirty_avg']:>6}MB {s['writeback_mb']:>6}MB {anc}{a['net_avg']:>8}{R} {adc}{a['disk_avg']:>8}{R}")
            print(f"  now  {s['dirty_mb']:>6}MB {s['writeback_mb']:>6}MB {cnc}{s['net_mbs']:>8}{R} {cdc}{s['disk_mbs']:>8}{R}")

            if duration > 0 and (time.time() - start_t) >= duration:
                break
    except KeyboardInterrupt:
        pass

    a = mon.averages()
    if a["net_samples"] or a["disk_samples"]:
        print()
        print(f"Average: Net={a['net_avg']} MB/s  Disk={a['disk_avg']} MB/s  ({a['net_samples']} samples)")


def cmd_save(mount_point="/backup"):
    """Save current values to timestamped YAML."""
    from datetime import datetime
    ts = datetime.now().strftime("%y%m%d-%H%M%S")
    filename = f"tune-{ts}.yaml"
    filepath = Path(__file__).resolve().parent.parent / "state" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(tuning.status_yaml(mount_point) + "\n")
    print(f"Saved to {filepath}")


def cmd_save_drive(mount_point="/backup"):
    """Save current values to backup volume."""
    drive_tune = Path(mount_point) / ".pullback-tune.yaml"
    drive_tune.write_text(tuning.status_yaml(mount_point) + "\n")
    print(f"Written to {drive_tune}")


def cmd_load(filepath, mount_point="/backup"):
    """Load and apply from saved YAML."""
    import yaml
    with open(filepath) as f:
        data = yaml.safe_load(f)
    if data and "tuning" in data:
        applied = tuning.apply_values(data["tuning"], mount_point)
        print(f"Applied {len(applied)} params from {filepath}")
    else:
        print(f"No tuning section in {filepath}", file=sys.stderr)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if not args:
        # Interactive mode
        _interactive()
        return

    cmd = args[0]

    if cmd == "list":
        cmd_list()
    elif cmd == "get" and len(args) >= 2:
        cmd_get(args[1])
    elif cmd == "set" and len(args) >= 3:
        cmd_set(args[1], args[2])
    elif cmd == "defaults":
        cmd_defaults()
    elif cmd == "monitor":
        duration = int(args[1]) if len(args) >= 2 else 0
        cmd_monitor(duration=duration)
    elif cmd == "save":
        cmd_save()
    elif cmd == "save-drive":
        cmd_save_drive()
    elif cmd == "load" and len(args) >= 2:
        cmd_load(args[1])
    else:
        print("Usage:")
        print("  tune-set.py                     Interactive mode")
        print("  tune-set.py list                List all params")
        print("  tune-set.py get <key>           Get a param value")
        print("  tune-set.py set <key> <value>   Set a param (use 'default' for OS default)")
        print("  tune-set.py defaults            Reset all to OS defaults")
        print("  tune-set.py monitor [seconds]   Monitor live stats")
        print("  tune-set.py save                Save to state/tune-YYMMDD.yaml")
        print("  tune-set.py save-drive          Save to /backup/.pullback-tune.yaml")
        print("  tune-set.py load <file>         Load and apply from YAML")
        sys.exit(1)


def _interactive():
    """Interactive menu mode."""
    from config import load_config

    mount_point = "/backup"

    while True:
        live = tuning.read_live(mount_point)
        registry = tuning.get_registry()

        print()
        print("  # Parameter                        Current             Default")
        print("  " + "─" * 70)
        for i, p in enumerate(registry, 1):
            key = p["key"]
            unit = p.get("unit", "str")
            val = live.get(key)
            if val is None:
                val = "?"
            default = p["default"]
            disp_val = _fmt(val, unit)
            disp_def = _fmt(default, unit)
            changed = str(val) != str(default)
            if changed:
                print(f"  \033[33m{i:>2}. {key:<32} {disp_val:<20} {disp_def} *\033[0m")
            else:
                print(f"  {i:>2}. {key:<32} {disp_val:<20} {disp_def}")

        dirty_kb = tuning._read_meminfo("Dirty") or 0
        wb_kb = tuning._read_meminfo("Writeback") or 0
        print()
        print(f"  Dirty: {dirty_kb//1024}MB   Writeback: {wb_kb//1024}MB")
        print()
        print("  q. Quit")
        print("  r. Refresh")
        print("  a. Set ALL to defaults")
        print("  s. Save current values to YAML")
        print("  w. Write current values to backup volume")
        print("  l. Load and apply from saved YAML")
        print("  m. Monitor live stats (any key to stop)")
        print()

        try:
            choice = input("  Select parameter #: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice.lower() == "q" or choice == "":
            break

        if choice.lower() == "r":
            continue

        if choice.lower() == "a":
            applied = tuning.apply_defaults(mount_point)
            print(f"  Reset {len(applied)} params to defaults")
            continue

        if choice.lower() == "s":
            cmd_save(mount_point)
            continue

        if choice.lower() == "w":
            cmd_save_drive(mount_point)
            continue

        if choice.lower() == "l":
            import yaml as _yaml
            state_dir = Path(__file__).resolve().parent.parent / "state"
            files = sorted(state_dir.glob("tune-*.yaml"))
            if not files:
                print("  No saved files found")
                continue
            print()
            for j, f in enumerate(files, 1):
                print(f"  {j}. {f.name}")
            print()
            try:
                pick = input("  Select file #: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            try:
                fidx = int(pick) - 1
                if fidx < 0 or fidx >= len(files):
                    print("  Invalid selection")
                    continue
            except ValueError:
                print("  Invalid selection")
                continue
            cmd_load(str(files[fidx]), mount_point)
            continue

        if choice.lower() == "m":
            print()
            _run_monitor_interactive(mount_point)
            continue

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(registry):
                print("  Invalid selection")
                continue
        except ValueError:
            print("  Invalid selection")
            continue

        param = registry[idx]
        key = param["key"]
        unit = param.get("unit", "str")
        default = param["default"]
        options = param.get("options")

        # Build sweep values from config autotune ranges
        sweep_vals = options
        if not sweep_vals:
            try:
                cfg = load_config()
                autotune_cfg = cfg.get("autotune", {})
                for layer in ["disk", "network", "rsync"]:
                    layer_ranges = autotune_cfg.get(layer, {})
                    if key in layer_ranges:
                        sweep_vals = layer_ranges[key]
                        break
            except Exception:
                pass

        # Param edit — single value then back to main
        current = live.get(key, "?")
        disp_current = _fmt(current, unit)
        disp_default = _fmt(default, unit)

        print()
        print(f"  {key}")
        print(f"  Current: {disp_current}")
        print(f"  Default: {disp_default}")
        if sweep_vals:
            sweep_display = []
            for v in sweep_vals:
                s = _fmt(v, unit)
                if str(v) == str(current):
                    s = f"[{s}]"
                sweep_display.append(s)
            print(f"  Range: {' '.join(sweep_display)}")
        print()
        print(f"  d=default  >=next  <=prev  enter=back")
        if unit == "bool":
            print(f"  1=on  2=off")
        elif options:
            for oi, opt in enumerate(options, 1):
                marker = " ◀" if str(opt) == str(current) else ""
                print(f"  {oi}={opt}{marker}")
        elif unit == "bytes":
            print(f"  or enter value in MB")
        else:
            print(f"  or enter a new value")
        print()

        try:
            val_input = input("  Value: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            continue

        if val_input == "":
            continue

        new_val = None

        if val_input.lower() == "d":
            new_val = default
        elif val_input == ">" and sweep_vals:
            cur_idx = _find_idx(current, sweep_vals, unit)
            if cur_idx is not None and cur_idx < len(sweep_vals) - 1:
                new_val = sweep_vals[cur_idx + 1]
            elif cur_idx is None:
                new_val = sweep_vals[0]
            else:
                print(f"  Already at max")
                continue
        elif val_input == "<" and sweep_vals:
            cur_idx = _find_idx(current, sweep_vals, unit)
            if cur_idx is not None and cur_idx > 0:
                new_val = sweep_vals[cur_idx - 1]
            elif cur_idx is None:
                new_val = sweep_vals[-1]
            else:
                print(f"  Already at min")
                continue
        elif options and val_input.isdigit() and 1 <= int(val_input) <= len(options):
            new_val = options[int(val_input) - 1]
        elif unit == "bool":
            if val_input in ("1", "on"):
                new_val = True
            elif val_input in ("2", "off"):
                new_val = False
        elif unit == "bytes":
            try:
                new_val = int(float(val_input)) * _MB
            except ValueError:
                print(f"  Invalid MB value: {val_input}")
                continue
        elif unit == "int":
            try:
                new_val = int(val_input)
            except ValueError:
                print(f"  Invalid integer: {val_input}")
                continue
        else:
            new_val = val_input

        if new_val is not None:
            applied = tuning.apply_values({key: new_val}, mount_point)
            if applied:
                disp = _fmt(new_val, unit)
                print(f"  Applied: {key}={disp}")

                # Sweep mode: > or < starts monitor loop
                if val_input in (">", "<") and sweep_vals:
                    while True:
                        kp = _run_monitor_interactive(mount_point,
                            f"{key}={disp}  (> next, < prev, any other key to stop)")
                        if kp not in (">", "<"):
                            break
                        cur_idx = _find_idx(new_val, sweep_vals, unit)
                        next_idx = (cur_idx + 1) if kp == ">" else (cur_idx - 1)
                        if cur_idx is None or next_idx < 0 or next_idx >= len(sweep_vals):
                            print(f"  End of range")
                            break
                        new_val = sweep_vals[next_idx]
                        tuning.apply_values({key: new_val}, mount_point)
                        disp = _fmt(new_val, unit)
                        print(f"  Applied: {key}={disp}")
            else:
                print(f"  Failed to apply {key}={new_val}")


def _find_idx(current, sweep_vals, unit):
    for i, v in enumerate(sweep_vals):
        if str(v) == str(current):
            return i
        if unit == "bytes":
            try:
                if int(v) == int(current):
                    return i
            except (ValueError, TypeError):
                pass
    return None


def _run_monitor_interactive(mount_point, header=None):
    """Run live monitor until any key pressed. Returns the key pressed."""
    import select, termios, tty

    mon = Monitor(mount_point)

    if header:
        print(f"  {header}")
    print(f"  {'':>8} {'Dirty':>8} {'WB':>8} {'Net MB/s':>10} {'Disk MB/s':>10}")
    print(f"  {'':>8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    key_pressed = ""
    last_active = time.time()
    R = "\033[0m"
    DIM = "\033[2m"

    try:
        tty.setraw(fd)

        while True:
            if select.select([sys.stdin], [], [], 2)[0]:
                key_pressed = sys.stdin.read(1)
                break

            s = mon.sample()
            a = mon.averages()

            if mon.is_idle(s):
                idle_secs = int(time.time() - last_active)
                sys.stdout.write(
                    f"\r  {DIM}{'idle':>8} {s['dirty_mb']:>6}MB {s['writeback_mb']:>6}MB {'--':>8} {'--':>8}  {idle_secs:>4}s{R}  "
                    f"\n\r  {DIM}{'':>8} {'':>6}   {'':>6}   {'':>8} {'':>8}       {R}  "
                    f"\033[A"
                )
                sys.stdout.flush()
                continue

            last_active = time.time()

            anc = _speed_colour(a["net_avg"])
            adc = _speed_colour(a["disk_avg"])
            cnc = _speed_colour(s["net_mbs"])
            cdc = _speed_colour(s["disk_mbs"])

            sys.stdout.write(
                f"\r  {'avg':>8} {a['dirty_avg']:>6}MB {s['writeback_mb']:>6}MB {anc}{a['net_avg']:>8}{R} {adc}{a['disk_avg']:>8}{R}       "
                f"\n\r  {'now':>8} {s['dirty_mb']:>6}MB {s['writeback_mb']:>6}MB {cnc}{s['net_mbs']:>8}{R} {cdc}{s['disk_mbs']:>8}{R}       "
                f"\033[A"
            )
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    print()
    print()
    return key_pressed


if __name__ == "__main__":
    main()
