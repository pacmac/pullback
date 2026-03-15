"""Retention: pattern-based backup file versioning and pruning.

See RETENTION.md for full spec (SSOT).
Two modes: pre-stamped (pattern) and system-stamped (retain_stamp with $?).
System-stamped uses hardlinks (not symlinks) so rsync can compare content normally.
"""

import fnmatch
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# YYMMDDHHmmss regex for system-stamped files
_STAMP_RE = re.compile(r"\d{12}")

# Known vzdump timestamp pattern: YYYY_MM_DD-HH_MM_SS
_VZDUMP_TS_RE = re.compile(r"-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})$")


def apply_retention(folder_path, retention_cfg):
    """Apply retention to a folder. Called by engine.py after sync.

    Args:
        folder_path: local folder path (str or Path)
        retention_cfg: dict with pattern|retain_stamp, extn_set, keep

    Returns list of deleted file paths.
    """
    folder = Path(folder_path)
    if not folder.exists():
        return []

    keep = retention_cfg["keep"]
    extn_set = retention_cfg.get("extn_set", [])

    if "retain_stamp" in retention_cfg:
        return _apply_system_stamped(folder, retention_cfg["retain_stamp"], extn_set, keep)
    elif "pattern" in retention_cfg:
        return _apply_pre_stamped(folder, retention_cfg["pattern"], extn_set, keep)

    return []


def post_sync_stamp(folder_path, retention_cfg):
    """After sync, handle system-stamped files: rename and hardlink.

    Called by engine.py after a successful sync for folders with retain_stamp.
    Only creates a new stamped version if the file content changed
    (detected by comparing inodes — if original has different inode from
    latest stamped version, rsync overwrote it with new content).
    """
    if "retain_stamp" not in retention_cfg:
        return

    folder = Path(folder_path)
    if not folder.exists():
        return

    template = retention_cfg["retain_stamp"]
    extn_set = retention_cfg.get("extn_set", [])
    stamp = datetime.now().strftime("%y%m%d%H%M%S")

    # Find original files matching the template (without $?)
    originals = _find_originals(folder, template, extn_set)

    for orig_path in originals:
        if not orig_path.exists():
            continue

        # Find the latest stamped version
        latest_stamped = _find_latest_stamped(folder, template, extn_set, orig_path.name)

        if latest_stamped:
            # Compare inodes — same inode means content unchanged (hardlink intact)
            orig_inode = os.stat(orig_path).st_ino
            stamped_inode = os.stat(folder / latest_stamped).st_ino
            if orig_inode == stamped_inode:
                # Content unchanged, skip
                continue

        # Content changed (or first sync) — create new stamped version
        stamped_name = _insert_stamp(orig_path.name, template, stamp, extn_set)
        if not stamped_name:
            continue

        stamped_path = folder / stamped_name

        # Rename original → stamped
        orig_path.rename(stamped_path)

        # Create hardlink: original → stamped (same inode)
        os.link(stamped_path, orig_path)


# ── Pre-stamped mode ──

def _apply_pre_stamped(folder, pattern, extn_set, keep):
    """Retention for files with existing timestamps in names."""
    # List all files matching pattern
    all_files = [f.name for f in folder.iterdir() if f.is_file() and fnmatch.fnmatch(f.name, pattern)]

    if not all_files:
        return []

    if extn_set:
        # Strip extensions to get bases, group files by base
        bases = defaultdict(list)
        for fname in all_files:
            base = _strip_extn_set(fname, extn_set)
            if base:
                bases[base].append(fname)

        # Group bases by identity (strip timestamp to get group key)
        groups = defaultdict(list)
        for base in sorted(bases.keys()):
            group_key = _extract_group_key(base)
            groups[group_key].append(base)
    else:
        # No extension set — each file is its own "base"
        bases = {f: [f] for f in all_files}
        groups = defaultdict(list)
        for fname in sorted(all_files):
            group_key = _extract_group_key(fname)
            groups[group_key].append(fname)

    # Prune each group
    deleted = []
    for group_key, group_bases in groups.items():
        # Sort bases (timestamps sort naturally)
        group_bases.sort()
        # Keep newest N
        to_delete = group_bases[:-keep] if len(group_bases) > keep else []
        for base in to_delete:
            for fname in bases[base]:
                fpath = folder / fname
                if fpath.exists():
                    fpath.unlink()
                    deleted.append(str(fpath))

    return deleted


def _strip_extn_set(filename, extn_set):
    """Strip a known extension from extn_set, return base or None."""
    for ext in sorted(extn_set, key=len, reverse=True):  # longest first
        if filename.endswith(ext):
            return filename[: -len(ext)]
    return None


