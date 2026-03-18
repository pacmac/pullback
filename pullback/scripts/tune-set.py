#!/usr/bin/env python3
"""tune-set.py — Interactive tuning parameter editor.

Reads live values from the OS, lets you select and change them one at a time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tuning


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
            marker = "" if str(val) == str(default) else " *"
            print(f"  {i:>2}. {key:<32} {str(val):<20} {default}{marker}")

        print()
        print("  0. Exit")
        print("  a. Set ALL to defaults")
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

        print()
        print(f"  {key}")
        print(f"  Current: {current}")
        print(f"  Default: {default}")
        print()
        print(f"  d = set to default ({default})")
        print(f"  or enter a new value")
        print()

        try:
            val_input = input("  Value: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            continue

        if val_input == "" :
            continue

        if val_input.lower() == "d":
            new_val = default
        else:
            # Parse the input to match the type of the default
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
