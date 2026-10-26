"""Small command interface for the native shell, hotkeys, and terminal."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sys

from .config import load_config, socket_path


async def request(cfg, method, params=None):
    reader, writer = await asyncio.open_unix_connection(str(socket_path(cfg)), limit=2_000_000)
    try:
        writer.write((json.dumps({"method": method, "params": params or {}}) + "\n").encode())
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 30))
        if not response["ok"]:
            raise RuntimeError(response["error"])
        return response["result"]
    finally:
        writer.close()
        await writer.wait_closed()


def doctor(cfg):
    tools = {name: shutil.which(name) for name in ("codex", "chromium", "pw-record", "pw-play", "voxtype", "hyprctl", "grim", "gdbus", "qs")}
    packages = {name: importlib.util.find_spec(name) is not None for name in ("playwright", "sherpa_onnx", "numpy", "sentencepiece", "piper_phonemize")}
    from .native import NativeWorker
    model_dir = Path(cfg["voice"]["model_dir"])
    return {"tools": tools, "packages": packages, "voice_models": {"wake": (model_dir / "kws" / "tokens.txt").exists(), "speech": (model_dir / "tts").is_dir()}, "socket": str(socket_path(cfg)), "state_dir": cfg["state_dir"], "workspace": cfg["workspace"], "browser": "Independent profile; no host mouse control", "native": NativeWorker(cfg)._status(), "room_devices": "Not connected", "calendar": "Connect through the agent's available integrations; no calendar adapter configured"}


def main():
    parser = argparse.ArgumentParser(description="Arvis desktop assistant")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("daemon", "toggle", "hide", "activate", "mute", "cancel", "new-conversation"):
        sub.add_parser(name)
    for name in ("status", "doctor"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    ask = sub.add_parser("ask")
    ask.add_argument("text", nargs="+")
    respond = sub.add_parser("respond")
    respond.add_argument("id")
    respond.add_argument("decision", choices=["allow", "deny", "answer"])
    respond.add_argument("text", nargs="*", default=[])
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        if args.command == "daemon":
            from .daemon import serve
            asyncio.run(serve(cfg))
            return
        if args.command == "doctor":
            output = doctor(cfg)
        else:
            params = {}
            if args.command == "ask":
                params = {"text": " ".join(args.text)}
            elif args.command == "respond":
                params = {"id": args.id, "approved": args.decision != "deny", "text": " ".join(args.text)}
            output = asyncio.run(request(cfg, args.command.replace("-", "_"), params))
        if args.command == "status" and not args.json:
            print(f"Arvis · {output['state']} · {output['activity']}\nVoice: {output['voice']}\nQueued: {output.get('queued', 0)}")
        else:
            print(json.dumps(output, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"Arvis: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
