# Tuning Test Data

## Targets

| Metric | Target |
|--------|--------|
| Net throughput | ~55 MB/s |
| Disk throughput | ~55 MB/s |
| Dirty pages | < 80 MB |

## System
- Raspberry Pi 4, 4GB RAM
- Gigabit Ethernet (bcmgenet)
- rsync over SSH (aes128-gcm cipher)

### Drive: USB 3.0 SSD 112GB (current)

### Drive: USB 3.0 HDD Toshiba Canvio Advance 4TB (0480:0820, Bulk-Only, no UAS) (previous)

## Baseline — SSD (untuned, Debian defaults, 2026-03-15)

Settings:
```
vm.dirty_ratio = 20
vm.dirty_background_ratio = 10
vm.dirty_expire_centisecs = 3000
vm.dirty_writeback_centisecs = 500
Governor: ondemand
RPS: disabled (0)
EEE: enabled - active
I/O scheduler: mq-deadline
Read-ahead: 131064 sectors
rsync: --archive --numeric-ids --partial --info=progress2,name1
SSH cipher: aes128-gcm@openssh.com
```

Results (10s sample, sync running):
```
CPU:    avg=42%  max=47%
Net:    avg=54 MB/s  max=55 MB/s    ✓ at target
Disk:   avg=57 MB/s  max=89 MB/s    ✓ at target
Dirty:  avg=303 MB  max=334 MB      ✗ above target (<80)
```

## Baseline — HDD (untuned, Debian defaults, 2026-03-15)

Settings:
```
vm.dirty_ratio = 20
vm.dirty_background_ratio = 10
vm.dirty_expire_centisecs = 3000
vm.dirty_writeback_centisecs = 500
Governor: ondemand
RPS: disabled (0)
EEE: enabled - active
I/O scheduler: mq-deadline
Read-ahead: 256 sectors
rsync: --archive --numeric-ids --partial --info=progress2,name1
SSH cipher: aes128-gcm@openssh.com
```

Results (20s sample, fresh install, sync running ~30 mins):
```
CPU:    avg=60%  max=71%
Disk:   avg=31 MB/s  max=45 MB/s
Net:    avg=35 MB/s  max=54 MB/s
Dirty:  avg=584 MB  max=624 MB

Governor:    ondemand
RPS:         0 (disabled)
EEE:         enabled - active
NET_RX:      CPU0=12885931 CPU1=0 CPU2=25 CPU3=10
RX dropped:  1
Dirty cfg:   ratio=20 bg_ratio=10
Scheduler:   mq-deadline (default)
Read-ahead:  256 sectors
```

---

## Test Results

Test each parameter individually. Record 30s sample with
`scripts/pi-bottleneck.sh --runsec=30` during active sync.

### SSD test results (2026-03-15)

Baseline: Net=54, Disk=57, Dirty=303. Targets: Net ~55, Disk ~55, Dirty <80.

| # | Parameter | Apply | Revert | Before (Net/Disk/Dirty) | After (Net/Disk/Dirty) | Keep? |
|---|-----------|-------|--------|------------------------|----------------------|-------|
| 1 | governor=performance | `echo performance \| tee cpu*/scaling_governor` | `echo ondemand \| tee ...` | 54/53/53 (2m) | 54/51/53 (2m) | **NO — no throughput gain** |
| 2 | dirty_ratio=5 + bg=2 | `sysctl -w vm.dirty_ratio=5 vm.dirty_background_ratio=2` | `sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=10` | 54/57/303 | 54/53/51 | **YES — dirty 303→51, throughput maintained** |
| 3 | dirty_bytes=48MB | `sysctl -w vm.dirty_bytes=50331648 vm.dirty_background_bytes=16777216` | `sysctl -w vm.dirty_bytes=0 vm.dirty_background_bytes=0` | — | — | **NO — stalled system completely. Global dirty_bytes throttles ALL devices, not just the slow one. Use per-device BDI max_bytes instead.** |
| 4 | RPS on CPU2+3 | `echo c > rps_cpus; echo 32768 > rps_sock_flow_entries` | `echo 0 > rps_cpus; echo 0 > rps_sock_flow_entries` | 54/53/53 (2m) | 52/49/52 (2m) | **NO — throughput dropped, adds overhead** |
| 5 | EEE off | `ethtool --set-eee eth0 eee off` | `ethtool --set-eee eth0 eee on` | 54/53/51 | 54/53/55 | **YES — same throughput, prevents long-run drops** |
| 6 | dirty_expire=1000 | `sysctl -w vm.dirty_expire_centisecs=1000` | `sysctl -w vm.dirty_expire_centisecs=3000` | 54/53/53 (2m) | 53/51/50 (2m) | **NO — no improvement** |
| 7 | dirty_writeback=500 | n/a | n/a | already default (500) | — | **SKIP — already at target value** |
| 8 | SSH cipher aes128-ctr | config.local.yaml `ssh.cipher: aes128-ctr` | revert to aes128-gcm | 53/51/52 (2m, gcm) | 78/75/51 (1m, ctr) | **YES — 47% throughput gain. SSH was at 97% CPU with gcm.** |
| 9 | rsync daemon (no encryption) | config.local.yaml `transport: rsync` | revert to `transport: ssh` | 78/75/51 (ctr) | 121/114/42 (1m, 29 samples) | **YES — 55% gain, gigabit wire speed. min Net=120, max=122.** |

