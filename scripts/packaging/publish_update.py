#!/usr/bin/env python3
"""Write a signed in-app update manifest for a built macOS package.

Outputs easel-update.json and easel-update.json.sig; upload both next to the ZIP
(the default manifest URL is the latest GitHub release of this repository).
The signature is checked against the public key embedded in easel/updater.py,
so a release signed with any other key is refused here rather than by clients.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from easel import updater  # noqa: E402


def manifest_for(artifact: dict, url: str, notes: str) -> dict:
    if type(artifact.get("version")) is not int:
        raise ValueError("artifact.json has no release version; rebuild with --version")
    return {"format": updater.FORMAT, "version": artifact["version"], "url": url,
            "sha256": artifact["sha256"], "bytes": artifact["archive_bytes"], "notes": notes,
            "minimum_macos": artifact.get("minimum_macos", "15.0")}


def publish(artifact_path: Path, url: str, notes: str, key_path: Path, output: Path, *,
            node: str, public_key: str = updater.PUBLIC_KEY) -> Path:
    manifest = manifest_for(json.loads(artifact_path.read_text(encoding="utf-8")), url, notes)
    data = json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n"
    updater.parse_manifest(data, signature := updater.sign(data, key_path.read_text(), node),
                           public_key=public_key, node=node)
    output.mkdir(parents=True, exist_ok=True)
    (output / "easel-update.json").write_bytes(data)
    (output / "easel-update.json.sig").write_text(signature + "\n")
    return output / "easel-update.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="artifact.json written by build_pi_macos.py")
    parser.add_argument("--url", required=True, help="HTTPS URL where the ZIP will be downloadable")
    parser.add_argument("--notes", default="", help="Release notes shown in the app")
    parser.add_argument("--key", type=Path, required=True, help="Offline Ed25519 private key (PEM)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    node = shutil.which("node")
    if node is None:
        parser.error("node is required to sign")
    try:
        path = publish(args.artifact, args.url, args.notes, args.key, args.output, node=node)
    except (ValueError, updater.UpdateError) as error:
        print(f"publish failed: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
