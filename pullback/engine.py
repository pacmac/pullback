"""Orchestrator: iterate sources → folders, run sync pipeline."""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import load_config
from state import (
    load_state, save_state,
    update_progress, clear_progress,
    is_cancel_requested, clear_cancel,
)
from sync import build_command, run_sync


log = logging.getLogger("pullback")


def _setup_logging(cfg):
    """Configure logging from config."""
    log_file = cfg["logging"]["file"]
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.addHandler(console)


def run_folder(source_name, source_cfg, folder_cfg, cfg, state):
    """Run sync pipeline for a single folder.

    Pipeline: ransomware check (if enabled) → rsync pull → retention (if pve-dump)
    Returns True on success.
    """
    folder_path = folder_cfg["path"]
    log.info(f"[{source_name}] syncing {folder_path}")

    # Clear any stale cancel flag
    clear_cancel(source_name)

    # Ransomware check (step 5, placeholder for now)
    if cfg["ransomware"]["enabled"]:
        try:
            from ransomware import check_ransomware
            safe, reason = check_ransomware(source_cfg, folder_cfg, cfg)
            if not safe:
                log.warning(f"[{source_name}] ransomware check FAILED for {folder_path}: {reason}")
                state["folders"][folder_path] = {
                    "last_synced_at": _now(),
                    "success": False,
                    "error": f"ransomware check failed: {reason}",
                }
                try:
                    from alerts import alert_ransomware
                    alert_ransomware(cfg, source_name, folder_path, reason)
                except ImportError:
                    pass
                return False
            log.info(f"[{source_name}] ransomware check passed for {folder_path}")
        except ImportError:
            log.warning(f"[{source_name}] ransomware.py not yet implemented, skipping check")

    # Build rsync command
    cmd, local_dest = build_command(
        source_cfg, folder_cfg, cfg["rsync"]["args"], cfg["mount_point"],
        cfg.get("ssh")
    )

    # Progress callback — writes to state/progress/ and console
    sync_start = time.time()

    def on_progress(p):
        p["step"] = f"syncing {folder_path}"
        p["source"] = source_name

        # Smoothed ETA based on elapsed time and percentage
        pct = p.get("overall_pct", 0)
        elapsed = time.time() - sync_start
        if pct > 1 and elapsed > 5:
            total_est = elapsed * 100 / pct
            remaining = total_est - elapsed
            mins, secs = divmod(int(remaining), 60)
            hours, mins = divmod(mins, 60)
            p["eta"] = f"{hours}:{mins:02d}:{secs:02d}"

        update_progress(source_name, p)
        # Live console progress
        import sys
        speed = p.get("speed", "")
        eta = p.get("eta", "")
        fname = p.get("current_file", "")
        line = f"\r[{source_name}] {folder_path}  {pct}%  {speed}  ETA {eta}  {fname}"
        sys.stderr.write(f"{line:<100}\r")
        sys.stderr.flush()

    # Check cancel before starting
    if is_cancel_requested(source_name):
        log.info(f"[{source_name}] cancel requested before sync, skipping {folder_path}")
        clear_cancel(source_name)
        return False

    # Run rsync
    update_progress(source_name, {
        "step": f"syncing {folder_path}",
        "source": source_name,
        "overall_pct": 0,
        "current_file": "",
        "speed": "",
        "bytes_transferred": 0,
        "elapsed": 0,
        "eta": "",
    })

    result = run_sync(cmd, local_dest, progress_callback=on_progress,
                      cancel_check=lambda: is_cancel_requested(source_name))

    # Update .fprint files after successful sync (if ransomware enabled)
    if result["success"] and cfg["ransomware"]["enabled"]:
        try:
            from ransomware import create_fprints
            create_fprints(local_dest, cfg["ransomware"]["fprint_depth"])
        except ImportError:
            pass

    # Retention: post-sync stamp then prune
    retention_cfg = folder_cfg.get("retention")
    if result["success"] and retention_cfg:
        try:
            from retention import apply_retention, post_sync_stamp

            # System-stamped: rename + symlink changed files
            if "retain_stamp" in retention_cfg:
                post_sync_stamp(local_dest, retention_cfg)

            # Prune old versions
            deleted = apply_retention(local_dest, retention_cfg)
            if deleted:
                log.info(f"[{source_name}] retention: deleted {len(deleted)} old backup files")
        except ImportError:
            log.warning(f"[{source_name}] retention.py not found, skipping")

    # Update folder state
    state["folders"][folder_path] = {
        "last_synced_at": _now(),
        "success": result["success"],
        "error": result["error"],
    }

    if result["success"]:
        log.info(f"[{source_name}] {folder_path} done — {result['bytes_total']} bytes in {result['duration']}s")
    else:
        log.error(f"[{source_name}] {folder_path} FAILED: {result['error']}")

    return result["success"]