### Cumulative improvement (Samsung SSD 870 4TB)

| Stage | Net | Disk | Dirty | Change |
|-------|-----|------|-------|--------|
| Untuned baseline | 35 MB/s | 31 MB/s | 584 MB | — |
| + dirty_ratio=5 | 54 MB/s | 53 MB/s | 51 MB | +54% net |
| + EEE off | 54 MB/s | 53 MB/s | 55 MB | prevents drops |
| + aes128-ctr | 78 MB/s | 75 MB/s | 51 MB | +44% net |
| + rsync daemon | 121 MB/s | 114 MB/s | 42 MB | +55% net |
| **Total** | **121 MB/s** | **114 MB/s** | **42 MB** | **3.5x baseline** |

### HDD results (previous session, not individually recorded)

**Known good result (all 7 combined):** 54-55 MB/s, dirty pages 57-65 MB.
Individual per-parameter results were not recorded in the initial session.

### HDD BDI test results (2026-03-16)

Drive: USB 3.0 HDD Toshiba MQ04UBB400 3.6TB. Transport: rsync daemon (no SSH).
Baseline config: dirty_ratio=5, EEE off.

**Problem:** With `dirty_ratio=5` (cap ~200MB on 4GB), dirty pages still
reached 127-170 MB during active sync — well above <80 MB target. The HDD
cannot flush fast enough at incoming wire speed (~120 MB/s).

| # | Parameter | Apply | Revert | Before (Dirty) | After (Dirty) | Keep? |
|---|-----------|-------|--------|----------------|----------------|-------|
| 1 | BDI strict_limit=1 + max_bytes=80MB | `echo 1 > /sys/block/sda/bdi/strict_limit; echo 83886080 > /sys/block/sda/bdi/max_bytes` | `echo 0 > /sys/block/sda/bdi/strict_limit` | Net avg=21, Disk avg=20, Dirty avg=117 max=127 (BDI off, 120s, 27 samples) | Net avg=63, Disk avg=63, Dirty avg=42 max=55 (BDI on, 120s, 27 samples) | **YES — 3x throughput, dirty under 80MB** |
| 2 | governor=performance | `echo performance \| tee cpu*/scaling_governor` | `echo ondemand \| tee ...` | Net avg=40, Disk avg=40, Dirty avg=43 max=53 (ondemand, 300s, 27 samples) | Net avg=51, Disk avg=50, Dirty avg=44 max=55 (performance, 300s, 27 samples) | **YES — +25% throughput with BDI+RPS enabled** |
| 3 | RPS CPU2+3 | `echo c > rps_cpus; echo 32768 > rps_sock_flow_entries` | `echo 0 > rps_cpus; echo 0 > rps_sock_flow_entries` | Net avg=35 min=0 max=96, Disk avg=34, Dirty avg=59 max=76 (RPS off, 120s, 27 samples) | Net avg=65 min=16 max=105, Disk avg=63, Dirty avg=41 max=51 (RPS on, 120s, 27 samples) | **YES — net nearly doubled, dirty dropped, no more 0 MB/s dips** |
| 4 | read-ahead=4096 (2MB) | `blockdev --setra 4096 /dev/sda` | `blockdev --setra 256 /dev/sda` | Net avg=25, Disk avg=26, Dirty avg=47 max=54 (256 sectors, 120s, 27 samples) | Net avg=13, Disk avg=14, Dirty avg=50 max=61 (4096 sectors, 120s, 27 samples) | **NO — throughput halved, dirty increased** |

