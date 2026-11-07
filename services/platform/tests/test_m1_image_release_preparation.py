"""No live host, credentials, or deployment mutations in preparation tests."""
import importlib.util
from pathlib import Path
import hashlib
import json
import pytest


def module():
    source=Path(__file__).parents[1]/'scripts/prepare_m1_image_release.py'
    assert source.is_file(), 'the bounded plan/apply entrypoint is required'
    spec=importlib.util.spec_from_file_location('m1_image_release',source)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_only_approved_internal_image_rate_and_parameters_are_constructed():
    value=module().image_definition('gpt-image-2.5-flare','a'*64)
    assert value['feature']=='image' and value['test_only'] is False
    assert value['rate']['meters']==[{'unit':'image','basis':'verified_delivery_v1','numerator':'1000000','denominator':'1'}]
    assert value['limits']['max_units']=={'image':1} and value['limits']['max_assets']==0
    assert set(value['parameters']['properties'])=={'n','size'}
    assert value['parameters']['properties']['n']=={'type':'integer','minimum':1,'maximum':1}
    with pytest.raises(ValueError):module().image_definition('unapproved-model','a'*64)


def test_plan_hash_is_exact_and_rejects_changed_private_plan(tmp_path):
    value=module();p=tmp_path/'plan.json';raw=b'{"phase":"review-required"}\n';p.write_bytes(raw);p.chmod(0o600)
    assert value.approved_plan(p,hashlib.sha256(raw).hexdigest())=={'phase':'review-required'}
    with pytest.raises(ValueError):value.approved_plan(p,'0'*64)
    p.write_bytes(raw+b' ')
    with pytest.raises(ValueError):value.approved_plan(p,hashlib.sha256(raw).hexdigest())


def test_private_input_rejects_links_group_access_and_duplicate_keys(tmp_path):
    value=module();p=tmp_path/'private.json';p.write_text('{"a":1}');p.chmod(0o600)
    link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises((ValueError,OSError)):value.read_private(link)
    p.chmod(0o640)
    with pytest.raises(ValueError):value.read_private(p)
    p.chmod(0o600);p.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):value.read_private(p)


def test_safe_receipt_never_returns_private_scope_connection_or_evidence_values():
    value=module();private={'owner':{'dsn':'synthetic-secret'},'original_grant':{'user_id':'private-user'},
        'financial_snapshot':{'credit_lots':{'rows':1,'sha256':'b'*64}},'definitions':[{},{}]}
    safe=value.safe_plan_receipt(private,'c'*64,Path('/root/approved/plan-private.json'))
    serialized=json.dumps(safe)
    assert 'synthetic-secret' not in serialized and 'private-user' not in serialized
    assert safe['financial_snapshot']==private['financial_snapshot'] and safe['model_definition_count']==2
    assert safe['planned_money_delta']=='0' and safe['source_scope_match'] is True


def test_apply_requires_both_unchanged_snapshot_and_stopped_original_units_before_any_db_write():
    value=module()
    assert value.approve_preconditions('a','a',{'api':False,'console':False}) is None
    for actual,units in [('b',{'api':False,'console':False}),('a',{'api':True,'console':False}),('a',{'api':False,'console':True})]:
        with pytest.raises(ValueError):value.approve_preconditions('a',actual,units)
