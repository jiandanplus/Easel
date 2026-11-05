"""Import-only local wheel profile preparation, never an installer or migrator.

The exact retained public release is immutable input. A prepared overlay is
explicitly incomplete until frozen-release guards and owned PG/runtime tests
are independently accepted. No entry point, application config or network IO.
"""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

BASE_SHA256 = "f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf"
DIST = "easel_central_platform-0.1.0.dist-info/"
B1 = {"media_backup/" + name for name in ("__init__.py", "closure.py", "contract.py", "files.py", "ustar.py")}
LICENSES = {"platform_service/licenses/" + name for name in ("Pillow-12.3.0-LICENSE.txt", "PyNaCl-1.6.2-LICENSE.txt", "libsodium-LICENSE.txt", "PyNaCl-1.6.2-provenance.json")}
GUARD_PATHS = {"platform_service/" + name for name in ("app.py", "execution_runtime.py", "media_runtime.py", "media_web.py", "permissions.py", "credits.py", "media/core.py", "media/storage.py", "media/artifacts.py", "media/rates.py")}

FROZEN_RESOURCE_SHA256 = {'media_backup/files.py': 'c35298ec15528d50ed96c29bb9a0a43776b06684fb73d65238f7363a9c7aef20', 'media_backup/__init__.py': '8cd13cf4a8259fba68ee6f648d8b78c61029936c742a2ee9b79503bb612806e2', 'media_backup/ustar.py': '96bea9aeb5c98ca7d439fba19941dc05d112834d200063628e2800258a548f6c', 'media_backup/contract.py': '98670e130bbc76ac602f6d638234aa9b210d9c65964fb7748908a735e90e5df2', 'media_backup/closure.py': '7c71ab156330efa86c42e038ac7c5127bbcdc57d0efe85059ec59ca1d6c2682e', 'platform_service/media_backup_b2/legacy_sources/sealed_transfer.py': '7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6', 'platform_service/media_backup_b2/legacy_sources/encrypted_backup.py': 'd330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1', 'platform_service/licenses/PyNaCl-1.6.2-LICENSE.txt': 'd3174ad63e721d4c9dccb8ad4320848992d314369bc46319720b5802c9153fe9', 'platform_service/licenses/libsodium-LICENSE.txt': '508a76d186356c0dd807a670ef510964f8724557024796a2c426c6c0e19ab683'}

class ProfileError(ValueError):
    def __init__(self):
        super().__init__("invalid_package_profile")

def _require(value):
    if not value:
        raise ProfileError()

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

def _load(path):
    path = Path(path)
    _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 64*1024**2)
    raw = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        _require(len(names) == len(set(names)))
        _require(sum(info.file_size for info in archive.infolist()) <= 128*1024**2)
        for name in names:
            _require(not name.startswith("/") and all(part not in ("", ".", "..", "__pycache__") for part in name.split("/")))
        return raw, {name: archive.read(name) for name in names}

def _allowed_addition(name):
    if name in B1 or name in LICENSES:
        return True
    for prefix in ("platform_service/media_backup_b2/", "platform_service/media_maintenance/"):
        if name.startswith(prefix):
            relative = name[len(prefix):]
            return relative.endswith((".py", ".sql")) and not relative.startswith(".")
    return False

def _record(members):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, raw in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(raw)))
    writer.writerow((DIST + "RECORD", "", ""))
    return output.getvalue().encode()

def build_profile(release_wheel, candidate_wheel, output_wheel, *, guard_deltas=None):
    """Write a new local-only wheel from exact release plus closed B2 resources.

    Guard deltas are explicitly reviewed bytes with before/after SHA bindings.
    Their presence is recorded but does not prove semantic/runtime acceptance.
    Existing output is refused with exclusive creation, preserving original input.
    """
    try:
        base_raw, base = _load(release_wheel)
        _require(_sha(base_raw) == BASE_SHA256)
        _, candidate = _load(candidate_wheel)
        _require(set(n for n in base if n.startswith("platform_service/migrations/")) == set(n for n in candidate if n.startswith("platform_service/migrations/") and int(Path(n).name[:3]) <= 16))
        for name in base:
            if name.startswith("platform_service/migrations/"):
                _require(base[name] == candidate[name])
        additions = {name: raw for name, raw in candidate.items() if _allowed_addition(name)}
        _require(B1 <= additions.keys() and LICENSES <= additions.keys())
        _require("platform_service/media_maintenance/sql/001_media_backup_control.sql" in additions)
        for name in ("sealed_transfer.py", "encrypted_backup.py"):
            _require("platform_service/media_backup_b2/legacy_sources/" + name in additions)
        for name, expected in FROZEN_RESOURCE_SHA256.items():
            _require(_sha(additions[name]) == expected)
        result = dict(base)
        for name, raw in additions.items():
            if name in base:
                _require(raw == base[name])
            result[name] = raw
        changes = {}
        for name, delta in (guard_deltas or {}).items():
            _require(name in GUARD_PATHS and name in base)
            _require(set(delta) == {"source", "before_sha256", "after_sha256"})
            _require(type(delta["source"]) is bytes)
            _require(_sha(base[name]) == delta["before_sha256"] and _sha(delta["source"]) == delta["after_sha256"])
            _require(delta["source"] != base[name])
            result[name] = delta["source"]
            changes[name] = {k:delta[k] for k in ("before_sha256", "after_sha256")}
        metadata = result[DIST + "METADATA"].decode()
        _require(not any(line.lower().startswith("requires-dist: pynacl") for line in metadata.splitlines()))
        header, sep, body = metadata.partition("\n\n")
        result[DIST + "METADATA"] = (header + "\nRequires-Dist: PyNaCl==1.6.2" + sep + body).encode()
        status = "delta_supplied_runtime_pg_acceptance_pending" if changes else "pending_reviewed_frozen_release_delta"
        profile = dict(schema_version=1, base_sha256=BASE_SHA256, public_migration_profile="001-016", guard_deltas=changes, runtime_guard_status=status, production_compatibility_proven=False,
            overlay_sha256={name:_sha(raw) for name,raw in sorted(additions.items())}, original_product_sha256={name:_sha(raw) for name,raw in sorted(base.items()) if name.endswith((".py", ".sql"))})
        result["platform_service/media_backup_b2/package_profile.json"] = (json.dumps(profile,sort_keys=True,indent=2)+"\n").encode()
        result.pop(DIST + "RECORD")
        result[DIST + "RECORD"] = _record(result)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in sorted(result.items()):
                info = zipfile.ZipInfo(name, (2026, 10, 30, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info,raw)
        wheel_raw = stream.getvalue()
        with Path(output_wheel).open("xb") as output:
            output.write(wheel_raw)
        return {**profile, "wheel_sha256":_sha(wheel_raw), "wheel_bytes":len(wheel_raw), "inventory_sha256":{name:_sha(raw) for name,raw in sorted(result.items())}}
    except ProfileError:
        raise
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile, UnicodeError):
        raise ProfileError() from None
