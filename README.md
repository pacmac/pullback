# pullBack

A dedicated rsync pull-backup appliance. Pulls incremental backups over SSH or rsync daemon from remote servers to USB drives. Built for Raspberry Pi but runs on any Linux box.

## Why another backup solution?

Most backup tools push data from the source to the backup. pullBack does the opposite — it **pulls** from remote servers. This means:

- **The backup server controls the schedule**, not the source. No agents to install on production machines.
- **Read-only access** — the backup appliance only needs read access to the source. A compromised source can't delete backups.
- **Air-gapped by design** — unplug the USB drive and walk away. No cloud, no subscription, no vendor lock-in.
- **Ransomware detection** — fingerprint files and entropy analysis detect encryption before syncing.

## What makes it different?

- **Zero dependencies** — Python stdlib only (pyyaml is the sole pip dependency). No Docker, no database, no framework.
- **USB drive hot-swap** — plug in a drive, it's auto-detected and mounted. Flag file prevents accidental formatting of unknown drives.
- **Live web dashboard** — real-time progress, system stats (CPU, disk, network, dirty pages), per-folder sync control.
- **Performance tuned** — extensive kernel tuning for sustained transfers. Achieved **121 MB/s** on Raspberry Pi 4 (3.5x over untuned baseline).
- **Two transport modes** — SSH (encrypted, works anywhere) or rsync daemon (no encryption, gigabit wire speed on trusted LAN).
- **Retention management** — automatic pruning of old backup versions (pre-stamped vzdump and system-stamped hardlink modes).
- **Email alerts** — sync success/failure, ransomware warnings, disk space warnings via SMTP.

## Features

- Incremental rsync pull backups over SSH or rsync daemon
- Web dashboard with real-time progress and system monitoring
- CLI for manual sync, status, cancel
- Per-folder sync control from dashboard
- Per-folder `--delete` option (mirror mode)
- Ransomware detection (`.fprint` fingerprint files, entropy analysis)
- Backup retention management (vzdump pattern + hardlink-based versioning)
- USB drive auto-mount with flag file safety (never auto-formats)
- Email alerts (success, failure, ransomware warning, disk space, sync start)
- Configurable disk space warning threshold
- Performance auto-tuning with sweep mode
- Bottleneck monitoring daemon with min/avg/max reporting
- Config overlay system (`config.local.yaml` for credentials and per-host overrides)

## Quick Start

```bash
git clone https://github.com/pacmac/pullback.git
cd pullback/pullback/scripts

# Preview what setup will do
sudo bash setup.sh --dry-run

# Install
sudo bash setup.sh

# Pi-specific (captures system defaults for tuning baseline)
sudo bash pi-setup.sh
```

## Installation

### Any Linux host (x86/ARM)

```bash
git clone https://github.com/pacmac/pullback.git
cd pullback/pullback/scripts
sudo bash setup.sh
```

This will:
1. Create Python venv and install pyyaml
2. Generate SSH key in `keys/`
3. Create `config.local.yaml` from template
4. Install udev rule for USB auto-mount
5. Install and start web dashboard service

### Raspberry Pi

```bash
sudo bash pi-setup.sh
```

Runs the general setup above, then captures system defaults to `docs/TUNEDEFAULT.local.md` for tuning baseline. Does **not** apply tuning — that's done separately after testing.

### Post-install

1. Edit `pullback/config.local.yaml` with your SMTP credentials
2. Edit `pullback/config.yaml` with your sources
3. Copy SSH pubkey to remote host(s):
   ```bash
   ssh-copy-id -i pullback/keys/id_ed25519 root@YOUR_HOST
   ```
4. Connect a USB drive (auto-mounted if it has `.pullback-volume` flag file)
5. Initialise a new drive: `bash scripts/hd-init.sh /dev/sdX --format`
6. Dashboard: `http://<pi-ip>:8080/`

## Configuration

### config.yaml

Main configuration file (committed to git, no secrets).

