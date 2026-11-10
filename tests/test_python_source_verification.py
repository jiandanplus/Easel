"""Reject unverifiable offline Python inputs before creating a delivery stage."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
NAME = "cpython-3.12.13+20260510-aarch64-apple-darwin-install_only_stripped.tar.gz"
URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20260510/" + NAME.replace("+", "%2B")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("python_source_builder", ROOT / "scripts/packaging/build_pi_macos.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    repo = tmp_path / "repo"
    authority = repo / "packaging/macos"
    authority.mkdir(parents=True)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    python = inputs / "python-download/cpython-3.12.13-macos-aarch64-none"
    (python / "bin").mkdir(parents=True)
    (python / "bin/python3.12").write_bytes(b"synthetic pinned interpreter")
    (python / "bin/python3").symlink_to("python3.12")
    (python / "BUILD").write_text("20260510")
    (inputs / "source-archives").mkdir()
    archive = inputs / "source-archives" / NAME
    (inputs / "wheels").mkdir()
    wheel = inputs / "wheels/example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic pinned wheel")
    state = {"builder": builder, "authority": authority, "inputs": inputs, "python": python, "archive": archive}
    monkeypatch.setattr(builder, "ROOT", repo)

    def write_metadata(name, value):
        data = json.dumps(value, sort_keys=True, indent=2) + "\n"
        (authority / name).write_text(data)
        (inputs / name).write_text(data)

    def inventory():
        files = {}
        for path in python.rglob("*"):
            if path.is_symlink():
                files[path.relative_to(python).as_posix()] = {"symlink": str(path.readlink())}
            elif path.is_file():
                files[path.relative_to(python).as_posix()] = {"sha256": sha(path)}
        write_metadata("python-runtime-files.json", {"python_version": "3.12.13", "build": "20260510", "uv_version": "0.11.16", "files": files})

    def make_archive(extra=None):
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo("python/bin/python3.12")
            payload = b"synthetic upstream interpreter"
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
            if extra is not None:
                tar.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
        write_metadata("python-assets.json", [{"name": NAME, "url": URL, "digest": "sha256:" + sha(archive)}])

    def provenance():
        write_metadata("python-source-provenance.json", {
            "schema_version": 1,
            "python": {"version": "3.12.13", "build": "20260510", "platform": "darwin", "architecture": "arm64"},
            "archive": {"name": NAME, "url": URL, "bytes": archive.stat().st_size, "sha256": sha(archive)},
            "runtime_inventory_sha256": sha(authority / "python-runtime-files.json"),
            "derivation": {"tool": "uv", "version": "0.11.16", "executable_sha256": "1" * 64,
                           "method": "offline-local-archive", "options": ["--offline", "--no-config", "--no-bin", "--no-registry"], "home_unchanged": True},
            # Prepared booleans must never replace actual archive hashing.
            "archive_verified": False,
        })

    for name in ["core-requirements.txt", "codex-vendor-provenance.json", "build-preparation-notes.json"]:
        (authority / name).write_text("{}\n")
        (inputs / name).write_text("{}\n")
    write_metadata("wheels-provenance.json", [{"filename": wheel.name, "sha256": sha(wheel)}])
    inventory()
    make_archive()
    provenance()
    state.update(write_metadata=write_metadata, inventory=inventory, make_archive=make_archive, provenance=provenance)
    return state


def test_valid_source_preserves_path_api(source_fixture):
    f = source_fixture
    assert f["builder"].verify_python(f["inputs"]) == f["python"]


def test_observed_source_proof_does_not_trust_prepared_boolean(source_fixture):
    f = source_fixture
    python, proof = f["builder"].verify_python(f["inputs"], with_provenance=True)
    assert python == f["python"]
    assert proof["archive_verified"] is True
    assert proof["archive_sha256"] == sha(f["archive"])
    assert proof["runtime_inventory_sha256"] == sha(f["authority"] / "python-runtime-files.json")


@pytest.mark.parametrize("mutation", ["missing", "same_size_tamper", "wrong_size"])
def test_rejects_missing_or_tampered_archive_despite_coherent_runtime(source_fixture, mutation):
    f = source_fixture
    if mutation == "missing":
        f["archive"].unlink()
    elif mutation == "wrong_size":
        f["archive"].write_bytes(f["archive"].read_bytes() + b"x")
    else:
        data = bytearray(f["archive"].read_bytes())
        data[-1] ^= 1
        f["archive"].write_bytes(data)
    with pytest.raises(ValueError, match="archive|Archive"):
        f["builder"].verify_python(f["inputs"])


def test_rejects_prepared_provenance_different_from_authority(source_fixture):
    f = source_fixture
    path = f["inputs"] / "python-source-provenance.json"
    data = json.loads(path.read_text())
    data["archive"]["sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="metadata|authority"):
        f["builder"].verify_python(f["inputs"])


@pytest.mark.parametrize("mutation", ["inventory_digest", "python_version", "uv_version", "wrong_official_url"])
def test_rejects_inconsistent_or_wrong_version_authority(source_fixture, mutation):
    f = source_fixture
    data = json.loads((f["authority"] / "python-source-provenance.json").read_text())
    if mutation == "inventory_digest":
        data["runtime_inventory_sha256"] = "0" * 64
    elif mutation == "python_version":
        data["python"]["version"] = "3.13.0"
    elif mutation == "uv_version":
        data["derivation"]["version"] = "0.12.0"
    else:
        data["archive"]["url"] = "https://example.test/" + NAME
    f["write_metadata"]("python-source-provenance.json", data)
    with pytest.raises(ValueError, match="provenance|inventory|version|archive|Python|derivation"):
        f["builder"].verify_python(f["inputs"])


def test_rejects_wrong_runtime_inventory_version_even_when_receipt_hash_matches(source_fixture):
    f = source_fixture
    data = json.loads((f["authority"] / "python-runtime-files.json").read_text())
    data["python_version"] = "3.13.0"
    f["write_metadata"]("python-runtime-files.json", data)
    f["provenance"]()
    with pytest.raises(ValueError, match="version|inventory|Python"):
        f["builder"].verify_python(f["inputs"])


@pytest.mark.parametrize("kind", ["absolute_internal", "directory_alias"])
def test_rejects_unsafe_runtime_links_even_if_inventory_lists_them(source_fixture, kind):
    f = source_fixture
    target = f["python"] / "bin/python3.12" if kind == "absolute_internal" else f["python"] / "bin"
    (f["python"] / "alias").symlink_to(target if kind == "absolute_internal" else "bin", target_is_directory=kind == "directory_alias")
    f["inventory"]()
    f["provenance"]()
    with pytest.raises(ValueError, match="link|Link"):
        f["builder"].verify_python(f["inputs"])


def test_rejects_archive_file_symlink(source_fixture):
    f = source_fixture
    saved = f["inputs"] / "saved.tar.gz"
    f["archive"].rename(saved)
    f["archive"].symlink_to(saved)
    with pytest.raises(ValueError, match="regular|link|archive"):
        f["builder"].verify_python(f["inputs"])


@pytest.mark.parametrize("name", ["python-source-provenance.json", "python-runtime-files.json"])
def test_rejects_authority_file_links_before_reading(source_fixture, name):
    f = source_fixture
    path = f["authority"] / name
    saved = f["authority"].parent / ("saved-" + name)
    path.rename(saved)
    path.symlink_to(saved)
    with pytest.raises(ValueError, match="regular|link"):
        f["builder"].verify_python(f["inputs"])


def test_rejects_wheel_directory_alias(source_fixture):
    f = source_fixture
    wheels = f["inputs"] / "wheels"
    moved = f["inputs"].parent / "outside-wheels"
    wheels.rename(moved)
    wheels.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="directory|link"):
        f["builder"].verify_python(f["inputs"])


@pytest.mark.parametrize("kind", ["traversal", "symlink_escape", "hardlink_escape"])
def test_rejects_unsafe_tar_even_when_its_hash_is_pinned(source_fixture, kind):
    f = source_fixture
    member = tarfile.TarInfo("python/../escape" if kind == "traversal" else "python/escape")
    if kind == "traversal":
        member.size = 1
    else:
        member.type = tarfile.SYMTYPE if kind == "symlink_escape" else tarfile.LNKTYPE
        member.linkname = "../../outside"
    f["make_archive"](member)
    f["provenance"]()
    with pytest.raises(ValueError, match="archive|tar|link|member"):
        f["builder"].verify_python(f["inputs"])
