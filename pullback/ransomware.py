"""Ransomware guard: pre-sync check using .fprint files."""

import hashlib
import math
import os
import random
import re
from pathlib import Path

from sync import build_dry_run_command, run_dry_run

# Known ransomware extensions
_RANSOM_EXTENSIONS = {
    ".encrypted", ".locked", ".crypto", ".crypt", ".locky", ".zepto",
    ".cerber", ".cerber3", ".wallet", ".onion", ".zzzzz", ".micro",
    ".crypted", ".enc", ".aes", ".rsa", ".cry", ".wncry", ".wcry",
    ".wnry", ".wanna", ".crab", ".gandcrab", ".hermes", ".ryuk",
    ".maze", ".clop", ".revil", ".sodinokibi", ".darkside", ".conti",
    ".lockbit", ".hive", ".blackcat", ".alphv", ".ransomware",
}


def check_ransomware(source_cfg, folder_cfg, cfg):
    """Run ransomware check for a folder before syncing.

    1. rsync --dry-run to get changed files
    2. Filter to files with local .fprint
    3. Hash/validate only those .fprint files
    4. Clean up .fprint for remote-deleted files
    5. Check entropy and ransomware extensions
    6. Calculate change ratio, abort if above threshold

    Returns (safe: bool, reason: str)
    """
    rw_cfg = cfg["ransomware"]
    mount_point = cfg["mount_point"]
    local_root = source_cfg["local_root"]
    folder_path = folder_cfg["path"].strip("/")
    local_base = Path(mount_point) / local_root / folder_path

    # Step 1: dry-run to get changed files
    cmd, _ = build_dry_run_command(
        source_cfg, folder_cfg, cfg["rsync"]["args"], mount_point,
        cfg.get("ssh")
    )
    changed_files = run_dry_run(cmd)

    if not changed_files:
        return True, "no changes detected"

    # Separate deletions (rsync marks with "deleting ")
    deletions = []
    updates = []
    for f in changed_files:
        if f.startswith("deleting "):
            deletions.append(f[len("deleting "):].strip())
        else:
            updates.append(f)

    # Step 4: clean up .fprint for remote-deleted files
    fprint_depth = rw_cfg["fprint_depth"]
    for deleted in deletions:
        _remove_fprint_for(local_base, deleted, fprint_depth)

    if not updates:
        return True, "only deletions, no updates to check"

    # Step 2: filter to files that have a local .fprint
    fprint_files = []
    for f in updates:
        fprint_path = _fprint_path_for(local_base, f, fprint_depth)
        if fprint_path and fprint_path.exists():
            fprint_files.append((f, fprint_path))

    if not fprint_files:
        return True, f"{len(updates)} changed files, none have .fprint files"

    # Step 3: sample and validate .fprint files
    sample_size = min(rw_cfg["sample_size"], len(fprint_files))
    sample = random.sample(fprint_files, sample_size)

    changed_count = 0
    suspicious_count = 0

    for rel_path, fprint_path in sample:
        local_file = local_base / rel_path
        if not local_file.exists():
            continue

        # Check if .fprint hash still matches local file
        stored_hash = _read_fprint(fprint_path)
        current_hash = _hash_file(local_file)

        if stored_hash and stored_hash != current_hash:
            changed_count += 1

        # Step 5: check for ransomware indicators
        if _has_ransom_extension(rel_path):
            suspicious_count += 1
        elif local_file.exists() and _is_high_entropy(local_file):
            suspicious_count += 1

    # Step 6: calculate change ratio
    if sample_size == 0:
        return True, "no samples to check"

    change_ratio = changed_count / sample_size
    threshold = rw_cfg["change_threshold"]

    if suspicious_count > 0:
        return False, (
            f"ransomware indicators: {suspicious_count} suspicious files "
            f"({change_ratio:.0%} change ratio, threshold {threshold:.0%})"
        )

    if change_ratio > threshold:
        return False, (
            f"change ratio {change_ratio:.0%} exceeds threshold {threshold:.0%} "
            f"({changed_count}/{sample_size} .fprint files changed)"
        )

    return True, f"passed ({change_ratio:.0%} change ratio, {sample_size} sampled)"


