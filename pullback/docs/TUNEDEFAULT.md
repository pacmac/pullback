# Tuning Defaults — OS/Kernel Factory Values

Reference file: every tunable parameter's default value before any pullback
tuning is applied. Use this to revert any change that doesn't show measurable
improvement.

## VM / Dirty Pages

| Parameter | Default | Source |
|-----------|---------|--------|
| `vm.dirty_ratio` | 20 | Debian kernel default |
| `vm.dirty_background_ratio` | 10 | Debian kernel default |
| `vm.dirty_expire_centisecs` | 3000 | Debian kernel default |
| `vm.dirty_writeback_centisecs` | 500 | Debian kernel default |
| `vm.dirty_bytes` | 0 (disabled, `_ratio` used) | Kernel default |
| `vm.dirty_background_bytes` | 0 (disabled, `_ratio` used) | Kernel default |

**Note:** Setting `dirty_bytes` or `dirty_background_bytes` to a non-zero
value disables the corresponding `_ratio` parameter. They are mutually
exclusive.

## Network Buffers

| Parameter | Default | Source |
|-----------|---------|--------|
| `net.core.rmem_max` | 212992 (~208 KB) | Debian kernel default |
| `net.core.wmem_max` | 212992 (~208 KB) | Debian kernel default |
| `net.ipv4.tcp_rmem` | 4096 131072 6291456 | Debian kernel default |
| `net.ipv4.tcp_wmem` | 4096 16384 4194304 | Debian kernel default |
| `net.core.netdev_max_backlog` | 1000 | Kernel default |
| `net.core.somaxconn` | 4096 | Kernel default (recent kernels) |

## TCP

| Parameter | Default | Source |
|-----------|---------|--------|
| `net.ipv4.tcp_congestion_control` | cubic | Debian kernel default |
| `net.core.default_qdisc` | fq_codel | Debian kernel default |
| `net.ipv4.tcp_window_scaling` | 1 (enabled) | Kernel default |
| `net.ipv4.tcp_timestamps` | 1 (enabled) | Kernel default |
| `net.ipv4.tcp_sack` | 1 (enabled) | Kernel default |
| `net.ipv4.tcp_slow_start_after_idle` | 1 (enabled) | Kernel default |

## I/O

| Parameter | Default | Source |
|-----------|---------|--------|
| I/O scheduler | varies (`mq-deadline` or `none` for USB) | Kernel/device dependent |
| Block read-ahead | 128 KB (256 sectors) | Kernel default |

## CPU

| Parameter | Default | Source |
|-----------|---------|--------|
| CPU governor | `ondemand` (Pi OS) / `schedutil` (Debian) | OS default |

## Network Hardware

| Parameter | Default | Source |
|-----------|---------|--------|
| RPS (`rps_cpus`) | 0 (disabled) | Kernel default |
| RFS (`rps_sock_flow_entries`) | 0 (disabled) | Kernel default |
| RFS (`rps_flow_cnt`) | 0 (disabled) | Kernel default |
| EEE | enabled | NIC default |
| Interrupt coalescing | driver default | NIC driver |
| Ring buffers | driver default | NIC driver |

## USB

| Parameter | Default | Source |
|-----------|---------|--------|
| Protocol | UAS if supported, else BOT | Kernel auto-detect |
| `usb-storage.quirks` | none | Kernel cmdline |

## rsync (over SSH)

| Parameter | Default | Source |
|-----------|---------|--------|
| `--whole-file` | off (delta transfer) | rsync default over SSH |
| `--inplace` | off (temp file + rename) | rsync default |
| `--compress` | off | rsync default |

## SSH

| Parameter | Default | Source |
|-----------|---------|--------|
| Cipher | negotiated (usually `chacha20-poly1305`) | OpenSSH default |
| Compression | off (since OpenSSH 9.0) | OpenSSH default |
| Pseudo-terminal | allocated with `-t` | OpenSSH default |
| X11 forwarding | off | OpenSSH default |
| `ControlMaster` | off | OpenSSH default |

## ext4 Mount Options

| Parameter | Default | Source |
|-----------|---------|--------|
| `atime` | `relatime` | ext4 default |
| `commit` | 5 seconds | ext4 default |
| `data` | `ordered` | ext4 default |
| `barrier` | 1 (enabled) | ext4 default |
