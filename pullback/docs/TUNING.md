# Performance Tuning — Spec

## Overview

Sustained rsync pull over SSH to a USB HDD degrades significantly over time
without tuning. This document is the **single source of truth** for all
tuning parameters, their rationale, and status.

See `TUNEDEFAULT.md` for OS/kernel factory defaults.
See `TUNEDATA.md` for test results.

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

**Proposed improvement:** Switch from `_ratio` to `_bytes` for absolute
control independent of RAM size:

| Parameter | Proposed | Status | Category |
|-----------|----------|--------|----------|
| `vm.dirty_bytes` | 50331648 (48 MB) | proposed | general |
| `vm.dirty_background_bytes` | 16777216 (16 MB) | proposed | general |
| `vm.dirty_expire_centisecs` | 300 | proposed | general |
| `vm.dirty_writeback_centisecs` | 100 | proposed | general |

**Rationale for `_bytes`:** Absolute values give predictable behaviour
regardless of RAM. 16MB background / 48MB hard limit is well-tested for USB
devices. More aggressive expire (3s) and writeback (1s) keep the USB device
draining continuously.

**Caveat:** `dirty_bytes` and `dirty_ratio` are mutually exclusive — setting
one disables the other.

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
Critical for SSH encryption throughput on Pi (no AES-NI). Measured 35-45 MB/s
with ondemand vs 54 MB/s with performance.

**Caveat:** Pi 4 Cortex-A72 throttles at ~80C. Ensure adequate cooling
(heatsink or fan).

### Disk I/O

| Parameter | Default | Value | Status | Category |
|-----------|---------|-------|--------|----------|
| I/O scheduler | varies | mq-deadline | proposed | general |
| Block read-ahead | 256 sectors (128 KB) | 4096 sectors (2 MB) | proposed | general |

**Rationale (scheduler):** `mq-deadline` provides deadline guarantees for
rotational media, preventing starvation. `bfq` has higher CPU overhead.
`none`/`noop` is for SSDs only — USB HDDs have physical seek times.

**Rationale (read-ahead):** Larger read-ahead suits rsync's sequential
patterns. 2MB is a good balance — above 4-8MB gives diminishing returns.

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
| Cipher (Pi) | negotiated | `aes128-ctr` | proposed | pi |
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
  rps_enabled: true
  eee_off: true
  cpu_governor: performance
```

All values are read by `pi-tune-install.sh` and written to:
- `/etc/sysctl.d/99-pullback.conf` — dirty page settings (persist via sysctl)
- `scripts/pi-tune-boot.sh` — RPS, EEE, governor (run on boot via systemd)

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
