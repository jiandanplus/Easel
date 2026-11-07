import pytest
from platform_service.media_backup_b2.contracts import B2Error

def test_actual_dump_sequence_parser_rejects_movement_and_omission():
    from platform_service.media_backup_b2.capture import parse_dump_sequence_values, compare_sequences
    raw=b"SELECT pg_catalog.setval('public.credit_entries_id_seq', 12, true);\n"
    expected=[{'schema':'public','name':'credit_entries_id_seq','last_value':12,'is_called':True}]
    assert parse_dump_sequence_values(raw)==expected
    compare_sequences(expected,expected,expected)
    with pytest.raises(B2Error):compare_sequences(expected,expected,[{**expected[0],'last_value':13}])
    with pytest.raises(B2Error):compare_sequences(expected,[],expected)

def test_dump_copy_parser_rejects_missing_and_changed_snapshot_rows():
    from platform_service.media_backup_b2.capture import parse_dump_copy_inventory
    raw=b'COPY public.users (id, active) FROM stdin;\nsynthetic\tt\n\\.\n'
    rows=parse_dump_copy_inventory(raw)
    assert rows['public.users']['count']==1
    assert rows!=parse_dump_copy_inventory(raw.replace(b'synthetic',b'different'))
    with pytest.raises(B2Error):parse_dump_copy_inventory(raw.replace(b'\\.\n',b''))

def test_full_dump_schema_requires_actual_schema_bytes_equal():
    from platform_service.media_backup_b2.capture import verify_dump_schema
    ddl=b'ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;\n'
    assert verify_dump_schema(ddl,ddl)['size']==len(ddl)
    with pytest.raises(B2Error):verify_dump_schema(ddl,ddl.replace(b'ENABLE',b'DISABLE'))
