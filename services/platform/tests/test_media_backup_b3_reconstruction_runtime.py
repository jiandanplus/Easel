"""Original-owner finite journal tests, synthetic capture/factory only."""
import importlib
from unittest.mock import patch
import pytest
import test_media_backup_b3_execution as helpers
m=helpers.m
from media_backup_b3_replay_fixture import matching_graph

@pytest.fixture
def admitted():
    case=helpers.ExecutionTests();case.setUp()
    try:
        graph=matching_graph(case);owner=case.owner();case.pair(owner)
        owner._admit_role_plan(graph.pair,source_owners=graph.owners,authorizations=graph.authorizations)
        yield case,owner,graph
    finally:case.doCleanups()

def runtime():
    return importlib.import_module('platform_service.media_backup_b3.reconstruction_runtime')

def test_original_none_policy_cannot_be_replaced_before_begin(admitted):
    case,owner,graph=admitted
    owner._reconstruction_policy=runtime().RootReconstructionPolicy(case.policy,'c'*64,'d'*64,'e'*64)
    with patch.object(m,'_popen') as native:
        with pytest.raises(m.B3Error):owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
        native.assert_not_called()

def test_original_owner_claim_precedes_all_native_allocation(admitted):
    case,owner,graph=admitted
    api=runtime()
    r=owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
    phase=r.claim('central',r.next_operation('central'))
    r.fail(phase)
    r=owner._role_slot.runtime
    assert r.owner is owner and r.admission is owner._role_original_slot
    assert r.phases[0].state=='UNKNOWN' and owner._unknown
    assert r.phases[0].intent.durable
    assert graph.pair._consumers==1
    assert b'sentinel' not in r.phases[0].intent.raw
    import json
    from platform_service.media_backup_b3.roles import _hash
    record=json.loads(r.phases[0].intent.raw)
    assert record['plan_sha256']==_hash(r.phases[0].plan)
    assert record['rendering_sha256']==_hash(owner._role_slot.renderings[0])
    assert record['policy_sha256']==owner._policy_pin
    with pytest.raises(m.B3Error):owner._close_role_plan_consumer()
    with pytest.raises(m.B3Error):owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')

def test_reconstruction_cannot_adopt_other_handle_or_input(admitted):
    case,owner,graph=admitted
    with pytest.raises(m.B3Error):owner._begin_reconstruction(object(),owner._input,'central')
    assert getattr(owner._role_slot,'runtime',None) is None

def test_phase_journal_never_uses_old_dispatch_secret_record(admitted):
    case,owner,graph=admitted;api=runtime();old=owner._ledger
    r=owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
    phase=r.claim('central',r.next_operation('central'));r.fail(phase)
    r=owner._role_slot.runtime
    assert r.journal is not old and owner._ledger is old
    assert all(b'request_frame_hex' not in row.raw for row in r.journal.records)
    assert owner._reconstruction_snapshot()['reconstruction_unknown']
    assert owner._reconstruction_snapshot()['reconstruction_phase_claims']==1

def test_fsync_ambiguity_burns_phase_before_transport(admitted):
    case,owner,graph=admitted;api=runtime()
    r=owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
    with patch.object(m,'_fsync',side_effect=OSError('synthetic fsync ambiguity')):
        with pytest.raises(m.B3Error):r.claim('central',r.next_operation('central'))
    assert owner._role_slot.runtime.phases[0].state=='UNKNOWN'
    assert graph.pair._consumers==1


def test_default_execution_snapshot_preserves_exact_old_schema(admitted):
    case,owner,graph=admitted
    assert not any(key.startswith('reconstruction_') for key in owner.snapshot())
    r=owner._begin_reconstruction(owner._targets['central'].original_handle,owner._input,'central')
    phase=r.claim('central',r.next_operation('central'))
    assert not any(key.startswith('reconstruction_') for key in owner.snapshot())
    assert owner.snapshot()['execution_unknown']
    assert owner._reconstruction_snapshot()['reconstruction_phase_claims']==1
    r.fail(phase)


def test_default_retention_hash_uses_exact_old_projection(admitted):
    case,owner,graph=admitted
    row=dict(job=list(owner._job),input_sha256=owner._input_pin,
        context_sha256=dict(zip(('central','identity'),owner._context_pins)),policy_sha256=owner._policy_pin,
        started_at_ns=owner._started_at_ns,deadline_ns=owner._deadline_ns,
        journal_head=owner._ledger.records[-1].sha256,restore_id=owner._restore_id)
    assert owner._retention_binding()==m.digest(row)


def test_phase_windows_are_independent_and_never_reopened(admitted):
    case,owner,graph=admitted
    r=owner._begin_reconstruction(owner._targets["central"].original_handle,owner._input,"central")
    p=r.claim("central",r.next_operation("central"))
    base=owner._clock()
    with patch.object(owner,"_clock",return_value=base):
        r.start_anchor(p)
    with pytest.raises(m.B3Error):
        r.start_anchor(p)
    p.state="COMMITTED"
    with pytest.raises(m.B3Error):r.next_operation("central")
    r.fail(p)
