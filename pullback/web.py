"""pullback web dashboard — stdlib HTTP server."""

import json
import os
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import load_config
from state import load_state, get_progress, request_cancel
from monitor import Monitor
import tuning


_BASE = Path(__file__).parent
_STATIC = _BASE / "static"
_cfg = None
_monitor = None  # initialised in main()

# CPU stats (not in monitor.py — CPU is web-only)
_cpu_prev = {"time": 0, "idle": 0, "total": 0}
_cpu_cache = 0


def _read_int(path, default=0):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def _get_system_stats():
    """Collect system stats. Uses monitor.py for disk/net/dirty, local for CPU/volume."""
    global _cpu_prev, _cpu_cache, _monitor

    if _monitor is None:
        iface = _cfg.get("tuning", {}).get("net_interface", "eth0")
        _monitor = Monitor(_cfg["mount_point"], iface)

    # Sample disk/net/dirty via monitor
    s = _monitor.sample()
    a = _monitor.averages()

    # CPU from /proc/stat (not in monitor — web-specific)
    now = time.time()
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        cpu_total = sum(int(x) for x in parts[1:8])
        cpu_idle = int(parts[4])
    except (OSError, ValueError, IndexError):
        cpu_total = cpu_idle = 0

    if _cpu_prev["time"] > 0:
        dt_total = cpu_total - _cpu_prev["total"]
        dt_idle = cpu_idle - _cpu_prev["idle"]
        _cpu_cache = round(100 - (dt_idle * 100 / max(dt_total, 1)))

    _cpu_prev = {"time": now, "idle": cpu_idle, "total": cpu_total}

    result = {
        "cpu_pct": _cpu_cache,
        "disk_mb_s": s["disk_mbs"],
        "net_mb_s": s["net_mbs"],
        "disk_avg": a["disk_avg"],
        "net_avg": a["net_avg"],
        "dirty_mb": s["dirty_mb"],
        "dirty_avg": a["dirty_avg"],
    }

    # RX dropped packets
    iface = _cfg.get("tuning", {}).get("net_interface", "eth0")
    result["rx_dropped"] = _read_int(f"/sys/class/net/{iface}/statistics/rx_dropped")

    # NET_RX softirq distribution
    try:
        with open("/proc/softirqs") as f:
            for line in f:
                if "NET_RX" in line:
                    parts = line.split()
                    cpus = [int(x) for x in parts[1:]]
                    prev_cpus = getattr(_get_system_stats, "_softirq_prev", None)
                    if prev_cpus and len(prev_cpus) == len(cpus):
                        deltas = [c - p for c, p in zip(cpus, prev_cpus)]
                        delta_total = sum(deltas)
                        result["softirq_cpu0_pct"] = round(deltas[0] * 100 / max(delta_total, 1)) if delta_total > 0 else 0
                    else:
                        result["softirq_cpu0_pct"] = 0
                    _get_system_stats._softirq_prev = cpus
                    break
    except (OSError, ValueError):
        result["softirq_cpu0_pct"] = 0

    # Volume info
    mount = _cfg["mount_point"]
    flag = Path(mount) / _cfg["usb"]["flag_file"]
    result["volume_mounted"] = flag.exists()
    try:
        st = os.statvfs(mount)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        result["volume_total_gb"] = round(total / 1024**3)
        result["volume_free_gb"] = round(free / 1024**3)
    except OSError:
        result["volume_total_gb"] = 0
        result["volume_free_gb"] = 0

    return result


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
        "config": {
            "disk_warn_pct": _cfg.get("disk_warn_pct", 90),
            "dirty_target": 80,
        },
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
        elif self.path == "/static/favicon.svg" or self.path == "/favicon.ico":
            self._file(_STATIC / "favicon.svg", "image/svg+xml")
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
        elif self.path == "/api/restart":
            self._json({"ok": True, "message": "restarting"})
            subprocess.Popen(["systemd-run", "--scope", "--quiet", "systemctl", "restart", "pullback-web"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.path == "/api/self-backup":
            script = str(_BASE / "scripts" / "self-backup.sh")
            keep = _cfg.get("self_backup", {}).get("keep", 2)
            subprocess.Popen(
                ["systemd-run", "--scope", "--quiet", "bash", script, f"--keep={keep}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._json({"ok": True, "message": "self-backup started"})
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