def create_fprints(local_base, fprint_depth):
    """Create .fprint files for a folder tree after successful sync.

    Called after sync to establish baseline fingerprints.
    Only creates .fprint for directories up to fprint_depth.
    """
    local_base = Path(local_base)
    if not local_base.exists():
        return

    for dirpath, dirnames, filenames in os.walk(local_base):
        rel_dir = Path(dirpath).relative_to(local_base)
        depth = len(rel_dir.parts) if str(rel_dir) != "." else 0
        if depth >= fprint_depth:
            dirnames.clear()
            continue

        fprint_dir = Path(dirpath) / ".fprint"
        fprint_dir.mkdir(exist_ok=True)

        for fname in filenames:
            if fname == ".fprint" or fname.startswith("."):
                continue
            filepath = Path(dirpath) / fname
            if filepath.is_file():
                h = _hash_file(filepath)
                if h:
                    (fprint_dir / fname).write_text(h)


def _fprint_path_for(local_base, rel_path, fprint_depth):
    """Get the .fprint file path for a given file, if within depth."""
    parts = Path(rel_path).parts
    if len(parts) < 1:
        return None
    # .fprint lives in the same directory as the file
    dir_depth = len(parts) - 1
    if dir_depth >= fprint_depth:
        return None
    parent = local_base / Path(rel_path).parent
    return parent / ".fprint" / parts[-1]


def _remove_fprint_for(local_base, rel_path, fprint_depth):
    """Remove .fprint file for a deleted remote file."""
    fprint_path = _fprint_path_for(local_base, rel_path, fprint_depth)
    if fprint_path:
        try:
            fprint_path.unlink()
        except FileNotFoundError:
            pass


def _read_fprint(path):
    """Read stored hash from .fprint file."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _hash_file(path, block_size=65536):
    """SHA-256 hash of a file's first 1MB (fast fingerprint)."""
    try:
        h = hashlib.sha256()
        remaining = 1024 * 1024  # 1MB cap
        with open(path, "rb") as f:
            while remaining > 0:
                chunk = f.read(min(block_size, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _has_ransom_extension(filename):
    """Check if file has a known ransomware extension."""
    name = filename.lower()
    for ext in _RANSOM_EXTENSIONS:
        if name.endswith(ext):
            return True
    return False


def _is_high_entropy(path, sample_bytes=4096):
    """Check if file has unusually high entropy (encrypted content)."""
    try:
        with open(path, "rb") as f:
            data = f.read(sample_bytes)
        if len(data) < 64:
            return False
        entropy = _shannon_entropy(data)
        return entropy > 7.5  # random/encrypted data is ~8.0
    except OSError:
        return False


def _shannon_entropy(data):
    """Calculate Shannon entropy of bytes."""
    if not data:
        return 0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    length = len(data)
    entropy = 0.0
    for count in freq:
        if count:
            p = count / length
            entropy -= p * math.log2(p)
    return entropy


# Run standalone to test
if __name__ == "__main__":
    import tempfile

    print("Testing ransomware.py...")

    # Test ransomware extension detection
    assert _has_ransom_extension("file.locked")
    assert _has_ransom_extension("doc.encrypted")
    assert not _has_ransom_extension("file.txt")
    assert not _has_ransom_extension("archive.tar.gz")
    print("  extension detection: OK")

    # Test entropy calculation
    # Low entropy (repeated bytes)
    low = bytes([65] * 1024)
    assert _shannon_entropy(low) < 1.0
    # High entropy (random bytes)
    high = bytes(range(256)) * 4
    assert _shannon_entropy(high) > 7.0
    print("  entropy calculation: OK")

    # Test hash file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"test content for hashing")
        f.flush()
        h1 = _hash_file(f.name)
        h2 = _hash_file(f.name)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex
        os.unlink(f.name)
    print("  file hashing: OK")

    # Test fprint path calculation
    base = Path("/backup/pve/shares/dis")
    fp = _fprint_path_for(base, "subdir/file.txt", 3)
    assert fp == base / "subdir" / ".fprint" / "file.txt"
    # Beyond depth
    fp = _fprint_path_for(base, "a/b/c/file.txt", 3)
    assert fp is None
    print("  fprint path: OK")

    print("All tests passed.")
