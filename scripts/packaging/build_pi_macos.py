"""Build an unsigned local macOS ARM64 Pi app from verified offline inputs.

Freeze reviewed sources first with --freeze-source-only. A later build requires
that exact authority and the matching frontend receipt; it never stages Git files.
"""
from __future__ import annotations
import argparse
import ctypes
import errno
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from easel.packaging_sources import source_records, frontend_records, verify_source_records
from easel.portable import digest
spec = importlib.util.spec_from_file_location("legacy_macos_builder", ROOT / "scripts/packaging/build_macos.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

METADATA = ["core-requirements.txt", "wheels-provenance.json", "python-assets.json", "python-runtime-files.json", "python-source-provenance.json", "codex-vendor-provenance.json", "build-preparation-notes.json"]
PYTHON_VERSION = "3.12.13"
PYTHON_BUILD = "20260510"
PYTHON_ARCHIVE = f"cpython-{PYTHON_VERSION}+{PYTHON_BUILD}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_ARCHIVE_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_BUILD}/" + PYTHON_ARCHIVE.replace("+", "%2B")
UV_DERIVATION_OPTIONS = ["--offline", "--no-config", "--no-bin", "--no-registry"]
CLONEFILE_FLAGS = 0x0001 | 0x0008  # CLONE_NOFOLLOW | CLONE_NOFOLLOW_ANY
COPY_SAFETY_RESERVE = 200 * 1024**2


def verify_pi_payload(payload):
    from easel.harnesses.pi_runtime import resolve_pi
    previous = os.environ.get("EASEL_PI_RUNTIME_MANIFEST")
    try:
        os.environ["EASEL_PI_RUNTIME_MANIFEST"] = str(payload / "runtime-manifest.json")
        return resolve_pi()
    finally:
        if previous is None:
            os.environ.pop("EASEL_PI_RUNTIME_MANIFEST", None)
        else:
            os.environ["EASEL_PI_RUNTIME_MANIFEST"] = previous


def freeze_sources(authority):
    records = source_records(ROOT)
    authority.parent.mkdir(parents=True, exist_ok=True)
    authority.write_text(json.dumps({"schema_version": 1, "policy_sha256": digest(ROOT / "packaging/pi/public-source-policy.json"), "build_inputs": build_input_records(authority), "files": records}, sort_keys=True, indent=2) + "\n")
    return records


def build_input_records(authority):
    paths = [ROOT / "LICENSE", ROOT / "scripts/packaging/build_pi_macos.py", ROOT / "scripts/packaging/build_macos.py", ROOT / "scripts/packaging/prepare_pi_runtime.py"]
    paths.extend(ROOT / "runtime/pi" / name for name in ("worker.mjs", "package.json", "package-lock.json"))
    for base in (ROOT / "packaging/pi", ROOT / "packaging/macos"):
        for parent, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in {"payload", "stage"})
            for name in dirs:
                if (Path(parent) / name).is_symlink():
                    raise ValueError("Build input directory link")
            paths.extend(Path(parent) / name for name in names)
    records = {}
    for path in paths:
        if path == authority or path.name.startswith("."):
            continue
        legacy.regular_input(path)
        records[path.relative_to(ROOT).as_posix()] = digest(path)
    return dict(sorted(records.items()))


def verified_sources(authority):
    data = json.loads(authority.read_text())
    if data.get("schema_version") != 1 or data.get("policy_sha256") != digest(ROOT / "packaging/pi/public-source-policy.json"):
        raise ValueError("Public source policy changed; review and freeze sources")
    records = data["files"]
    verify_source_records(ROOT, records)
    if data.get("build_inputs") != build_input_records(authority):
        raise ValueError("Public build inputs differ or are stale; review and freeze again")
    return records


def regular_directory(path):
    for current in (path, *path.parents):
        if current.is_symlink() or not current.is_dir():
            raise ValueError("Python input directory link or missing directory: " + str(path))


