# Web Dashboard — Spec

## Overview

Single-page dark-themed web dashboard served by Python stdlib `http.server`.
Shows live sync progress, source status, system stats, and provides run/cancel controls.
No external dependencies — HTML/CSS/JS inline or in a single static file.

## Visual

Dark GitHub-style theme. Monospace font. Minimal, functional, no frameworks.

## Layout

```
┌─────────────────────────────────────────────────┐
│  pibak                              [Run All]   │
├─────────────────────────────────────────────────┤
│  System                                         │
│  CPU: 61%  DISK: 44 MB/s  NET: 53 MB/s         │
│  Backup volume: /backup (3.6TB, 2.1TB free)     │
├─────────────────────────────────────────────────┤
│  pve (proxmox.home)            [Run] [Cancel]   │
│                                                 │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░ 72%  38.9 MB/s          │
│  ETA 0:45:12  elapsed 1:22:03                   │
│  shares/prox-backups/dump/vzdump-lxc-100-...    │
│                                                 │
│  Folders:                                       │
│  ✓ shares/pac          2026-03-14 10:30  12s    │
│  ● shares/prox-backups/dump    syncing...       │
│  ○ shares/prox-backups/ellis/dump  pending      │
│                                                 │
│  Last sync: 2026-03-14 08:00  OK  (1h 22m)     │
├─────────────────────────────────────────────────┤
│  Log (last 20 lines)                            │
│  ...                                            │
└─────────────────────────────────────────────────┘
```

## Data sources

All data read from existing files — no new state mechanisms needed.

| Data | Source |
|------|--------|
| Sync progress | `state/progress/<source>.json` |
| Source status | `state/<source>.json` |
| Config (sources, folders) | `config.yaml` (loaded once) |
| Log tail | `logging.file` from config |
| System stats | `/proc/stat`, `/proc/diskstats`, `/sys/class/net/eth0/statistics/`, `df` |
| Volume info | `mount_point` + `df` |

## API endpoints

### GET /

Serves the dashboard HTML page.

### GET /api/status

Returns JSON with all sources, their state, and any active progress.

```json
{
  "sources": {
    "pve": {
      "host": "proxmox.home",
      "state": { ... },
      "progress": { ... },
      "folders": [ ... ]
    }
  },
  "system": {
    "cpu_pct": 61,
    "disk_mb_s": 44,
    "net_mb_s": 53,
    "volume_total_gb": 3600,
    "volume_free_gb": 2100,
    "volume_mounted": true
  }
}
```

### GET /api/log?lines=20

Returns last N lines of the log file as JSON array.

### POST /api/run

Start a sync. Body: `{"source": "pve"}` or `{"source": "pve", "folder": "shares/pac"}` or `{}` for all.

Launches `engine.py` as a subprocess. Returns immediately.

```json
{"ok": true, "message": "sync started"}
```

### POST /api/cancel

Cancel a running sync. Body: `{"source": "pve"}`

Creates the cancel flag file via `state.request_cancel()`.

```json
{"ok": true, "message": "cancel requested"}
```

## Frontend

### Polling

JavaScript polls `/api/status` every 2 seconds. Updates DOM in place.
When no sync is running, polls every 10 seconds.

### Progress bar

CSS-only progress bar. Width set by `overall_pct` from progress JSON.

### Folder status icons

- `✓` — last sync succeeded (green)
- `✗` — last sync failed (red)
- `●` — currently syncing (blue, animated pulse)
- `○` — pending / not yet synced (grey)

### System stats

CPU, disk IO, and network displayed as compact one-line bar.
Volume info shows total/free from `df` on mount point.

### Log viewer

Scrollable `<pre>` block showing last 20 log lines. Auto-scrolls to bottom.
Refreshes every 5 seconds.

## Implementation

### File: `web.py`

Single file. Uses `http.server.HTTPServer` and `BaseHTTPRequestHandler`.

- `do_GET` — serves `/`, `/api/status`, `/api/log`
- `do_POST` — handles `/api/run`, `/api/cancel`
- HTML/CSS/JS embedded as a string constant or read from `static/dashboard.html`
- System stats collected via `/proc` reads (same approach as `bottleneck.sh`)
- Run command spawns `engine.py` via `subprocess.Popen` (detached, non-blocking)

### Startup

```bash
./venv/bin/python3 web.py
```

Listens on `0.0.0.0:<web_port>` (default 8080 from config).

### systemd service (future)

```ini
[Unit]
Description=pibak web dashboard

[Service]
ExecStart=/usr/share/pac/venv/bin/python3 /usr/share/pac/web.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Reference

`docs/Backup Dashboard.html` — previous dashboard from the old project. Use as a
visual starting point but fix: tiny text sizes, missing system stats (CPU, disk IO,
network), missing volume info, missing per-folder status breakdown.

## Not in scope

- Authentication (trusted LAN only)
- WebSocket (polling is sufficient)
- Multiple simultaneous users
- Mobile-specific layout
