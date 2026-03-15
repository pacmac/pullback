"""pullback CLI — sync, status, cancel, config."""

import argparse
import json
import sys

from config import load_config
from state import load_state, get_progress, request_cancel


def cmd_sync(args):
    """Run sync with live console progress."""
    from engine import run_all, _setup_logging

    cfg = load_config(args.config)
    _setup_logging(cfg)

    if args.folder and not args.source:
        print("Error: --folder requires --source", file=sys.stderr)
        sys.exit(1)

    ok = run_all(cfg, source_filter=args.source, folder_filter=args.folder)
    sys.exit(0 if ok else 1)


def cmd_status(args):
    """Show current state and progress."""
    cfg = load_config(args.config)

    sources = [args.source] if args.source else list(cfg["sources"].keys())

    for name in sources:
        state = load_state(name)
        progress = get_progress(name)

        print(f"=== {name} ===")
        print(f"  Last run:      {state.get('last_run_started_at', 'never')}")
        print(f"  Success:       {state.get('last_run_success', 'n/a')}")
        print(f"  Duration:      {state.get('last_sync_duration', 0)}s")
        print(f"  Last error:    {state.get('last_error', 'none')}")

        if progress:
            print(f"  ** RUNNING: {progress.get('step', '?')} "
                  f"{progress.get('overall_pct', 0)}% "
                  f"{progress.get('speed', '')} "
                  f"ETA {progress.get('eta', '?')}")

        folders = state.get("folders", {})
        if folders:
            print(f"  Folders:")
            for fpath, fstate in folders.items():
                ok = "OK" if fstate.get("success") else "FAIL"
                ts = fstate.get("last_synced_at", "?")
                err = fstate.get("error", "")
                line = f"    {fpath}: {ok} ({ts})"
                if err:
                    line += f" — {err}"
                print(line)
        print()


def cmd_cancel(args):
    """Request cancellation of a running sync."""
    if not args.source:
        print("Error: --source is required", file=sys.stderr)
        sys.exit(1)

    request_cancel(args.source)
    print(f"Cancel requested for '{args.source}'")


def cmd_config(args):
    """Validate and display config."""
    try:
        cfg = load_config(args.config)
        print(json.dumps(cfg, indent=2))
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="pullback backup CLI")
    parser.add_argument("--config", default=None, help="Path to config.yaml")

    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Run sync")
    p_sync.add_argument("--source", default=None)
    p_sync.add_argument("--folder", default=None)

    p_status = sub.add_parser("status", help="Show status")
    p_status.add_argument("--source", default=None)

    p_cancel = sub.add_parser("cancel", help="Cancel running sync")
    p_cancel.add_argument("--source", required=True)

    sub.add_parser("config", help="Show loaded config")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "sync": cmd_sync,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "config": cmd_config,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
