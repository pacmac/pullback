"""System monitor — SSOT for disk/net/dirty stats and averages.

Used by tune-set.py, engine.py, and web.py. All monitoring goes through here.
"""

import os
import time

import tuning

_MB = 1024 * 1024


class Monitor:
    """Samples disk write, network receive, and dirty page stats."""

    def __init__(self, mount_point="/backup", iface=None):
        self._mount_point = mount_point
        self._dev = tuning.block_device(mount_point) or "sda"
        self._iface = iface or "eth0"

        # Previous sample for rate calculation
        self._prev_rx = self._read_rx_bytes()
        self._prev_disk = self._read_disk_sectors()
        self._prev_t = time.time()

        # Running averages (exclude zeros)
        self._net_samples = []
        self._disk_samples = []
        self._dirty_samples = []

    def sample(self):
        """Take a sample, return current rates and dirty stats.

        Returns dict: {net_mbs, disk_mbs, dirty_mb, writeback_mb}
        Also updates running averages.
        """
        curr_rx = self._read_rx_bytes()
        curr_disk = self._read_disk_sectors()
        curr_t = time.time()
        dt = curr_t - self._prev_t

        dirty_kb = tuning._read_meminfo("Dirty") or 0
        wb_kb = tuning._read_meminfo("Writeback") or 0

        net_mbs = int((curr_rx - self._prev_rx) / _MB / dt) if dt > 0 else 0
        disk_mbs = int((curr_disk - self._prev_disk) * 512 / _MB / dt) if dt > 0 else 0

        self._prev_rx = curr_rx
        self._prev_disk = curr_disk
        self._prev_t = curr_t

        # Update averages (exclude noise < 6 MB/s)
        if net_mbs >= 6:
            self._net_samples.append(net_mbs)
        if disk_mbs >= 6:
            self._disk_samples.append(disk_mbs)
        if dirty_kb > 0:
            self._dirty_samples.append(dirty_kb // 1024)

        return {
            "net_mbs": net_mbs,
            "disk_mbs": disk_mbs,
            "dirty_mb": dirty_kb // 1024,
            "writeback_mb": wb_kb // 1024,
        }

    def averages(self):
        """Return running averages excluding zero samples.

        Returns dict: {net_avg, disk_avg, dirty_avg, samples}
        """
        return {
            "net_avg": sum(self._net_samples) // len(self._net_samples) if self._net_samples else 0,
            "disk_avg": sum(self._disk_samples) // len(self._disk_samples) if self._disk_samples else 0,
            "dirty_avg": sum(self._dirty_samples) // len(self._dirty_samples) if self._dirty_samples else 0,
            "net_samples": len(self._net_samples),
            "disk_samples": len(self._disk_samples),
        }

    def reset(self):
        """Clear running averages."""
        self._net_samples.clear()
        self._disk_samples.clear()
        self._dirty_samples.clear()

    def is_idle(self, sample):
        """Check if a sample shows no activity."""
        return sample["net_mbs"] == 0 and sample["disk_mbs"] == 0

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
