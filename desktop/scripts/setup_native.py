#!/usr/bin/env python3
"""Install signed Arch virtual-desktop tools in user space, without sudo."""
import argparse
from pathlib import Path
import subprocess
import urllib.request
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path.home() / ".local/share/arvis/native-tools")
    args = parser.parse_args()
    destination = args.destination.expanduser().resolve()
    cache = destination / "packages"
    cache.mkdir(parents=True, exist_ok=True)
    urls = subprocess.check_output(["pacman", "-Sp", "--print-format", "%l", "--", "xorg-server-xvfb", "xdotool"], text=True).splitlines()
    source_keyrings = sorted(Path("/usr/share/pacman/keyrings").glob("*.gpg"))
    if not source_keyrings:
        raise RuntimeError("No installed distribution package-signing keys found")
    keyrings = []
    for source in source_keyrings:
        data = source.read_bytes()
        if data.lstrip().startswith(b"-----BEGIN PGP"):
            data = subprocess.check_output(["gpg", "--batch", "--dearmor"], input=data)
        target = cache / source.name
        target.write_bytes(data)
        keyrings.append(target)
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.path.endswith(".pkg.tar.zst"):
            raise ValueError(f"Unexpected package URL: {url}")
        package = cache / Path(parsed.path).name
        for source, output in [(url, package), (url + ".sig", package.with_suffix(package.suffix + ".sig"))]:
            with urllib.request.urlopen(source, timeout=45) as response, output.open("wb") as out:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 32 * 1024 * 1024:
                        raise ValueError("Unexpectedly large package")
                    out.write(chunk)
        verify = ["gpgv"]
        for keyring in keyrings:
            verify.extend(["--keyring", str(keyring)])
        subprocess.run(verify + [str(package) + ".sig", str(package)], check=True)
        subprocess.run(["bsdtar", "-xf", str(package), "-C", str(destination), "usr/"], check=True)
    print(f"Native desktop tools installed: {destination / 'usr/bin'}")


if __name__ == "__main__":
    main()
