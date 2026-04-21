"""Rsync wrapper: build command, run, parse progress."""

import os
import re
import subprocess
import time
from pathlib import Path


# Regex for rsync --info=progress2 output lines
# Example: "  1,234,567  50%   12.34MB/s    0:01:23"
_PROGRESS_RE = re.compile(
    r"^\s*(?P<bytes>[\d,]+)\s+"
    r"(?P<pct>\d+)%\s+"
    r"(?P<speed>\S+)\s+"
    r"(?P<eta>\S+)"
)


def build_command(source_cfg, folder_cfg, rsync_args, mount_point, ssh_cfg=None):
    """Build the rsync command list.

    Returns (cmd_list, local_dest_path).

    Transport modes:
      - ssh (default): rsync over SSH, uses ssh_cfg for key/cipher
      - rsync: rsync daemon mode, no encryption. Uses host::module/path syntax.
               Requires 'rsync_module' in source_cfg.
    """
    host = source_cfg["host"]
    remote_root = source_cfg["remote_root"].rstrip("/")
    folder_path = folder_cfg["path"].strip("/")
    local_root = source_cfg["local_root"]
    transport = source_cfg.get("transport", "ssh")

    local = str(Path(mount_point) / local_root / folder_path) + "/"

    args = list(rsync_args)

    # Per-folder --delete (default: false)
    if folder_cfg.get("delete", False):
        args.append("--delete")

    if transport == "rsync":
        # Rsync daemon mode — no SSH, no encryption
        module = source_cfg.get("rsync_module", "")
        if module:
            remote = f"{host}::{module}/{folder_path}/"
        else:
            remote = f"{host}::{remote_root.strip('/')}/{folder_path}/"
    else:
        # SSH mode (default)
        remote = f"{host}:{remote_root}/{folder_path}/"
        if ssh_cfg and ssh_cfg.get("key"):
            key_path = ssh_cfg["key"]
            if not os.path.isabs(key_path):
                key_path = str(Path(__file__).parent / key_path)
            ssh_cmd = f"ssh -i {key_path} -o StrictHostKeyChecking=accept-new"
            cipher = ssh_cfg.get("cipher")
            if cipher:
                ssh_cmd += f" -c {cipher}"
            args += ["-e", ssh_cmd]

    cmd = ["rsync"] + args + [remote, local]
    return cmd, local


def build_dry_run_command(source_cfg, folder_cfg, rsync_args, mount_point, ssh_cfg=None):
    """Build rsync --dry-run command. Strips --delete if present."""
    cmd, local = build_command(source_cfg, folder_cfg, rsync_args, mount_point, ssh_cfg)
    # Insert --dry-run after 'rsync'
    cmd.insert(1, "--dry-run")
    # Remove --delete variants (should not be there, but safety)
    cmd = [a for a in cmd if a not in ("--delete", "--delete-before", "--delete-after", "--delete-during")]
    return cmd, local


def run_sync(cmd, local_dest, progress_callback=None, cancel_check=None):
    """Run rsync and parse progress output.

    Args:
        cmd: rsync command list
        local_dest: local destination path (created if missing)
        progress_callback: callable(progress_dict) called on each progress update
        cancel_check: callable() returns True if cancel was requested — kills rsync

    Returns dict with: success, files_transferred, bytes_total, duration, error
    """
    start = time.time()
    current_file = None
    last_pct = 0
    last_bytes = 0
    last_speed = ""
    last_eta = ""

    try:
        Path(local_dest).mkdir(parents=True, exist_ok=True)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )

        # Read in chunks, split on \r and \n for progress2 output
        buf = b""
        cancelled = False
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            # Check cancel
            if cancel_check and not cancelled and cancel_check():
                proc.terminate()
                cancelled = True
            buf += chunk
            while b"\r" in buf or b"\n" in buf:
                # Split on whichever delimiter comes first
                r_pos = buf.find(b"\r")
                n_pos = buf.find(b"\n")
                if r_pos == -1: pos = n_pos
                elif n_pos == -1: pos = r_pos
                else: pos = min(r_pos, n_pos)

                line = buf[:pos].decode("utf-8", errors="replace").strip()
                buf = buf[pos+1:]

                if not line:
                    continue

                m = _PROGRESS_RE.match(line)
                if m:
                    last_bytes = int(m.group("bytes").replace(",", ""))
                    last_pct = int(m.group("pct"))
                    last_speed = m.group("speed")
                    last_eta = m.group("eta")

                    if progress_callback:
                        progress_callback({
                            "overall_pct": last_pct,
                            "bytes_transferred": last_bytes,
                            "speed": last_speed,
                            "eta": last_eta,
                            "current_file": current_file or "",
                            "elapsed": int(time.time() - start),
                        })
                else:
                    if not line.startswith("sending") and not line.startswith("total"):
                        current_file = line

        proc.wait()
        duration = time.time() - start

        if cancelled:
            return {
                "success": False,
                "bytes_total": last_bytes,
                "duration": round(duration, 1),
                "error": "cancelled by user",
            }
        elif proc.returncode == 0:
            return {
                "success": True,
                "bytes_total": last_bytes,
                "duration": round(duration, 1),
                "error": None,
            }
        else:
            stderr = proc.stderr.read() if proc.stderr else ""
            return {
                "success": False,
                "bytes_total": last_bytes,
                "duration": round(duration, 1),
                "error": stderr.strip() or f"rsync exited with code {proc.returncode}",
            }

    except Exception as e:
        return {
            "success": False,
            "bytes_total": 0,
            "duration": round(time.time() - start, 1),
            "error": str(e),
        }


def run_dry_run(cmd):
    """Run rsync --dry-run and return list of changed file paths.

    Returns list of relative file paths that would be transferred.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        changed = []
        for line in result.stdout.splitlines():
            line = line.strip()
            # Skip blank, summary, and directory lines
            if not line or line.startswith("sending") or line.startswith("total") or line.endswith("/"):
                continue
            changed.append(line)
        return changed
    except Exception:
        return []


# Run standalone to test command building
if __name__ == "__main__":
    import json
    from config import load_config

    cfg = load_config()
    for src_name, src in cfg["sources"].items():
        for folder in src["folders"]:
            cmd, dest = build_command(src, folder, cfg["rsync"]["args"], cfg["mount_point"])
            print(f"Source: {src_name}, Folder: {folder['path']}")
            print(f"  Command: {' '.join(cmd)}")
            print(f"  Dest:    {dest}")

            dry_cmd, _ = build_dry_run_command(src, folder, cfg["rsync"]["args"], cfg["mount_point"])
            print(f"  Dry-run: {' '.join(dry_cmd)}")
            print()
