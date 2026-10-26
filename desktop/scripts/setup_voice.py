#!/usr/bin/env python3
"""Install Arvis's local wake-word and optional speech models.

Downloads only named upstream releases, verifies their published SHA-256, and
extracts regular files into a staging directory before making them available.
This script never starts a microphone or modifies desktop configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import urllib.request


RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
KWS = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
TTS = "vits-piper-en_US-lessac-medium"
SUFFIX = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
# Verified against the upstream release checksum manifests on 2026-10-26.
PINNED_SHA256 = {
    f"{KWS}.tar.bz2": "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a",
    f"{TTS}.tar.bz2": "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e",
}


def request(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Arvis-Voice-Setup/1.0"}), timeout=60)


def published_hash(tag: str, filename: str) -> str:
    with request(f"{RELEASES}/{tag}/checksum.txt") as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Upstream checksum file is unexpectedly large")
    for line in data.decode("utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        # Sherpa publishes filename<TAB>sha256; accept the conventional
        # sha256sum order too without interpreting unrelated manifest lines.
        if parts[0].removeprefix("./") == filename:
            digest = parts[1].lower()
        elif parts[1].lstrip("*").removeprefix("./") == filename:
            digest = parts[0].lower()
        else:
            continue
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            if filename in PINNED_SHA256 and digest != PINNED_SHA256[filename]:
                raise ValueError(f"Upstream checksum changed for pinned model {filename}")
            return digest
    raise ValueError(f"No published SHA-256 found for {filename}; refusing an unverified download")


def download(url: str, destination: Path, digest: str, max_bytes: int) -> None:
    total = 0
    checksum = hashlib.sha256()
    with request(url) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Model download exceeds its size limit")
            checksum.update(chunk)
            output.write(chunk)
    if checksum.hexdigest() != digest:
        raise ValueError(f"SHA-256 mismatch for {destination.name}")


def extract_model(archive: Path, staging: Path, expected_root: str, max_bytes: int = 512 * 1024 * 1024) -> Path:
    """Extract regular files only, with strict root/path and expanded-size bounds."""
    total = 0
    count = 0
    with tarfile.open(archive, "r:bz2") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or path.parts[0] != expected_root
                    or ".." in path.parts or "\\" in member.name):
                raise ValueError(f"Unsafe archive member: {member.name}")
            if not member.isfile() and not member.isdir():
                raise ValueError(f"Links and special files are not allowed: {member.name}")
            count += 1
            total += member.size
            if member.size < 0 or total > max_bytes or count > 20000:
                raise ValueError("Model archive exceeds extraction limits")
            destination = staging.joinpath(*path.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as content, destination.open("xb") as output:
                shutil.copyfileobj(content, output)
            destination.chmod(0o600)
    return staging / expected_root


def install_model(base: Path, kind: str, tag: str, name: str, required: list[str], limit: int) -> None:
    destination = base / kind
    if destination.exists():
        if not all((destination / item).exists() for item in required):
            raise ValueError(f"{destination} is incomplete. Move it aside and rerun setup.")
        print(f"Using existing {kind} model: {destination}")
        return
    filename = f"{name}.tar.bz2"
    digest = published_hash(tag, filename)
    print(f"Downloading {filename}", flush=True)
    with tempfile.TemporaryDirectory(prefix=f".{kind}-", dir=base) as temp:
        staging = Path(temp)
        archive = staging / filename
        download(f"{RELEASES}/{tag}/{filename}", archive, digest, limit)
        extracted = extract_model(archive, staging, name)
        for item in required:
            if not (extracted / item).exists():
                raise ValueError(f"Downloaded {kind} model is missing {item}")
        (extracted / ".arvis-model.json").write_text(json.dumps({
            "source": f"{RELEASES}/{tag}/{filename}", "sha256": digest,
        }, indent=2) + "\n")
        extracted.rename(destination)
    print(f"Installed {kind}: {destination}")


def tokenize_arvis(base: Path) -> str:
    import sentencepiece
    processor = sentencepiece.SentencePieceProcessor(model_file=str(base / "bpe.model"))
    pieces = processor.encode("ARVIS", out_type=str)
    known = {line.rsplit(maxsplit=1)[0] for line in (base / "tokens.txt").read_text().splitlines() if line.strip()}
    if not pieces or any(piece not in known or piece == "<unk>" for piece in pieces):
        raise ValueError("The downloaded tokenizer cannot represent ARVIS")
    result = " ".join(pieces) + " @ARVIS\n"
    (base / "keywords_raw.txt").write_text("ARVIS\n", encoding="utf-8")
    temporary = base / ".keywords.txt.tmp"
    temporary.write_text(result, encoding="utf-8")
    temporary.replace(base / "keywords.txt")
    return result.strip()


def main() -> None:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=data_home / "arvis/voice")
    parser.add_argument("--with-tts", action="store_true", help="Also install a local English spoken response voice")
    args = parser.parse_args()
    try:
        import numpy  # noqa: F401
        import sentencepiece  # noqa: F401
        import sherpa_onnx  # noqa: F401
    except ImportError as exc:
        parser.error(f"Missing voice dependency: {exc}. Install the project's voice extra in its venv first.")
    if args.with_tts:
        try:
            import piper_phonemize  # noqa: F401
        except ImportError:
            parser.error("Local TTS needs piper-phonemize==1.5.9. Install it with --find-links "
                         "https://k2-fsa.github.io/icefall/piper_phonemize.html in the voice venv.")
    base = args.model_dir.expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    install_model(base, "kws", "kws-models", KWS,
                  [f"{part}-{SUFFIX}" for part in ("encoder", "decoder", "joiner")]
                  + ["tokens.txt", "bpe.model"], 64 * 1024 * 1024)
    print(f"Exact wake word configured: {tokenize_arvis(base / 'kws')}")
    if args.with_tts:
        install_model(base, "tts", "tts-models", TTS,
                      ["en_US-lessac-medium.onnx", "tokens.txt", "espeak-ng-data"], 256 * 1024 * 1024)
    print("Voice models ready. No microphone has been started.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"Voice setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
