"""Offline bounded protocol; no native process or connection."""
import dataclasses as dc
import importlib.util
import json
import pytest
from platform_service.media_backup_b3 import roles
from test_media_backup_b3_roles import fixture

MODULE = 'platform_service.media_backup_b3.reconstruction_contract'

def api():
    assert importlib.util.find_spec(MODULE) is not None, 'missing private reconstruction protocol'
    return __import__(MODULE, fromlist=['*'])

def program():
    return roles.build_role_database_plan(**fixture())

def document():
    c = api()
    return dict(format=c.REQUEST_FORMAT, operation='ROLES_APPLY',
        binding=dict(restore_id='toy_job',job_dev=1,job_ino=2,kind='central',operation_id='n'*32,
            ordinal=1,source_cid='a'*64,target_cid='b'*64,system_id_sha256='c'*64,
            input_sha256='d'*64,context_sha256='e'*64,pair_sha256='f'*64,
            plan_sha256=roles._hash(program()),rendering_sha256=roles._hash(('UTC','ISO, MDY')),started_ns=100,deadline_ns=1900),
        policy=dict(request_limit=c.REQUEST_LIMIT,result_limit=c.RESULT_LIMIT,plan_limit=c.PLAN_LIMIT,
            auth_limit=c.AUTH_LIMIT,diagnostic_limit=c.DIAGNOSTIC_LIMIT),
        plan=c.project_plan(program()), identity_before=dict(operational_name='B',operational_oid=10,
            source_name='A',source_oid=None,helper_name=None,helper_oid=None,
            operational_attributes_sha256='a'*64,operational_verifier_sha256='b'*64,initdb_sha256='c'*64),
        prerequisite=dict(ordinal=0,head_sha256='0'*64,record_sha256='0'*64),
        private_auth={'operational_password':'c2VudGluZWw='}, remaining_ns=1000,
        source_rendering={'timezone':'UTC','datestyle':'ISO, MDY'},
        build=dict(artifact_sha256='a'*64,manifest_sha256='a'*64,entrypoint_sha256='a'*64,client_sha256='a'*64,program_sha256='a'*64,executable_sha256='a'*64,
            image_id='sha256:'+'a'*64,cpu_nanos=10**9,memory_bytes=256*1024**2,pids_limit=64,tmpfs_bytes=16*1024**2))

def test_missing_private_protocol_is_red():
    api()

def test_projection_preserves_typed_fields_and_order_without_source_tree():
    c=api(); p=program(); value=c.project_plan(p)
    assert 'source_roles' not in value and 'source_graph_edges' not in value
    replay=c.decode_plan(value)
    assert replay.database == p.database
    assert replay.roles == p.roles
    assert replay.memberships == p.memberships
    assert replay.database_acl_raw == p.source_database.acl
    assert replay.plan_sha256 == roles._hash(p)

def test_private_frame_roundtrip_and_redacted_repr():
    c=api(); d=document(); raw=c.encode_request(d); request=c.decode_request(raw)
    assert request.operation is c.Operation.ROLES_APPLY
    assert request.private_auth['operational_password'] == b'sentinel'
    assert 'sentinel' not in repr(request) and 'c2VudGluZWw=' not in repr(request)
    assert raw[:4] == (len(raw)-4).to_bytes(4,'big')

@pytest.mark.parametrize('mutation', [
    lambda d:d.update(operation='SQL'), lambda d:d.update(remaining_ns=True),
    lambda d:d.update(extra=1),lambda d:d['binding'].update(ordinal=True),
    lambda d:d['binding'].update(target_cid=d['binding']['source_cid']),
    lambda d:d['policy'].update(request_limit=1),
    lambda d:d['private_auth'].update(extra='YWJj'),
    lambda d:d['private_auth'].update(operational_password='!!!!'),
    lambda d:d['plan'].update(extra=1),
    lambda d:d['plan']['roles'][0]['expectation'].update(superuser=1),
])
def test_closed_request_rejects_before_any_native(mutation):
    c=api(); d=document(); mutation(d)
    with pytest.raises(c.ProtocolError): c.encode_request(d)