```yaml
mount_point: /backup          # Where USB backup volume mounts
web_port: 8080
disk_warn_pct: 90             # Email alert when disk usage exceeds this %

sources:
  pve:
    host: proxmox.home
    remote_root: /data/
    transport: ssh              # "ssh" (default) or "rsync" (daemon, no encryption)
    rsync_module: backup        # Required when transport=rsync
    folders:
      - path: shares/documents
      - path: shares/media
        delete: true            # Mirror mode — delete files not on source
      - path: shares/backups/dump
        retention:
          pattern: "vzdump-*"
          extn_set: [.vma.zst, .log, .notes]
          keep: 3

rsync:
  args:
    - --archive
    - --numeric-ids
    - --partial
    - --info=progress2,name1

ssh:
  key: keys/id_ed25519
  cipher: aes128-gcm@openssh.com  # Use aes128-ctr on Pi (no AES-NI)

email:
  enabled: true
  on_failure: true
  on_success: false
  on_warning: true
  on_start: true

ransomware:
  enabled: false
  sample_size: 30
  change_threshold: 0.30
  fprint_depth: 3

usb:
  flag_file: .pullback-volume
  filesystem: ext4
  reserved_pct: 1

tuning:
  dirty_ratio: 5
  dirty_background_ratio: 2
  dirty_expire_centisecs: 1000
  dirty_writeback_centisecs: 500
  rps_enabled: true
  eee_off: true
  cpu_governor: performance
```

### config.local.yaml

Per-host overrides (gitignored, never committed). Created from `config.local.yaml.example` during setup. Merged on top of `config.yaml` using deep merge.

```yaml
email:
  smtp_host: your-smtp-host
  smtp_user: your-user
  smtp_pass: your-password
  from: pullback@yourdomain
  to: alerts@yourdomain

# Override tuning for this specific host
tuning:
  dirty_ratio: 5
  rps_enabled: false
  cpu_governor: ondemand

# Override transport
sources:
  pve:
    transport: rsync
    rsync_module: backup
    remote_root: /

# Override cipher for Pi
ssh:
  cipher: aes128-ctr
```

### Per-folder options

| Option | Default | Description |
|--------|---------|-------------|
| `path` | required | Remote folder path relative to `remote_root` |
| `delete` | `false` | Enable `--delete` to mirror source (removes files not on source) |
| `retention.pattern` | — | Glob pattern for pre-stamped files (e.g. vzdump) |
| `retention.extn_set` | — | File extensions to match for retention |
| `retention.keep` | — | Number of versions to keep |
| `retention.retain_stamp` | — | System-stamped retention with hardlinks |

## Transport Modes

### SSH (default)

Encrypted transfer over SSH. Works across any network.

```yaml
sources:
  myserver:
    host: server.example.com
    transport: ssh          # default, can be omitted
    remote_root: /data/
```

**Pi cipher tip:** Use `aes128-ctr` in `config.local.yaml` — 47% faster than `aes128-gcm` on Pi (no AES-NI hardware).

### Rsync daemon (no encryption)

Direct rsync protocol on port 873. No SSH overhead — reaches gigabit wire speed on trusted LAN.

```yaml
sources:
  myserver:
    host: server.local
    transport: rsync
    rsync_module: backup
    remote_root: /
```

Requires `rsyncd` on the source server. See [docs/RSYNCD.md](pullback/docs/RSYNCD.md) for setup.

## Performance Tuning

pullBack includes extensive tuning for sustained rsync transfers, particularly on Raspberry Pi 4.

### Measured results (Samsung SSD 870 4TB, Pi 4)

| Stage | Net | Improvement |
|-------|-----|-------------|
| Untuned baseline | 35 MB/s | — |
| + dirty_ratio=5 | 54 MB/s | +54% |
| + EEE disabled | 54 MB/s | prevents drops |
| + aes128-ctr cipher | 78 MB/s | +44% |
| + rsync daemon | **121 MB/s** | +55% |
| **Total** | **121 MB/s** | **3.5x baseline** |

### Auto-tuning

```bash
# Preview
bash scripts/autotune.sh --dry-run

# Binary test (on/off per param, needs active sync)
bash scripts/autotune.sh

# Sweep mode (find optimal value per sysctl param)
bash scripts/autotune.sh --sweep

# Custom sample duration (default 120s)
bash scripts/autotune.sh --sweep --sample=60
```

### Monitoring

