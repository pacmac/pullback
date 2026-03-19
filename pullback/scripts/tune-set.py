#!/usr/bin/env python3
"""tune-set.py — Interactive tuning parameter editor.

Reads live values from the OS, lets you select and change them one at a time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning

# Params displayed and entered in MB
_MB_KEYS = {"bdi_max_bytes", "rmem_max", "wmem_max"}
_MB = 1024 * 1024


def _to_mb(key, val):
    """Convert bytes to MB for display."""
    if key in _MB_KEYS and isinstance(val, (int, float)) and val > 0:
        return f"{int(val) // _MB}MB"
    if key in _MB_KEYS and isinstance(val, str) and val.isdigit() and int(val) > 0:
        return f"{int(val) // _MB}MB"
    return str(val)


def _from_mb(key, val_str):
    """Convert MB input to bytes for byte params."""
    if key in _MB_KEYS:
        try:
            return int(float(val_str)) * _MB
        except ValueError:
            return None
    return val_str


def main():
    mount_point = "/backup"

    while True:
        # Read live values
        live = tuning.read_live(mount_point)
        registry = tuning.get_registry()

        # Display with numbers
        print()
        print("  # Parameter                        Current Value       Default")
        print("  " + "─" * 70)
        for i, p in enumerate(registry, 1):
            key = p["key"]
            val = live.get(key)
            if val is None:
                val = "?"
            default = p["default"]
            disp_val = _to_mb(key, val)
            disp_def = _to_mb(key, default)
            changed = str(val) != str(default)
            if changed:
                print(f"  \033[33m{i:>2}. {key:<32} {disp_val:<20} {disp_def} *\033[0m")
            else:
                print(f"  {i:>2}. {key:<32} {disp_val:<20} {disp_def}")

        print()
        print("  0. Exit")
        print("  a. Set ALL to defaults")
        print("  s. Save current values to YAML")
        print()

        # Prompt for selection
        try:
            choice = input("  Select parameter #: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice == "0" or choice == "":
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
        current = live.get(key, "?")
        default = param["default"]

        disp_current = _to_mb(key, current)
        disp_default = _to_mb(key, default)
        unit = " (MB)" if key in _MB_KEYS else ""

        print()
        print(f"  {key}")
        print(f"  Current: {disp_current}")
        print(f"  Default: {disp_default}")
        print()
        print(f"  d = set to default ({disp_default})")
        print(f"  or enter a new value{unit}")
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
        elif key in _MB_KEYS:
            new_val = _from_mb(key, val_input)
            if new_val is None:
                print(f"  Invalid MB value: {val_input}")
                continue
        else:
            if isinstance(default, bool):
                new_val = val_input.lower() in ("true", "1", "yes", "on")
            elif isinstance(default, int):
                try:
                    new_val = int(val_input)
                except ValueError:
                    print(f"  Invalid integer: {val_input}")
                    continue
            else:
                new_val = val_input

        # Apply
        applied = tuning.apply_values({key: new_val}, mount_point)
        if applied:
            print(f"  Applied: {', '.join(applied)}")
        else:
            print(f"  Failed to apply {key}={new_val}")


if __name__ == "__main__":
    main()
