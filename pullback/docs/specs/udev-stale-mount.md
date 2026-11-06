# Spec — udev-mount.sh must replace a dead mount

source_hash: 9cc17c18acdcdfb1ccefa3484fd24205de9a9ff789989f9f298f352ef3d7a9f6  # sha256 of pullback/scripts/udev-mount.sh after the fix

## Defect

`pullback/scripts/udev-mount.sh:39` decides `/backup` is occupied with
`mountpoint -q`. That answers "is something mounted", not "does it work".

Observed on piback 2026-11-07: the USB drive lost power (xhci over-current),
`/dev/sda` vanished but stayed mounted at `/backup` with ext4 `shutdown` set
and every read returning EIO. The drive re-enumerated as `sdb`; udev fired
`pullback-mount@sdb` eight times and each logged
`/backup already mounted, skipping`. Recovery needed a manual
`umount -l /backup`.

## Change — `pullback/scripts/udev-mount.sh`

Replace the "Already mounted? Skip." block. If `mountpoint -q` is true:

1. Probe the mount: `timeout 5 ls -A "$MOUNT_POINT" >/dev/null 2>&1`.
2. Probe succeeds -> unchanged: log `already mounted, skipping`, exit 0.
   (A healthy mount, with or without the flag file, is never touched.)
3. Probe fails (EIO, or no answer within 5 s) -> log
   `${MOUNT_POINT} is mounted but unreadable (dead mount) — detaching`,
   run `umount -l "$MOUNT_POINT"`, and fall through to the existing
   mount-and-flag-file path. If the umount fails, `die`.

Timeout: a dead mount answered EIO immediately on the Pi, so the timeout
only guards a hang; a healthy root-directory read is one block. 5 s is a
judgement, stated here, not a measured limit.

Safety: only `umount -l` and `mount`. No format, no delete, nothing that
writes to the drive (consistent with feedback_no_autoformat).

## Change — docs (stale text, verified against the code)

- `pullback/docs/USBUDEV.md`: rewritten to match the real behaviour — flag
  file `.pullback-volume`, systemd `pullback-mount@.service` triggered by
  `udev/99-pullback-usb.rules` (no `RUN+=`), scripts in `pullback/scripts/`,
  never auto-formats, plus the dead-mount rule above.
- `pullback/scripts/setup.sh:106`: says a drive is "auto-formatted on first
  plug-in". False and dangerous; replace with the `hd-init.sh --format` step.
- `pullback/docs/CLI.md`, `pullback/docs/ALERTS.md`, `pullback/docs/DASHBOARD.md`: product name `pibak` ->
  `pullback`.

## New file — `pullback/tests/test_udev_stale_mount.sh`

Copies the script into a temp tree with its own `config.yaml`, puts shims for
`mountpoint`, `ls`, `umount`, `mount`, `logger` on PATH, and asserts:

- dead mount: `umount -l` called, `mount` called, `Mounted pullback volume`
  logged. **Red before the fix, green after.**
- healthy mount (control): no `umount`, no `mount`, `already mounted` logged.
- healthy mount without flag file (control): same as above.

## Not changing

- `engine.py`, `web.py`: already treat an unreadable `/backup` as "no volume"
  (`OSError` handled, commit 2026-04-22). Correct as is.
- `hd-init.sh`, `self-backup.sh`, `pi-setup.sh` `mountpoint -q` uses: manual,
  interactive scripts.
- `pullback/udev/pullback-mount@.service`: `udev-install.sh` rewrites the path
  at install time.
- No SMART/`smartmontools` install on the Pi.
