#!/usr/bin/env python3
"""tune-set.py — Interactive tuning parameter editor with live monitor.

Reads live values from the OS, lets you select and change them one at a time.
Sweep mode: > and < step through values with live monitoring after each change.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning
from config import load_config

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


def _parse(val_str, unit):
    if unit == "bytes":
        try:
            return int(float(val_str)) * _MB, None
        except ValueError:
            return None, f"Invalid MB value: {val_str}"
    elif unit == "bool":
        if val_str in ("1", "on", "true", "yes"):
            return True, None
        elif val_str in ("2", "off", "false", "no"):
            return False, None
        else:
            return None, "Enter 1 (on) or 2 (off)"
    elif unit == "int":
        try:
            return int(val_str), None
        except ValueError:
            return None, f"Invalid integer: {val_str}"
    else:
        return val_str, None


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


def _run_monitor(mount_point, header=None):
    """Run live monitor until any key pressed. Returns the key pressed."""
    import time, select, termios, tty

    iface = "eth0"
    dev = tuning.block_device(mount_point) or "sda"

    if header:
        print(f"  {header}")
    print(f"  {'':>8} {'Dirty':>8} {'WB':>8} {'Net MB/s':>10} {'Disk MB/s':>10}")
    print(f"  {'':>8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    key_pressed = ""
    try:
        tty.setraw(fd)

        prev_rx = int(tuning._read_sysfs(f"/sys/class/net/{iface}/statistics/rx_bytes") or 0)
        prev_disk = 0
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 10 and parts[2] == dev:
                    prev_disk = int(parts[9])
        prev_t = time.time()

        net_samples = []
        disk_samples = []
        dirty_samples = []
        last_active = time.time()

        while True:
            if select.select([sys.stdin], [], [], 2)[0]:
                key_pressed = sys.stdin.read(1)
                break

            curr_rx = int(tuning._read_sysfs(f"/sys/class/net/{iface}/statistics/rx_bytes") or 0)
            curr_disk = 0
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 10 and parts[2] == dev:
                        curr_disk = int(parts[9])
            curr_t = time.time()
            dt = curr_t - prev_t

            dirty_kb = tuning._read_meminfo("Dirty") or 0
            wb_kb = tuning._read_meminfo("Writeback") or 0
            net_mbs = int((curr_rx - prev_rx) / _MB / dt) if dt > 0 else 0
            disk_mbs = int((curr_disk - prev_disk) * 512 / _MB / dt) if dt > 0 else 0

            prev_rx = curr_rx
            prev_disk = curr_disk
            prev_t = curr_t

            R = "\033[0m"
            DIM = "\033[2m"

            if net_mbs == 0 and disk_mbs == 0:
                idle_secs = int(curr_t - last_active)
                sys.stdout.write(
                    f"\r  {DIM}{'idle':>8} {dirty_kb//1024:>6}MB {wb_kb//1024:>6}MB {'--':>8} {'--':>8}  {idle_secs:>4}s{R}  "
                    f"\n\r  {DIM}{'':>8} {'':>6}   {'':>6}   {'':>8} {'':>8}       {R}  "
                    f"\033[A"
                )
                sys.stdout.flush()
                continue

            last_active = curr_t

            if net_mbs > 0:
                net_samples.append(net_mbs)
            if disk_mbs > 0:
                disk_samples.append(disk_mbs)
            if dirty_kb > 0:
                dirty_samples.append(dirty_kb // 1024)

            avg_net = sum(net_samples) // len(net_samples) if net_samples else 0
            avg_disk = sum(disk_samples) // len(disk_samples) if disk_samples else 0
            avg_dirty = sum(dirty_samples) // len(dirty_samples) if dirty_samples else 0

            anc = _speed_colour(avg_net)
            adc = _speed_colour(avg_disk)
            cnc = _speed_colour(net_mbs)
            cdc = _speed_colour(disk_mbs)

            sys.stdout.write(
                f"\r  {'avg':>8} {avg_dirty:>6}MB {wb_kb//1024:>6}MB {anc}{avg_net:>8}{R} {adc}{avg_disk:>8}{R}       "
                f"\n\r  {'now':>8} {dirty_kb//1024:>6}MB {wb_kb//1024:>6}MB {cnc}{net_mbs:>8}{R} {cdc}{disk_mbs:>8}{R}       "
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


def main():
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

        if choice.lower() == "a":
            applied = tuning.apply_defaults(mount_point)
            print(f"  Reset {len(applied)} params to defaults")
            continue

        if choice.lower() == "s":
            from datetime import datetime
            ts = datetime.now().strftime("%y%m%d-%H%M%S")
            filename = f"tune-{ts}.yaml"
            filepath = Path(__file__).resolve().parent.parent / "state" / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(tuning.status_yaml(mount_point) + "\n")
            print(f"  Saved to {filepath}")
            continue

        if choice.lower() == "w":
            drive_tune = Path(mount_point) / ".pullback-tune.yaml"
            drive_tune.write_text(tuning.status_yaml(mount_point) + "\n")
            print(f"  Written to {drive_tune}")
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
            with open(files[fidx]) as fh:
                data = _yaml.safe_load(fh)
            if data and "tuning" in data:
                applied = tuning.apply_values(data["tuning"], mount_point)
                print(f"  Applied {len(applied)} params from {files[fidx].name}")
            else:
                print(f"  No tuning section in {files[fidx].name}")
            continue

        if choice.lower() == "m":
            print()
            _run_monitor(mount_point, "Press any key to stop")
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
        sweep_vals = options  # options ARE the sweep for governor/scheduler
        if not sweep_vals:
            cfg = load_config()
            autotune_cfg = cfg.get("autotune", {})
            for layer in ["disk", "network", "rsync"]:
                layer_ranges = autotune_cfg.get(layer, {})
                if key in layer_ranges:
                    sweep_vals = layer_ranges[key]
                    break

        # Param edit loop — stays on this param, > and < sweep with monitor
        while True:
            current = tuning.read_live(mount_point).get(key, "?")
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
                    if _find_idx(current, [v], unit) is not None:
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
                break

            if val_input == "":
                break

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
            else:
                new_val, err = _parse(val_input, unit)
                if err:
                    print(f"  {err}")
                    continue

            if new_val is not None:
                applied = tuning.apply_values({key: new_val}, mount_point)
                if applied:
                    disp = _fmt(new_val, unit)
                    print(f"  Applied: {key}={disp}")

                    # After > or <, enter sweep-monitor loop
                    if val_input in (">", "<") and sweep_vals:
                        while True:
                            kp = _run_monitor(mount_point,
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


if __name__ == "__main__":
    main()
