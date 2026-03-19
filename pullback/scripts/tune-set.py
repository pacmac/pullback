#!/usr/bin/env python3
"""tune-set.py — Interactive tuning parameter editor.

Reads live values from the OS, lets you select and change them one at a time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning

_MB = 1024 * 1024

def _speed_colour(mbs):
    """Return ANSI colour for MB/s: <50 red, 50-80 orange, >80 green."""
    if mbs < 50:
        return "\033[31m"    # red
    elif mbs <= 80:
        return "\033[33m"    # orange/yellow
    else:
        return "\033[32m"    # green


def _fmt(val, unit):
    """Format a value for display based on unit type."""
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
    """Parse user input based on unit type. Returns (value, error)."""
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

        print()
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
            import time, select, termios, tty
            iface = "eth0"
            dev = tuning.block_device(mount_point) or "sda"
            print()
            print("  Press any key to stop")
            print(f"  {'':>8} {'Dirty':>8} {'WB':>8} {'Net MB/s':>10} {'Disk MB/s':>10}")
            print(f"  {'':>8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
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

                # Running averages
                net_samples = []
                disk_samples = []
                dirty_samples = []

                while True:
                    if select.select([sys.stdin], [], [], 2)[0]:
                        sys.stdin.read(1)
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

                    if net_mbs > 0:
                        net_samples.append(net_mbs)
                    if disk_mbs > 0:
                        disk_samples.append(disk_mbs)
                    if dirty_kb > 0:
                        dirty_samples.append(dirty_kb // 1024)

                    avg_net = sum(net_samples) // len(net_samples) if net_samples else 0
                    avg_disk = sum(disk_samples) // len(disk_samples) if disk_samples else 0
                    avg_dirty = sum(dirty_samples) // len(dirty_samples) if dirty_samples else 0

                    R = "\033[0m"
                    anc = _speed_colour(avg_net)
                    adc = _speed_colour(avg_disk)
                    cnc = _speed_colour(net_mbs)
                    cdc = _speed_colour(disk_mbs)

                    sys.stdout.write(
                        f"\r  {'avg':>8} {avg_dirty:>6}MB {wb_kb//1024:>6}MB {anc}{avg_net:>8}{R} {adc}{avg_disk:>8}{R}  "
                        f"\n\r  {'now':>8} {dirty_kb//1024:>6}MB {wb_kb//1024:>6}MB {cnc}{net_mbs:>8}{R} {cdc}{disk_mbs:>8}{R}  "
                        f"\033[A"  # move cursor back up one line
                    )
                    sys.stdout.flush()

                    prev_rx = curr_rx
                    prev_disk = curr_disk
                    prev_t = curr_t
            except KeyboardInterrupt:
                pass
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            print()
            print()
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
        current = live.get(key, "?")
        default = param["default"]

        disp_current = _fmt(current, unit)
        disp_default = _fmt(default, unit)

        print()
        print(f"  {key}")
        print(f"  Current: {disp_current}")
        print(f"  Default: {disp_default}")
        print()
        print(f"  d = set to default ({disp_default})")
        if unit == "bool":
            print(f"  1 = on")
            print(f"  2 = off")
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

        if val_input.lower() == "d":
            new_val = default
        else:
            new_val, err = _parse(val_input, unit)
            if err:
                print(f"  {err}")
                continue

        applied = tuning.apply_values({key: new_val}, mount_point)
        if applied:
            print(f"  Applied: {', '.join(applied)}")
        else:
            print(f"  Failed to apply {key}={new_val}")


if __name__ == "__main__":
    main()
