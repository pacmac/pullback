# CLI — Spec

## Overview

`cli.py` is the command-line interface for pibak. It provides sync execution with
live console progress, status checks, and cancel support. It is the primary way
to run and test pibak before the web dashboard is built.

## Usage

All commands run on the Pi via SSH.

```
./venv/bin/python3 cli.py <command> [options]
```

## Commands

### sync

Run a sync job with live console progress.

```
cli.py sync [--source NAME] [--folder PATH]
```

- No args: syncs all sources/folders
- `--source pve`: syncs all folders for source `pve`
- `--source pve --folder dump`: syncs only `dump` folder from source `pve`

**Console output:**
- Start/end log lines per folder
- Live progress line (overwritten in-place via `\r`):
  ```
  [pve] shares/dis  45%  12.3MB/s  ETA 0:02:30  some/file.txt
  ```
- Final summary: success/fail, bytes transferred, duration

### status

Show current state of all sources.

```
cli.py status [--source NAME]
```

Reads from `state/<source>.json` files. Shows:
- Last sync time, success/fail, duration
- Per-folder last sync result
- If a sync is running: live progress from `state/progress/<source>.json`

### cancel

Request cancellation of a running sync.

```
cli.py cancel <source>
```

Creates the cancel flag file. The running sync will stop after the current folder
completes.

### config

Validate and display the loaded config.

```
cli.py config
```

Loads config.yaml, validates, applies defaults, prints as JSON. Same as
`python3 config.py` but accessible from the CLI.

## Live progress

When `sync` is running, the progress callback in engine.py must:
1. Write to `state/progress/<source>.json` (for web dashboard)
2. Print a `\r`-overwritten line to stderr (for console)

The console progress line format:
```
[source] folder  pct%  speed  ETA eta  current_file
```

Printed to stderr so stdout remains clean for piping/logging.

## Config

Reads `config.yaml` from the project folder (same as engine.py).
Optional `--config PATH` override on all commands.

## File

```
pibak/
  cli.py              # CLI entry point
```

## Not in scope

- Daemonisation (that's the web dashboard's job)
- Scheduling (that's cron)
- USB management (use bash scripts directly)