def run_source(source_name, source_cfg, cfg):
    """Run sync pipeline for all folders in a source."""
    log.info(f"=== Source: {source_name} ({source_cfg['host']}) ===")

    try:
        from alerts import alert_sync_start
        alert_sync_start(cfg, source_name, source_cfg, source_cfg["folders"])
    except ImportError:
        pass

    state = load_state(source_name)
    state["last_run_started_at"] = _now()

    all_ok = True
    total_bytes = 0

    start = time.time()

    for folder_cfg in source_cfg["folders"]:
        ok = run_folder(source_name, source_cfg, folder_cfg, cfg, state)
        if not ok:
            all_ok = False

        # Check cancel between folders
        if is_cancel_requested(source_name):
            log.info(f"[{source_name}] cancel requested, stopping remaining folders")
            clear_cancel(source_name)
            all_ok = False
            break

    duration = time.time() - start

    state["last_run_success"] = all_ok
    state["last_sync_duration"] = round(duration, 1)
    if all_ok:
        state["last_success_at"] = _now()
        state["last_error"] = None
    else:
        state["last_error"] = "one or more folders failed"

    save_state(source_name, state)
    clear_progress(source_name)

    status = "OK" if all_ok else "FAILED"
    log.info(f"=== Source: {source_name} {status} ({duration:.1f}s) ===")

    try:
        from alerts import alert_sync_result
        alert_sync_result(cfg, source_name, source_cfg, all_ok, state, duration)
    except ImportError:
        pass

    return all_ok


def run_all(cfg, source_filter=None, folder_filter=None):
    """Run sync for all sources (or filtered subset).

    Args:
        cfg: loaded config dict
        source_filter: if set, only run this source name
        folder_filter: if set, only run this folder path (requires source_filter)
    """
    # Verify backup volume is mounted
    flag = Path(cfg["mount_point"]) / cfg["usb"]["flag_file"]
    if not flag.exists():
        log.error(f"No backup volume mounted — {flag} not found. Aborting.")
        try:
            from alerts import alert_no_volume
            alert_no_volume(cfg)
        except ImportError:
            pass
        return False

    # Check disk space
    mount = cfg["mount_point"]
    disk_warn_pct = cfg.get("disk_warn_pct", 90)
    try:
        st = os.statvfs(mount)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used_pct = round(100 - (free * 100 / total)) if total > 0 else 0
        total_gb = total / 1024**3
        free_gb = free / 1024**3
        if used_pct >= disk_warn_pct:
            log.warning(f"Disk space warning: {mount} is {used_pct}% full ({free_gb:.1f} GB free)")
            try:
                from alerts import alert_disk_space
                alert_disk_space(cfg, mount, used_pct, free_gb, total_gb)
            except ImportError:
                pass
    except OSError:
        pass

    all_ok = True

    for source_name, source_cfg in cfg["sources"].items():
        if source_filter and source_name != source_filter:
            continue

        if folder_filter:
            # Filter to specific folder — check config first, allow ad-hoc
            matching = [f for f in source_cfg["folders"] if f["path"] == folder_filter]
            if not matching:
                # Ad-hoc folder not in config — create minimal folder entry
                matching = [{"path": folder_filter}]
                log.info(f"Folder '{folder_filter}' not in config, syncing ad-hoc")
            filtered_cfg = dict(source_cfg)
            filtered_cfg["folders"] = matching
            ok = run_source(source_name, filtered_cfg, cfg)
        else:
            ok = run_source(source_name, source_cfg, cfg)

        if not ok:
            all_ok = False

    return all_ok


def _now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description="pullback rsync pull backup")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--source", default=None, help="Run only this source")
    parser.add_argument("--folder", default=None, help="Run only this folder (requires --source)")
    args = parser.parse_args()

    if args.folder and not args.source:
        parser.error("--folder requires --source")

    cfg = load_config(args.config)
    _setup_logging(cfg)

    log.info("pullback starting")
    ok = run_all(cfg, source_filter=args.source, folder_filter=args.folder)
    log.info(f"pullback finished: {'OK' if ok else 'FAILED'}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
