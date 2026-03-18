#!/bin/bash
# pi-tune-install.sh — Install persistent performance tuning from merged config.
# Reads config.yaml + config.local.yaml via Python (respects local overrides).
# Writes sysctl config and creates a systemd oneshot for non-sysctl settings.
# Run as root on the target machine.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"

SYSCTL_DST="/etc/sysctl.d/99-pullback.conf"
SERVICE_NAME="pullback-tune"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"
TUNE_SCRIPT="${SCRIPT_DIR}/pi-tune-boot.sh"

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Error: venv not found at ${VENV_PYTHON} — run pyenv-setup.sh first" >&2
    exit 1
fi

# ── Read merged config via Python ──

read_cfg() {
    "$VENV_PYTHON" -c "
from config import load_config
cfg = load_config()
t = cfg.get('tuning', {})
u = cfg.get('usb', {})
for key in ['dirty_ratio', 'dirty_background_ratio', 'dirty_expire_centisecs',
            'dirty_writeback_centisecs', 'bdi_max_bytes', 'scheduler',
            'nr_requests', 'max_sectors_kb']:
    print(f'{key}={t.get(key, \"\")}')
for key in ['rps_enabled', 'eee_off']:
    print(f'{key}={str(t.get(key, False)).lower()}')
print(f'cpu_governor={t.get(\"cpu_governor\", \"performance\")}')
print(f'uas={str(u.get(\"uas\", False)).lower()}')
" 2>&1
}

# Run from project dir so imports work
cd "$PROJECT_DIR"
CFG_OUTPUT=$(read_cfg)
if [[ $? -ne 0 ]]; then
    echo "Error reading config:" >&2
    echo "$CFG_OUTPUT" >&2
    exit 1
fi

# Parse into variables
eval "$CFG_OUTPUT"

echo ""
echo "=== Config (merged) ==="
echo "  dirty_ratio            = ${dirty_ratio}"
echo "  dirty_background_ratio = ${dirty_background_ratio}"
echo "  dirty_expire_centisecs = ${dirty_expire_centisecs}"
echo "  dirty_writeback_centisecs = ${dirty_writeback_centisecs}"
echo "  bdi_max_bytes          = ${bdi_max_bytes}"
echo "  rps_enabled            = ${rps_enabled}"
echo "  eee_off                = ${eee_off}"
echo "  cpu_governor           = ${cpu_governor}"
echo "  scheduler              = ${scheduler}"
echo "  nr_requests            = ${nr_requests}"
echo "  max_sectors_kb         = ${max_sectors_kb}"
echo "  uas                    = ${uas}"
echo ""

# ── Write sysctl config via tuning.py ──

"$VENV_PYTHON" -c "
from config import load_config
from tuning import generate_sysctl_conf
cfg = load_config()
print(generate_sysctl_conf(cfg.get('tuning', {})), end='')
" > "$SYSCTL_DST"

sysctl --load="$SYSCTL_DST" >/dev/null 2>&1
echo "Installed: ${SYSCTL_DST}"

# ── Generate boot script via tuning.py ──

"$VENV_PYTHON" -c "
from config import load_config
from tuning import generate_boot_script
cfg = load_config()
print(generate_boot_script(cfg.get('tuning', {})), end='')
" > "$TUNE_SCRIPT"

chmod +x "$TUNE_SCRIPT"
echo "Generated: ${TUNE_SCRIPT}"

# ── UAS: force USB Attached SCSI for backup drive ──

if [[ "$uas" == "true" ]]; then
    CMDLINE="/boot/firmware/cmdline.txt"
    if [[ ! -f "$CMDLINE" ]]; then
        CMDLINE="/boot/cmdline.txt"
    fi

    if [[ -f "$CMDLINE" ]]; then
        USB_ID=$(lsusb | grep -i -E 'mass storage|external|canvio|seagate|wd|toshiba|hitachi|backup' | head -1 | grep -oP '\b[0-9a-f]{4}:[0-9a-f]{4}\b' || true)

        if [[ -z "$USB_ID" ]]; then
            USB_ID=$(lsusb | grep 'Bus 002' | grep -v 'root hub' | head -1 | grep -oP '\b[0-9a-f]{4}:[0-9a-f]{4}\b' || true)
        fi

        if [[ -n "$USB_ID" ]]; then
            PROTO=$(lsusb -v -d "$USB_ID" 2>/dev/null | grep 'bInterfaceProtocol' | head -1 | awk '{print $NF}')

            if [[ "$PROTO" == "Bulk-Only" ]]; then
                echo "UAS: ${USB_ID} Bulk-Only — not supported"
                sed -i "s| usb-storage.quirks=[0-9a-f:]*:u||g" "$CMDLINE"
            else
                QUIRK="usb-storage.quirks=${USB_ID}:u"
                sed -i "s| usb-storage.quirks=[0-9a-f:]*:u||g" "$CMDLINE"
                sed -i "s|rootwait|rootwait ${QUIRK}|" "$CMDLINE"
                echo "UAS: enabled for ${USB_ID} — REBOOT REQUIRED"
            fi
        else
            echo "UAS: no USB storage device detected"
        fi
    else
        echo "UAS: cmdline.txt not found"
    fi
fi

# ── Create systemd service ──

cat > "$SERVICE_DST" <<EOF
[Unit]
Description=pullback performance tuning
After=network.target

[Service]
Type=oneshot
ExecStart=${TUNE_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
echo "Installed: ${SERVICE_DST}"

# ── Summary ──

echo ""
echo "=== Applied ==="
"$VENV_PYTHON" -c "from tuning import status_yaml; print(status_yaml())"
echo ""
