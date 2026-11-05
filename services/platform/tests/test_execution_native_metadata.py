"""Real gateway admission and provider boundary for native transport metadata."""
import pytest

from platform_service.errors import PlatformError
from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from test_execution_core import setup
from test_execution_gateway_integration import provider


@pytest.mark.asyncio
async def test_native_metadata_is_hashed_but_not_forwarded_or_authoritative(db):
    service, ledger, scope, _ = await setup(db)
    async with provider() as (endpoint, calls):
        runtime = ExecutionRuntime(db, service.identity, ExecutionSettings(mode="test", providers={
            "fixture": ExecutionProvider("1", endpoint, "dummy-local-fixture", {"fixture-text": "local-upstream"})}), service=service)
        try:
            minted = await runtime.mint(scope, {"model": "fixture-text", "invocation_key": "owned-native-metadata"})
            body = {"model": "fixture-text", "input": "owned fixture", "client_metadata": {
                "session_id": "client-untrusted-session", "thread_id": "owned-thread", "x-codex-turn-metadata": "{}"}}
            response = await runtime.respond(minted["task_token"], "owned-operation", body)
            replay = await runtime.respond(minted["task_token"], "owned-operation", body)
            assert response.body == replay.body and len(calls) == 1
            assert "client_metadata" not in calls[0]
            assert calls[0]["model"] == "local-upstream"
            with pytest.raises(PlatformError, match="idempotency_conflict"):
                await runtime.respond(minted["task_token"], "owned-operation", {**body, "client_metadata": {"session_id": "different"}})
            assert len(calls) == 1
            assert (await ledger.summary(scope))["spent"] == "700"
        finally:
            await runtime.close()
