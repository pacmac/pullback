"""pullback web dashboard — stdlib HTTP server."""

import json
import os
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import load_config
from state import load_state, get_progress, request_cancel


_BASE = Path(__file__).parent
_STATIC = _BASE / "static"
_cfg = None

# System stats snapshots for delta calculation
_sys_prev = {"time": 0, "cpu_idle": 0, "cpu_total": 0, "disk_sectors": 0, "rx": 0, "tx": 0}
_sys_cache = {"cpu_pct": 0, "disk_mb_s": 0, "net_mb_s": 0}


def _read_int(path, default=0):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def _get_system_stats():
    """Collect system stats from /proc. Returns cached values, updates every 2s."""
    global _sys_prev, _sys_cache

    now = time.time()
    if now - _sys_prev["time"] < 2:
        return _sys_cache

    # CPU from /proc/stat
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        cpu_total = sum(int(x) for x in parts[1:8])
        cpu_idle = int(parts[4])
    except (OSError, ValueError, IndexError):
        cpu_total = cpu_idle = 0

    # Disk from /proc/diskstats (sda)
    disk_sectors = 0
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 10 and fields[2] == "sda":
                    disk_sectors = int(fields[5]) + int(fields[9])
                    break
    except (OSError, ValueError):
        pass

    # Network
    rx = _read_int("/sys/class/net/eth0/statistics/rx_bytes")
    tx = _read_int("/sys/class/net/eth0/statistics/tx_bytes")

    # Calculate deltas
    dt = now - _sys_prev["time"] if _sys_prev["time"] > 0 else 1
    if dt > 0 and _sys_prev["time"] > 0:
        cpu_delta_total = cpu_total - _sys_prev["cpu_total"]
        cpu_delta_idle = cpu_idle - _sys_prev["cpu_idle"]
        _sys_cache["cpu_pct"] = round(100 - (cpu_delta_idle * 100 / max(cpu_delta_total, 1)))

        disk_delta = disk_sectors - _sys_prev["disk_sectors"]
        _sys_cache["disk_mb_s"] = round(disk_delta * 512 / 1024 / 1024 / dt)

        net_delta = (rx - _sys_prev["rx"]) + (tx - _sys_prev["tx"])
        _sys_cache["net_mb_s"] = round(net_delta / 1024 / 1024 / dt)

    _sys_prev = {"time": now, "cpu_idle": cpu_idle, "cpu_total": cpu_total,
                 "disk_sectors": disk_sectors, "rx": rx, "tx": tx}

    # Dirty pages
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Dirty:"):
                    _sys_cache["dirty_mb"] = int(line.split()[1]) // 1024
                    break
    except (OSError, ValueError):
        _sys_cache["dirty_mb"] = 0

    # RX dropped packets
    _sys_cache["rx_dropped"] = _read_int("/sys/class/net/eth0/statistics/rx_dropped")

    # NET_RX softirq distribution (delta-based, not cumulative)
    try:
        with open("/proc/softirqs") as f:
            for line in f:
                if "NET_RX" in line:
                    parts = line.split()
                    cpus = [int(x) for x in parts[1:]]
                    prev_cpus = _sys_prev.get("softirq_cpus")
                    if prev_cpus and len(prev_cpus) == len(cpus):
                        deltas = [c - p for c, p in zip(cpus, prev_cpus)]
                        delta_total = sum(deltas)
                        cpu0_pct = round(deltas[0] * 100 / max(delta_total, 1)) if delta_total > 0 else 0
                        _sys_cache["softirq_cpu0_pct"] = cpu0_pct
                    _sys_prev["softirq_cpus"] = cpus
                    break
    except (OSError, ValueError):
        _sys_cache["softirq_cpu0_pct"] = 0

    # Volume info
    mount = _cfg["mount_point"]
    flag = Path(mount) / _cfg["usb"]["flag_file"]
    _sys_cache["volume_mounted"] = flag.exists()
    try:
        st = os.statvfs(mount)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        _sys_cache["volume_total_gb"] = round(total / 1024**3)
        _sys_cache["volume_free_gb"] = round(free / 1024**3)
    except OSError:
        _sys_cache["volume_total_gb"] = 0
        _sys_cache["volume_free_gb"] = 0

    return _sys_cache


def _get_status():
    """Build full status response."""
    sources = {}
    for name, src_cfg in _cfg["sources"].items():
        sources[name] = {
            "host": src_cfg["host"],
            "state": load_state(name),
            "progress": get_progress(name),
            "folders": src_cfg["folders"],
        }
    return {
        "sources": sources,
        "system": _get_system_stats(),
    }


def _get_log(lines=30):
    """Read last N lines of log file."""
    log_path = _cfg["logging"]["file"]
    try:
        with open(log_path) as f:
            all_lines = f.readlines()
        return [l.rstrip() for l in all_lines[-lines:]]
    except OSError:
        return []


def _run_sync(source=None, folder=None):
    """Launch engine.py in its own systemd scope — survives web service restart."""
    venv_python = str(_BASE / "venv" / "bin" / "python3")
    engine = str(_BASE / "engine.py")
    inner_cmd = [venv_python, engine]
    if source:
        inner_cmd += ["--source", source]
    if folder:
        inner_cmd += ["--folder", folder]
    # systemd-run launches in a separate scope, not tied to pullback-web's cgroup
    cmd = ["systemd-run", "--scope", "--quiet"] + inner_cmd
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default stderr logging

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._file(_STATIC / "dashboard.html", "text/html")
        elif self.path == "/static/dashboard.css":
            self._file(_STATIC / "dashboard.css", "text/css")
        elif self.path == "/api/status":
            self._json(_get_status())
        elif self.path.startswith("/api/log"):
            # Parse ?lines=N
            lines = 30
            if "?" in self.path:
                params = dict(p.split("=") for p in self.path.split("?")[1].split("&") if "=" in p)
                lines = int(params.get("lines", 30))
            self._json({"lines": _get_log(lines)})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/run":
            body = self._read_body()
            source = body.get("source")
            folder = body.get("folder")
            _run_sync(source, folder)
            self._json({"ok": True, "message": "sync started"})
        elif self.path == "/api/cancel":
            body = self._read_body()
            source = body.get("source")
            if source:
                request_cancel(source)
                self._json({"ok": True, "message": "cancel requested"})
            else:
                self._json({"ok": False, "message": "source required"}, 400)
        else:
            self.send_error(404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}


def main():
    global _cfg
    _cfg = load_config()

    host = _cfg.get("web_host", "0.0.0.0")
    port = _cfg["web_port"]

    server = HTTPServer((host, port), Handler)
    print(f"pullback dashboard: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
