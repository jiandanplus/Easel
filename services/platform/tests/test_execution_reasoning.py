"""Reasoning capability is immutable provider metadata, never a model-name guess."""
import json

import httpx
import pytest

from platform_service.errors import PlatformError
from platform_service.execution.core import validate_definition
from test_execution_core import definition
from test_execution_runtime import runtime, request, terminal


def test_optional_reasoning_definition_preserves_old_unknown_and_explicit_none():
    old = definition()
    assert "reasoning" not in validate_definition(old)
    for efforts in ([], ["low", "medium", "high"], ["minimal", "xhigh", "max"]):
        value = {**old, "reasoning": {"efforts": efforts}}
        result = validate_definition(value)
        assert result["reasoning"] == {"efforts": efforts}
        assert result["reasoning"] is not value["reasoning"]
        assert result["reasoning"]["efforts"] is not efforts


@pytest.mark.parametrize("reasoning", [None, True, [], {}, {"efforts": "high"},
    {"efforts": ["high", "high"]}, {"efforts": ["off"]}, {"efforts": [True]},
    {"efforts": ["HIGH"]}, {"efforts": ["high"], "provider_secret": "synthetic"},
    {"efforts": ["minimal", "low", "medium", "high", "xhigh", "max", "max"]}])
def test_definition_rejects_unbounded_or_unrecognized_reasoning(reasoning):
    with pytest.raises(PlatformError, match="invalid_model_reasoning"):
        validate_definition({**definition(), "reasoning": reasoning})


@pytest.mark.asyncio
@pytest.mark.parametrize("capability, effort", [(None, "high"), ({"efforts": []}, "low"),
    ({"efforts": ["low"]}, "high"), ({"efforts": ["high"]}, "off"),
    ({"efforts": ["high"]}, True), ({"efforts": ["high"]}, None)])
async def test_runtime_rejects_effort_before_reserving_or_dispatching(capability, effort):
    upstream = []
    async def handler(req):
        upstream.append(req)
        return httpx.Response(200, json=terminal())
    gateway = runtime(handler)
    authenticate = gateway.service.authenticate_task
    async def with_capability(token):
        context = await authenticate(token)
        if capability is not None:
            context["model_reasoning"] = capability
        return context
    gateway.service.authenticate_task = with_capability
    try:
        with pytest.raises(PlatformError):
            await gateway.respond("private-task", "unsupported", request(reasoning={"effort": effort}))
        assert not gateway.service.calls
        assert not gateway.service.jobs
        assert not upstream
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_runtime_declared_effort_reaches_upstream_and_summary_only_stays_compatible():
    received = []
    async def handler(req):
        received.append(json.loads(req.content))
        return httpx.Response(200, json=terminal())
    gateway = runtime(handler)
    authenticate = gateway.service.authenticate_task
    async def with_capability(token):
        return {**await authenticate(token), "model_reasoning": {"efforts": ["low", "high"]}}
    gateway.service.authenticate_task = with_capability
    try:
        await gateway.respond("private-task", "declared", request(reasoning={"effort": "high"}))
        assert received[0]["reasoning"] == {"effort": "high"}
        gateway.service.authenticate_task = authenticate
        await gateway.respond("private-task", "summary", request(reasoning={"summary": "auto"}))
        assert received[1]["reasoning"] == {"summary": "auto"}
        await gateway.respond("private-task", "legacy", request())
        assert "reasoning" not in received[2]
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_effort_changes_conflict_with_original_key_without_second_dispatch():
    received = []
    async def handler(req):
        received.append(json.loads(req.content))
        return httpx.Response(200, json=terminal())
    gateway = runtime(handler)
    authenticate = gateway.service.authenticate_task
    async def with_capability(token):
        return {**await authenticate(token), "model_reasoning": {"efforts": ["low", "high"]}}
    gateway.service.authenticate_task = with_capability
    try:
        await gateway.respond("private-task", "same-operation", request(reasoning={"effort": "low"}))
        with pytest.raises(PlatformError, match="idempotency_conflict"):
            await gateway.respond("private-task", "same-operation", request(reasoning={"effort": "high"}))
        assert len(received) == 1
        assert gateway.service.calls.count("dispatch") == 1
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_catalog_whitelists_reasoning_and_auth_uses_immutable_definition(db):
    from test_execution_core import setup, OPERATOR, BINDINGS
    service, ledger, scope, _ = await setup(db)
    model = {**definition(), "id": "reasoning-fixture", "reasoning": {"efforts": ["low", "high"]}}
    bindings = {**BINDINGS, model["id"]: BINDINGS["fixture-text"]}
    await service.publish(model, OPERATOR)
    await service.set_enabled(model["id"], True, OPERATOR)
    catalog = await service.catalog(scope, mode="test", bindings=bindings)
    public = next(row for row in catalog["models"] if row["id"] == model["id"])
    assert public["reasoning"] == model["reasoning"]
    assert "provider_id" not in public and "verification_ref" not in public
    old = next(row for row in catalog["models"] if row["id"] == "fixture-text")
    assert "reasoning" not in old
    credential = await service.mint(scope, model=model["id"], invocation_key="reasoning",
                                    mode="test", bindings=bindings)
    auth = await service.authenticate_task(credential["task_token"])
    assert auth["model_reasoning"] == {"efforts": ["low", "high"]}
    assert "model_reasoning" not in credential
    assert (await ledger.summary(scope))["reserved"] == "0"
