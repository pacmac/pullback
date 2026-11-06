#!/bin/bash
# test_udev_stale_mount.sh — udev-mount.sh must replace a DEAD mount at the
# mount point, and must leave a HEALTHY one alone.
#
# Copies udev-mount.sh into a temp tree with its own config.yaml, puts shims for
# mountpoint/ls/umount/mount/logger ahead of the real ones, runs the script,
# and asserts on the calls it made. Touches nothing real: no mount, no umount.
#
# Exit 0 = all scenarios pass. Exit 1 = a scenario failed, and the line says
# which call was (or was not) made.

SRC="$(cd "$(dirname "$0")/../scripts" && pwd)/udev-mount.sh"
[[ -f "$SRC" ]] || { echo "ERROR: ${SRC} not found"; exit 127; }

FAIL=0
fail() { echo "FAIL: $*"; FAIL=1; }
pass() { echo "PASS: $*"; }

# scenario <name> <dead|healthy> <flag_on_disk yes|no> <expect_replace yes|no>
scenario() {
    local name="$1" state="$2" flag="$3" expect="$4"
    local T; T="$(mktemp -d)"
    mkdir -p "$T/scripts" "$T/bin" "$T/mnt"
    cp "$SRC" "$T/scripts/udev-mount.sh"
    printf 'mount_point: %s\nusb:\n  flag_file: .pullback-volume\n' "$T/mnt" > "$T/config.yaml"
    mknod "$T/blk" b 7 99 2>/dev/null || { echo "ERROR: cannot mknod a block device"; exit 127; }

    : > "$T/mounted"                              # something is mounted at mount_point
    [[ "$state" == dead ]] && : > "$T/dead"       # ...and reading it returns EIO
    [[ "$flag" == yes && "$state" == healthy ]] && : > "$T/mnt/.pullback-volume"
    [[ "$flag" == yes ]] && : > "$T/flag_after_mount"

    cat > "$T/bin/mountpoint" <<EOF
#!/bin/bash
[[ -f "$T/mounted" ]]
EOF
    cat > "$T/bin/ls" <<EOF
#!/bin/bash
[[ -f "$T/dead" ]] && { echo "ls: reading directory: Input/output error" >&2; exit 2; }
exit 0
EOF
    cat > "$T/bin/umount" <<EOF
#!/bin/bash
echo "umount \$*" >> "$T/calls"
rm -f "$T/mounted" "$T/dead"
EOF
    cat > "$T/bin/mount" <<EOF
#!/bin/bash
echo "mount \$*" >> "$T/calls"
: > "$T/mounted"
[[ -f "$T/flag_after_mount" ]] && : > "$T/mnt/.pullback-volume"
exit 0
EOF
    cat > "$T/bin/logger" <<EOF
#!/bin/bash
shift 2
echo "\$*" >> "$T/log"
EOF
    chmod +x "$T"/bin/*
    : > "$T/calls"; : > "$T/log"

    PATH="$T/bin:$PATH" DEVNAME="$T/blk" bash "$T/scripts/udev-mount.sh" >/dev/null 2>&1
    local rc=$?

    local umounted=no mounted=no logged_mounted=no logged_skip=no
    grep -q '^umount -l ' "$T/calls" && umounted=yes
    grep -q '^mount ' "$T/calls" && mounted=yes
    grep -q 'Mounted pullback volume' "$T/log" && logged_mounted=yes
    grep -q 'already mounted, skipping' "$T/log" && logged_skip=yes

    if [[ "$expect" == yes ]]; then
        if [[ $umounted == yes && $mounted == yes && $logged_mounted == yes ]]; then
            pass "${name}: dead mount detached and replaced (umount -l, mount, 'Mounted pullback volume')"
        else
            fail "${name}: dead mount NOT replaced — umount -l=${umounted} mount=${mounted} logged_mounted=${logged_mounted} logged_skip=${logged_skip} rc=${rc}"
        fi
    else
        if [[ $umounted == no && $mounted == no && $logged_skip == yes ]]; then
            pass "${name}: healthy mount left alone (no umount, no mount, 'already mounted' logged)"
        else
            fail "${name}: healthy mount was disturbed — umount -l=${umounted} mount=${mounted} logged_skip=${logged_skip} rc=${rc}"
        fi
    fi
    rm -rf "$T"
}

scenario "dead mount"                       dead    yes yes
scenario "healthy mount, flag file present" healthy yes no
scenario "healthy mount, no flag file"      healthy no  no

exit $FAIL
