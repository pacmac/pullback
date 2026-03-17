# Performance Tuning — Spec

## Overview

Sustained rsync pull over SSH to a USB HDD degrades significantly over time
without tuning. This document is the **single source of truth** for all
tuning parameters, their rationale, and status.

See `TUNEDEFAULT.md` for OS/kernel factory defaults.
See `TUNEDATA.md` for test results.

## Tuning Targets

| Metric | Target | Untuned baseline | Achieved |
|--------|--------|-----------------|----------|
| Net throughput | ~55 MB/s | 35 MB/s (drops to 16 over time) | **121 MB/s** |
| Disk throughput | ~55 MB/s | 31 MB/s (drops to 16 over time) | **114 MB/s** |
| Dirty pages | < 80 MB | 584 MB avg, 624 MB max | **42 MB** |

Achieved with: dirty_ratio=5, EEE off, rsync daemon (no encryption). 3.5x baseline.

## Baseline problem (untuned)

Measured over 12 hours of continuous rsync from proxmox.home to Pi 4 USB HDD:

| Metric | Start | After 12h |
|--------|-------|-----------|
| Network throughput | 51 MB/s | 16 MB/s |
| Disk write speed | ~50 MB/s | ~16 MB/s |
| Dirty pages | ~60 MB | 568 MB |
| NET_RX softirqs on CPU0 | 100% | 100% |
| CPU governor | ondemand | ondemand |
| EEE | enabled | enabled |
| RPS | disabled | disabled |

## Root causes identified

### 1. NET_RX softirq imbalance

The bcmgenet ethernet driver processes ALL network receive softirqs on CPU0.
After 12 hours: 1.3 billion softirqs on CPU0, 10-28 on other cores.
CPU0 becomes saturated handling both network interrupts and SSH encryption.

**Evidence:**
```
NET_RX: 1,323,817,618    10    28    19
rps_cpus: 0 (disabled)
```

**Fix:** Enable RPS (Receive Packet Steering) to distribute to CPU2+3.

### 2. EEE (Energy Efficient Ethernet)

bcmgenet EEE bug causes periodic link negotiation, dropping packets.
240 dropped RX packets over 12 hours. Well-documented Pi 4 issue.

**Fix:** Disable EEE via ethtool.

### 3. CPU governor

Default `ondemand` governor scales CPU frequency down during perceived idle,
adding latency to interrupt processing and SSH encryption.

**Evidence:** After reboot with `ondemand`, speeds dropped to 35-45 MB/s.
Switching to `performance` immediately restored 54 MB/s.

**Fix:** Set governor to `performance`.

### 4. Dirty page accumulation

With default `dirty_ratio=20` (800MB on 4GB), the kernel buffers huge amounts
of write data before flushing. The USB HDD cannot sustain the flush rate,
causing periodic stalls where network throughput drops to near zero.

**Evidence:**
- Default dirty_ratio=20: dirty pages reached 568MB, throughput 16 MB/s
- dirty_ratio=5 + dirty_background_ratio=2: dirty pages stayed 57-65MB, throughput 54-55 MB/s

**Fix:** Lower dirty ratios to force earlier, smaller flushes.

**Better fix:** Use per-device BDI dirty limits (see BDI section below).
`dirty_ratio` is a **global** limit — on slow USB devices it throttles writes
to ALL devices including the SD card, which is why `dirty_bytes=48MB` stalled
the entire system. BDI `strict_limit` + `max_bytes` constrains only the slow
device while leaving the rest of the system unaffected.

---

## Parameter Reference

Status key:
- **proven** — tested and validated with measured improvement
- **proposed** — research indicates benefit, needs testing
- **default-ok** — OS default is already optimal, no change needed

### VM / Dirty Pages

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| `vm.dirty_ratio` | 20 | 5 | proven | general |
| `vm.dirty_background_ratio` | 10 | 2 | proven | general |
| `vm.dirty_expire_centisecs` | 3000 | 1000 | proven | general |
| `vm.dirty_writeback_centisecs` | 500 | 500 | proven | general |

**Rationale:** On 4GB RAM, `dirty_ratio=20` allows 800MB dirty — far more
than a USB HDD can flush without stalling. `dirty_ratio=5` (~200MB) keeps
dirty pages manageable. Dirty pages averaged 57-65MB with these settings.

