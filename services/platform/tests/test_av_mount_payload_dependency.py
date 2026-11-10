"""The visible mount model must pass the same outer payload gate as its caller."""
import hashlib
import json

import pytest

from services.platform.tests.test_av_remote_operator import subject


NAMES = ('safe_archive.py', 'linux_sandbox.py', 'archive_worker.py', 'remote_operator.py', 'mount_table.py')


def test_mount_model_is_a_required_verified_payload_member():
    module = subject()
    assert set(module.PAYLOAD_FILES) == {'code/av_sources/' + name for name in NAMES}


def test_payload_cannot_omit_model_and_reach_layout(tmp_path):
    module = subject()
    stage = tmp_path / 'stage'; stage.mkdir(mode=0o700)
    (stage / 'code/av_sources').mkdir(mode=0o700, parents=True)
    (stage / 'code').chmod(0o700)
    entries = {}
    # These four records matched the prior payload contract. The now-required
    # model is absent from disk and manifest, so its omission must fail closed.
    for name in NAMES[:-1]:
        relative = 'code/av_sources/' + name
        data = b'# synthetic public code\n'
        path = stage / relative; path.write_bytes(data); path.chmod(0o600)
        entries[relative] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    raw = json.dumps({'schema_version': 1, 'files': entries}, sort_keys=True).encode()
    manifest = stage / 'payload-manifest.json'; manifest.write_bytes(raw); manifest.chmod(0o600)
    with pytest.raises(module.OperatorError): module.verify_payload(stage, hashlib.sha256(raw).hexdigest())
    assert not (stage / 'jail').exists() and not (stage / 'work').exists()
