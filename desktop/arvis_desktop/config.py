"""Explicit per-user paths and configuration; no import-time hardware access."""
from __future__ import annotations

import json
import os
from pathlib import Path


def config_path() -> Path:
    return Path(os.environ.get("ARVIS_CONFIG", Path.home() / ".config/arvis/config.json")).expanduser()


def load_config(path: Path | None = None) -> dict:
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "arvis"
    state = Path(os.environ.get("ARVIS_STATE_DIR", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "arvis"))
    cfg = {
        "state_dir": str(state),
        "workspace": str(data / "workspace"),
        "project_roots": [str(Path.home() / "Work")],
        "browser_executable": "/usr/bin/chromium",
        "browser_headless": True,
        "notifications": True,
        "voice": {"enabled": True, "wake_enabled": True, "model_dir": str(data / "voice"), "speak_enabled": True},
    }
    path = path or config_path()
    if path.exists():
        overrides = json.loads(path.read_text())
        if not isinstance(overrides, dict):
            raise ValueError("Arvis configuration must be a JSON object")
        cfg["voice"].update(overrides.pop("voice", {}))
        cfg.update(overrides)
    for name in ("state_dir", "workspace"):
        cfg[name] = str(Path(cfg[name]).expanduser().resolve())
    cfg["voice"]["model_dir"] = str(Path(cfg["voice"]["model_dir"]).expanduser())
    return cfg


def socket_path(cfg: dict) -> Path:
    return Path(cfg["state_dir"]) / "arvis.sock"


def write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.write("\n")
    temp.replace(path)
