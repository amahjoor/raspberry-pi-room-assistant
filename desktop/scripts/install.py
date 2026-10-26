#!/usr/bin/env python3
"""Install Arvis's user service and native Omarchy integration. No root required."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def backup(path, backup_root, home):
    if path.exists():
        target = backup_root / path.relative_to(home)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--no-hotkeys", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        raise RuntimeError("Create desktop/.venv and install this package before installing the service")
    subprocess.run(["omarchy", "plugin", "validate", str(ROOT / "omarchy/arvis.desktop")], check=True)
    backup_root = data_home / "arvis/backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    launcher = home / ".local/bin/arvis-pc"
    # Complete collision and parse checks before changing any user files.
    bindings_file = config_home / "hypr/bindings.lua"
    bindings = bindings_file.read_text()
    marker = "-- Arvis desktop assistant"
    binding_block = ""
    if not args.no_hotkeys and marker not in bindings:
        existing = json.loads(subprocess.check_output(["hyprctl", "-j", "binds"], text=True))
        shortcuts = [("SUPER + ALT + A", 72, "A", "activate", "Talk to Arvis"),
                     ("SUPER + CTRL + ALT + A", 76, "A", "toggle", "Arvis conversation"),
                     ("SUPER + CTRL + ESCAPE", 68, "Escape", "cancel", "Stop Arvis")]
        for label, mods, key, command, description in shortcuts:
            if any(int(b.get("modmask", -1)) == mods and str(b.get("key", "")).lower() == key.lower() for b in existing):
                raise RuntimeError(f"{label} is already bound. Rerun with --no-hotkeys to install without replacing it.")
        binding_block = "\n" + marker + "\n"
        for label, _, _, command, description in shortcuts:
            binding_block += f"o.bind({json.dumps(label)}, {json.dumps(description)}, {json.dumps(str(launcher) + ' ' + command)})\n"
    shell_file = config_home / "omarchy/shell.json"
    shell_config = json.loads(shell_file.read_text()) if shell_file.exists() else {"version": 1}
    launcher.parent.mkdir(parents=True, exist_ok=True)
    backup(launcher, backup_root, home)
    launcher.write_text("#!/bin/sh\nexec " + shlex.quote(str(python)) + " -m arvis_desktop.cli \"$@\"\n")
    launcher.chmod(0o755)

    config_dir = config_home / "arvis"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_file = config_dir / "config.json"
    if not config_file.exists():
        cfg = {"state_dir": str(state_home / "arvis"), "workspace": str(data_home / "arvis/workspace"),
               "project_roots": [str(home / "Work")], "notifications": True,
               "browser_executable": shutil.which("chromium"), "browser_headless": True,
               "native_bin_dir": str(data_home / "arvis/native-tools/usr/bin"),
               "native_lib_dir": str(data_home / "arvis/native-tools/usr/lib"),
               "native_apps": {name: argv for name, argv in {"files": ["/usr/bin/nautilus", "--new-window"], "video_editor": ["/usr/bin/kdenlive"]}.items() if Path(argv[0]).exists()},
               "instructions": "The user calls you Arvis. Priorities: capable computer use, getting work done on Linux, helpful conversation, and eventually room controls and calendar. Avoid adding a generic study mode. The main local product projects are under ~/Work/kits. Do not assume calendar or room accounts are connected.",
               "voice": {"enabled": True, "wake_enabled": True, "speak_enabled": True, "model_dir": str(data_home / "arvis/voice")}}
        config_file.write_text(json.dumps(cfg, indent=2) + "\n")
        config_file.chmod(0o600)

    plugin_dir = config_home / "omarchy/plugins/arvis.desktop"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    # Copy entry points before the manifest, which triggers discovery/reload.
    for source in sorted((ROOT / "omarchy/arvis.desktop").iterdir(), key=lambda p: p.name == "manifest.json"):
        if source.is_file():
            backup(plugin_dir / source.name, backup_root, home)
            shutil.copy2(source, plugin_dir / source.name)

    backup(shell_file, backup_root, home)
    right = shell_config.setdefault("bar", {}).setdefault("layout", {}).setdefault("right", [])
    right[:] = [item for item in right if (item.get("id") if isinstance(item, dict) else item) != "arvis.assistant"]
    if not any((item.get("id") if isinstance(item, dict) else item) == "arvis.desktop" for item in right):
        right.insert(0, {"id": "arvis.desktop"})
    shell_file.write_text(json.dumps(shell_config, indent=2) + "\n")

    if binding_block:
        backup(bindings_file, backup_root, home)
        bindings_file.write_text(bindings + binding_block)

    service_path = config_home / "systemd/user/arvis.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    backup(service_path, backup_root, home)
    # Preserve installed tool locations (mise's node/codex) without putting secrets in a unit.
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin")
    safe_path = path.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    executable = str(launcher).replace("%", "%%").replace('"', '\\"')
    service_path.write_text("[Unit]\nDescription=Arvis personal desktop assistant\nAfter=graphical-session.target pipewire.service\nPartOf=graphical-session.target\n\n[Service]\nType=simple\n" + f'ExecStart="{executable}" daemon\nEnvironment="PATH={safe_path}"\n' + "Restart=on-failure\nRestartSec=4\nTimeoutStopSec=15\nUMask=0077\n\n[Install]\nWantedBy=graphical-session.target\n")
    if not args.no_hotkeys:
        subprocess.run(["hyprctl", "reload"], check=True)
        errors = subprocess.check_output(["hyprctl", "configerrors"], text=True).strip()
        if errors and errors != "ok":
            raise RuntimeError("Hyprland reported configuration errors: " + errors)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if args.start:
        subprocess.run(["systemctl", "--user", "enable", "arvis.service"], check=True)
        subprocess.run(["systemctl", "--user", "restart", "arvis.service"], check=True)
    print(f"Installed Arvis. Backups: {backup_root}")
    print("Talk: Super+Alt+A. Conversation: Super+Ctrl+Alt+A. Stop: Super+Ctrl+Escape.")


if __name__ == "__main__":
    main()
