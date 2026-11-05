"""Actual explicitly supplied wheel; never select a release or install globally."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
B1 = ("__init__.py", "closure.py", "contract.py", "files.py", "ustar.py")


@pytest.fixture(scope="module")
def wheel_path():
    selected = os.environ.get("EASEL_B2_WHEEL_TEST_ARTIFACT")
    if selected is None:
        pytest.skip("requires an explicitly built B2 local wheel artifact")
    path = Path(selected)
    assert path.is_absolute() and path.is_file() and path.suffix == ".whl"
    return path


def test_actual_wheel_carries_exact_b1_sql_legacy_and_licenses(wheel_path):
    with zipfile.ZipFile(wheel_path) as package:
        names = package.namelist()
        assert len(names) == len(set(names))
        assert not any(name.startswith("/") or ".." in name.split("/")
                       or "__pycache__" in name.split("/") for name in names)
        assert not any(part.startswith(".env") or part in (".git", ".aws", ".ssh", ".pgpass")
                       or part.endswith((".pem", ".key", ".p12", ".dump"))
                       for name in names for part in name.split("/"))
        assert not any(name.endswith(".dist-info/entry_points.txt") for name in names)
        for path in (ROOT / "frontend/dist").rglob("*"):
            if path.is_file():
                target = "platform_service/console_static/" + path.relative_to(ROOT / "frontend/dist").as_posix()
                assert package.read(target) == path.read_bytes()
        for name in B1:
            assert package.read("media_backup/" + name) == (ROOT / "scripts/media_backup" / name).read_bytes()
        paths = ["media_maintenance/sql/001_media_backup_control.sql",
                 "media_backup_b2/legacy_sources/sealed_transfer.py",
                 "media_backup_b2/legacy_sources/encrypted_backup.py"]
        for path in paths:
            assert package.read("platform_service/" + path) == (ROOT / "src/platform_service" / path).read_bytes()
        for name in ("PyNaCl-1.6.2-LICENSE.txt", "libsodium-LICENSE.txt",
                     "PyNaCl-1.6.2-provenance.json", "Pillow-12.3.0-LICENSE.txt"):
            assert package.read("platform_service/licenses/" + name) == (ROOT / "licenses" / name).read_bytes()
        provenance = json.loads(package.read("platform_service/licenses/PyNaCl-1.6.2-provenance.json"))
        assert provenance["native_version_evidence"] == "source-attributed"
        assert provenance["direct_native_version_query"] is False


def test_actual_wheel_preserves_all_public_migration_bytes_and_exact_dependency(wheel_path):
    with zipfile.ZipFile(wheel_path) as package:
        migrations = sorted((ROOT / "src/platform_service/migrations").glob("*.sql"))
        assert len(migrations) == 20
        for path in migrations:
            assert package.read("platform_service/migrations/" + path.name) == path.read_bytes()
        metadata_name, = [name for name in package.namelist() if name.endswith(".dist-info/METADATA")]
        metadata = package.read(metadata_name).decode()
        assert "requires-dist: pynacl==1.6.2" in metadata.lower()


def test_install_actual_wheel_outside_checkout_has_no_b1_sql_or_legacy_shadow(wheel_path, tmp_path):
    target = tmp_path / "installed"
    target.mkdir(mode=0o700)
    run_dir = tmp_path / "outside-checkout"
    run_dir.mkdir(mode=0o700)
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith(("EASEL_", "PG")) and key not in ("PYTHONPATH", "PYTHONHOME")}
    installed = subprocess.run(["uv", "pip", "install", "--offline", "--no-deps", "--target", str(target), str(wheel_path)],
        cwd=run_dir, env=clean, timeout=60, capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr[-2000:]
    # Existing test interpreter supplies only its reviewed native dependencies;
    # all application and B1 code must resolve inside the installed wheel prefix.
    (run_dir / "synthetic_fixture.py").write_bytes((ROOT / "tests/media_backup_fixture.py").read_bytes())
    code = """import hashlib,importlib.resources,io,json,pathlib,sys
import media_backup,media_backup.closure,importlib,pkgutil
import platform_service.media_backup_b2 as b2
import platform_service.media_maintenance as maintenance
from platform_service.media_backup_b2 import wire,stream,legacy,dependencies
from platform_service.media_backup_b2.contracts import ExpectedV2
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from media_backup.contract import encode_manifest,physical_size,ClosureError
from media_backup.closure import write_archive,restore_archive
from synthetic_fixture import capture
root=pathlib.Path(sys.argv[1]).resolve()
for module in (media_backup,media_backup.closure,wire,stream,legacy,dependencies):
 assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
for package in (b2,maintenance):
 for info in pkgutil.walk_packages(package.__path__,package.__name__+'.'):
  module=importlib.import_module(info.name)
  assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
sql=importlib.resources.files('platform_service').joinpath('media_maintenance/sql/001_media_backup_control.sql').read_bytes()
assert hashlib.sha256(sql).hexdigest()==sys.argv[2]
assert wire.MAGIC==b'EASELBS\\x02'
key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
der=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
with capture(pathlib.Path.cwd()/'synthetic-capture',[(b'synthetic attached blob','attached',True)]) as source:
 plan=encode_manifest(source.plan)
 expected=ExpectedV2('12345678-1234-4234-9234-123456789abc','central-archive','synthetic',hashlib.sha256(der).hexdigest(),physical_size(source.plan),hashlib.sha256(plan).hexdigest(),source.plan['registry_sha256'])
 sink=io.BytesIO()
 encoder=stream.V2Encoder(public,expected,sink)
 b1=write_archive(source.plan,source.media_fd,source.fds,encoder,owner=source.owner)
 final=encoder.finish(b1)
 decoder=stream.V2Decoder(private,io.BytesIO(sink.getvalue()),expected)
 restored=restore_archive(decoder,source.parent_fd,'verified',source.plan)
 assert restored['status']=='private_stage_restored_verified' and decoder.verified and decoder.receipt==final
 failed=stream.V2Decoder(private,io.BytesIO(sink.getvalue()[:-1]),expected)
 try: restore_archive(failed,source.parent_fd,'corrupt',source.plan)
 except ClosureError: pass
 else: raise AssertionError('truncated cipher accepted')
 assert not failed.verified
assert dependencies.probe_runtime()['api_ready']
for unsupported in ('platform_service.media.async_store','platform_service.media.audio_execution','platform_service.media.voice_execution','platform_service.media.adapters.incho_music'):
 assert unsupported not in sys.modules
print(json.dumps({'installed_imports':True,'sql_exact':True,'outside_checkout':True}))
"""
    clean["PYTHONPATH"] = str(target)
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    expected_sql = hashlib.sha256((ROOT / "src/platform_service/media_maintenance/sql/001_media_backup_control.sql").read_bytes()).hexdigest()
    interpreter = os.environ.get("EASEL_B2_WHEEL_TEST_PYTHON", sys.executable)
    completed = subprocess.run([interpreter, "-B", "-c", code, str(target), expected_sql],
        cwd=run_dir, env=clean, timeout=60, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert json.loads(completed.stdout) == {"installed_imports": True, "sql_exact": True, "outside_checkout": True}
