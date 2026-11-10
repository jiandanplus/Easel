"""Local, synthetic, frozen-016 fixture for nonempty restore verification."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest

HELPER = Path(__file__).with_name("settled_media_restore_fixture.py")


def helper():
    assert HELPER.is_file(), "settled-media fixture helper must exist"
    spec = importlib.util.spec_from_file_location("settled_media_fixture", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_source_requires_exact_wheel_and_every_python_sql_byte(tmp_path):
    lib = helper()
    result = lib.verify_frozen_source(lib.STAGE)
    assert result["wheel_sha256"] == lib.WHEEL_SHA
    assert result["source_count"] == 76 and result["migration_count"] == 16
    with pytest.raises(lib.FixtureRejected):
        lib.verify_frozen_source(tmp_path)


@pytest.mark.parametrize("field,value", [
    ("dsn", "postgresql://fixture_owner:secret@203.0.113.1/easel_s1_test_media_aaaaaaaaaaaa"),
    ("host", "203.0.113.1"), ("database", "existing_service"),
    ("schema", "production"), ("bootstrap_user", "postgres"),
])
def test_connection_metadata_refuses_nonfixture_or_remote_targets(field, value):
    lib = helper()
    valid = dict(nonce='a'*32, host='127.0.0.1', port=54321,
                 database='easel_s1_test_media_'+'a'*12, schema='central',
                 bootstrap_user='fixture_owner', bootstrap_oid=10,
                 dsn='postgresql://fixture_owner:'+'b'*43+'@127.0.0.1:54321/easel_s1_test_media_'+'a'*12)
    lib.validate_connection_metadata(valid)
    with pytest.raises(lib.FixtureRejected):
        lib.validate_connection_metadata(valid | {field: value})


@pytest.mark.asyncio
async def test_two_settled_image_jobs_have_real_pngs_typed_evidence_and_zero_holds(tmp_path):
    lib = helper()
    root = tmp_path / "private-fixture"
    handle = await lib.produce_fixture(root)
    try:
        assert handle["postconditions"] == {
            "jobs": 2, "settled": 2, "artifacts": 2, "blobs": 2,
            "http_references": 2, "receipts": 2, "terminal_evidence": 2,
            "amount": 2000000, "spent": 2000000, "held": 0,
            "storage_budgets_released": 2, "models_disabled": 2,
            "pngs_decoded": 2, "provider_posts": 0, "fake_transport_posts": 2,
            "migration_count": 16, "source_count": 76,
        }
        metadata = Path(handle["connection_file"])
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(metadata.stat().st_mode) == 0o600
        value = json.loads(metadata.read_text())
        assert value["bootstrap_user"] == handle["source"]["user"]
        assert value["host"] == "127.0.0.1" and value["bootstrap_oid"] == 10
        assert "dsn" not in json.dumps(handle) and "password" not in json.dumps(handle)
        pngs = list(Path(handle["storage_root"]).iterdir())
        assert len(pngs) == 2 and all(p.stat().st_size > 1000000 for p in pngs)
        # The worker reopens the committed DB, verifies ownership, typed references,
        # wallet/journal reconciliation, artifact bytes and source migration checksums.
        assert await lib.verify_fixture(handle) == handle["postconditions"]
        with pytest.raises(lib.FixtureRejected):
            await lib.produce_fixture(root)
    finally:
        await lib.stop_fixture(handle)
    assert lib.inspect_stopped(handle) is True
