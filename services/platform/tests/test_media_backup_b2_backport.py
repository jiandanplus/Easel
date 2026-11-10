"""Explicit exact retained public-release fixture; no generic migrator or DB."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf"

@pytest.fixture
def builder():
    path = ROOT / "scripts/build_media_backup_b2_profile.py"
    assert path.is_file(), "production-profile builder missing"
    spec = importlib.util.spec_from_file_location("b2_profile_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture
def artifacts():
    base = os.environ.get("EASEL_B2_BACKPORT_BASE_ARTIFACT")
    candidate = os.environ.get("EASEL_B2_WHEEL_TEST_ARTIFACT")
    if base is None or candidate is None:
        pytest.skip("requires explicitly selected exact release and interim B2 wheel")
    return Path(base), Path(candidate)

def test_exact_base_profile_keeps_all_product_bytes_and_only_001_016(builder, artifacts, tmp_path):
    base, candidate = artifacts
    output = tmp_path / candidate.name
    receipt = builder.build_profile(base, candidate, output)
    assert receipt["base_sha256"] == BASE_SHA
    assert receipt["runtime_guard_status"] == "pending_reviewed_frozen_release_delta"
    assert receipt["production_compatibility_proven"] is False
    assert hashlib.sha256(output.read_bytes()).hexdigest() == receipt["wheel_sha256"]
    with zipfile.ZipFile(base) as old, zipfile.ZipFile(output) as new:
        for name in old.namelist():
            if name.endswith((".dist-info/RECORD", ".dist-info/METADATA")):
                continue
            assert new.read(name) == old.read(name), name
        migrations = [n for n in new.namelist() if n.startswith("platform_service/migrations/")]
        assert len(migrations) == 16 and all(int(Path(n).name[:3]) <= 16 for n in migrations)
        assert not any(any(token in name for token in ("async_store", "audio_execution", "voice_execution", "incho")) for name in new.namelist())
        assert new.read("media_backup/closure.py") == (ROOT / "scripts/media_backup/closure.py").read_bytes()
        profile = json.loads(new.read("platform_service/media_backup_b2/package_profile.json"))
        assert profile["base_sha256"] == BASE_SHA
        assert profile["guard_deltas"] == {}
        assert "Requires-Dist: PyNaCl==1.6.2" in new.read("easel_central_platform-0.1.0.dist-info/METADATA").decode()

def test_unreviewed_base_and_existing_output_refused(builder, artifacts, tmp_path):
    base, candidate = artifacts
    bad = tmp_path / "bad.whl"
    bad.write_bytes(base.read_bytes() + b"unreviewed")
    with pytest.raises(builder.ProfileError, match="^invalid_package_profile$"):
        builder.build_profile(bad, candidate, tmp_path / candidate.name)
    out = tmp_path / candidate.name
    out.write_bytes(b"preserve existing")
    with pytest.raises(builder.ProfileError):
        builder.build_profile(base, candidate, out)
    assert out.read_bytes() == b"preserve existing"

def test_guard_delta_requires_exact_base_and_result_hash_and_allowlist(builder, artifacts, tmp_path):
    base, candidate = artifacts
    with zipfile.ZipFile(base) as package:
        original = package.read("platform_service/media_runtime.py")
    changed = original + b"\n# reviewed synthetic guard fixture only\n"
    delta = {"platform_service/media_runtime.py": {"before_sha256": hashlib.sha256(original).hexdigest(), "after_sha256": hashlib.sha256(changed).hexdigest(), "source": changed}}
    receipt = builder.build_profile(base, candidate, tmp_path / candidate.name, guard_deltas=delta)
    assert receipt["runtime_guard_status"] == "delta_supplied_runtime_pg_acceptance_pending"
    assert receipt["production_compatibility_proven"] is False
    for wrong in ({"platform_service/media_runtime.py": {**delta["platform_service/media_runtime.py"], "before_sha256": "0"*64}}, {"platform_service/migrations/017_voice_stages.sql": delta["platform_service/media_runtime.py"]}):
        with pytest.raises(builder.ProfileError):
            builder.build_profile(base, candidate, tmp_path / (str(len(wrong))+str(hash(str(wrong)))+".whl"), guard_deltas=wrong)

@pytest.mark.parametrize("member", ["media_backup/contract.py", "platform_service/media_backup_b2/legacy_sources/sealed_transfer.py", "platform_service/licenses/libsodium-LICENSE.txt"])
def test_frozen_resource_tamper_refused(builder, artifacts, tmp_path, member):
    base, candidate = artifacts
    poisoned = tmp_path / "poisoned.whl"
    with zipfile.ZipFile(candidate) as original, zipfile.ZipFile(poisoned, "w") as edited:
        for info in original.infolist():
            raw = original.read(info.filename)
            edited.writestr(info, raw + b"tampered" if info.filename == member else raw)
    with pytest.raises(builder.ProfileError):
        builder.build_profile(base, poisoned, tmp_path / candidate.name)

def test_prepared_profile_installed_imports_and_synthetic_roundtrip_only(builder, artifacts, tmp_path):
    base, candidate = artifacts
    output = tmp_path / candidate.name
    builder.build_profile(base, candidate, output)
    test_path = ROOT / "tests/test_media_backup_b2_packaging.py"
    spec = importlib.util.spec_from_file_location("b2_installed_wheel_probe", test_path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    # Reuse the actual installed-prefix probe, never import application from repo.
    probe.test_install_actual_wheel_outside_checkout_has_no_b1_sql_or_legacy_shadow(output, tmp_path)
