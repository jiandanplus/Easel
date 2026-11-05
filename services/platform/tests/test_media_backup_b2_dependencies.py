"""Dependency gate faults; target-wheel execution is recorded separately by T0."""
from dataclasses import replace
import hashlib
import importlib.util
from pathlib import Path
import sys
import zipfile

import nacl.bindings
import pytest


SOURCE = Path(__file__).parents[1] / "src/platform_service/media_backup_b2/dependencies.py"
LICENSES = Path(__file__).parents[1] / "licenses"


def gate():
    if not SOURCE.is_file():
        pytest.fail("B2 dependency verification implementation is missing", pytrace=False)
    name = "b2_dependencies_test_" + str(id(object()))
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def wheel(module, tmp_path, monkeypatch, *, version="1.6.2", tag=None,
          omitted=None, corrupt_license=False, native=b"fixture-native"):
    pin = module._PINS["macos-universal2"]
    entries = {
        "pynacl-1.6.2.dist-info/METADATA": f"Name: PyNaCl\nVersion: {version}\n".encode(),
        "pynacl-1.6.2.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {tag or pin.tag}\n".encode(),
        "pynacl-1.6.2.dist-info/licenses/LICENSE":
            (LICENSES / "PyNaCl-1.6.2-LICENSE.txt").read_bytes(),
        "pynacl-1.6.2.dist-info/licenses/licenses/LICENSE.libsodium.txt":
            (LICENSES / "libsodium-LICENSE.txt").read_bytes(),
        "nacl/_sodium.abi3.so": native,
    }
    if omitted:
        entries.pop(omitted)
    if corrupt_license:
        entries["pynacl-1.6.2.dist-info/licenses/LICENSE"] += b"tampered"
    path = tmp_path / pin.filename
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    # Synthetic wheel bytes isolate metadata/license checks from the outer SHA
    # check. Exact official wheel/native hashes are never replaced at runtime.
    pins = dict(module._PINS)
    pins["macos-universal2"] = replace(
        pin, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        native_sha256=hashlib.sha256(b"fixture-native").hexdigest(),
    )
    monkeypatch.setattr(module, "_PINS", pins)
    return path


def test_rejects_wrong_wheel_sha(tmp_path, monkeypatch):
    module = gate()
    path = wheel(module, tmp_path, monkeypatch)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(module.DependencyError, match="^invalid_dependency$"):
        module.verify_wheel(path, target="macos-universal2")


@pytest.mark.parametrize("change", ["version", "tag", "license", "missing_license", "native"])
def test_rejects_wrong_metadata_license_or_native_after_outer_hash(tmp_path, monkeypatch, change):
    module = gate()
    changes = {
        "version": {"version": "1.6.1"},
        "tag": {"tag": "cp38-abi3-manylinux_2_34_aarch64"},
        "license": {"corrupt_license": True},
        "missing_license": {"omitted": "pynacl-1.6.2.dist-info/licenses/licenses/LICENSE.libsodium.txt"},
        "native": {"native": b"wrong-architecture-native"},
    }
    path = wheel(module, tmp_path, monkeypatch, **changes[change])
    with pytest.raises(module.DependencyError, match="^invalid_dependency$"):
        module.verify_wheel(path, target="macos-universal2")


def test_rejects_other_target_and_unknown_target(tmp_path, monkeypatch):
    module = gate()
    path = wheel(module, tmp_path, monkeypatch)
    for target in ("linux-x86_64", "unreviewed-platform"):
        with pytest.raises(module.DependencyError):
            module.verify_wheel(path, target=target)


def test_validated_wheel_receipt_contains_only_exact_public_pins(tmp_path, monkeypatch):
    module = gate()
    path = wheel(module, tmp_path, monkeypatch)
    proof = module.verify_wheel(path, target="macos-universal2")
    assert proof["verified"] is True
    assert proof["version"] == "1.6.2"
    assert proof["tag"] == "cp38-abi3-macosx_10_10_universal2"
    assert proof["native_sha256"] == hashlib.sha256(b"fixture-native").hexdigest()
    assert set(proof["license_sha256"]) == {"PyNaCl", "libsodium"}


def test_runtime_rejects_wrong_package_version(monkeypatch):
    module = gate()
    original = module.metadata.version
    monkeypatch.setattr(module.metadata, "version", lambda name: "1.6.1" if name == "PyNaCl" else original(name))
    with pytest.raises(module.DependencyError):
        module.probe_runtime()


@pytest.mark.parametrize("suffix", ["state", "keygen", "init_push", "init_pull", "push", "pull", "rekey"])
def test_runtime_rejects_missing_required_binding(monkeypatch, suffix):
    module = gate()
    monkeypatch.delattr(nacl.bindings, "crypto_secretstream_xchacha20poly1305_" + suffix)
    with pytest.raises(module.DependencyError):
        module.probe_runtime()


@pytest.mark.parametrize("suffix,bad", [("KEYBYTES", 31), ("HEADERBYTES", 23), ("ABYTES", 16), ("TAG_FINAL", 0), ("TAG_FINAL", True)])
def test_runtime_rejects_wrong_constants(monkeypatch, suffix, bad):
    module = gate()
    monkeypatch.setattr(nacl.bindings, "crypto_secretstream_xchacha20poly1305_" + suffix, bad)
    with pytest.raises(module.DependencyError):
        module.probe_runtime()


def test_actual_loaded_native_bindings_roundtrip_final_and_patch_without_claiming_version_api():
    proof = gate().probe_runtime()
    assert proof["api_ready"] is True
    assert proof["versions"] == {"PyNaCl": "1.6.2", "cffi": "2.1.1", "cryptography": "50.0.2"}
    assert proof["constants"]["KEYBYTES"] == 32
    assert proof["constants"]["HEADERBYTES"] == 24
    assert proof["constants"]["ABYTES"] == 17
    assert proof["aad_rejected"] and proof["final_roundtrip"] and proof["patched_point_rejected"]
    assert proof["native_version_api_available"] is (proof["native_version"] is not None)
    assert "ready" not in proof  # T0 provenance/ABI acceptance is a separate gate.
