# Email Alerts — Spec

## Overview

pibak sends email alerts via SMTP to notify of backup events. Sends directly
to the local mailcow instance on the home network — no authentication required.
Uses Python stdlib `smtplib` — no dependencies.

## Config (config.yaml)

```yaml
email:
  enabled: true
  smtp_host: mailcow.home
  smtp_port: 25
  from: pibak@home
  to: admin@example.com
  on_failure: true
  on_success: false
  on_warning: true
```

## Alert types

### Sync failure

Triggered when a source sync fails (any folder).

**Subject:** `[pibak] FAILED: pve`

**Body:**
```
Source: pve (proxmox.home)
Status: FAILED
Time: 2026-03-14 12:58:36
Duration: 414s

Failed folders:
  shares/prox-backups/dump: rsync exit code 23
  shares/prox-backups/ellis/dump: OK

Successful folders:
  shares/pac: OK
```

### Sync success

Triggered when all folders in a source complete successfully.
Only sent if `on_success: true`.

**Subject:** `[pibak] OK: pve`

**Body:**
```
Source: pve (proxmox.home)
Status: OK
Time: 2026-03-14 12:58:36
Duration: 414s
Bytes transferred: 18.9 GB

Folders:
  shares/pac: OK (0.7s)
  shares/prox-backups/dump: OK (120s, 5.2 GB)
  shares/prox-backups/ellis/dump: OK (293s, 13.7 GB)
```

### Ransomware warning

Triggered when ransomware check fails for a folder.

**Subject:** `[pibak] RANSOMWARE WARNING: pve/shares/dis`

**Body:**
```
Source: pve (proxmox.home)
Folder: shares/dis
Status: RANSOMWARE CHECK FAILED

Reason: 12 of 30 sampled files (40%) show high entropy (>7.5)
Threshold: 30%

Sync was SKIPPED for this folder.
Action: Investigate the remote files before re-enabling sync.
```

### Volume missing

Triggered when engine.py starts and no backup volume is mounted.

**Subject:** `[pibak] NO VOLUME: /backup not mounted`

**Body:**
```
No backup volume detected at /backup.
Flag file .pibak-volume not found.

Check USB drive connection.
```

### Retention summary

Included in the sync success/failure email, not a separate alert.

```
Retention:
  shares/prox-backups/dump: deleted 9 files (3 vzdump sets)
  shares/pac/retain_test.txt: 2 versions kept
```

## Implementation

### File: `alerts.py`

Single module. Key function:

```python
def send_alert(cfg, subject, body):
    """Send email using stdlib smtplib. No auth."""
```

Called from:
- `engine.py` — end of `run_all()` for success/failure
- `engine.py` — after ransomware check failure
- `engine.py` — when volume check fails

### Email format

- Plain text only (no HTML)
- `From:` from config
- `To:` from config
- `Subject:` prefixed with `[pibak]`
- `Date:` header set
- UTF-8 encoding

### Error handling

If SMTP send fails, log the error but do not crash the sync.
Email failures must never prevent backups from running.

## Not in scope

- HTML emails
- Multiple recipients (use a mail alias for that)
- Attachment of log files
- Rate limiting / deduplication