**Key finding:** Per-device BDI limits are the correct solution for slow block
devices. Unlike global `dirty_bytes` (which stalled the entire system at 48MB),
BDI `strict_limit` + `max_bytes` only throttles writes to the specific slow
device. The kernel's built-in `balance_dirty_pages()` feedback loop handles
proportional throttling automatically — no userspace dynamic tuning needed.

**Governor note:** Initial 120s test without RPS showed ondemand winning.
Retested with BDI+RPS enabled at 300s: performance wins +25% (51 vs 40 MB/s).

### Proposed parameters (to be tested)

| # | Parameter | Apply | Revert | Before | After | Keep? |
|---|-----------|-------|--------|--------|-------|-------|
| 8 | dirty_bytes=48MB | `sysctl vm.dirty_bytes=50331648` | `sysctl vm.dirty_bytes=0` | — | — | **REJECTED — global dirty_bytes stalls all devices. Use BDI max_bytes instead.** |
| 9 | dirty_background_bytes=16MB | `sysctl vm.dirty_background_bytes=16777216` | `sysctl vm.dirty_background_bytes=0` | — | — | **REJECTED — see #8** |
| 10 | dirty_expire=300 | `sysctl vm.dirty_expire_centisecs=300` | `sysctl vm.dirty_expire_centisecs=3000` | (pending) | (pending) | (pending) |
| 11 | dirty_writeback=100 | `sysctl vm.dirty_writeback_centisecs=100` | `sysctl vm.dirty_writeback_centisecs=500` | (pending) | (pending) | (pending) |
| 12 | SSH cipher aes128-ctr | set in config.yaml | revert to aes128-gcm | (pending) | (pending) | (pending) |
| 13 | rsync --whole-file | add to rsync.args | remove | (pending) | (pending) | (pending) |
| 14 | rsync --inplace | add to rsync.args | remove | (pending) | (pending) | (pending) |
| 15 | I/O scheduler mq-deadline | `echo mq-deadline > /sys/block/sda/queue/scheduler` | `echo none > /sys/block/sda/queue/scheduler` | (pending) | (pending) | (pending) |
| 16 | read-ahead 4096 | `blockdev --setra 4096 /dev/sda` | `blockdev --setra 256 /dev/sda` | (pending) | (pending) | (pending) |
| 17 | RFS flow steering | `echo 4096 > rps_sock_flow_entries; echo 2048 > rps_flow_cnt` | `echo 0 > rps_sock_flow_entries` | (pending) | (pending) | (pending) |
| 18 | tcp_slow_start_after_idle=0 | `sysctl net.ipv4.tcp_slow_start_after_idle=0` | `sysctl net.ipv4.tcp_slow_start_after_idle=1` | (pending) | (pending) | (pending) |
| 19 | rmem_max/wmem_max=16MB | `sysctl net.core.rmem_max=16777216 net.core.wmem_max=16777216` | `sysctl net.core.rmem_max=212992 net.core.wmem_max=212992` | (pending) | (pending) | (pending) |
| 20 | SSH -T -x -o Compression=no | add to ssh_cmd in sync.py | remove | (pending) | (pending) | (pending) |
| 21 | SSH ControlMaster | add to ssh config | remove | (pending) | (pending) | (pending) |
| 22 | ext4 nodiratime | add to fstab | remove | (pending) | (pending) | (pending) |
| 23 | netdev_max_backlog=5000 | `sysctl net.core.netdev_max_backlog=5000` | `sysctl net.core.netdev_max_backlog=1000` | (pending) | (pending) | (pending) |
| 24 | IRQ affinity (USB+net separation) | manual via /proc/irq | remove | (pending) | (pending) | (pending) |
| 25 | RPS all cores (0xf) | `echo f > rps_cpus` | `echo c > rps_cpus` | (pending) | (pending) | (pending) |

---

## Final configuration

(To be filled after all tests)

```
(pending)
```

## Final results

(To be filled after all tests)

```
(pending)
```