**Rejected: global `dirty_bytes`**

| Parameter | Tested | Result |
|-----------|--------|--------|
| `vm.dirty_bytes` | 50331648 (48 MB) | **stalled system completely** |
| `vm.dirty_background_bytes` | 16777216 (16 MB) | (tested together) |

Global `dirty_bytes` throttles writes to ALL block devices, not just the
backup drive. With a cap too low for the incoming data rate, all writers
are blocked by `balance_dirty_pages()`. This is why `dirty_bytes=48MB`
stalled the system.

### BDI Per-Device Dirty Limits

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| `/sys/block/sda/bdi/strict_limit` | 0 | 1 | proven | general |
| `/sys/block/sda/bdi/max_bytes` | 0 (unlimited) | 83886080 (80 MB) | proven | general |

**Rationale:** The kernel's dirty page throttling (`balance_dirty_pages()`) is
**global** — when a slow USB device accumulates dirty pages, writes to ALL
devices are throttled. BDI (Backing Device Info) `strict_limit` enables
**per-device** enforcement: only writers to the backup drive are throttled when
that device's dirty pages approach `max_bytes`. Writes to other devices
(SD card, tmpfs, etc.) are unaffected.

**How it works:** The kernel already implements a feedback loop in
`balance_dirty_pages()`. It estimates per-device write bandwidth, calculates
a proportional throttle ratio, and pauses dirtying processes for 10-200ms as
dirty pages approach the limit. `strict_limit=1` makes this per-device check
active even when the system-wide dirty level is low. No userspace feedback
loop is needed.

**Evidence:**
- Without BDI limit: `dirty_ratio=5` still allowed 127-170 MB dirty
- With `strict_limit=1` + `max_bytes=80MB`: dirty stayed 15-55 MB, self-correcting
- Global `dirty_bytes=48MB` stalled the entire system; BDI `max_bytes=80MB` did not

**Apply:**
```bash
echo 1 > /sys/block/sda/bdi/strict_limit
echo 83886080 > /sys/block/sda/bdi/max_bytes
```

**Available BDI sysfs attributes (kernel 6.1+):**
- `strict_limit` — enforce per-device dirty checks before global thresholds
- `max_bytes` — absolute byte limit on dirty pages for this device
- `max_ratio` — percentage-based limit (less precise)
- `min_bytes` / `min_ratio` — guarantee minimum writeback cache share (QoS)

**Why not dynamic userspace tuning?** The kernel's `balance_dirty_pages()`
already IS a feedback loop — it continuously estimates device bandwidth and
proportionally throttles writers. A userspace script adjusting `dirty_bytes`
would fight this mechanism, and the global nature of `dirty_bytes` means it
would affect all devices. BDI `max_bytes` operates per-device within the
kernel's own feedback loop, which is strictly superior.

### Network Stack

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| `net.core.rmem_max` | 212992 | 16777216 | proposed | general |
| `net.core.wmem_max` | 212992 | 16777216 | proposed | general |
| `net.ipv4.tcp_rmem` | 4096 131072 6291456 | 4096 131072 16777216 | proposed | general |
| `net.ipv4.tcp_wmem` | 4096 16384 4194304 | 4096 131072 16777216 | proposed | general |
| `net.core.netdev_max_backlog` | 1000 | 5000 | proposed | general |
| `net.ipv4.tcp_slow_start_after_idle` | 1 | 0 | proposed | general |
| `net.ipv4.tcp_congestion_control` | cubic | cubic | default-ok | general |
| `net.ipv4.tcp_window_scaling` | 1 | 1 | default-ok | general |
| `net.ipv4.tcp_timestamps` | 1 | 1 | default-ok | general |
| `net.ipv4.tcp_sack` | 1 | 1 | default-ok | general |

**Rationale:** Larger buffers allow TCP windows to grow for sustained
throughput. `tcp_slow_start_after_idle=0` prevents congestion window reset
between files. Network buffers are **low impact** on gigabit LAN with sub-ms
RTT — the bottleneck is USB HDD or SSH CPU, not the network.

