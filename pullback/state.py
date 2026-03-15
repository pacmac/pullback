"""State, progress, and cancel persistence. All files in project folder."""

import json
import os
import time
from pathlib import Path

_BASE = Path(__file__).parent
_STATE_DIR = _BASE / "state"
_PROGRESS_DIR = _STATE_DIR / "progress"
_CANCEL_DIR = _STATE_DIR / "cancel"


def _ensure_dirs():
    _STATE_DIR.mkdir(exist_ok=True)
    _PROGRESS_DIR.mkdir(exist_ok=True)
    _CANCEL_DIR.mkdir(exist_ok=True)


def _read_json(path):
    """Read JSON file, return empty dict on missing/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path, data):
    """Write JSON file atomically (write tmp, rename)."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(path)


# ── State (per-source) ──

def _state_path(source_name):
    return _STATE_DIR / f"{source_name}.json"


def load_state(source_name):
    """Load state for a source. Returns dict with defaults for missing fields."""
    _ensure_dirs()
    state = _read_json(_state_path(source_name))
    state.setdefault("last_run_started_at", None)
    state.setdefault("last_run_success", None)
    state.setdefault("last_success_at", None)
    state.setdefault("last_error", None)
    state.setdefault("last_files_total", 0)
    state.setdefault("last_sync_bytes", 0)
    state.setdefault("last_sync_duration", 0.0)
    state.setdefault("folders", {})
    return state


def save_state(source_name, state):
    """Save state for a source."""
    _ensure_dirs()
    _write_json(_state_path(source_name), state)


# ── Progress (per-source, ephemeral) ──

def _progress_path(source_name):
    return _PROGRESS_DIR / f"{source_name}.json"


def update_progress(source_name, progress):
    """Write current sync progress for a source."""
    _ensure_dirs()
    progress["updated_at"] = time.time()
    progress["pid"] = os.getpid()
    _write_json(_progress_path(source_name), progress)


def get_progress(source_name):
    """Read current sync progress. Returns empty dict if stale/none.

    Auto-clears progress if the owning process is dead or the file
    hasn't been updated in 60 seconds.
    """
    data = _read_json(_progress_path(source_name))
    if not data:
        return {}

    # Check if process is alive
    pid = data.get("pid")
    if pid:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            # Process is dead — stale progress
            clear_progress(source_name)
            return {}

    # Check if update is stale (>60s old)
    updated = data.get("updated_at", 0)
    if time.time() - updated > 60:
        clear_progress(source_name)
        return {}

    return data


def clear_progress(source_name):
    """Remove progress file after sync completes."""
    path = _progress_path(source_name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ── Cancel (per-source, flag file) ──

def _cancel_path(source_name):
    return _CANCEL_DIR / source_name


def request_cancel(source_name):
    """Write cancel flag for a source."""
    _ensure_dirs()
    _cancel_path(source_name).touch()


def is_cancel_requested(source_name):
    """Check if cancel was requested."""
    return _cancel_path(source_name).exists()


def clear_cancel(source_name):
    """Remove cancel flag."""
    try:
        _cancel_path(source_name).unlink()
    except FileNotFoundError:
        pass


# Run standalone to test
if __name__ == "__main__":
    import sys

    print("Testing state.py...")

    # Test state round-trip
    save_state("_test", {"last_run_success": True, "last_error": None, "folders": {"a": {"success": True}}})
    s = load_state("_test")
    assert s["last_run_success"] is True
    assert s["folders"]["a"]["success"] is True
    print("  state round-trip: OK")

    # Test defaults on missing
    s = load_state("_nonexistent")
    assert s["last_run_started_at"] is None
    assert s["last_sync_bytes"] == 0
    print("  missing state defaults: OK")

    # Test progress round-trip
    update_progress("_test", {"overall_pct": 42, "speed": "5MB/s"})
    p = get_progress("_test")
    assert p["overall_pct"] == 42
    assert p["speed"] == "5MB/s"
    assert "updated_at" in p
    clear_progress("_test")
    assert get_progress("_test") == {}
    print("  progress round-trip: OK")

    # Test cancel flag
    assert not is_cancel_requested("_test")
    request_cancel("_test")
    assert is_cancel_requested("_test")
    clear_cancel("_test")
    assert not is_cancel_requested("_test")
    print("  cancel flag: OK")

    # Cleanup test state file
    _state_path("_test").unlink(missing_ok=True)

    print("All tests passed.")
