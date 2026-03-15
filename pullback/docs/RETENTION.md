# Retention Spec

## Overview

Retention manages local backup file versions. Two modes:

1. **Pre-stamped files** — files already have timestamps in their names (e.g., vzdump). No renaming needed.
2. **System-stamped files** — files without timestamps. The system adds a stamp after each sync, using a symlink to preserve the original filename for rsync.

## Config Fields

```yaml
folders:
  - path: <folder>
    retention:
      pattern: "<glob>"           # match files for retention (pre-stamped mode)
      retain_stamp: "<template>"  # naming template with $? token (system-stamped mode)
      extn_set: [.ext1, .ext2]   # extensions that form one backup set (empty = standalone files)
      keep: <int>                 # how many versions to keep per group
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `pattern` | For pre-stamped | Glob pattern to match files. Used when files already contain timestamps. |
| `retain_stamp` | For system-stamped | Filename template with `$?` marking where the system inserts a `YYMMDDHHmmss` timestamp. |
| `extn_set` | No (default `[]`) | List of file extensions that form one backup set. Empty means each file is standalone. |
| `keep` | Yes | Number of versions to keep per group. Older versions are deleted. |

Use `pattern` OR `retain_stamp`, not both.

## Mode 1: Pre-stamped (e.g., PVE vzdump)

Files already have timestamps in their names. Retention matches, groups, sorts by filename, and prunes.

### Example: PVE dumps

```yaml
- path: dump
  retention:
    pattern: "vzdump-*"
    extn_set: [.vma.zst, .log, .notes]
    keep: 3
```

Files on disk:
```
vzdump-lxc-100-2026_03_10-02_00_00.vma.zst
vzdump-lxc-100-2026_03_10-02_00_00.log
vzdump-lxc-100-2026_03_10-02_00_00.notes
vzdump-lxc-100-2026_03_11-02_00_00.vma.zst
vzdump-lxc-100-2026_03_11-02_00_00.log
vzdump-lxc-100-2026_03_11-02_00_00.notes
vzdump-lxc-100-2026_03_12-02_00_00.vma.zst
vzdump-lxc-100-2026_03_12-02_00_00.log
vzdump-lxc-100-2026_03_12-02_00_00.notes
vzdump-lxc-100-2026_03_13-02_00_00.vma.zst
vzdump-lxc-100-2026_03_13-02_00_00.log
vzdump-lxc-100-2026_03_13-02_00_00.notes
vzdump-lxc-108-2026_03_12-02_00_00.vma.zst
vzdump-lxc-108-2026_03_12-02_00_00.log
vzdump-lxc-108-2026_03_12-02_00_00.notes
vzdump-lxc-108-2026_03_13-02_00_00.vma.zst
vzdump-lxc-108-2026_03_13-02_00_00.log
vzdump-lxc-108-2026_03_13-02_00_00.notes
```

Logic:
1. Match files with `pattern: "vzdump-*"`
2. Strip extensions from `extn_set` to get the base: `vzdump-lxc-100-2026_03_10-02_00_00`
3. Group by base → each base has up to 3 files (one per extension)
4. Group bases by identity prefix (everything that makes them "the same backup source"). For vzdump: strip the timestamp to get `vzdump-lxc-100`. All bases sharing this prefix are versions of the same VM's backups.
5. Sort bases by filename (timestamps sort naturally)
6. Keep 3 newest bases per group, delete all files in older bases

Result with `keep: 3` for vmid 100:
- KEEP: `vzdump-lxc-100-2026_03_13-*` (newest)
- KEEP: `vzdump-lxc-100-2026_03_12-*`
- KEEP: `vzdump-lxc-100-2026_03_11-*`
- DELETE: `vzdump-lxc-100-2026_03_10-*` (3 files deleted)

vmid 108 only has 2 backups, both kept (under the limit).

### Grouping for pre-stamped files

The group key is determined by removing the timestamp portion from the filename. For vzdump files, the timestamp always follows the pattern `YYYY_MM_DD-HH_MM_SS`. Everything before the timestamp is the group key.

General rule: sort all matched bases alphabetically. Files that differ only in their trailing sortable portion belong to the same group.

## Mode 2: System-stamped (retain_stamp with $?)

For files that don't have timestamps in their names. The system adds a `YYMMDDHHmmss` stamp after each sync.

### Example: Single file

```yaml
- path: reports
  retention:
    retain_stamp: "report.js.$?"
    extn_set: []
    keep: 5