@pytest.mark.parametrize('tail',[b'X',b'\x00\x00\x00\x02{}'])
def test_frame_rejects_tail(tail):
    c=api(); raw=c.encode_request(document())
    with pytest.raises(c.ProtocolError): c.decode_request(raw+tail)

def test_duplicate_keys_and_nonfinite_rejected():
    c=api()
    for raw in (b'{"format":1,"format":2}',b'{"remaining_ns":NaN}'):
        with pytest.raises(c.ProtocolError): c.decode_request(len(raw).to_bytes(4,'big')+raw)

def test_stream_cap_rejects_during_encoding():
    c=api()
    with pytest.raises(c.ProtocolError): c.bounded_frame({'x':'z'*1024},32)

def test_shared_witness_is_deduplicated_and_rebound():
    c=api(); p=program(); d=c.project_plan(p)
    assert 'witnesses' in d
    assert all(type(g['witness_index']) is int and 'witness' not in g for g in d['memberships'])
    d['memberships'][0]['witness_index']=True
    with pytest.raises(c.ProtocolError): c.decode_plan(d)

@pytest.mark.parametrize('mutation', [
    lambda d:d.update(identity_before=None),
    lambda d:d['identity_before'].update(operational_oid=True),
    lambda d:d['identity_before'].update(extra=1),
    lambda d:d.update(prerequisite={'anything':'arbitrary'}),
    lambda d:d['private_auth'].update(credentials={'undeclared':'YWJj'}),
])
def test_identity_prerequisite_and_credentials_are_closed(mutation):
    c=api();d=document();mutation(d)
    with pytest.raises(c.ProtocolError):c.encode_request(d)

@pytest.mark.parametrize('path,value',[
    ('roles',[{}]),('handover',{}),('roles',[None]),('memberships',[None]),
    ('settings',[{}]),('authentication',[{}]),('database',{}),('witnesses',[None])])
def test_nested_malformed_plan_always_fixed_error(path,value):
    c=api();d=document();d['plan'][path]=value
    with pytest.raises(c.ProtocolError,match='^RECONSTRUCTION_PROTOCOL_REFUSED$'):c.encode_request(d)

@pytest.mark.parametrize('mutate',[
    lambda d:d['identity_before'].update(helper_name='x'),
    lambda d:d['binding'].update(restore_id='\ud800'),
])
def test_unicode_is_fixed_protocol_error(mutate):
    c=api();d=document();mutate(d)
    if d['identity_before']['helper_name']=='x':
        d['operation']='H_CREATE';d['private_auth'].update(helper_name='/w==',helper_password='YQ==',helper_verifier='Yg==')
    with pytest.raises(c.ProtocolError,match='^RECONSTRUCTION_PROTOCOL_REFUSED$'):c.encode_request(d)

def test_source_rendering_and_independent_build_are_required():
    c=api();d=document();del d['build'];del d['source_rendering']
    with pytest.raises(c.ProtocolError):c.encode_request(d)
    d['source_rendering']={'timezone':'UTC','datestyle':'ISO, MDY'}
    d['build']={k:'a'*64 for k in ('artifact_sha256','manifest_sha256','entrypoint_sha256','client_sha256','program_sha256','executable_sha256')}
    d['build'].update(image_id='sha256:'+'a'*64,cpu_nanos=10**9,memory_bytes=256*1024**2,pids_limit=64,tmpfs_bytes=16*1024**2)
    d['binding']['rendering_sha256']=roles._hash(('UTC','ISO, MDY'))
    assert c.decode_request(c.encode_request(d)).source_rendering==('UTC','ISO, MDY')

def test_independent_executable_pin_required():
    c=api();d=document();d['build'].pop('executable_sha256',None)
    with pytest.raises(c.ProtocolError):c.encode_request(d)
