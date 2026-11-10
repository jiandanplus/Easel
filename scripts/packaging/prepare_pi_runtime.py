"""Build-time only: prepare the pinned public Pi SDK/Node payload, never at launch."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PI_VERSION = "1.4.0"
NODE_VERSION = "v24.22.1"
NODE_SHA = "89c682593d76cd44233fa53b1563264e7de9cbb189cad169135c5b4fadfbc0a7"

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_inputs(root):
    pin = json.loads((root / "packaging/pi/node-provenance.json").read_text())
    if pin["version"] != NODE_VERSION or pin["archive_sha256"] != NODE_SHA:
        raise ValueError("Node pin differs from approved official public digest")
    package = json.loads((root / "runtime/pi/package.json").read_text())
    lock = json.loads((root / "runtime/pi/package-lock.json").read_text())
    for name in ("@earendil-works/pi-agent-core", "@earendil-works/pi-ai"):
        if package["dependencies"][name] != PI_VERSION or lock["packages"]["node_modules/" + name]["version"] != PI_VERSION:
            raise ValueError("Pi SDK must be exactly pinned")
    for path, entry in lock["packages"].items():
        if path and (not entry.get("integrity", "").startswith("sha512-") or not entry.get("resolved", "").startswith("https://registry.npmjs.org/")):
            raise ValueError("Unpinned or non-public npm input: " + path)
    return pin


def materialize_links(root):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Payload link escapes outside owned root")
            if not target.is_file():
                raise ValueError("Payload directory links are unsupported")
            path.unlink()
            shutil.copy2(target, path)


def write_manifest(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Pi payload contains link")
        if path.is_file() and path != root / "runtime-manifest.json":
            files[path.relative_to(root).as_posix()] = digest(path)
    manifest = {"schema_version": 2, "platform": "darwin", "architecture": "arm64",
                "node": {"version": NODE_VERSION.removeprefix("v"), "executable": "node/bin/node", "sha256": files["node/bin/node"]},
                "pi": {"version": PI_VERSION, "worker": "worker.mjs", "sha256": files["worker.mjs"]}, "files": files}
    (root / "runtime-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


def refresh_worker(output):
    """Refresh only the authored worker in an already verified pinned payload."""
    sys.path.insert(0, str(ROOT))
    from easel.harnesses.pi_runtime import resolve_pi
    previous = os.environ.get("EASEL_PI_RUNTIME_MANIFEST")
    try:
        os.environ["EASEL_PI_RUNTIME_MANIFEST"] = str(output / "runtime-manifest.json")
        resolve_pi()
    finally:
        if previous is None:
            os.environ.pop("EASEL_PI_RUNTIME_MANIFEST", None)
        else:
            os.environ["EASEL_PI_RUNTIME_MANIFEST"] = previous
    read_inputs(ROOT)
    for name in ("package.json", "package-lock.json"):
        if digest(output / name) != digest(ROOT / "runtime/pi" / name):
            raise ValueError("Cannot refresh worker after dependency authority drift")
    provenance = json.loads((output / "dependency-provenance.json").read_text())
    if provenance["node"]["archive_sha256"] != NODE_SHA or not provenance["node"]["archive_sha256_verified"]:
        raise ValueError("Cannot refresh unknown payload")
    shutil.copy2(ROOT / "runtime/pi/worker.mjs", output / "worker.mjs")
    write_manifest(output)
    print(json.dumps({"refreshed_worker": str(output / "worker.mjs"), "sha256": digest(output / "worker.mjs")}))


def prepare(output, *, archive=None):
    pin = read_inputs(ROOT)
    if output.exists():
        raise ValueError("Refusing existing payload; use a fresh output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="easel-pi-download-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        if archive is None:
            archive = temporary / pin["archive_name"]
            with urllib.request.urlopen(pin["archive_url"], timeout=120) as response, archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        if digest(archive) != NODE_SHA:
            raise ValueError("Official Node archive SHA256 mismatch")
        unpacked = temporary / "unpacked"
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        upstream = unpacked / ("node-" + NODE_VERSION + "-darwin-arm64")
        (output / "node/bin").mkdir(parents=True)
        for relative in ("bin/node", "LICENSE", "README.md"):
            shutil.copy2(upstream / relative, output / "node" / relative)
        for name in ("package.json", "package-lock.json", "worker.mjs"):
            shutil.copy2(ROOT / "runtime/pi" / name, output / name)
        # Use downloaded Node and its npm only while preparing; no npm is shipped.
        npm = upstream / "lib/node_modules/npm/bin/npm-cli.js"
        (temporary / "user.npmrc").write_text("")
        (temporary / "global.npmrc").write_text("")
        env = {"HOME": str(temporary / "npm-home"), "PATH": str(upstream / "bin") + ":/usr/bin:/bin", "npm_config_cache": str(temporary / "npm-cache"), "npm_config_userconfig": str(temporary / "user.npmrc"), "npm_config_globalconfig": str(temporary / "global.npmrc"), "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_update_notifier": "false"}
        subprocess.run([str(upstream / "bin/node"), str(npm), "ci", "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund"], cwd=output, env=env, check=True)
    materialize_links(output)
    licenses = []
    packages = []
    for path in sorted((output / "node_modules").rglob("package.json")):
        metadata = json.loads(path.read_text())
        packages.append({"path": path.parent.relative_to(output).as_posix(), "name": metadata.get("name"), "version": metadata.get("version"), "license": metadata.get("license")})
    for path in sorted((output / "node_modules").rglob("*")):
        if path.is_file() and path.name.lower().startswith(("license", "licence", "notice", "copying")):
            licenses.append(path.relative_to(output).as_posix())
    provenance = {"node": {**pin, "archive_downloaded": True, "archive_sha256_verified": True, "signature_verified": False}, "pi_version": PI_VERSION, "lock_sha256": digest(output / "package-lock.json"), "npm_lifecycle_scripts": False, "packages": packages, "license_files": licenses}
    (output / "dependency-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    manifest = write_manifest(output)
    print(json.dumps({"payload": str(output), "manifest": str(output / "runtime-manifest.json"), "files": len(manifest["files"]), "node": NODE_VERSION, "pi": PI_VERSION}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--node-archive", type=Path, help="Optional already-downloaded exact public archive")
    parser.add_argument("--refresh-worker", action="store_true", help="Verify existing payload, then refresh current authored worker only")
    args = parser.parse_args()
    if args.refresh_worker:
        refresh_worker(args.output.absolute())
    else:
        prepare(args.output.absolute(), archive=args.node_archive)

if __name__ == "__main__":
    main()
