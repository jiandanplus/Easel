"""Additional raw SQL scalar/idempotent/observe authority boundaries."""
import copy
from uuid import uuid4
import pytest
from psycopg.types.json import Jsonb
from b2_owned_media import owned_media
from test_media_maintenance_sql_contracts import _window_inputs,_raw_call,_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('side,key,bad',[
    ('binding','host_id','not valid'),
    ('binding','host_id','非ASCII'),
    ('binding','host_id','x'*129),
    ('binding','host_id',True),
    ('binding','boot_id',None),
    ('binding','process_start',{}),
    ('binding','control_dev','1'),
    ('binding','control_ino',1.0),
    ('binding','deadline_ns',0),
    ('binding','deadline_ns',-1),
    ('binding','deadline_ns',True),
    ('binding','deadline_ns',1.5),
    ('binding','deadline_ns',2**63),
    ('binding','generation_digest','A'*64),
    ('binding','generation_digest','0'*64),
    ('binding','generation_digest',{}),
    ('original','source_ino',True),
    ('original','control_ino',1.0),
    ('original','generation_digest','A'*64),
    ('original','configuration_state',False),
])
async def test_raw_begin_rejects_scalar_authority_before_any_change(tmp_path,side,key,bad):
    async with owned_media(tmp_path)as value:
        nonce,window,binding,original=await _window_inputs(value)
        context=binding if side=='binding'else original
        context[key]=bad
        before=await _snapshot(value['env'])
        code,_=await _raw_call(value['env'],'controller','SELECT easel_backup_control.begin_window(%s,%s,%s,%s)',(nonce,window,Jsonb(binding),Jsonb(original)))
        assert code=='23514'
        assert await _snapshot(value['env'])==before


@pytest.mark.asyncio
async def test_raw_begin_same_window_rejects_different_valid_binding(tmp_path):
    async with owned_media(tmp_path)as value:
        nonce,window,binding,original=await _window_inputs(value)
        statement='SELECT easel_backup_control.begin_window(%s,%s,%s,%s)'
        code,row=await _raw_call(value['env'],'controller',statement,(nonce,window,Jsonb(binding),Jsonb(original)))
        assert code is None and row==(1,)
        before=await _snapshot(value['env']);changed=copy.deepcopy(binding);changed['deadline_ns']+=1
        code,_=await _raw_call(value['env'],'controller',statement,(nonce,window,Jsonb(changed),Jsonb(original)))
        assert code=='23514' and await _snapshot(value['env'])==before


@pytest.mark.asyncio
@pytest.mark.parametrize('window,nonce',[(None,b'x'*32),('00000000-0000-0000-0000-000000000001',b'x'*32),(str(uuid4()),None),(str(uuid4()),b'short')])
async def test_raw_observe_rejects_incomplete_identity_before_any_read_result(tmp_path,window,nonce):
    async with owned_media(tmp_path)as value:
        before=await _snapshot(value['env'])
        code,_=await _raw_call(value['env'],'controller','SELECT easel_backup_control.observe_window(%s,%s)',(nonce,window))
        assert code=='23514' and await _snapshot(value['env'])==before
