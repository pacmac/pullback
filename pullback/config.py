"""Load and validate config.yaml."""

import sys
from pathlib import Path

import yaml


def load_config(path=None):
    """Load config.yaml, merge config.local.yaml overrides, validate, apply defaults."""
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    else:
        path = Path(path)

    with open(path) as f:
        cfg = yaml.safe_load(f)

    # Merge local overrides (credentials, host-specific settings)
    local_path = path.parent / "config.local.yaml"
    if local_path.exists():
        with open(local_path) as f:
            local = yaml.safe_load(f)
        if local:
            _deep_merge(cfg, local)

    _validate(cfg, path)
    _apply_defaults(cfg)
    return cfg


def _deep_merge(base, override):
    """Recursively merge override dict into base dict."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def _validate(cfg, path):
    """Validate required fields exist."""
    errors = []

    if "mount_point" not in cfg:
        errors.append("missing 'mount_point'")

    if "sources" not in cfg or not cfg["sources"]:
        errors.append("missing or empty 'sources'")
    else:
        for name, src in cfg["sources"].items():
            prefix = f"sources.{name}"
            if "host" not in src:
                errors.append(f"{prefix}: missing 'host'")
            if "remote_root" not in src:
                errors.append(f"{prefix}: missing 'remote_root'")
            if "folders" not in src or not src["folders"]:
                errors.append(f"{prefix}: missing or empty 'folders'")
            else:
                for i, folder in enumerate(src["folders"]):
                    if isinstance(folder, str):
                        continue
                    if isinstance(folder, dict):
                        if "path" not in folder:
                            errors.append(f"{prefix}.folders[{i}]: missing 'path'")
                        ret = folder.get("retention")
                        if ret:
                            if "keep" not in ret:
                                errors.append(f"{prefix}.folders[{i}].retention: missing 'keep'")
                            has_pattern = "pattern" in ret
                            has_stamp = "retain_stamp" in ret
                            if not has_pattern and not has_stamp:
                                errors.append(f"{prefix}.folders[{i}].retention: needs 'pattern' or 'retain_stamp'")
                            if has_pattern and has_stamp:
                                errors.append(f"{prefix}.folders[{i}].retention: use 'pattern' or 'retain_stamp', not both")
                    else:
                        errors.append(f"{prefix}.folders[{i}]: must be string or dict")

    if errors:
        msg = f"Config errors in {path}:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(msg)


def _apply_defaults(cfg):
    """Apply default values for optional fields."""
    cfg.setdefault("web_port", 8080)
    cfg.setdefault("web_host", "0.0.0.0")
    cfg.setdefault("disk_warn_pct", 90)

    cfg.setdefault("ransomware", {})
    rw = cfg["ransomware"]
    rw.setdefault("enabled", False)
    rw.setdefault("sample_size", 30)
    rw.setdefault("change_threshold", 0.30)
    rw.setdefault("fprint_depth", 3)

    cfg.setdefault("rsync", {})
    cfg["rsync"].setdefault("args", [
        "--archive", "--numeric-ids", "--partial", "--info=progress2,name1"
    ])

    cfg.setdefault("logging", {})
    cfg["logging"].setdefault("file", "/var/log/pullback.log")

    cfg.setdefault("email", {})
    email = cfg["email"]
    email.setdefault("enabled", False)
    email.setdefault("smtp_port", 25)
    email.setdefault("on_failure", True)
    email.setdefault("on_success", False)
    email.setdefault("on_warning", True)
    email.setdefault("on_start", False)

    cfg.setdefault("usb", {})
    usb = cfg["usb"]
    usb.setdefault("flag_file", ".pullback-volume")
    usb.setdefault("filesystem", "ext4")
    usb.setdefault("reserved_pct", 1)

    for name, src in cfg["sources"].items():
        src.setdefault("local_root", name)
        # Normalise folders to dicts
        normalised = []
        for folder in src["folders"]:
            if isinstance(folder, str):
                normalised.append({"path": folder})
            else:
                normalised.append(folder)
        src["folders"] = normalised


# Run standalone to test config loading
if __name__ == "__main__":
    import json
    path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        cfg = load_config(path)
        print(json.dumps(cfg, indent=2))
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
