"""Public source authority, Pi payload preparation and launcher boundaries."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/packaging" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_node_pin_and_locked_sdk():
    module = load("prepare_pi_runtime")
    pin = module.read_inputs(ROOT)
    assert pin["version"] == "v24.22.1"
    assert pin["archive_sha256"] == "89c682593d76cd44233fa53b1563264e7de9cbb189cad169135c5b4fadfbc0a7"
    lock = json.loads((ROOT / "runtime/pi/package-lock.json").read_text())
    assert lock["packages"]["node_modules/@earendil-works/pi-ai"]["version"] == "1.4.0"


def test_source_authority_includes_untracked_and_rejects_drift(tmp_path):
    from easel.packaging_sources import source_records, verify_source_records
    (tmp_path / "easel/platform").mkdir(parents=True)
    source = tmp_path / "easel/platform/new_central.py"
    source.write_text("# public fixture")
    records = source_records(tmp_path)
    assert "easel/platform/new_central.py" in records
    verify_source_records(tmp_path, records)
    source.write_text("# changed")
    with pytest.raises(ValueError, match="changed|differ|stale"):
        verify_source_records(tmp_path, records)


def test_source_excludes_user_state_and_rejects_links(tmp_path):
    from easel.packaging_sources import source_records
    for name in ["easel/.env", "easel/outputs/key.py", "easel/node_modules/a/index.js", "easel/__pycache__/x.py", "web/frontend/.env.local", "web/frontend/node_modules/x.js", "skills/private/secrets.json", "skills/private/api_keys.json", "easel/credentials.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private")
    assert source_records(tmp_path) == {}
    (tmp_path / "easel/link.py").symlink_to(tmp_path / "easel/.env")
    with pytest.raises(ValueError, match="link|regular"):
        source_records(tmp_path)


def test_frontend_receipt_tracks_new_react_sources(tmp_path):
    from easel.packaging_sources import frontend_records
    source = tmp_path / "web/frontend/src/NewPi.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export const Pi = 1")
    assert list(frontend_records(tmp_path)) == ["web/frontend/src/NewPi.tsx"]


def test_payload_symlinks_materialized_inside_only(tmp_path):
    module = load("prepare_pi_runtime")
    (tmp_path / "real").write_text("public")
    (tmp_path / "alias").symlink_to("real")
    module.materialize_links(tmp_path)
    assert not (tmp_path / "alias").is_symlink()
    assert (tmp_path / "alias").read_text() == "public"
    (tmp_path / "escape").symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="escape|outside"):
        module.materialize_links(tmp_path)


def test_launcher_selects_pi_only_for_v2_and_preserves_legacy(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "environ", os.environ.copy())
    from easel.portable import configure_runtime
    resource = tmp_path / "resource"
    workspace = tmp_path / "workspace"
    (resource / "runtime/pi").mkdir(parents=True)
    (resource / "runtime/pi/runtime-manifest.json").write_text("{}")
    monkeypatch.setenv("EASEL_RUNTIME_MANIFEST", "/host/codex")
    assert configure_runtime(resource, workspace) == "pi"
    assert os.environ["EASEL_PI_RUNTIME_MANIFEST"] == str(resource / "runtime/pi/runtime-manifest.json")
    assert "EASEL_RUNTIME_MANIFEST" not in os.environ
    (resource / "runtime/pi/runtime-manifest.json").unlink()
    assert configure_runtime(resource, workspace) == "codex"
    assert os.environ["EASEL_RUNTIME_MANIFEST"] == str(resource / "runtime-manifest.json")


def test_stale_frontend_receipt_rejected_before_copy(tmp_path):
    module = load("build_pi_macos")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("fixture")
    from easel.portable import digest
    (dist / "portable-assets.json").write_text(json.dumps({"schema_version": 1, "files": {"index.html": digest(dist / "index.html")}, "source": {"web/frontend/src/Pi.tsx": "old"}}))
    with pytest.raises(ValueError, match="stale"):
        module.legacy.copy_frontend(dist, tmp_path / "target", source_records={"web/frontend/src/Pi.tsx": "new"})
    assert not (tmp_path / "target").exists()


def test_source_rejects_new_unreviewed_file(tmp_path):
    from easel.packaging_sources import source_records, verify_source_records
    (tmp_path / "easel").mkdir()
    records = source_records(tmp_path)
    (tmp_path / "easel/unreviewed.py").write_text("# fixture")
    with pytest.raises(ValueError, match="stale"):
        verify_source_records(tmp_path, records)


def test_public_source_root_cannot_be_a_symlink(tmp_path):
    from easel.packaging_sources import source_records
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "public.py").write_text("# fixture")
    (tmp_path / "easel").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="link|regular"):
        source_records(tmp_path)


def test_portable_default_does_not_replace_existing_configuration(tmp_path):
    from easel.installation import initialize_runtime
    from easel.harnesses.store import read_json, write_json
    config = tmp_path / "outputs/_harness/config.json"
    assert initialize_runtime(tmp_path, "pi") == "pi"
    assert read_json(config)["runtime"] == "pi"
    write_json(config, {"runtime": "codex", "models": {"codex": "old-model"}})
    before = config.read_bytes()
    assert initialize_runtime(tmp_path, "pi") == "codex"
    assert config.read_bytes() == before


@pytest.mark.parametrize("legacy", ["environment", "state"])
def test_portable_pi_initialization_preserves_legacy_openclaw(tmp_path, monkeypatch, legacy):
    from easel.installation import initialize_runtime
    monkeypatch.delenv("EASEL_AGENT_RUNTIME", raising=False)
    monkeypatch.delenv("EASEL_CHAT_TRANSPORT", raising=False)
    state = tmp_path / "old-openclaw"
    monkeypatch.setenv("EASEL_OPENCLAW_STATE_DIR", str(state))
    if legacy == "environment":
        monkeypatch.setenv("EASEL_CHAT_TRANSPORT", "openclaw")
    else:
        state.mkdir()
        (state / "openclaw.json").write_text("{}")
    assert initialize_runtime(tmp_path, "pi") == "openclaw"


def test_bundled_launcher_ignores_poisoned_python_startup(tmp_path):
    import os
    import subprocess
    module = load("build_pi_macos")
    python = ROOT / "outputs/_run/portable-build/python-download/cpython-3.12.13-macos-aarch64-none"
    if not python.is_dir():
        pytest.skip("prepared public CPython payload not present")
    resource = tmp_path / "Easel.app/Contents/Resources"
    macos = resource.parent / "MacOS"
    macos.mkdir(parents=True)
    (resource / "easel").mkdir(parents=True)
    (resource / "easel/__init__.py").write_text("")
    (resource / "easel/portable.py").write_text("import sys,os,json,site\ndef main():\n print(json.dumps({'executable':sys.executable,'isolated':sys.flags.isolated,'user_site':site.ENABLE_USER_SITE,'pythonhome':os.environ.get('PYTHONHOME'),'userbase':os.environ.get('PYTHONUSERBASE')}));return 0\n")
    (resource / "python").symlink_to(python, target_is_directory=True)
    launcher = macos / "Easel"
    launcher.write_text(module.launcher_script())
    launcher.chmod(0o755)
    home = tmp_path / "home"
    malicious_site = tmp_path / "untrusted-userbase/lib/python3.12/site-packages"
    malicious_site.mkdir(parents=True)
    marker = tmp_path / "user-site-executed"
    (malicious_site / "sitecustomize.py").write_text(f"from pathlib import Path;Path({str(marker)!r}).write_text('UNTRUSTED')")
    (malicious_site / "poison.pth").write_text(f"import pathlib;pathlib.Path({str(marker)!r}).write_text('UNTRUSTED')\n")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONHOME": str(tmp_path / "poison-home"), "PYTHONUSERBASE": str(tmp_path / "untrusted-userbase"), "PYTHONPATH": str(malicious_site)}
    result = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
    log = home / "Library/Logs/Easel/launcher.log"
    assert result.returncode == 0, log.read_text() if log.exists() else result.stderr
    observed = json.loads(log.read_text().strip())
    assert observed['isolated'] == 1 and observed['user_site'] is False
    assert observed['pythonhome'] is None and observed['userbase'] is None
    assert str(resource / "python/bin") in observed['executable']
    assert not marker.exists()


def test_launcher_log_can_be_redirected_for_the_native_shell():
    module = load("build_pi_macos")
    assert '>>"${EASEL_LOG_FILE:-$LOG/launcher.log}" 2>&1' in module.launcher_script()


@pytest.mark.skipif(not Path("/usr/bin/swiftc").exists(), reason="needs the Xcode command line tools")
def test_native_shell_forwards_arguments_to_the_launcher(tmp_path):
    import subprocess
    module = load("build_pi_macos")
    macos = tmp_path / "Easel.app/Contents/MacOS"
    macos.mkdir(parents=True)
    module.compile_shell(macos / "Easel")
    seen = tmp_path / "seen"
    forward = macos / "easel-launcher"
    forward.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {seen}\nexit 7\n')
    forward.chmod(0o755)
    result = subprocess.run([str(macos / "Easel"), "--workspace", "/a b", "--stop"], timeout=30)
    assert result.returncode == 7
    assert seen.read_text().splitlines() == ["--workspace", "/a b", "--stop"]


def test_desktop_image_decoder_is_pinned_and_licensed():
    lock=(ROOT/'packaging/macos/core-requirements.txt').read_text()
    sha='ffd0c5368496f41b0944be820fcb7a838aa6e623d250b01acf2643939c3f99d7'
    assert 'pillow==12.3.0 --hash=sha256:'+sha in lock.lower()
    rows=json.loads((ROOT/'packaging/macos/wheels-provenance.json').read_text())
    decoder=[row for row in rows if row['name'].lower()=='pillow']
    assert len(decoder)==1 and decoder[0]['sha256']==sha
    assert decoder[0]['filename']=='pillow-12.3.0-cp312-cp312-macosx_11_0_arm64.whl'
    assert decoder[0]['verified_against_pypi_metadata'] is True
    assert (ROOT/'packaging/macos/licenses/Pillow-12.3.0/LICENSE').is_file()