**Note:** CUBIC is correct for LAN. BBR provides no advantage at <1ms RTT
and can cause unnecessary retransmissions.

### Network Hardware (Pi-specific)

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| RPS `rps_cpus` | 0 | 0xc (CPU2+3) | proven | pi |
| RPS `rps_sock_flow_entries` | 0 | 32768 | proven | pi |
| RFS `rps_flow_cnt` | 0 | 2048 | proposed | pi |
| EEE | enabled | disabled | proven | pi |

**Rationale (RPS):** Pi 4 bcmgenet is single-queue. Without RPS, CPU0
handles all network softirqs and saturates. RPS distributes processing to
other cores. Current mask `0xc` (CPU2+3) leaves CPU0+1 for other work.

**Proposed (RPS broader):** Consider `0xf` (all 4 cores) — research suggests
full distribution may be better than reserving cores.

**Proposed (RFS):** Receive Flow Steering directs packets to the CPU running
the receiving application, improving cache locality.

**Rationale (EEE):** bcmgenet EEE bug drops packets. Saves negligible power
on a dedicated appliance.

### CPU (Pi-specific)

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| CPU governor | ondemand | performance | proven | pi |

**Rationale:** Max clock frequency eliminates frequency scaling latency.
Measured with BDI+RPS enabled, rsync daemon mode: performance avg=51 MB/s
vs ondemand avg=40 MB/s (+25%, 300s, 27 samples each).

**Caveat:** Pi 4 Cortex-A72 throttles at ~80C. Ensure adequate cooling
(heatsink or fan).

### Disk I/O

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| I/O scheduler | varies | mq-deadline | proposed | general |
| Block read-ahead | 256 sectors (128 KB) | 256 sectors (128 KB) | rejected | general |

**Rationale (scheduler):** `mq-deadline` provides deadline guarantees for
rotational media, preventing starvation. `bfq` has higher CPU overhead.
`none`/`noop` is for SSDs only — USB HDDs have physical seek times.

**Rationale (read-ahead):** Tested 4096 sectors (2MB) vs default 256 sectors.
Throughput halved (Net 25→13 MB/s). Read-ahead primarily affects reads, not
writes. Default 256 is correct for this write-heavy workload.

**Apply via udev rule:**
```
# /etc/udev/rules.d/60-pullback-ioscheduler.rules
ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/scheduler}="mq-deadline"
```

**Apply read-ahead:** `blockdev --setra 4096 /dev/sda`

### ext4 Mount Options

| Option | Default | Value | Status | Category |
|--------|---------|-------|--------|----------|
| `noatime` | relatime | noatime | proven | general |
| `nodiratime` | relatime | nodiratime | proposed | general |
| `commit` | 5 | 60 | proven | general |
| `data` | ordered | ordered | default-ok | general |
| `barrier` | 1 | 1 | default-ok | general |

**Rationale:** `noatime` eliminates a write per read. `commit=60` reduces
journal commit frequency (default 5s is excessive for bulk writes).
`data=ordered` is the right balance of safety and performance for a backup
appliance. `data=writeback` is ~5-10% faster but risks stale data after crash.

### USB

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| UAS protocol | auto-detect | force UAS if supported | proven | general |

**Rationale:** UAS supports command queuing and out-of-order completion —
20-50% throughput gain over BOT. Not all USB-SATA bridges support it.

**Check:** `lsusb -t` (look for `Driver=uas` vs `Driver=usb-storage`)

### rsync

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| `--whole-file` | off (delta) | on | proposed | general |
| `--inplace` | off (temp+rename) | on | proposed | general |
| `--compress` | off | off | default-ok | general |

**Rationale (--whole-file):** On LAN, sending the whole file is faster than
computing checksums on both ends. rsync auto-detects for local transfers but
**not** over SSH — must be explicit.

**Rationale (--inplace):** Writes directly to destination file, avoiding temp
file allocation + rename. Reduces disk I/O by ~50%.

**Caveat (--inplace):** If rsync is interrupted, the destination file is in a
partial state. Mitigated by `--partial` (already enabled) which keeps partial
files for resume. Not suitable if other processes read the destination during
sync.

**Note:** `--compress` wastes CPU on LAN where network is not the bottleneck.

