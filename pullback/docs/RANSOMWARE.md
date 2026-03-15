# Ransomware Guard — Spec

## Overview

Pre-sync check that detects ransomware-encrypted files before pulling them
into the backup. Runs a local-only analysis using `.fprint` fingerprint files
and rsync `--dry-run` output. Designed to be fast — no remote file access
beyond the dry-run file list.

## Config (config.yaml)

```yaml
ransomware:
  enabled: false          # master switch, disable during dev
  sample_size: 30         # max files to sample per folder
  change_threshold: 0.30  # abort if >30% of sampled fprint files changed
  fprint_depth: 3         # directory depth for fprint file creation
```

## How it works

### Pipeline (runs before each folder sync)

1. **rsync --dry-run** (WITHOUT `--delete`) to get list of changed files on remote
2. **Filter** changed files to those that have a local `.fprint` file
3. **Sample** up to `sample_size` files from the filtered list
4. **Validate** each sampled file:
   - Compare stored `.fprint` hash against current local file hash
   - Check for known ransomware file extensions
   - Check Shannon entropy (>7.5 = likely encrypted)
5. **Calculate** change ratio (changed / sampled)
6. **Decision**:
   - If any ransomware indicators found → **FAIL** (skip sync, send alert)
   - If change ratio > `change_threshold` → **FAIL**
   - Otherwise → **PASS** (proceed with sync)

### After successful sync

`create_fprints()` is called to update `.fprint` files for the synced folder,
establishing the baseline for the next check.

## .fprint files

### Structure

Each directory (up to `fprint_depth`) gets a `.fprint/` subdirectory containing
one file per tracked file. Each `.fprint/<filename>` contains the SHA-256 hash
of the first 1MB of the original file.

```
shares/dis/
  .fprint/
    report.pdf          # contains sha256 of first 1MB of report.pdf
    invoice.xlsx        # contains sha256 of first 1MB of invoice.xlsx
  subdir/
    .fprint/
      data.csv          # contains sha256 of first 1MB of data.csv
  deep/nested/dir/
    (no .fprint — beyond fprint_depth=3)
```

### Why first 1MB only

- Fast: avoids reading entire large files (backups, disk images)
- Sufficient: ransomware encrypts from the start of the file
- Consistent: hash is stable for unchanged files regardless of appends

### Depth limit

`fprint_depth` controls how deep `.fprint` directories are created.
Depth 0 = only the folder root, depth 3 = up to 3 levels of subdirectories.
Files deeper than this are not fingerprinted and are excluded from the check.

## Detection methods

### 1. File extension check

Compares against a list of ~35 known ransomware extensions:
`.encrypted`, `.locked`, `.crypto`, `.locky`, `.ryuk`, `.conti`, `.lockbit`, etc.

Any match is immediately flagged as suspicious.

### 2. Shannon entropy

Reads first 4KB of a file and calculates Shannon entropy.
- Normal files: 3-6 bits per byte
- Compressed files: 6-7.5 bits per byte
- Encrypted/ransomware: >7.5 bits per byte (near-random)

Threshold: >7.5 = suspicious.

**Note:** Compressed archives (.gz, .zst, .xz) naturally have high entropy.
This is acceptable — the check runs on the LOCAL file (pre-sync), not the
remote file. If the local file was not previously encrypted and now the
remote is, the change in hash + high entropy is the signal.

### 3. Change ratio

Even without ransomware indicators, a large number of `.fprint` mismatches
is suspicious. If more than `change_threshold` (default 30%) of sampled
files have changed hashes, sync is aborted.

## Important design decisions

### Only checks local .fprint files

The check does NOT hash every local file. It only checks files that:
1. Appear in the rsync `--dry-run` changelist (remote changed)
2. Have an existing local `.fprint` file

This keeps the check fast and local.

### Remote-deleted files

If a file is deleted on the remote (appears as `deleting` in dry-run output),
the corresponding local `.fprint` file is removed. The file is not checked
for ransomware — deletion is a normal operation.

### No --delete in dry-run

The dry-run command strips `--delete` flags to prevent rsync from reporting
files that exist locally but not remotely. We only care about files that
changed or were added on the remote.

### Disabled during dev

Set `enabled: false` during development and initial syncs. Enable once
`.fprint` baselines have been established by at least one successful sync.

## Alert integration

When the ransomware check fails, an email alert is sent (if email is enabled)
with the source, folder, reason, and action to take. See ALERTS.md.

## Functions

### `check_ransomware(source_cfg, folder_cfg, cfg)`

Called by engine.py before sync. Returns `(safe: bool, reason: str)`.

### `create_fprints(local_base, fprint_depth)`

Called by engine.py after successful sync. Creates/updates `.fprint` files.

## Limitations

- Does not detect ransomware that appends to files without changing the header
- High-entropy false positives on compressed/binary files (mitigated by only
  checking files with changed `.fprint`)
- No protection on first sync (no baseline `.fprint` exists yet)
- Sampling means some affected files may not be checked (mitigated by
  large enough `sample_size`)