def _extract_group_key(base):
    """Extract the group key from a base filename by stripping the timestamp.

    For vzdump: 'vzdump-lxc-100-2026_03_13-02_00_00' → 'vzdump-lxc-100'
    General: strip trailing portion that looks like a timestamp.
    """
    # Try vzdump timestamp pattern first
    m = _VZDUMP_TS_RE.search(base)
    if m:
        return base[: m.start()]

    # Fallback: strip trailing digits/separators that look like a timestamp
    # Remove trailing portion after last non-alnum-dash separator
    stripped = re.sub(r"[-_]\d{4,}[-_\d]*$", "", base)
    if stripped and stripped != base:
        return stripped

    return base


# ── System-stamped mode ──

def _apply_system_stamped(folder, template, extn_set, keep):
    """Retention for system-stamped files."""
    # Find all stamped versions (real files matching stamped pattern)
    stamped_pattern = _template_to_glob(template, stamped=True)
    orig_pattern = _template_to_glob(template, stamped=False)

    all_files = [f.name for f in folder.iterdir()
                 if f.is_file()
                 and fnmatch.fnmatch(f.name, stamped_pattern)
                 and not fnmatch.fnmatch(f.name, orig_pattern)]

    if not all_files:
        return []

    if extn_set:
        # Group by base (strip extension and stamp)
        groups = defaultdict(list)
        for fname in all_files:
            base = _strip_extn_set(fname, extn_set)
            if base:
                groups[_strip_stamp(base)].append(fname)
            else:
                groups[_strip_stamp(fname)].append(fname)
    else:
        groups = defaultdict(list)
        for fname in all_files:
            groups[_strip_stamp(fname)].append(fname)

    deleted = []
    for group_key, files in groups.items():
        files.sort()
        if len(files) <= keep:
            continue

        to_delete = files[:-keep]
        for fname in to_delete:
            fpath = folder / fname
            if fpath.exists():
                fpath.unlink()
                deleted.append(str(fpath))

    # Re-link originals to newest remaining stamped version
    _relink_originals(folder, template, extn_set)

    return deleted


def _strip_stamp(name):
    """Remove YYMMDDHHmmss stamp from a filename to get the group key."""
    return _STAMP_RE.sub("", name)


def _relink_originals(folder, template, extn_set):
    """After retention prune, ensure originals are hardlinked to newest stamped version.

    If the stamped version an original was linked to was deleted,
    re-link to the newest remaining stamped version.
    """
    orig_pattern = _template_to_glob(template, stamped=False)

    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        if not fnmatch.fnmatch(entry.name, orig_pattern):
            continue

        # Find the latest stamped version for this original
        latest = _find_latest_stamped(folder, template, extn_set, entry.name)
        if not latest:
            # No stamped versions left — original is orphaned, leave it
            continue

        latest_path = folder / latest
        orig_inode = os.stat(entry).st_ino
        latest_inode = os.stat(latest_path).st_ino

        if orig_inode != latest_inode:
            # Original points to a deleted version — re-link
            entry.unlink()
            os.link(latest_path, entry)


def _find_latest_stamped(folder, template, extn_set, orig_name):
    """Find the newest stamped version of an original file."""
    stamped_pattern = _template_to_glob(template, stamped=True)
    orig_pattern = _template_to_glob(template, stamped=False)

    candidates = sorted([
        f.name for f in folder.iterdir()
        if f.is_file()
        and fnmatch.fnmatch(f.name, stamped_pattern)
        and not fnmatch.fnmatch(f.name, orig_pattern)
    ])

    return candidates[-1] if candidates else None


# ── Template helpers ──

def _template_to_glob(template, stamped=False):
    """Convert a retain_stamp template to a glob pattern.

    template: "report.js.$?" or "db-*.$?.sql.gz"
    stamped=False: "report.js" (match originals)
    stamped=True: "report.js.????????????" (match stamped — 12 digit stamp)
    """
    if stamped:
        return template.replace("$?", "????????????")
    else:
        # Original filename: remove the $? and any surrounding dot/separator
        # "report.js.$?" → "report.js"
        # "db-*.$?.sql.gz" → "db-*.sql.gz"
        result = template.replace(".$?", "").replace("$?.", "").replace("$?", "")
        return result


def _find_originals(folder, template, extn_set):
    """Find original (non-stamped) files matching the template."""
    orig_pattern = _template_to_glob(template, stamped=False)
    results = []
    for entry in folder.iterdir():
        if entry.is_file() and fnmatch.fnmatch(entry.name, orig_pattern):
            results.append(entry)
    return results


