# USB Drive & udev — Spec

## Overview

Backup drives are USB HDDs plugged into the Pi. A drive must be initialised once
(format + flag file), after which it is auto-mounted on plug-in via udev.
Drives are swappable — any flagged drive inherits the `/backup` mount point.

## Setup

On the Pi:

1. `bash/udev-install.sh` — one-time: installs the udev rule

That's it. After this, any USB drive plugged in is handled automatically:

- **Known drive** (has flag file): mounted at `/backup`
- **New drive** (no flag file): auto-formatted, flag file created, mounted at `/backup`

This Pi is a dedicated backup appliance. Any USB drive plugged in is assumed
to be a backup drive. There is no manual step.

## Config fields (config.yaml)

```yaml
mount_point: /backup          # where the active backup drive is mounted

usb:
  flag_file: .pibak-volume    # file placed on root of drive after init
  filesystem: ext4            # format type
  reserved_pct: 1             # ext4 reserved block percentage
```

All values come from config.yaml — nothing hardcoded in scripts.

## Flag file

- File: `<mount_point>/<flag_file>` (e.g. `/backup/.pibak-volume`)
- Created by `hd-init.sh --format` on first init
- Checked by udev mount script and by engine.py before sync
- Contains: single line with ISO timestamp of when the drive was initialised
- A drive without this file is NOT a pibak volume and must not be auto-mounted

## Scripts

### bash/hd-init.sh

Optional manual fallback. Not needed for normal operation — udev-mount.sh
handles everything automatically. Use this only if you want to manually
init a drive with interactive confirmation.

**Usage:** `hd-init.sh <device> [--format]`

Reads config values from config.yaml via grep/awk.

### bash/udev-install.sh

Installs the udev rule. Run once on the Pi.

1. Copies `udev/99-pibak-usb.rules` to `/etc/udev/rules.d/`
2. Runs `udevadm control --reload-rules`
3. Runs `udevadm trigger`
4. Reports success

### bash/udev-mount.sh

Called by the udev rule. Handles both known and new drives automatically.

1. Receives device path from udev environment (`$DEVNAME`)
2. Reads config.yaml for `mount_point`, `flag_file`, `filesystem`, `reserved_pct`
3. If already mounted at mount point → skip
4. Gets UUID of the device

**Known drive** (UUID found in fstab):
5. Mount it
6. Verify `<flag_file>` exists → if missing, unmount and error
7. Log success

**New drive** (UUID not in fstab):
5. Format as `usb.filesystem` with `-m <reserved_pct>`, label `pibak`
6. Get new UUID after format
7. Add fstab entry (UUID-based, `noatime,commit=60,nofail`)
8. Mount it
9. Create `<flag_file>` with init timestamp
10. Log success

**Important:** udev scripts run in a restricted environment. No interactive
prompts. All output via `logger` to syslog.

## udev rule

File: `udev/99-pibak-usb.rules`

```
# Auto-mount pibak backup drives on USB plug-in
ACTION=="add", SUBSYSTEM=="block", KERNEL=="sd[a-z][0-9]", \
  RUN+="/usr/share/pac/pibak/bash/udev-mount.sh"
```

- Triggers on partition add (not whole disk)
- Path in RUN uses the Pi path (`/usr/share/pac/`), not the dev server path
- Only fires for `sd*` devices (USB HDDs)

## Engine pre-sync check

Before syncing, engine.py must verify:

```python
flag = Path(cfg["mount_point"]) / cfg["usb"]["flag_file"]
if not flag.exists():
    log.error("No backup volume mounted — aborting")
    sys.exit(1)
```

This goes at the top of `run_all()`, before iterating sources.

## Drive swap workflow

1. Unplug old drive (unmounts automatically or via `umount /backup`)
2. Plug in new drive
3. udev fires → `udev-mount.sh` runs → mounts at `/backup`
4. Next sync run writes to the new drive
5. Local state files are unaffected (they live in the project folder, not on the drive)

## File layout

```
pibak/
  udev/
    99-pibak-usb.rules
  bash/
    hd-init.sh          # manual init (format + flag)
    udev-install.sh     # install udev rule
    udev-mount.sh       # called by udev on plug-in
```

## Not in scope

- Partition creation (user provides a partition device like `/dev/sda1`)
- RAID / multi-drive
- Drive health monitoring
- Encrypted volumes
