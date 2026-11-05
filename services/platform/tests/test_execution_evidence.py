"""Server-owned evidence survives financial failure without replaying providers."""
import hashlib
import json
import asyncio
from copy import deepcopy

import httpx
import psycopg
import pytest

from platform_service.errors import PlatformError
from platform_service.execution_evidence import correlation_id
from test_execution_core import setup, admit


def terminal(identifier="resp_evidence", **patch):
    return {"id": identifier, "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 2}, "output": [], **patch}


def test_correlation_ids_are_bounded_opaque_values():
    assert correlation_id("req_123-abc:1") == "req_123-abc:1"
    for value in [None, "", "x"*257, "a\nb", "Bearer secret", "https://secret.example/?key=x", True]:
        with pytest.raises(PlatformError, match="invalid_execution_evidence"):
            correlation_id(value)


async def record(service, job, identifier="resp_evidence", *, usage=None):
    await service.record_provider_attempt(job["id"], job["attempt_id"], provider_scope_hash="c"*64, provider_request_hash="a"*64,
                                         upstream_model="fixture-upstream", request_id="req_"+job["id"])
    await service.dispatch(job["id"], job["attempt_id"])
    await service.record_provider_association(job["id"], job["attempt_id"], http_status=200,
                                             http_request_id="http_"+job["id"])
    await service.record_provider_association(job["id"], job["attempt_id"], provider_response_id=identifier)
    await service.record_terminal_evidence(job["id"], job["attempt_id"], terminal=terminal(identifier, **({"usage":usage} if usage else {})), evidence_hash="b"*64)


@pytest.mark.asyncio
async def test_evidence_is_separate_immutable_and_finance_only_recovery_has_no_reply(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await record(service, job)
    assert (await ledger.summary(scope))["spent"] == "0"
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM execution_receipts")).fetchone())["n"] == 0
        row = await (await c.execute("SELECT * FROM execution_terminal_evidence")).fetchone()
        assert set(row) == {"job_id","attempt_id","binding_hash","provider_scope_hash","provider_response_id","input_tokens","output_tokens","terminal_status","evidence_hash","created_at"}
    await service.pending(job["id"], job["attempt_id"], "database_unavailable")
    result = await service.finish_from_evidence(job["id"], job["attempt_id"])
    assert result["status"] == "settled" and result["actual"] == "700"
    await service.finish_from_evidence(job["id"], job["attempt_id"])
    assert (await ledger.summary(scope))["spent"] == "700"
    lookup = await service.lookup_operation(scope, ctx["invocation_id"], "op")
    assert lookup["result_state"] == "not_available"
    with pytest.raises(PlatformError, match="result_expired"):
        await service.result(ctx, "op")
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM execution_results")).fetchone())["n"] == 0
    for table in ["execution_provider_attempts", "execution_provider_associations", "execution_terminal_evidence"]:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:
                await c.execute("DELETE FROM "+table)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:
                await c.execute("UPDATE "+table+" SET binding_hash='changed'")
        with pytest.raises(psycopg.errors.CheckViolation):
            async with db.connection() as c:
                await c.execute("TRUNCATE "+table+" CASCADE")


@pytest.mark.asyncio
async def test_exact_attempt_binding_and_response_reuse_are_rejected(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    with pytest.raises(PlatformError, match="execution_attempt_mismatch"):
        await service.record_provider_attempt(job["id"], "other", provider_scope_hash="c"*64, provider_request_hash="a"*64, upstream_model="upstream", request_id="req_other")
    await record(service, job)
    # Same immutable evidence is idempotent, changed usage is not.
    await service.record_terminal_evidence(job["id"], job["attempt_id"], terminal=terminal(), evidence_hash="b"*64)
    with pytest.raises(PlatformError, match="idempotency_conflict"):
        await service.record_terminal_evidence(job["id"], job["attempt_id"], terminal=terminal(usage={"input_tokens":4,"output_tokens":2}), evidence_hash="b"*64)
    other = await admit(service, ctx, "other")
    await service.record_provider_attempt(other["id"], other["attempt_id"], provider_scope_hash="c"*64, provider_request_hash="c"*64, upstream_model="fixture-upstream", request_id="req_other")
    await service.dispatch(other["id"], other["attempt_id"])
    await service.record_provider_association(other["id"], other["attempt_id"], http_status=200)
    with pytest.raises(PlatformError, match="execution_response_reused"):
        await service.record_terminal_evidence(other["id"], other["attempt_id"], terminal=terminal(), evidence_hash="b"*64)
    assert (await ledger.summary(scope))["spent"] == "0"


@pytest.mark.asyncio
async def test_missing_or_invalid_evidence_does_not_release_or_settle(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await service.dispatch(job["id"], job["attempt_id"])
    await service.pending(job["id"], job["attempt_id"], "upstream_error")
    before = await ledger.summary(scope)
    with pytest.raises(PlatformError, match="execution_evidence_not_found"):
        await service.finish_from_evidence(job["id"], job["attempt_id"])
    with pytest.raises(PlatformError, match="execution_attempt_evidence_required"):
        await service.record_terminal_evidence(job["id"], job["attempt_id"], terminal=terminal(), evidence_hash="b"*64)
    assert await ledger.summary(scope) == before


@pytest.mark.asyncio
async def test_terminal_limits_still_preserve_pending_and_block_binding(db):
    service, ledger, scope, ctx = await setup(db)
    job = await admit(service, ctx)
    await record(service, job)
    # Above-reservation evidence is preserved, but must never debit beyond hold.
    over = await admit(service, ctx, "over")
    await service.record_provider_attempt(over["id"], over["attempt_id"], provider_scope_hash="c"*64, provider_request_hash="a"*64, upstream_model="fixture-upstream", request_id="req_over")
    await service.dispatch(over["id"], over["attempt_id"])
    await service.record_provider_association(over["id"], over["attempt_id"], http_status=200)
    await service.record_terminal_evidence(over["id"], over["attempt_id"], terminal=terminal("resp_over",usage={"input_tokens":3,"output_tokens":101}), evidence_hash="d"*64)
    with pytest.raises(PlatformError, match="settlement_exceeds_reservation"):
        await service.finish_from_evidence(over["id"], over["attempt_id"])
    assert (await service.get(scope, over["id"]))["status"] == "pending_reconciliation"
    assert (await ledger.summary(scope))["spent"] == "0"
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM execution_blocked_bindings")).fetchone())["n"] == 1


@pytest.mark.asyncio
async def test_runtime_records_terminal_before_failed_finish_then_recovery_has_one_post(db, monkeypatch):
    from platform_service.execution_config import ExecutionProvider, ExecutionSettings
    from platform_service.execution_runtime import ExecutionRuntime
    service, ledger, scope, ctx = await setup(db)
    settings = ExecutionSettings(mode="test", providers={"fixture":ExecutionProvider("1","http://127.0.0.1:9999/v1/responses","fixture-key",{"fixture-text":"actual-upstream"})})
    calls=[]
    def provider(request):
        calls.append(request)
        assert json.loads(request.content)["model"]=="actual-upstream"
        assert request.headers["x-request-id"]
        return httpx.Response(200,json=terminal("resp_runtime"),headers={"x-request-id":"upstream-req-1"})
    runtime=ExecutionRuntime(db,service.identity,settings,service=service,transport=httpx.MockTransport(provider))
    token=(await runtime.mint(scope,{"model":"fixture-text","invocation_key":"evidence-runtime"}))["task_token"]
    original=service.finish
    async def failed_finish(*args,**kwargs):raise RuntimeError("fixture_commit_failed")
    monkeypatch.setattr(service,"finish",failed_finish)
    try:
        with pytest.raises(PlatformError):
            await runtime.respond(token,"runtime-operation",{"model":"fixture-text","input":"private prompt must not persist","stream":False})
        async with db.connection() as c:
            row=await (await c.execute("SELECT * FROM execution_terminal_evidence")).fetchone()
            attempt=await (await c.execute("SELECT * FROM execution_provider_attempts")).fetchone()
            assert row["provider_response_id"]=="resp_runtime"
            assert attempt["provider_request_hash"]==hashlib.sha256(calls[0].content).hexdigest()
            assert "private prompt" not in json.dumps({**row,**attempt},default=str)
        monkeypatch.setattr(service,"finish",original)
        await runtime.run_once();await runtime.run_once()
        assert len(calls)==1 and (await ledger.summary(scope))["spent"]=="700"
        async with db.connection() as c:
            assert (await (await c.execute("SELECT count(*) n FROM execution_results")).fetchone())["n"]==0
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_response_identity_is_unique_across_aliases_but_not_other_providers(db):
    from test_execution_core import definition, OPERATOR
    service, ledger, scope, ctx = await setup(db)
    original = await admit(service, ctx)
    await record(service, original)
    for public, provider_hash, blocked in [("alias", "c"*64, True), ("other-provider", "d"*64, False)]:
        d=deepcopy(definition());d["id"]=public
        await service.publish(d,OPERATOR);await service.set_enabled(public,True,OPERATOR)
        bindings={public:{"provider_id":"fixture","provider_version":"1","binding_hash":"binding-"+public}}
        token=await service.mint(scope,model=public,invocation_key="run-"+public,mode="test",bindings=bindings)
        context=await service.authenticate_task(token["task_token"])
        job=await service.admit(context,key="op",body_hash="hash",output_limit=100,bindings=bindings)
        await service.record_provider_attempt(job["id"],job["attempt_id"],provider_scope_hash=provider_hash,
                                             provider_request_hash="a"*64,upstream_model="upstream",request_id="req-"+public)
        await service.dispatch(job["id"],job["attempt_id"])
        await service.record_provider_association(job["id"],job["attempt_id"],http_status=200)
        if blocked:
            with pytest.raises(PlatformError,match="execution_response_reused"):
                await service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=terminal(),evidence_hash="b"*64)
        else:
            await service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=terminal(),evidence_hash="b"*64)
            await service.finish_from_evidence(job["id"],job["attempt_id"])
    assert (await ledger.summary(scope))["spent"]=="700"


@pytest.mark.asyncio
@pytest.mark.parametrize("value",[
    terminal(usage={"input_tokens":True,"output_tokens":2}),
    terminal(usage={"input_tokens":3.0,"output_tokens":2}),
    terminal(usage={"input_tokens":-1,"output_tokens":2}),
    terminal(status="failed"),terminal(error={"message":"private"}),
])
async def test_invalid_terminal_never_becomes_financial_authority(db,value):
    service,ledger,scope,ctx=await setup(db);job=await admit(service,ctx)
    await service.record_provider_attempt(job["id"],job["attempt_id"],provider_scope_hash="c"*64,provider_request_hash="a"*64,upstream_model="upstream",request_id="req-invalid")
    await service.dispatch(job["id"],job["attempt_id"])
    await service.record_provider_association(job["id"],job["attempt_id"],http_status=200)
    before=await ledger.summary(scope)
    with pytest.raises(PlatformError):
        await service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=value,evidence_hash="b"*64)
    assert await ledger.summary(scope)==before
    async with db.connection() as c:
        assert not await (await c.execute("SELECT 1 FROM execution_terminal_evidence")).fetchall()


@pytest.mark.asyncio
async def test_concurrent_recovery_settles_exactly_once(db):
    service,ledger,scope,ctx=await setup(db);job=await admit(service,ctx)
    await record(service,job)
    await asyncio.gather(*[service.finish_from_evidence(job["id"],job["attempt_id"]) for _ in range(4)])
    assert (await ledger.summary(scope))["spent"]=="700"
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM execution_receipts")).fetchone())["n"]==1


@pytest.mark.asyncio
async def test_terminal_evidence_and_waiver_cannot_both_win(db):
    from platform_service.execution.reconciliation import ExecutionReconciliation
    from test_execution_reconciliation import WRITEOFF
    service,ledger,scope,ctx=await setup(db);job=await admit(service,ctx)
    await service.record_provider_attempt(job["id"],job["attempt_id"],provider_scope_hash="c"*64,provider_request_hash="a"*64,upstream_model="upstream",request_id="req-race")
    await service.dispatch(job["id"],job["attempt_id"])
    await service.record_provider_association(job["id"],job["attempt_id"],http_status=200)
    await service.pending(job["id"],job["attempt_id"],"unknown_cost")
    async with db.connection() as c:await c.execute("UPDATE execution_jobs SET lease_until=now()-interval '1 second'")
    r=ExecutionReconciliation(service)
    plan=await r.plan_waivers(WRITEOFF,[job["id"]],"Unrecoverable fixture supplier record","fixture-review")
    outcomes=await asyncio.gather(service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=terminal(),evidence_hash="b"*64),
                                  r.apply_waivers(WRITEOFF,plan,plan["plan_sha256"],"race-key"),return_exceptions=True)
    assert sum(isinstance(value,Exception) for value in outcomes)==1
    current=await service.get(scope,job["id"])
    if current["status"]=="waived":
        with pytest.raises(PlatformError,match="execution_waived"):
            await service.finish_from_evidence(job["id"],job["attempt_id"])
        with pytest.raises(PlatformError,match="execution_waived"):
            await service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=terminal(),evidence_hash="b"*64)
        assert (await service.cancel(scope,job["id"]))["status"]=="waived"
        with pytest.raises(PlatformError,match="execution_waived"):await service.result(ctx,"op")
        assert (await ledger.summary(scope))["spent"]=="0"
    else:
        await service.finish_from_evidence(job["id"],job["attempt_id"])
        assert (await ledger.summary(scope))["spent"]=="700"


