# Tuning Test Data

## Targets

| Metric | Target |
|--------|--------|
| Net throughput | ~55 MB/s |
| Disk throughput | ~55 MB/s |
| Dirty pages | < 80 MB |

## System
- Raspberry Pi 4, 4GB RAM
- USB 3.0 HDD: Toshiba Canvio Advance 4TB (0480:0820, Bulk-Only, no UAS)
- Gigabit Ethernet (bcmgenet)
- rsync over SSH (aes128-gcm cipher)

## Baseline (untuned, Debian defaults)

Settings:
```
vm.dirty_ratio = 20
vm.dirty_background_ratio = 10
vm.dirty_expire_centisecs = 3000
vm.dirty_writeback_centisecs = 500
Governor: ondemand
RPS: disabled
EEE: enabled
I/O scheduler: (check with cat /sys/block/sda/queue/scheduler)
Read-ahead: 256 sectors (128 KB)
rsync: --archive --numeric-ids --partial --info=progress2,name1
SSH cipher: aes128-gcm@openssh.com
```

Results (20s sample, fresh install 2026-03-15, sync running ~30 mins):
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

### Proven parameters (from initial tuning session)

| # | Parameter | Before | After | Keep? |
|---|-----------|--------|-------|-------|
| 1 | governor=performance | (pending) | (pending) | (pending) |
| 2 | RPS on CPU2+3 | (pending) | (pending) | (pending) |
| 3 | EEE off | (pending) | (pending) | (pending) |
| 4 | dirty_ratio=5 | (pending) | (pending) | (pending) |
| 5 | dirty_bg_ratio=2 | (pending) | (pending) | (pending) |
| 6 | dirty_expire=1000 | (pending) | (pending) | (pending) |
| 7 | dirty_writeback=500 | (pending) | (pending) | (pending) |

**Known good result (all 7 combined):** 54-55 MB/s, dirty pages 57-65 MB.
Individual per-parameter results were not recorded in the initial session.

### Proposed parameters (to be tested)

| # | Parameter | Apply | Revert | Before | After | Keep? |
|---|-----------|-------|--------|--------|-------|-------|
| 8 | dirty_bytes=48MB | `sysctl vm.dirty_bytes=50331648` | `sysctl vm.dirty_bytes=0` | (pending) | (pending) | (pending) |
| 9 | dirty_background_bytes=16MB | `sysctl vm.dirty_background_bytes=16777216` | `sysctl vm.dirty_background_bytes=0` | (pending) | (pending) | (pending) |
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