```

### Sync flow

Uses **hardlinks** (not symlinks) so rsync sees a regular file and can compare
content/metadata normally.

**First sync:**
1. rsync pulls `report.js` (real file, no local copy exists)
2. Post-sync: rename `report.js` → `report.js.260314120000`
3. Create hardlink: `report.js` → `report.js.260314120000` (same inode)

**Subsequent syncs (content changed):**
1. rsync sees `report.js` as a regular file (hardlink is transparent)
2. Remote file differs → rsync writes new temp file, renames over `report.js`
3. This breaks the hardlink — `report.js` gets new inode, `report.js.260314120000` keeps old content
4. Post-sync: compares inodes — `report.js` inode ≠ latest stamp inode → content changed
5. Rename `report.js` → `report.js.260315120000`
6. Create hardlink: `report.js` → `report.js.260315120000`
7. Old `report.js.260314120000` still exists with old content

**Subsequent syncs (content unchanged):**
1. rsync sees `report.js` as a regular file, compares size/mtime with remote
2. Remote file is same → rsync skips (no transfer)
3. Post-sync: compares inodes — `report.js` inode == latest stamp inode → no new version

**Retention:**
1. Match stamped files: `report.js.260314120000`, `report.js.260315120000`, etc.
2. Sort by stamp (YYMMDDHHmmss)
3. Keep 5 newest, delete older
4. If original was hardlinked to a deleted stamp, re-link to newest remaining

### Example: Wildcard with extension set

```yaml
- path: backups
  retention:
    retain_stamp: "db-*.$?"
    extn_set: [.sql.gz, .sha256]
    keep: 3
```

Files after several syncs:
```
db-main.sql.gz           (hardlink → db-main.260315120000.sql.gz, same inode)
db-main.sha256           (hardlink → db-main.260315120000.sha256, same inode)
db-main.260314120000.sql.gz
db-main.260314120000.sha256
db-main.260315120000.sql.gz
db-main.260315120000.sha256
db-replica.sql.gz        (hardlink → db-replica.260315120000.sql.gz, same inode)
db-replica.sha256        (hardlink → db-replica.260315120000.sha256, same inode)
db-replica.260315120000.sql.gz
db-replica.260315120000.sha256
```

Groups: `db-main` and `db-replica` are independent. Each keeps 3 versions.

### Stamp insertion

The `$?` token is replaced with `YYMMDDHHmmss`. The stamp is inserted AT the `$?` position:

| retain_stamp | Original file | Stamped file |
|-------------|---------------|--------------|
| `report.js.$?` | `report.js` | `report.js.260314120000` |
| `db-*.$?.sql.gz` | `db-main.sql.gz` | `db-main.260314120000.sql.gz` |
| `backup-$?.tar` | `backup.tar` | `backup-260314120000.tar` |

### Detecting content change via inode comparison

```python
import os
orig_inode = os.stat("report.js").st_ino
latest_inode = os.stat("report.js.260314120000").st_ino
if orig_inode == latest_inode:
    # Hardlink intact — content unchanged, no new version
    pass
else:
    # Different inode — rsync overwrote, content changed
    # Rename and create new hardlink
```

## Re-linking after retention prune

When retention deletes a stamped file that the original was hardlinked to,
the original's inode no longer matches any stamped version. Retention must:
1. Compare original's inode against newest remaining stamped version
2. If different, delete the original and re-link to newest stamped version

## Retention never touches the remote

All retention operations are local only. Retention never deletes files on the remote.