def verify_python_archive(archive):
    """Check members without extracting or executing the fixed source archive."""
    try:
        with tarfile.open(archive, "r:gz") as stream:
            members = stream.getmembers()
    except (tarfile.TarError, OSError) as error:
        raise ValueError("Invalid Python source archive") from error
    if not members or len(members) > 10000 or sum(member.size for member in members) > 1024**3:
        raise ValueError("Invalid Python archive member inventory")
    names, links = {}, {}
    for member in members:
        path = PurePosixPath(member.name)
        if (not path.parts or path.is_absolute() or path.parts[0] != "python" or ".." in path.parts
                or "\\" in member.name or "\x00" in member.name or path.as_posix() in names
                or member.mode & 0o6000 or not (member.isfile() or member.isdir() or member.issym())):
            raise ValueError("Unsafe Python archive member: " + member.name)
        names[path.as_posix()] = member
        if member.issym():
            target = PurePosixPath(member.linkname)
            resolved = posixpath.normpath(posixpath.join(str(path.parent), member.linkname))
            if target.is_absolute() or "\\" in member.linkname or not resolved.startswith("python/"):
                raise ValueError("Unsafe Python archive link: " + member.name)
            links[path.as_posix()] = resolved
    for name in names:
        if any(str(parent) in links for parent in PurePosixPath(name).parents):
            raise ValueError("Python archive member traverses a directory link")
    for name, target in links.items():
        visited = {name}
        while target in links and target not in visited:
            visited.add(target)
            target = links[target]
        if target in visited or target not in names or not names[target].isfile():
            raise ValueError("Unsafe or broken Python archive link: " + name)


def verify_python(inputs, *, with_provenance=False):
    regular_directory(inputs)
    authority = ROOT / "packaging/macos"
    for name in METADATA:
        legacy.regular_input(authority / name)
        legacy.regular_input(inputs / name)
    legacy.verify_metadata(inputs, authority, METADATA)
    source = json.loads((authority / "python-source-provenance.json").read_text())
    inventory_path = authority / "python-runtime-files.json"
    inventory = json.loads(inventory_path.read_text())
    expected_python = {"version": PYTHON_VERSION, "build": PYTHON_BUILD, "platform": "darwin", "architecture": "arm64"}
    derivation = source.get("derivation", {})
    if (source.get("schema_version") != 1 or source.get("python") != expected_python
            or derivation.get("tool") != "uv" or derivation.get("version") != "0.11.16"
            or derivation.get("method") != "offline-local-archive"
            or derivation.get("options") != UV_DERIVATION_OPTIONS or derivation.get("home_unchanged") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", str(derivation.get("executable_sha256", "")))):
        raise ValueError("Invalid Python source provenance or derivation version")
    inventory_sha = digest(inventory_path)
    if (inventory.get("python_version") != PYTHON_VERSION or inventory.get("build") != PYTHON_BUILD
            or inventory.get("uv_version") != "0.11.16" or not isinstance(inventory.get("files"), dict)
            or source.get("runtime_inventory_sha256") != inventory_sha):
        raise ValueError("Python runtime inventory does not match source provenance")
    pins = [entry for entry in json.loads((authority / "python-assets.json").read_text()) if entry.get("name") == PYTHON_ARCHIVE]
    asset = source.get("archive", {})
    if (len(pins) != 1 or pins[0].get("url") != PYTHON_ARCHIVE_URL
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(pins[0].get("digest", "")))
            or asset.get("name") != PYTHON_ARCHIVE or asset.get("url") != PYTHON_ARCHIVE_URL
            or asset.get("sha256") != pins[0]["digest"].removeprefix("sha256:")
            or type(asset.get("bytes")) is not int or asset["bytes"] <= 0):
        raise ValueError("Python archive provenance differs from fixed official asset authority")
    archive = inputs / "source-archives" / PYTHON_ARCHIVE
    if not archive.exists():
        raise ValueError("Missing Python source archive")
    legacy.regular_input(archive)
    archive_sha = digest(archive)
    if archive.stat().st_size != asset["bytes"] or archive_sha != asset["sha256"]:
        raise ValueError("Python source archive bytes or SHA256 mismatch")
    verify_python_archive(archive)
    python = inputs / "python-download/cpython-3.12.13-macos-aarch64-none"
    regular_directory(python)
    for path in python.rglob("*"):
        if path.is_symlink():
            if path.readlink().is_absolute() or not path.resolve().is_relative_to(python.resolve()) or not path.resolve().is_file():
                raise ValueError("Unsafe Python runtime link: " + str(path))
    legacy.verify_payload(python, inventory["files"], bytecode=True)
    wheels = json.loads((ROOT / "packaging/macos/wheels-provenance.json").read_text())
    # Existing public wheel authority stores an array of filename/hash records.
    entries = wheels["wheels"] if isinstance(wheels, dict) and "wheels" in wheels else wheels
    if isinstance(entries, list):
        entries = {entry.get("filename", entry.get("file", "")): entry["sha256"] for entry in entries}
    regular_directory(inputs / "wheels")
    legacy.verify_payload(inputs / "wheels", entries)
    proof = {"archive_verified": archive_sha == pins[0]["digest"].removeprefix("sha256:"),
             "archive_sha256": archive_sha, "archive_bytes": archive.stat().st_size,
             "source_provenance_sha256": digest(authority / "python-source-provenance.json"),
             "runtime_inventory_sha256": inventory_sha}
    return (python, proof) if with_provenance else python


