# Rsync Daemon Mode — No Encryption

## Why

SSH encryption uses ~97% of one Pi CPU core during rsync transfers. On a
trusted home LAN, encryption provides no security benefit. Rsync daemon
mode eliminates SSH entirely — direct rsync protocol on port 873.

Measured improvement: 78 MB/s (SSH aes128-ctr) → ~110 MB/s (daemon, no encryption).

## Setup on the source server (e.g. Proxmox)

### 1. Create `/etc/rsyncd.conf`

```ini
# /etc/rsyncd.conf
uid = root
gid = root
use chroot = yes
max connections = 4
log file = /var/log/rsyncd.log

[backup]
    path = /ssd8704t
    comment = Backup source
    read only = yes
    hosts allow = 192.168.0.0/24
    # Optional: restrict to Pi's IP only
    # hosts allow = 192.168.0.94
```

### 2. Enable and start rsyncd

```bash
systemctl enable rsync
systemctl start rsync
```

### 3. Test from the Pi

```bash
# List modules
rsync proxmox.home::

# List files in module
rsync proxmox.home::backup/shares/pac/

# Test transfer
rsync --archive --info=progress2 proxmox.home::backup/shares/pac/ /tmp/test-rsync/
```

## Pullback config

```yaml
sources:
  pve:
    host: proxmox.home
    transport: rsync
    rsync_module: backup
    remote_root: /
    folders:
      - path: shares/pac
      - path: shares/multimedia
        delete: true
```

Key differences from SSH mode:
- `transport: rsync` — uses daemon mode
- `rsync_module: backup` — the module name from rsyncd.conf
- `remote_root: /` — paths are relative to the module's path
- No SSH key needed

## Security

- **LAN only** — `hosts allow` in rsyncd.conf restricts access by IP
- **Read only** — the module is `read only = yes`, Pi can only pull
- **No auth** — no passwords, relies on network-level trust
- **No encryption** — data travels in plain text on the LAN

This is appropriate for a home network. Do NOT use on untrusted networks.
