"""Local, synthetic, frozen-016 fixture for nonempty restore verification."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest

HELPER = Path(__file__).with_name("mixed_media_restore_fixture.py")


def helper():
    assert HELPER.is_file(), "mixed-media fixture helper must exist"
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


