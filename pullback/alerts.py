"""Email alerts via SMTP. No auth — local network delivery."""

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

log = logging.getLogger("pullback")


def send_alert(cfg, subject, body):
    """Send an email alert. Fails silently — never blocks backups."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled"):
        return

    msg = EmailMessage()
    msg["From"] = email_cfg["from"]
    msg["To"] = email_cfg["to"]
    msg["Subject"] = f"[pullback] {subject}"
    msg["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.set_content(body)

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587), timeout=10) as smtp:
            smtp.ehlo()
            if email_cfg.get("smtp_tls", True):
                smtp.starttls()
            user = email_cfg.get("smtp_user")
            pwd = email_cfg.get("smtp_pass")
            if user and pwd:
                smtp.login(user, pwd)
            smtp.send_message(msg)
        log.info(f"Alert sent: {subject}")
    except Exception as e:
        log.error(f"Failed to send alert '{subject}': {e}")


def alert_sync_result(cfg, source_name, source_cfg, all_ok, state, duration, retention_summary=None):
    """Send sync success or failure alert."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled"):
        return

    if all_ok and not email_cfg.get("on_success"):
        return
    if not all_ok and not email_cfg.get("on_failure", True):
        return

    status = "OK" if all_ok else "FAILED"
    subject = f"{status}: {source_name}"

    lines = [
        f"Source: {source_name} ({source_cfg.get('host', '?')})",
        f"Status: {status}",
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duration: {duration:.0f}s",
        "",
    ]

    folders = state.get("folders", {})
    if not all_ok:
        failed = {k: v for k, v in folders.items() if not v.get("success")}
        if failed:
            lines.append("Failed folders:")
            for fpath, fstate in failed.items():
                err = fstate.get("error", "unknown")
                lines.append(f"  {fpath}: {err}")
            lines.append("")

    ok_folders = {k: v for k, v in folders.items() if v.get("success")}
    if ok_folders:
        lines.append("Successful folders:" if not all_ok else "Folders:")
        for fpath, fstate in ok_folders.items():
            lines.append(f"  {fpath}: OK")

    if retention_summary:
        lines.append("")
        lines.append("Retention:")
        for item in retention_summary:
            lines.append(f"  {item}")

    send_alert(cfg, subject, "\n".join(lines))


def alert_ransomware(cfg, source_name, folder_path, reason):
    """Send ransomware warning alert."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled") or not email_cfg.get("on_warning", True):
        return

    subject = f"RANSOMWARE WARNING: {source_name}/{folder_path}"
    body = "\n".join([
        f"Source: {source_name}",
        f"Folder: {folder_path}",
        f"Status: RANSOMWARE CHECK FAILED",
        "",
        f"Reason: {reason}",
        "",
        "Sync was SKIPPED for this folder.",
        "Action: Investigate the remote files before re-enabling sync.",
    ])
    send_alert(cfg, subject, body)


def alert_sync_start(cfg, source_name, source_cfg, folders):
    """Send sync start alert."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled") or not email_cfg.get("on_start"):
        return

    subject = f"STARTED: {source_name}"
    folder_list = "\n".join(f"  {f['path']}" for f in folders)
    body = "\n".join([
        f"Source: {source_name} ({source_cfg.get('host', '?')})",
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Folders:",
        folder_list,
    ])
    send_alert(cfg, subject, body)


def alert_disk_space(cfg, mount, used_pct, free_gb, total_gb):
    """Send disk space warning alert."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled") or not email_cfg.get("on_warning", True):
        return

    subject = f"DISK SPACE WARNING: {mount} {used_pct}% full"
    body = "\n".join([
        f"Backup volume at {mount} is {used_pct}% full.",
        f"Free: {free_gb:.1f} GB / {total_gb:.1f} GB total",
        "",
        "Action: free space, rotate old backups, or connect a larger drive.",
    ])
    send_alert(cfg, subject, body)


def alert_no_volume(cfg):
    """Send volume missing alert."""
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled") or not email_cfg.get("on_failure", True):
        return

    mount = cfg.get("mount_point", "/backup")
    flag = cfg.get("usb", {}).get("flag_file", ".pullback-volume")
    subject = f"NO VOLUME: {mount} not mounted"
    body = "\n".join([
        f"No backup volume detected at {mount}.",
        f"Flag file {flag} not found.",
        "",
        "Check USB drive connection.",
    ])
    send_alert(cfg, subject, body)