def _insert_stamp(filename, template, stamp, extn_set):
    """Insert a stamp into a filename based on the template.

    template: "report.js.$?" filename: "report.js" → "report.js.260314120000"
    template: "db-*.$?.sql.gz" filename: "db-main.sql.gz" → "db-main.260314120000.sql.gz"
    """
    # Find where $? sits in the template relative to the filename structure
    parts_template = template.replace("$?", "\x00")  # placeholder

    if ".\x00" in parts_template:
        # $? follows a dot: insert stamp after that dot position
        before_token = parts_template.split(".\x00")[0]
        after_token = parts_template.split(".\x00")[1] if ".\x00" in parts_template else ""

        # Match the before part against the filename
        if after_token:
            # "db-*" before, ".sql.gz" after
            if filename.endswith(after_token):
                base = filename[: -len(after_token)]
                return f"{base}.{stamp}{after_token}"
        else:
            return f"{filename}.{stamp}"

    elif "\x00." in parts_template:
        # $? precedes a dot: "backup-\x00.tar"
        after_dot = parts_template.split("\x00.")[1]
        suffix = f".{after_dot}"
        if filename.endswith(suffix):
            base = filename[: -len(suffix)]
            return f"{base}{stamp}{suffix}"

    elif "\x00" in parts_template:
        # $? with no surrounding dots
        before = parts_template.split("\x00")[0]
        after = parts_template.split("\x00")[1]
        if filename.endswith(after) and fnmatch.fnmatch(filename, template.replace("$?", "*")):
            base = filename[: -len(after)] if after else filename
            return f"{base}{stamp}{after}"

    return None


# ── Standalone test ──

