"""System monitor — SSOT for disk/net/dirty stats and averages.

Used by tune-set.py, engine.py, and web.py. All monitoring goes through here.
Persists rolling window to state/monitor.json so all consumers share the same averages.
"""

import json
import os
import time
from collections import deque
from pathlib import Path

import tuning

_MB = 1024 * 1024
_STATE_FILE = Path(__file__).parent / "state" / "monitor.json"
_WINDOW_SIZE = 150  # rolling window: last 150 samples (~5 min at 2s intervals)
_MIN_MBS = 6  # ignore samples below this (noise)


class Monitor:
    """Samples disk write, network receive, and dirty page stats.

    Each sample is written to a shared state file. Averages are computed
    from the rolling window so all consumers see the same values.
    """

    def __init__(self, mount_point="/backup", iface=None):
        self._mount_point = mount_point
        self._iface = iface or "eth0"

        # Detect device (re-detected on each sample in case drive changes)
        self._dev = tuning.block_device(mount_point) or "sda"

        # Previous sample for rate calculation
        self._prev_rx = self._read_rx_bytes()
        self._prev_disk = self._read_disk_sectors()
        self._prev_t = time.time()
        self._last_sample = {"net_mbs": 0, "disk_mbs": 0, "dirty_mb": 0, "writeback_mb": 0}

        # Ensure state dir exists
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    def sample(self):
        """Take a sample, return current rates and dirty stats.

        Rate-limited to minimum 2 second intervals. Returns cached result
        if called more frequently (e.g. multiple HTTP polls).

        Returns dict: {net_mbs, disk_mbs, dirty_mb, writeback_mb}
        Also persists to rolling window.
        """
        curr_t = time.time()
        dt = curr_t - self._prev_t

        # Rate limit — return cached if less than 2 seconds since last sample
        if dt < 2:
            return self._last_sample

        # Re-detect device in case drive was swapped
        new_dev = tuning.block_device(self._mount_point) or "sda"
        if new_dev != self._dev:
            self._dev = new_dev
            self._prev_disk = self._read_disk_sectors()
            self.reset()

        curr_rx = self._read_rx_bytes()
        curr_disk = self._read_disk_sectors()

        dirty_kb = tuning._read_meminfo("Dirty") or 0
        wb_kb = tuning._read_meminfo("Writeback") or 0

        net_mbs = int((curr_rx - self._prev_rx) / _MB / dt) if dt > 0 else 0
        disk_mbs = int((curr_disk - self._prev_disk) * 512 / _MB / dt) if dt > 0 else 0

        self._prev_rx = curr_rx
        self._prev_disk = curr_disk
        self._prev_t = curr_t

        # Persist to rolling window
        self._append_sample(net_mbs, disk_mbs, dirty_kb // 1024)

        self._last_sample = {
            "net_mbs": net_mbs,
            "disk_mbs": disk_mbs,
            "dirty_mb": dirty_kb // 1024,
            "writeback_mb": wb_kb // 1024,
        }
        return self._last_sample

    def averages(self):
        """Return rolling window averages (excluding noise < 6 MB/s).

        Returns dict: {net_avg, disk_avg, dirty_avg, net_samples, disk_samples}
        Reads from shared state file so all consumers get the same result.
        """
        window = self._read_window()

        net = [s["net"] for s in window if s["net"] >= _MIN_MBS]
        disk = [s["disk"] for s in window if s["disk"] >= _MIN_MBS]
        dirty = [s["dirty"] for s in window if s["dirty"] > 0]

        return {
            "net_avg": sum(net) // len(net) if net else 0,
            "disk_avg": sum(disk) // len(disk) if disk else 0,
            "dirty_avg": sum(dirty) // len(dirty) if dirty else 0,
            "net_samples": len(net),
            "disk_samples": len(disk),
        }

    def reset(self):
        """Clear rolling window."""
        try:
            _STATE_FILE.write_text("[]")
        except OSError:
            pass

    def is_idle(self, sample):
        """Check if a sample shows no activity."""
        return sample["net_mbs"] == 0 and sample["disk_mbs"] == 0

    def _append_sample(self, net_mbs, disk_mbs, dirty_mb):
        """Append a sample to the rolling window file."""
        window = self._read_window()
        window.append({
            "t": time.time(),
            "net": net_mbs,
            "disk": disk_mbs,
            "dirty": dirty_mb,
        })
        # Trim to window size
        if len(window) > _WINDOW_SIZE:
            window = window[-_WINDOW_SIZE:]
        try:
            _STATE_FILE.write_text(json.dumps(window))
        except OSError:
            pass

    def _read_window(self):
        """Read the rolling window from shared state file."""
        try:
            data = json.loads(_STATE_FILE.read_text())
            if isinstance(data, list):
                return data
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        return []

    def _read_rx_bytes(self):
        """Read network rx_bytes."""
        path = f"/sys/class/net/{self._iface}/statistics/rx_bytes"
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (IOError, ValueError):
            return 0

    def _read_disk_sectors(self):
        """Read sectors written from /proc/diskstats."""
        try:
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 10 and parts[2] == self._dev:
                        return int(parts[9])
        except (IOError, ValueError, IndexError):
            pass
        return 0