@lru_cache(maxsize=1)
def clonefile_api():
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "copy-on-write requires macOS clonefile")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.clonefile
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


def clone_regular_file(source, destination):
    if clonefile_api()(os.fsencode(source), os.fsencode(destination), CLONEFILE_FLAGS) != 0:
        code = ctypes.get_errno()
        raise OSError(code, "clonefile failed; no copy fallback: " + os.strerror(code), str(source))


def runtime_tree_snapshot(root, *, symlinks, ignore_bytecode):
    records = {}
    resolved_root = root.resolve()
    for path in (root, *root.rglob("*")):
        relative = path.relative_to(root)
        if ignore_bytecode and any(part == "__pycache__" or part.endswith(".pyc") for part in relative.parts):
            continue
        info = path.lstat()
        row = {"mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns,
               "device": info.st_dev, "inode": info.st_ino}
        if stat.S_ISLNK(info.st_mode):
            target = path.readlink()
            if not symlinks or target.is_absolute() or not path.resolve().is_relative_to(resolved_root) or not path.resolve().is_file():
                raise ValueError("Unsafe runtime source link: " + str(path))
            row.update(kind="link", target=str(target))
        elif stat.S_ISDIR(info.st_mode):
            row.update(kind="directory")
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            row.update(kind="file", size=info.st_size, sha256=digest(path))
        else:
            raise ValueError("Runtime source must be regular and not hardlinked: " + str(path))
        records[relative.as_posix()] = row
    return records