### SSH

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| Cipher (Pi) | negotiated | `aes128-ctr` | proven | pi |
| Cipher (x86/AES-NI) | negotiated | `aes128-gcm@openssh.com` | proven | general |
| `-T` (no pseudo-tty) | allocated | disabled | proposed | general |
| `-x` (no X11) | off | off | proposed | general |
| `-o Compression=no` | off (since 9.0) | explicit off | proposed | general |
| `ControlMaster` | off | auto | proposed | general |
| `ControlPersist` | off | 600 | proposed | general |
| `IPQoS` | interactive | throughput | proposed | general |

**Rationale (cipher):** Pi has no AES-NI. `aes128-ctr` is faster in software
than `aes128-gcm` on Cortex-A72. On x86 with AES-NI, `aes128-gcm` is
fastest. Current config uses `aes128-gcm` — correct for x86 source, but the
Pi is the bottleneck (decryption side).

**Rationale (ControlMaster):** Reuses SSH connection across multiple rsync
invocations, avoiding repeated key exchange. Useful when syncing multiple
folders from the same source.

**Rationale (-T -x):** Eliminates pseudo-terminal and X11 overhead. Minor
but free.

### Transport: rsync daemon (no encryption)

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| `transport` | ssh | rsync | proven | general |

**Rationale:** SSH encryption consumes ~97% of one Pi CPU core during
transfers. On a trusted home LAN, encryption provides no benefit. Rsync
daemon mode (`rsyncd` on port 873) eliminates SSH entirely.

**Measured:** 78 MB/s (SSH aes128-ctr) → 121 MB/s (daemon). 55% gain,
reaching gigabit wire speed.

**Setup:** See `RSYNCD.md` for server configuration.

**Config:**
```yaml
sources:
  pve:
    transport: rsync
    rsync_module: backup
    remote_root: /
```

**Security:** LAN-only. `hosts allow` in rsyncd.conf restricts by IP.
Module is `read only`. No encryption — do NOT use on untrusted networks.

---

## IRQ Affinity (Pi-specific, micro-optimisation)

| Strategy | Status |
|----------|--------|
| Separate USB and network IRQs onto different cores | proposed |
| Pin rsync/ssh to remaining cores via `taskset` | proposed |
| Disable `irqbalance` | proposed |

Recommended core layout for Pi 4:
- **CPU 0:** System / kernel threads
- **CPU 1:** Ethernet IRQ + network softirqs
- **CPU 2:** USB controller IRQ
- **CPU 3:** rsync/ssh process

**Impact:** 2-5% — only worth doing after bigger wins are locked in.

---

## Tuning procedure

**CRITICAL: Never apply multiple changes at once. Never make untracked live
changes. Always measure before and after each single change.**

### Step 0: Clean slate

Remove all custom sysctl files and reboot to system defaults:

```bash
rm -f /etc/sysctl.d/99-backup.conf /etc/sysctl.d/99-pullback.conf
systemctl disable pullback-tune 2>/dev/null
rm -f /etc/systemd/system/pullback-tune.service
reboot
```

### Step 1: Measure untuned baseline

Start a sync and let it run for 10 minutes, then sample:

```bash
bash scripts/pi-bottleneck.sh --runsec=30
```

Record: CPU%, Disk MB/s, Net MB/s, dirty pages, top process.

### Step 2: Apply ONE change

Apply a single parameter change live. Measure for 30 seconds.

### Step 3: Evaluate

- **Improved:** record the result, keep the change, move to next parameter
- **No change after several attempts:** revert to default, move on
- **Degraded:** revert immediately, wait for system to stabilise

### Step 4: Repeat

Test each parameter in impact order. See `TUNEDATA.md` for the test table.

### Step 5: Make permanent

Update `config.yaml` with the kept values and run `pi-tune-install.sh`.
Reboot and verify the values persist.

---

## Config.yaml

```yaml
tuning:
  dirty_ratio: 5
  dirty_background_ratio: 2
  dirty_expire_centisecs: 1000
  dirty_writeback_centisecs: 500
  bdi_max_bytes: 83886080  # 80 MB per-device cap for backup drive
  rps_enabled: true
  eee_off: true
  cpu_governor: performance
```