@pytest.mark.asyncio
async def test_normal_reply_can_backfill_after_financial_only_recovery_without_recharge(db):
    service,ledger,scope,ctx=await setup(db);job=await admit(service,ctx)
    value=terminal("resp_backfill");payload=json.dumps(value).encode();digest=hashlib.sha256(payload).hexdigest()
    await service.record_provider_attempt(job["id"],job["attempt_id"],provider_scope_hash="c"*64,provider_request_hash="a"*64,upstream_model="upstream",request_id="req-backfill")
    await service.dispatch(job["id"],job["attempt_id"])
    await service.record_provider_association(job["id"],job["attempt_id"],http_status=200)
    await service.record_terminal_evidence(job["id"],job["attempt_id"],terminal=value,evidence_hash=digest)
    await service.finish_from_evidence(job["id"],job["attempt_id"])
    assert (await service.lookup_operation(scope,ctx["invocation_id"],"op"))["result_state"]=="not_available"
    kwargs=dict(input_tokens=3,output_tokens=2,terminal_status="completed",provider_response_id="resp_backfill",evidence_hash=digest,payload=payload,content_type="application/json")
    with pytest.raises(PlatformError,match="execution_evidence_mismatch"):
        await service.finish(job["id"],job["attempt_id"],**{**kwargs,"payload":b'{"fake":"reply"}'})
    await asyncio.gather(service.finish(job["id"],job["attempt_id"],**kwargs),service.finish_from_evidence(job["id"],job["attempt_id"]))
    assert (await service.result(ctx,"op"))["payload"]==payload
    assert (await ledger.summary(scope))["spent"]=="700"
    async with db.connection() as c:
        assert (await (await c.execute("SELECT count(*) n FROM execution_receipts")).fetchone())["n"]==1
        assert (await (await c.execute("SELECT count(*) n FROM execution_results")).fetchone())["n"]==1


@pytest.mark.asyncio
async def test_excess_evidence_does_not_starve_later_recoverable_job(db):
    service,ledger,scope,ctx=await setup(db)
    over=await admit(service,ctx,"over-page");good=await admit(service,ctx,"good-page")
    await record(service,over,"resp_over_page",usage={"input_tokens":3,"output_tokens":101})
    await record(service,good,"resp_good_page")
    await service.pending(over["id"],over["attempt_id"],"database_error")
    await service.pending(good["id"],good["attempt_id"],"database_error")
    with pytest.raises(PlatformError,match="settlement_exceeds_reservation"):
        await service.finish_from_evidence(over["id"],over["attempt_id"])
    # A one-row page proves the invalid older record cannot monopolize a batch.
    assert await service.recover_evidence(limit=1)==1
    assert await service.recover_evidence(limit=1)==0
    assert (await service.get(scope,over["id"]))["status"]=="pending_reconciliation"
    assert (await service.get(scope,good["id"]))["status"]=="settled"
    assert (await ledger.summary(scope))["spent"]=="700"