```bash
# Interactive monitor
bash scripts/pi-bottleneck.sh

# Run as daemon, log to file
bash scripts/pi-bottleneck.sh --daemon

# Report from last N minutes of log
bash scripts/pi-bottleneck.sh --report=5
```

### Tuning docs

- [TUNING.md](pullback/docs/TUNING.md) — Parameter reference, rationale, procedure
- [TUNEDATA.md](pullback/docs/TUNEDATA.md) — Test results with before/after data
- [TUNEDEFAULT.md](pullback/docs/TUNEDEFAULT.md) — OS/kernel factory defaults

### Revert all tuning

```bash
sudo bash scripts/pi-tune-revert.sh
```

### Persist tuning

```bash
sudo bash scripts/pi-tune-install.sh
```

Reads merged config (config.yaml + config.local.yaml) and writes sysctl + systemd boot service.

## USB Drive Management

### Flag file safety

Every pullBack volume has a `.pullback-volume` flag file on its root. The udev auto-mount script:

- **Flag file found** — mount the drive
- **No flag file** — refuse to mount, log instructions
- **Never auto-formats** — formatting only via `hd-init.sh --format` with interactive YES confirmation

### Initialise a new drive

```bash
sudo bash scripts/hd-init.sh /dev/sdX --format
```

### Swap drives

Unplug old drive, plug in new one. If it has a `.pullback-volume` flag file, it auto-mounts. If not, initialise it first.

## Web Dashboard

Real-time monitoring at `http://<host>:8080/`

- System stats: CPU, disk throughput, network throughput, dirty pages, RX drops, IRQ balance
- Per-source status with Run/Cancel buttons
- Per-folder sync buttons
- Live progress bar with speed, bytes transferred, elapsed time
- Disk usage bar with configurable warning threshold
- Log viewer

## CLI

```bash
cd pullback
venv/bin/python3 cli.py sync                          # Sync all sources
venv/bin/python3 cli.py sync --source pve             # Sync one source
venv/bin/python3 cli.py sync --source pve --folder shares/pac  # Sync one folder
venv/bin/python3 cli.py status                        # Show status
venv/bin/python3 cli.py cancel --source pve           # Cancel running sync
venv/bin/python3 cli.py config                        # Show loaded config
```

## Scripts

### General
| Script | Description |
|--------|-------------|
| `setup.sh` | Full install (venv, SSH keys, udev, web service) |
| `pyenv-setup.sh` | Create Python venv and install pyyaml |
| `ssh-setup.sh` | Generate SSH key and configure access |
| `udev-install.sh` | Install udev rule and systemd mount service |
| `udev-mount.sh` | Called by udev on USB plug-in (mount or refuse) |
| `web-install.sh` | Install web dashboard systemd service |
| `hd-init.sh` | Initialise USB drive (format with confirmation) |
| `autotune.sh` | Automated tuning with binary test and sweep modes |

### Pi-specific
| Script | Description |
|--------|-------------|
| `pi-setup.sh` | General setup + capture system defaults |
| `pi-tune-install.sh` | Persist tuning to sysctl + systemd boot service |
| `pi-tune-revert.sh` | Revert all tuning to OS defaults |
| `pi-tune-boot.sh` | Applied on boot (RPS, EEE, governor) |
| `pi-bottleneck.sh` | Performance monitor with daemon and report modes |
| `pi-capture-defaults.sh` | Capture system defaults before tuning |

## Project Structure

```
pullback/
  config.yaml              # Main config (committed)
  config.local.yaml        # Per-host overrides (gitignored)
  config.local.yaml.example
  engine.py                # Sync orchestrator
  sync.py                  # rsync wrapper
  config.py                # Config loader with deep merge
  state.py                 # JSON state persistence
  cli.py                   # Command-line interface
  web.py                   # Web dashboard server
  alerts.py                # Email alerts
  ransomware.py            # Ransomware detection
  retention.py             # Backup version pruning
  scripts/                 # Setup and maintenance scripts
  static/                  # Dashboard HTML/CSS/JS
  udev/                    # udev rules and systemd service
  docs/                    # Specifications and test data
  keys/                    # SSH keys (gitignored)
  state/                   # Runtime state (gitignored)
```

## License

MIT
