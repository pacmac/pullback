# Deletion Grace Spec

## Status: Deferred — implement after main system is working

## Overview

When files are deleted on the remote, don't immediately lose them locally. Soft-delete with a grace period, then hard-delete after expiry.

## Behaviour

1. After sync, detect files that exist locally but have been deleted on the remote
2. Rename with soft-delete prefix: `file.txt` → `x.260314.file.txt` (x.YYMMDD.)
3. On each sync, check existing `x.YYMMDD.*` files — hard-delete if past grace period
4. If file reappears on remote before grace expires, rename back (remove prefix)

## Config

```yaml
sources:
  pve:
    deleted_grace_days: 30
```

## Open Questions (resolve before implementation)

### 1. Dry-run coordination
Ransomware dry-run strips `--delete`. Deletion grace needs `--delete` to detect remote deletions. Solution: one shared dry-run WITH `--delete`, parsed by both modules. Ransomware uses the changed files list, deletions module uses the deletion list. Requires refactoring ransomware.py to accept a pre-computed changelist rather than running its own dry-run.

### 2. Already-renamed files in subsequent dry-runs
After renaming `file.txt` → `x.260314.file.txt`, the next dry-run with `--delete` will flag `x.260314.file.txt` as a deletion candidate (exists locally, not on remote). Must filter out `x.YYMMDD.*` files from deletion detection to avoid re-processing.

### 3. Folder-level deletions
Synced folders are rsync targets defined in config. Renaming an entire sync target folder breaks rsync (it would recreate the folder). Deletion grace operates at the FILE level within synced folders only. Cannot soft-delete a folder that is a sync target.

### 4. Interaction with retention
Files managed by retention (e.g. vzdump sets, system-stamped files) have their own lifecycle. Deletion grace should NOT apply to retention-managed files. Only applies to regular unmanaged files.

### 5. User's manual `x.` convention
User already uses `x.filename` (no date) to manually mark files for deletion. Options:
- `x.filename` (no date) = manually soft-deleted, never auto-expires, user hard-deletes when ready
- `x.YYMMDD.filename` (with date) = system soft-deleted, auto-expires after grace period
- System should respect both formats and not interfere with manually prefixed files