**`bdi_max_bytes`:** Per-device dirty page cap applied to the backup drive
via `/sys/block/<dev>/bdi/max_bytes` with `strict_limit=1`. This caps dirty
pages for the backup drive only, without throttling other devices. Set to 0
to disable.

All values are read by `pi-tune-install.sh` and written to:
- `/etc/sysctl.d/99-pullback.conf` — dirty page settings (persist via sysctl)
- `scripts/pi-tune-boot.sh` — RPS, EEE, governor, BDI limits (run on boot via systemd)

## Per-Drive Tuning

Different drives need different tuning. A slow HDD needs BDI `max_bytes=80MB`
to prevent dirty page stalls. A fast SSD doesn't need BDI at all. The tuning
should follow the drive, not the system config.

### How it works

A `.pullback-tune.yaml` file on the backup volume (alongside `.pullback-volume`)
provides drive-specific tuning overrides. At sync start, `tuning.py` reads it
and merges with `config.yaml` defaults — drive values win.

### File format

Same `tuning:` section as `config.yaml`. Only include keys you want to override:

**HDD example** (`/backup/.pullback-tune.yaml`):
```yaml
tuning:
  bdi_max_bytes: 83886080  # 80 MB — essential for slow HDD
```

**SSD example** (no file needed — defaults are fine, or explicitly):
```yaml
tuning:
  bdi_max_bytes: 0  # no BDI cap needed
```

**Supported keys:** `dirty_ratio`, `dirty_background_ratio`,
`dirty_expire_centisecs`, `dirty_writeback_centisecs`, `bdi_max_bytes`,
`rps_enabled`, `eee_off`, `cpu_governor`.

### Override order

1. `config.yaml` — system defaults
2. `config.local.yaml` — host-specific overrides (merged at load)
3. `.pullback-tune.yaml` — drive-specific overrides (merged at sync start)

Drive config wins over everything for tuning settings.

### Creating a drive tune file

Use `pi-tune-status.sh --save=/backup/.pullback-tune.yaml` to snapshot the
current live settings onto the drive.

---

## UAS (USB Attached SCSI)

UAS is a faster USB storage protocol that supports command queuing. It can
significantly improve USB 3.0 drive throughput vs the default Bulk-Only
transport.

### Config

```yaml
usb:
  uas: true
```

### How it works

`pi-tune-install.sh`:
1. Detects connected USB drive vendor:product ID via `lsusb`
2. Checks if the drive advertises a UAS interface (`bInterfaceProtocol`)
3. If Bulk-Only: reports "UAS not supported" and makes no changes
4. If UAS capable: adds `usb-storage.quirks=XXXX:XXXX:u` to kernel cmdline
5. Requires reboot for UAS to take effect

### Limitation

Not all USB drives support UAS. The USB-SATA bridge chip inside the
enclosure determines this, not the drive itself. Example:
- Toshiba Canvio Advance (0480:0820): Bulk-Only, no UAS support
- Many newer enclosures with ASMedia ASM1153/ASM235CM: UAS supported

Check with: `lsusb -v -d XXXX:XXXX | grep bInterfaceProtocol`

## Hardware limitations

The Raspberry Pi 4 has inherent bottlenecks:
- USB 3.0 and Gigabit Ethernet share the VL805 controller
- No hardware AES acceleration (SSH encryption is CPU-bound)
- Single interrupt handler for bcmgenet (RPS is a software workaround)

Maximum practical throughput with USB HDD: ~55 MB/s.
With USB SSD + UAS: potentially higher but limited by shared bus.
ZimaBoard or similar x86 with SATA eliminates all these bottlenecks.

## Conflicting files

`pi-hw-init.sh` creates `/etc/sysctl.d/99-backup.conf` with aggressive dirty
page settings (dirty_ratio=40) that cause severe degradation during sustained
transfers. This file MUST be removed before running `pi-tune-install.sh`:

```bash
rm -f /etc/sysctl.d/99-backup.conf
```

## Lesson learned

Never make multiple untracked live changes to kernel parameters. Each change
compounds and makes it impossible to identify what helped and what hurt.
Always:
1. Change one parameter
2. Measure
3. Record the result
4. Commit or revert
5. Then move to the next parameter
