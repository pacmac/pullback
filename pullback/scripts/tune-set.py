#!/usr/bin/env python3
"""tune-set.py — Interactive tuning parameter editor.

Reads live values from the OS, lets you select and change them one at a time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning

_MB = 1024 * 1024


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
        # Live stats
        dirty_kb = tuning._read_meminfo("Dirty")
        wb_kb = tuning._read_meminfo("Writeback")
        dirty_mb = dirty_kb // 1024 if dirty_kb else 0
        wb_mb = wb_kb // 1024 if wb_kb else 0

        dev = tuning.block_device(mount_point)
        iface = "eth0"
        net_rx = tuning._read_sysfs(f"/sys/class/net/{iface}/statistics/rx_bytes")
        net_rx_mb = int(net_rx) // _MB if net_rx and net_rx.isdigit() else 0

        print(f"  ── Live: Dirty={dirty_mb}MB  Writeback={wb_mb}MB  Net RX={net_rx_mb}MB ──")
        print()
        print("  q. Quit")
        print("  a. Set ALL to defaults")
        print("  s. Save current values to YAML")
        print("  l. Load and apply from saved YAML")
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