def copy_runtime_tree(source, destination, *, copy_on_write=False, symlinks=False, ignore_bytecode=False):
    """Explicit clonefile mode never falls back; normal mode keeps copytree behavior."""
    source, destination = Path(source), Path(destination)
    if not copy_on_write:
        shutil.copytree(source, destination, symlinks=symlinks,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc") if ignore_bytecode else None)
        return "copytree"
    regular_directory(source)
    regular_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Runtime destination already exists: " + str(destination))
    src, dst = source.resolve(), destination.resolve()
    if src.is_relative_to(dst) or dst.is_relative_to(src):
        raise ValueError("Runtime source and destination overlap")
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise OSError(errno.EXDEV, "clonefile requires the same filesystem")
    before = runtime_tree_snapshot(source, symlinks=symlinks, ignore_bytecode=ignore_bytecode)
    destination.mkdir()
    order = sorted(before, key=lambda name: (len(Path(name).parts), name))
    for name in order:
        if name == ".":
            continue
        item, target = source / name, destination / name
        if before[name]["kind"] == "directory":
            target.mkdir()
        elif before[name]["kind"] == "link":
            target.symlink_to(before[name]["target"])
        else:
            clone_regular_file(item, target)
    for name in reversed(order):
        shutil.copystat(source / name, destination / name, follow_symlinks=False)
    if runtime_tree_snapshot(source, symlinks=symlinks, ignore_bytecode=ignore_bytecode) != before:
        raise ValueError("Runtime source changed during clonefile copy")
    copied = runtime_tree_snapshot(destination, symlinks=symlinks, ignore_bytecode=ignore_bytecode)
    if set(copied) != set(before):
        raise ValueError("Cloned runtime inventory differs")
    for name, original in before.items():
        actual = copied[name]
        if original["kind"] == "file" and (original["device"], original["inode"]) == (actual["device"], actual["inode"]):
            raise ValueError("clonefile did not create an independent inode")
        if {k: v for k, v in original.items() if k not in {"device", "inode"}} != {k: v for k, v in actual.items() if k not in {"device", "inode"}}:
            raise ValueError("Cloned runtime hash, link or attributes differ: " + name)
    return "clonefile"


def require_copy_space(path, remaining_bytes):
    path = Path(path)
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free < remaining_bytes + COPY_SAFETY_RESERVE:
        raise OSError(errno.ENOSPC, "copy budget requires remaining estimate plus 200 MiB free space")


def copy_licenses(resource):
    for category, base in (("python", ROOT / "packaging/macos/licenses"), ("pi", ROOT / "packaging/pi")):
        for parent, dirs, files in os.walk(base, followlinks=False):
            for name in dirs:
                if (Path(parent) / name).is_symlink():
                    raise ValueError("License directory links forbidden")
            for name in files:
                source = Path(parent) / name
                legacy.regular_input(source)
                destination = resource / "licenses" / category / source.relative_to(base)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    shutil.copy2(ROOT / "LICENSE", resource / "licenses/Easel-LICENSE")


def launcher_script():
    return '''#!/bin/sh
HERE="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources" && pwd)"
LOG="$HOME/Library/Logs/Easel"
mkdir -p "$LOG"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME PYTHONUSERBASE PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONPLATLIBDIR PYTHONPYCACHEPREFIX
cd "$HERE" || exit 1
exec "$HERE/python/bin/python3" -I -B -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from easel.portable import main; raise SystemExit(main())' "$HERE" "$@" >>"$LOG/launcher.log" 2>&1
'''


def build(inputs, pi_root, output, authority, *, copy_on_write=False, version=None):
    if output.exists():
        raise ValueError("Refusing existing delivery directory; choose fresh output")
    records = verified_sources(authority)
    python, python_proof = verify_python(inputs, with_provenance=True)
    verify_pi_payload(pi_root)
    for name in ("worker.mjs", "package.json", "package-lock.json"):
        if digest(pi_root / name) != digest(ROOT / "runtime/pi" / name):
            raise ValueError("Prepared Pi source differs from current authority: " + name)
    # Explicit remaining physical-allocation estimates; the reserve is never spent.
    if copy_on_write:
        require_copy_space(output.parent, 225 * 1024**2)
    # Check frontend before creating a large stage.
    legacy.copy_frontend(ROOT / "web/frontend/dist", output / ".frontend-check", source_records=frontend_records(ROOT))
    container = output / "Easel"
    app = container / "Easel.app"
    resource = app / "Contents/Resources"
    resource.mkdir(parents=True)
    shutil.move(str(output / ".frontend-check"), str(resource / "web/frontend/dist"))
    for relative in records:
        if relative.startswith("web/frontend/"):
            continue
        target = resource / ("seed/" + relative if relative.split("/")[0] in {"skills", "agents", "scripts"} else relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    registry = resource / "skills/shared/scripts/model_registry.py"
    registry.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "skills/shared/scripts/model_registry.py", registry)
    if copy_on_write:
        require_copy_space(output.parent, 190 * 1024**2)
    copy_strategy = copy_runtime_tree(python, resource / "python", copy_on_write=copy_on_write, symlinks=True, ignore_bytecode=True)
    site = resource / "python/lib/python3.12/site-packages"
    if copy_on_write:
        require_copy_space(output.parent, 185 * 1024**2)
    subprocess.run(["uv", "pip", "install", "--offline", "--no-index", "--python", str(resource / "python/bin/python3"), "--require-hashes", "--no-deps", "--no-compile-bytecode", "--target", str(site), "--find-links", str(inputs / "wheels"), "-r", str(ROOT / "packaging/macos/core-requirements.txt")], check=True, env={**os.environ, "UV_NO_CONFIG": "1", "UV_CACHE_DIR": str(output / ".uv-cache")})
    shutil.rmtree(site / "bin", ignore_errors=True)
    if copy_on_write:
        verify_python(inputs)
        require_copy_space(output.parent, 145 * 1024**2)
        (resource / "runtime").mkdir(exist_ok=True)
    pi_strategy = copy_runtime_tree(pi_root, resource / "runtime/pi", copy_on_write=copy_on_write)
    if pi_strategy != copy_strategy:
        raise ValueError("Python and Pi copy strategies differ")
    copy_licenses(resource)
    for name in METADATA:
        shutil.copy2(ROOT / "packaging/macos" / name, resource / name)
    shutil.copy2(authority, resource / "source-manifest.json")
    provenance = {"schema_version": 1, "engine": "pi", "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "source_manifest_sha256": digest(authority), "pi_manifest_sha256": digest(pi_root / "runtime-manifest.json"), "frontend_receipt_sha256": digest(ROOT / "web/frontend/dist/portable-assets.json"), "python_archive_verified": python_proof["archive_verified"], "python_archive_sha256": python_proof["archive_sha256"], "python_source_provenance_sha256": python_proof["source_provenance_sha256"], "python_runtime_inventory_sha256": python_proof["runtime_inventory_sha256"], "python_runtime_inventory_verified": True, "copy_strategy": copy_strategy, "clonefile_flags": CLONEFILE_FLAGS if copy_on_write else None, "python_version": PYTHON_VERSION, "wheel_count": len(list((inputs / "wheels").glob("*.whl"))), "minimum_macos": "15.0", "unsigned": True, "notarized": False, "ui": "loopback web browser"}
    if version is not None:
        # In-app updates compare this number; it is covered by build-inventory.json.
        provenance["version"] = version
    (resource / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    macos = app / "Contents/MacOS"
    macos.mkdir()
    launcher = macos / "Easel"
    launcher.write_text(launcher_script())
    launcher.chmod(0o755)
    stop = container / "Stop Easel.command"
    stop.write_text('''#!/bin/sh
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$HERE/Easel.app/Contents/MacOS/Easel" --stop "$@"
''')
    stop.chmod(0o755)
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": "Easel", "CFBundleIdentifier": "local.easel.pi-preview", "CFBundleName": "Easel", "CFBundleVersion": "0.1.0", "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "15.0", "LSArchitecturePriority": ["arm64"]}, stream)
    shutil.copy2(ROOT / "packaging/pi/README.md", container / "README.md")
    verified_sources(authority)
    inventory = {}
    for path in resource.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(resource):
            raise ValueError("External resource symlink")
        if path.is_file():
            inventory[path.relative_to(resource).as_posix()] = digest(path)
    (resource / "build-inventory.json").write_text(json.dumps(inventory, sort_keys=True) + "\n")
    archive = output / "Easel-Pi-macOS15-arm64.zip"
    if copy_on_write:
        require_copy_space(output.parent, 130 * 1024**2)
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(container), str(archive)], check=True)
    report = {"app": str(app), "archive": str(archive), "sha256": digest(archive), "archive_bytes": archive.stat().st_size, "inventory_files": len(inventory), **provenance}
    (output / "artifact.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=ROOT / "outputs/_run/portable-build")
    parser.add_argument("--pi-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-manifest", type=Path, default=ROOT / "packaging/pi/source-manifest.json")
    parser.add_argument("--freeze-source-only", action="store_true")
    parser.add_argument("--copy-on-write", action="store_true", help="Use clonefile with no fallback for verified runtime trees")
    parser.add_argument("--version", type=int, help="Release number used by in-app updates, e.g. 38")
    args = parser.parse_args()
    if args.freeze_source_only:
        print(json.dumps({"authority": str(args.source_manifest), "files": len(freeze_sources(args.source_manifest))}))
        return
    if args.pi_root is None or args.output is None or args.version is None:
        parser.error("--pi-root, --output and --version required for build")
    build(args.inputs.resolve(), args.pi_root.resolve(), args.output.resolve(), args.source_manifest, copy_on_write=args.copy_on_write, version=args.version)

if __name__ == "__main__":
    main()
