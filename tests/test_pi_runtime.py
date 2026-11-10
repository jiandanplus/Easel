"""Noncommercial fixtures for embedded runtime integrity."""
import hashlib
import json
from pathlib import Path
import pytest
from easel.harnesses.base import HarnessError


def runtime(tmp_path, monkeypatch):
    from easel.harnesses import pi_runtime
    root = tmp_path / "pi"
    (root / "node/bin").mkdir(parents=True)
    (root / "node_modules/pkg").mkdir(parents=True)
    (root / "node/bin/node").write_bytes(b"fixture-node")
    (root / "node/bin/node").chmod(0o755)
    (root / "worker.mjs").write_text("// fixture")
    (root / "node_modules/pkg/index.js").write_text("// dependency")
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file()}
    data = {"schema_version": 2, "platform": "darwin", "architecture": "arm64",
            "node": {"version": "22.21.1", "executable": "node/bin/node", "sha256": files["node/bin/node"]},
            "pi": {"version": "1.4.0", "worker": "worker.mjs", "sha256": files["worker.mjs"]}, "files": files}
    manifest = root / "runtime-manifest.json"
    manifest.write_text(json.dumps(data))
    monkeypatch.setenv("EASEL_PI_RUNTIME_MANIFEST", str(manifest))
    monkeypatch.setattr(pi_runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pi_runtime.platform, "machine", lambda: "arm64")
    return root, data, manifest


def test_runtime_missing_never_uses_path(tmp_path, monkeypatch):
    from easel.harnesses.pi_runtime import resolve_pi
    monkeypatch.setenv("EASEL_PI_RUNTIME_MANIFEST", str(tmp_path / "absent"))
    with pytest.raises(HarnessError): resolve_pi()


def test_runtime_verifies_full_payload(tmp_path, monkeypatch):
    from easel.harnesses.pi_runtime import resolve_pi, pi_command
    root, _, _ = runtime(tmp_path, monkeypatch)
    result = resolve_pi()
    assert result.node == root / "node/bin/node"
    assert pi_command() == [str(result.node), str(root / "worker.mjs")]
    (root / "node_modules/pkg/index.js").write_text("tampered")
    with pytest.raises(HarnessError): resolve_pi()


@pytest.mark.parametrize("mutation", ["platform", "extra", "symlink", "old-node", "version", "schema"])
def test_runtime_rejects_invalid_inventory(tmp_path, monkeypatch, mutation):
    from easel.harnesses.pi_runtime import resolve_pi
    root, data, manifest = runtime(tmp_path, monkeypatch)
    if mutation == "platform": data["architecture"] = "x64"
    elif mutation == "extra": (root / "undeclared.js").write_text("extra")
    elif mutation == "symlink": (root / "link").symlink_to(root / "worker.mjs")
    elif mutation == "old-node": data["node"]["version"] = "20.0.0"
    elif mutation == "version": data["pi"]["version"] = "latest"
    else: data["schema_version"] = True
    manifest.write_text(json.dumps(data))
    with pytest.raises(HarnessError): resolve_pi()


def test_default_manifest_resource_root(tmp_path, monkeypatch):
    from easel.harnesses import pi_runtime
    root, _, manifest = runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("EASEL_PI_RUNTIME_MANIFEST")
    monkeypatch.setattr(pi_runtime, "resource_root", lambda: tmp_path.parent)
    # No runtime at the resource fallback; developer PATH must not rescue it.
    with pytest.raises(HarnessError): pi_runtime.resolve_pi()



def test_default_manifest_resolves_owned_resource_payload(tmp_path, monkeypatch):
    from easel.harnesses import pi_runtime
    root, _, _ = runtime(tmp_path / "runtime", monkeypatch)
    monkeypatch.delenv("EASEL_PI_RUNTIME_MANIFEST")
    monkeypatch.setattr(pi_runtime, "resource_root", lambda: tmp_path)
    assert pi_runtime.resolve_pi().worker == root / "worker.mjs"