if __name__ == "__main__":
    import shutil
    import tempfile

    print("Testing retention.py...")

    # ── Test pre-stamped mode (vzdump) ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Create vzdump files: 4 backups for vmid 100, 2 for vmid 108
        for ts in ["2026_03_10-02_00_00", "2026_03_11-02_00_00",
                    "2026_03_12-02_00_00", "2026_03_13-02_00_00"]:
            for ext in [".vma.zst", ".log", ".notes"]:
                (tmpdir / f"vzdump-lxc-100-{ts}{ext}").touch()
        for ts in ["2026_03_12-02_00_00", "2026_03_13-02_00_00"]:
            for ext in [".vma.zst", ".log", ".notes"]:
                (tmpdir / f"vzdump-lxc-108-{ts}{ext}").touch()

        deleted = apply_retention(tmpdir, {
            "pattern": "vzdump-*",
            "extn_set": [".vma.zst", ".log", ".notes"],
            "keep": 3,
        })

        # vmid 100: 4 backups, keep 3 → delete oldest (3 files)
        assert len(deleted) == 3, f"Expected 3 deleted, got {len(deleted)}: {deleted}"
        for d in deleted:
            assert "2026_03_10" in d, f"Wrong file deleted: {d}"

        # vmid 108: 2 backups, keep 3 → delete nothing
        remaining = list(tmpdir.iterdir())
        vmid108_files = [f for f in remaining if "108" in f.name]
        assert len(vmid108_files) == 6, f"Expected 6 vmid 108 files, got {len(vmid108_files)}"

        print("  pre-stamped (vzdump): OK")
    finally:
        shutil.rmtree(tmpdir)

    # ── Test group key extraction ──
    assert _extract_group_key("vzdump-lxc-100-2026_03_13-02_00_00") == "vzdump-lxc-100"
    assert _extract_group_key("vzdump-qemu-200-2026_03_13-02_00_00") == "vzdump-qemu-200"
    print("  group key extraction: OK")

    # ── Test extension stripping ──
    assert _strip_extn_set("file.vma.zst", [".vma.zst", ".log", ".notes"]) == "file"
    assert _strip_extn_set("file.log", [".vma.zst", ".log", ".notes"]) == "file"
    assert _strip_extn_set("file.txt", [".vma.zst", ".log", ".notes"]) is None
    print("  extension stripping: OK")

    # ── Test template helpers ──
    assert _template_to_glob("report.js.$?", stamped=True) == "report.js.????????????"
    assert _template_to_glob("report.js.$?", stamped=False) == "report.js"
    assert _template_to_glob("db-*.$?.sql.gz", stamped=True) == "db-*.????????????.sql.gz"
    assert _template_to_glob("db-*.$?.sql.gz", stamped=False) == "db-*.sql.gz"
    print("  template helpers: OK")

    # ── Test stamp insertion ──
    assert _insert_stamp("report.js", "report.js.$?", "260314120000", []) == "report.js.260314120000"
    assert _insert_stamp("db-main.sql.gz", "db-*.$?.sql.gz", "260314120000", [".sql.gz"]) == "db-main.260314120000.sql.gz"
    print("  stamp insertion: OK")

    # ── Test system-stamped mode ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Simulate 5 stamped versions + hardlinked original
        for stamp in ["260310120000", "260311120000", "260312120000",
                      "260313120000", "260314120000"]:
            (tmpdir / f"report.js.{stamp}").touch()
        # Hardlink original to latest
        os.link(tmpdir / "report.js.260314120000", tmpdir / "report.js")

        deleted = apply_retention(tmpdir, {
            "retain_stamp": "report.js.$?",
            "extn_set": [],
            "keep": 3,
        })

        assert len(deleted) == 2, f"Expected 2 deleted, got {len(deleted)}: {deleted}"
        remaining_stamps = sorted([f.name for f in tmpdir.iterdir()
                                   if f.name != "report.js"
                                   and f.is_file()])
        assert len(remaining_stamps) == 3
        assert "report.js.260312120000" in remaining_stamps
        assert "report.js.260313120000" in remaining_stamps
        assert "report.js.260314120000" in remaining_stamps
        # Original should still exist and be hardlinked to latest
        assert (tmpdir / "report.js").exists()
        assert os.stat(tmpdir / "report.js").st_ino == os.stat(tmpdir / "report.js.260314120000").st_ino
        print("  system-stamped retention: OK")
    finally:
        shutil.rmtree(tmpdir)

    # ── Test relink after retention prune ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Original hardlinked to oldest version (which will be pruned)
        (tmpdir / "report.js.260310120000").write_text("old")
        (tmpdir / "report.js.260314120000").write_text("new")
        os.link(tmpdir / "report.js.260310120000", tmpdir / "report.js")

        deleted = apply_retention(tmpdir, {
            "retain_stamp": "report.js.$?",
            "extn_set": [],
            "keep": 1,
        })

        # Should have deleted the old version and re-linked original to newest
        assert len(deleted) == 1
        assert (tmpdir / "report.js").exists()
        assert os.stat(tmpdir / "report.js").st_ino == os.stat(tmpdir / "report.js.260314120000").st_ino
        assert (tmpdir / "report.js").read_text() == "new"
        print("  relink after prune: OK")
    finally:
        shutil.rmtree(tmpdir)

    # ── Test post_sync_stamp — first sync ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Simulate first sync — real file exists, no stamped versions
        (tmpdir / "report.js").write_text("content v1")

        post_sync_stamp(tmpdir, {"retain_stamp": "report.js.$?", "extn_set": [], "keep": 3})

        # Should have been renamed and hardlinked
        stamped_files = [f for f in tmpdir.iterdir() if f.name != "report.js"]
        assert len(stamped_files) == 1, f"Expected 1 stamped file, got {stamped_files}"
        # Original and stamped should share inode
        assert os.stat(tmpdir / "report.js").st_ino == os.stat(stamped_files[0]).st_ino
        assert (tmpdir / "report.js").read_text() == "content v1"
        print("  post_sync_stamp (first sync): OK")
    finally:
        shutil.rmtree(tmpdir)

    # ── Test post_sync_stamp — unchanged content (same inode) ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Simulate: stamped version exists, original is hardlinked to it
        (tmpdir / "report.js.260314120000").write_text("content v1")
        os.link(tmpdir / "report.js.260314120000", tmpdir / "report.js")

        file_count_before = len(list(tmpdir.iterdir()))

        post_sync_stamp(tmpdir, {"retain_stamp": "report.js.$?", "extn_set": [], "keep": 3})

        # Should NOT create a new stamped version (same inode = unchanged)
        file_count_after = len(list(tmpdir.iterdir()))
        assert file_count_after == file_count_before, \
            f"Should not create new stamp when unchanged: {file_count_before} → {file_count_after}"
        print("  post_sync_stamp (unchanged): OK")
    finally:
        shutil.rmtree(tmpdir)

    # ── Test post_sync_stamp — changed content (different inode) ──
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Simulate: old stamped version exists, rsync overwrote original with new content
        (tmpdir / "report.js.260314120000").write_text("content v1")
        # Write new content directly (simulates rsync overwrite breaking hardlink)
        (tmpdir / "report.js").write_text("content v2")

        post_sync_stamp(tmpdir, {"retain_stamp": "report.js.$?", "extn_set": [], "keep": 3})

        # Should create a new stamped version
        all_files = sorted([f.name for f in tmpdir.iterdir()])
        assert len(all_files) == 3, f"Expected 3 files (old stamp, new stamp, original), got {all_files}"
        # Original should be hardlinked to newest stamp (not the old one)
        newest_stamp = sorted([f for f in all_files if f != "report.js" and f != "report.js.260314120000"])
        assert len(newest_stamp) == 1
        assert os.stat(tmpdir / "report.js").st_ino == os.stat(tmpdir / newest_stamp[0]).st_ino
        assert (tmpdir / "report.js").read_text() == "content v2"
        assert (tmpdir / "report.js.260314120000").read_text() == "content v1"
        print("  post_sync_stamp (changed): OK")
    finally:
        shutil.rmtree(tmpdir)

    print("All tests passed.")
