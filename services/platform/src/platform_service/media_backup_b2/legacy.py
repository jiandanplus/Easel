"""Explicit bounded legacy authentication; never a DB restore or v2 fallback."""
from importlib.resources import files
import hashlib
import types

from .contracts import B2Error, require, validate_uuid

_PINS = {
    "sealed_transfer.py": "7dd81fbc97d60c782fe2d2c43d81e37fc0b4ea926504e66309a43abc144ad7d6",
    "encrypted_backup.py": "d330bccf0c71f9c4d808411ba48d87c217048dde0763023e4e9ca0cb45bb14a1",
}
MAX_PAYLOAD = 32 * 1024**2
MAX_ENVELOPE = 12 + 4096 + MAX_PAYLOAD + 16


def _resource_bytes(name):
    require(name in _PINS)
    with files(__package__).joinpath("legacy_sources", name).open("rb") as source:
        raw = source.read(65537)
    require(type(raw) is bytes and len(raw) <= 65536)
    return raw


def _load(name):
    raw = _resource_bytes(name)
    require(hashlib.sha256(raw).hexdigest() == _PINS[name])
    # These exact frozen sources have no top-level private IO. A neutral name
    # prevents their guarded main/entrypoint blocks from running.
    module = types.ModuleType("_easel_b2_frozen_" + name.removesuffix(".py"))
    exec(compile(raw, "b2-frozen-resource/" + name, "exec"), module.__dict__)
    return module


def format_for_magic(raw):
    require(type(raw) is bytes and len(raw) == 8)
    if raw == b"EASELST\x01":
        return "v1"
    if raw == b"EASELBS\x02":
        return "v2"
    raise B2Error()


def verify_legacy(private_pem, envelope, expected_transfer_id, expected_kind, expected_target, expected_manifest):
    try:
        require(type(envelope) is bytes and 29 <= len(envelope) <= MAX_ENVELOPE)
        require(format_for_magic(envelope[:8]) == "v1")
        require(type(private_pem) is bytes and 1 <= len(private_pem) <= 32768)
        validate_uuid(expected_transfer_id)
        require(type(expected_kind) is str and expected_kind in ("central", "logto"))
        require(type(expected_target) is str and expected_target == "ser362540173134")
        require(type(expected_manifest) is dict)
        helper = _load("sealed_transfer.py")
        verifier = _load("encrypted_backup.py")
        raw = helper.unseal(private_pem, envelope, expected_transfer_id, expected_kind, expected_target)
        require(type(raw) is bytes and len(raw) <= MAX_PAYLOAD)
        verifier.verify_payload(raw, expected_manifest)
        return None
    except Exception:
        raise B2Error() from None
