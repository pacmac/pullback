# USB Drive & udev — Spec

## Overview

Backup drives are USB HDDs plugged into the Pi. A drive is initialised once
(format + flag file) with `hd-init.sh --format`, after which it mounts at
`/backup` automatically on plug-in. Drives are swappable — any flagged drive
inherits the mount point.

**Nothing here ever formats a drive.** Formatting happens only in
`hd-init.sh --format`, interactively, after typing `YES`.

## Setup

```
sudo bash pullback/scripts/udev-install.sh     # once, on the Pi
```

It installs `udev/99-pullback-usb.rules` into `/etc/udev/rules.d/`, writes
`/etc/systemd/system/pullback-mount@.service` with the real path of
`udev-mount.sh`, reloads udev and systemd.

After that:

- **Known drive** (flag file present): mounted at `/backup`.
- **Unflagged drive**: mounted briefly, flag file not found, unmounted and
  refused. Log line tells you to run `hd-init.sh <dev> --format`.
- **Not a filesystem**: ignored.

## Config fields (config.yaml)

```yaml
mount_point: /backup          # where the active backup drive is mounted

usb:
  flag_file: .pullback-volume # file on the root of the drive marking it ours
  filesystem: ext4            # used by hd-init.sh --format
  reserved_pct: 1             # ext4 reserved block percentage (hd-init.sh)
```

`udev-mount.sh` reads `mount_point` and `flag_file` from `config.yaml` with
grep/awk. Nothing is hardcoded in the scripts.

## Flag file

- Path: `<mount_point>/<flag_file>` (e.g. `/backup/.pullback-volume`)
- Written by `hd-init.sh --format` (an ISO timestamp)
- Checked by `udev-mount.sh` after mounting and by `engine.py` before a sync
- A drive without it is NOT a pullback volume and is never kept mounted

## udev rule

File: `udev/99-pullback-usb.rules`. It does not use `RUN+=` (udev blocks
device access while RUN runs). It tags the device for systemd and starts a
service instead:

```
ACTION=="add", SUBSYSTEM=="block", KERNEL=="sd[a-z]", ENV{DEVTYPE}=="disk", \
  TAG+="systemd", ENV{SYSTEMD_WANTS}="pullback-mount@%k.service"
ACTION=="add", SUBSYSTEM=="block", KERNEL=="sd[a-z][0-9]", ENV{DEVTYPE}=="partition", \
  TAG+="systemd", ENV{SYSTEMD_WANTS}="pullback-mount@%k.service"
```

Both whole disks and partitions trigger it, because the drive may carry the
filesystem directly on the disk.

## Scripts (`pullback/scripts/`)

### udev-install.sh

Installs the rule and the templated service (see Setup). Detects paths from
where it is run.

### udev-mount.sh

Started by `pullback-mount@<dev>.service` with `DEVNAME=/dev/<dev>`. Unattended:
no prompts, output via `logger -t pullback-udev` (read it with
`journalctl -t pullback-udev`). Mount-only — does not touch fstab, does not
format.

1. Read `mount_point` and `flag_file` from `config.yaml`.
2. **Something already mounted at the mount point?**
   - Readable (`timeout 5 ls -A` succeeds) -> log `already mounted, skipping`, exit.
   - **Unreadable** (EIO or no answer in 5 s) -> a *dead mount*: log it,
     `umount -l` it, continue. This happens when the drive loses power and
     re-enumerates as a new `sdX` while the old mount stays behind with ext4
     `shutdown` set. Before this rule, every later plug-in was skipped until
     someone ran `umount -l /backup` by hand.
3. `mount -o noatime,commit=60 <dev> <mount_point>`; if it will not mount, log
   and ignore.
4. Flag file present -> keep mounted. Absent -> `umount`, refuse.

Test: `bash pullback/tests/test_udev_stale_mount.sh` (shims every mount call;
touches nothing real).

### hd-init.sh

Manual, interactive. `hd-init.sh <device> [--format]` — formats (after
typing `YES`), labels `pullback`, writes the flag file. The only place a drive
is ever formatted.

## Engine pre-sync check

`engine.py` checks `<mount_point>/<flag_file>` before syncing and aborts with
a logged error if it is missing or the mount returns an I/O error.

## Drive swap workflow

1. Unplug the old drive (`umount /backup` first if you can; if not, the next
   plug-in detaches the dead mount itself).
2. Plug in the new, flagged drive.
3. udev -> `pullback-mount@sdX.service` -> `udev-mount.sh` mounts it at `/backup`.
4. The next sync writes to the new drive. State files live in the project
   folder, not on the drive.

## File layout

```
pullback/
  udev/
    99-pullback-usb.rules
    pullback-mount@.service    # template; udev-install.sh writes the real one
  scripts/
    hd-init.sh                 # manual init (format + flag)
    udev-install.sh            # install rule + service
    udev-mount.sh              # mount / refuse / replace a dead mount
  tests/
    test_udev_stale_mount.sh
```

## Not in scope

- Partition creation
- RAID / multi-drive
- Drive health monitoring (SMART is not installed on the Pi)
- Encrypted volumes
