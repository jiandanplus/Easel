import base64
from dataclasses import replace
import hashlib
import io

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn
from PIL import Image

from platform_service.errors import PlatformError
from platform_service.media_config import MediaSettings, MediaProvider
from platform_service.media_runtime import MediaRuntime
from test_media_credits import setup
from test_media_contracts import definition


def image():
    result = io.BytesIO(); Image.new("RGB", (7, 4), (92, 115, 140)).save(result, format="PNG"); return result.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["image.generate", "image.edit"])
async def test_real_pg_image_decode_owned_artifact_fixed_rate_and_no_replay_charge(db, tmp_path, capability,legacy_media):
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=definition(capability))
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider(
        "1", "http://127.0.0.1:9999/v1", "synthetic-private-provider", {"fixture-image": "private-upstream"})})
    minted = await service.mint(scope, model="fixture-image", capability=capability, invocation_key="runtime-integration", mode="test", bindings=settings.bindings)
    calls = []
    async def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/images/edits" if capability == "image.edit" else "/images/generations")
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image()).decode()}]}, headers={"x-request-id": "fixture-image-response"})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        before = await ledger.summary(scope)
        assets = []
        if capability == "image.edit":
            async def chunks(): yield image()
            assets.append((await runtime.upload_asset(minted["task_token"], chunks(), mime="image/png", declared_size=len(image())))["id"])
        request = {"model": "fixture-image", "capability": capability, "prompt": "Synthetic controlled image", "assets": assets, "parameters": {"n": 1}}
        result = await runtime.submit(minted["task_token"], "one", request)
        assert result["execution_state"] == "completed" and result["financial_state"] == "settled"
        assert result["actual"] == "2" and len(result["artifacts"]) == 1
        public = result["artifacts"][0]
        assert "storage_key" not in public and public["properties"] == {"width": 7, "height": 4, "format": "PNG"}
        raw, mime, sha = await runtime.artifact(scope, public["id"], content=True)
        assert raw == image() and mime == "image/png" and sha == hashlib.sha256(image()).hexdigest()
        with pytest.raises(PlatformError): await runtime.artifact(replace(scope, product_id="second"), public["id"], content=True)
        assert (await runtime.submit(minted["task_token"], "one", request))["id"] == result["id"]
        after = await ledger.summary(scope)
        assert int(after["spent"]) - int(before["spent"]) == 2 and after["reserved"] == "0"
        assert len(calls) == 1
        async with db.connection() as c:
            receipt = await (await c.execute("SELECT * FROM media_receipts WHERE job_id=%s", (result["id"],))).fetchone()
            evidence = await (await c.execute("SELECT * FROM media_terminal_evidence WHERE job_id=%s", (result["id"],))).fetchone()
            assert receipt["actual"] == 2 and evidence["quantities"] == {"image": 1}
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_real_pg_unknown_image_response_retains_hold_and_no_second_attempt(db, tmp_path,legacy_media):
    service, ledger, scope, *_ = await legacy_media.setup(db)
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "private-fixture", {"fixture-image": "upstream"})})
    token = await service.mint(scope, model="fixture-image", capability="image.generate", invocation_key="unknown-fixture", mode="test", bindings=settings.bindings)
    seen = []
    def handler(request): seen.append(request); raise httpx.ReadTimeout("synthetic unknown", request=request)
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    request = {"model": "fixture-image", "capability": "image.generate", "prompt": "Synthetic image", "assets": [], "parameters": {"n": 1}}
    try:
        with pytest.raises(PlatformError): await runtime.submit(token["task_token"], "unknown", request)
        original = await runtime.submit(token["task_token"], "unknown", request)
        assert original["financial_state"] == "pending_reconciliation" and original["actual"] is None
        summary = await ledger.summary(scope)
        assert summary["reserved"] == "6" and summary["spent"] == "0" and len(seen) == 1
        await runtime.run_once()
        assert len(seen) == 1 and (await ledger.summary(scope))["reserved"] == "6"
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_real_pg_replay_after_input_expiry_only_queries_original(db, tmp_path, monkeypatch,legacy_media):
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=definition("image.edit"))
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "fixture-private", {"fixture-image": "upstream"})})
    token = await service.mint(scope, model="fixture-image", capability="image.edit", invocation_key="expiry-replay", mode="test", bindings=settings.bindings)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image()).decode()}]}, headers={"x-request-id": "expired-input-original"})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        async def chunks(): yield image()
        asset = await runtime.upload_asset(token["task_token"], chunks(), mime="image/png")
        request = {"model": "fixture-image", "capability": "image.edit", "prompt": "Synthetic edit", "assets": [asset["id"]], "parameters": {"n": 1}}
        original = await runtime.submit(token["task_token"], "original", request)
        async def unavailable(*args): raise PlatformError(404, "media_asset_unavailable")
        monkeypatch.setattr(service.raw, "get_asset", unavailable)
        def full(*args): raise PlatformError(507, "media_storage_unavailable")
        monkeypatch.setattr(runtime.store, "check_capacity", full)
        result = await runtime.submit(token["task_token"], "original", request)
        assert result["id"] == original["id"] and len(seen) == 1
        with pytest.raises(PlatformError) as caught:
            await runtime.submit(token["task_token"], "original", {**request, "prompt": "different"})
        assert caught.value.code == "idempotency_conflict"
        assert (await ledger.summary(scope))["spent"] == "2"
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_real_pg_financial_recovery_preserves_decoded_delivery_and_never_regenerates(db, tmp_path, monkeypatch,legacy_media):
    service, ledger, scope, *_ = await legacy_media.setup(db)
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "fixture-private", {"fixture-image": "upstream"})})
    token = await service.mint(scope, model="fixture-image", capability="image.generate", invocation_key="settlement-recovery", mode="test", bindings=settings.bindings)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image()).decode()}]}, headers={"x-request-id": "financial-recovery"})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    complete = service.ledger._complete
    async def fail(*args, **kwargs): raise RuntimeError("synthetic database financial outage")
    monkeypatch.setattr(service.ledger, "_complete", fail)
    try:
        request = {"model": "fixture-image", "capability": "image.generate", "prompt": "Synthetic image", "assets": [], "parameters": {"n": 1}}
        original = await runtime.submit(token["task_token"], "once", request)
        assert original["execution_state"] == "completed" and original["delivery_state"] == "available"
        assert original["financial_state"] == "pending_reconciliation" and original["actual"] is None
        assert (await runtime.artifact(scope, original["artifacts"][0]["id"], content=True))[0] == image()
        assert (await ledger.summary(scope))["reserved"] == "6"
        monkeypatch.setattr(service.ledger, "_complete", complete)
        await runtime.run_once()
        result = await runtime.submit(token["task_token"], "once", request)
        assert result["financial_state"] == "settled" and result["actual"] == "2" and len(seen) == 1
        assert (await ledger.summary(scope))["reserved"] == "0"
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_real_pg_http_sessions_task_tokens_upload_job_download_and_revocation(db, tmp_path,legacy_media):
    from platform_service.app import create_app
    from platform_service.config import Settings
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=definition("image.edit"))
    product_token = "synthetic-product-private-token"
    async with db.connection() as c:
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,'access',now()+interval '1 hour')", (hashlib.sha256(product_token.encode()).hexdigest(), scope.session_id))
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "fixture-private", {"fixture-image": "upstream"})})
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image()).decode()}]}, headers={"x-request-id": "full-http-owned"})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"), database=db, identity=service.identity, media_runtime=runtime)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as client:
            headers = {"Authorization": "Bearer " + product_token}
            catalog = await client.get("/v2/products/easel/media-models", headers=headers)
            assert catalog.status_code == 200 and catalog.json()["models"][0]["id"] == "fixture-image"
            token = await client.post("/v2/media/task-tokens", headers=headers, json={"model": "fixture-image", "capability": "image.edit", "invocation_key": "http-once"})
            assert token.status_code == 200
            task = {"Authorization": "Bearer " + token.json()["task_token"]}
            assert (await client.post("/v2/media/assets", headers={**headers, "Content-Type": "image/png"}, content=image())).status_code == 401
            uploaded = await client.post("/v2/media/assets", headers={**task, "Content-Type": "image/png"}, content=image())
            assert uploaded.status_code == 200 and "storage_key" not in uploaded.json()
            body = {"model": "fixture-image", "capability": "image.edit", "prompt": "Synthetic HTTP image", "assets": [uploaded.json()["id"]], "parameters": {"n": 1}}
            response = await client.post("/v2/media/jobs", headers={**task, "Idempotency-Key": "original"}, json=body)
            assert response.status_code == 200 and response.json()["actual"] == "2"
            job = response.json()
            repeated = await client.post("/v2/media/jobs", headers={**task, "Idempotency-Key": "original"}, json=body)
            assert repeated.json()["id"] == job["id"] and len(calls) == 1
            link = "/v2/media/artifacts/" + job["artifacts"][0]["id"] + "/content"
            assert (await client.get(link, headers=task)).status_code == 401
            content = await client.get(link, headers=headers)
            assert content.status_code == 200 and content.content == image() and content.headers["content-type"] == "image/png"
            assert (await client.get("/v2/media/jobs/" + job["id"], headers=headers)).json()["execution_state"] == "completed"
            async with db.connection() as c:
                await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (scope.session_id,))
            assert (await client.get(link, headers=headers)).status_code == 401
            assert (await client.post("/v2/media/jobs", headers={**task, "Idempotency-Key": "other"}, json=body)).status_code == 401
            assert len(calls) == 1
    finally:
        await runtime.close()
        await app.state.execution_runtime.close()


@pytest.mark.asyncio
async def test_real_pg_image_provider_token_usage_rounds_once_from_verified_response(db, tmp_path,legacy_media):
    configured = definition()
    configured["rate"] = {"version": "token-fixture", "meters": [
        {"unit": "input_token", "basis": "provider_usage_v1", "numerator": "1", "denominator": "3"},
        {"unit": "output_token", "basis": "provider_usage_v1", "numerator": "2", "denominator": "3"}]}
    configured["limits"]["max_units"] = {"input_token": 9, "output_token": 9}
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=configured)
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "fixture-private", {"fixture-image": "upstream"})})
    token = await service.mint(scope, model="fixture-image", capability="image.generate", invocation_key="metered", mode="test", bindings=settings.bindings)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image()).decode()}], "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}, headers={"x-request-id": "trusted-token-usage"})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        request = {"model": "fixture-image", "capability": "image.generate", "prompt": "Synthetic image", "assets": [], "parameters": {"n": 1}}
        result = await runtime.submit(token["task_token"], "metered-once", request)
        assert result["actual"] == "2" and result["reserved"] == "9"
        async with db.connection() as c:
            evidence = await (await c.execute("SELECT quantities,basis_evidence FROM media_terminal_evidence WHERE job_id=%s", (result["id"],))).fetchone()
            assert evidence["quantities"] == {"input_token": 2, "output_token": 1}
            assert len(evidence["basis_evidence"]["provider_usage_v1"]) == 64
        assert (await ledger.summary(scope))["spent"] == "2" and len(seen) == 1
    finally: await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["provider_capability", "meter_basis"])
async def test_real_pg_readiness_and_mint_close_unsupported_adapter_contract(db, tmp_path, mismatch,legacy_media):
    configured = definition("image.edit" if mismatch == "provider_capability" else "image.generate")
    if mismatch == "meter_basis": configured["rate"]["meters"][0]["basis"] = "provider_usage_v1"
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=configured)
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={"fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "fixture-private", {"fixture-image": "upstream"}, capabilities=("image.generate",))})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(lambda request: None))
    await legacy_media.start_runtime(runtime)
    try:
        assert (await runtime.capabilities("easel"))["test_media_execution_enabled"] is False
        assert await runtime.catalog(scope) == {"models": []}
        with pytest.raises(PlatformError):
            await runtime.mint(scope, {"model": "fixture-image", "capability": configured["capability"], "invocation_key": "unsupported"})
        assert (await ledger.summary(scope))["reserved"] == "0"
    finally: await runtime.close()


@pytest.mark.asyncio
async def test_postdispatch_logout_preserves_known_image_evidence_and_frozen_wallet_settlement(db,tmp_path,legacy_media):
    service,ledger,scope,*_=await legacy_media.setup(db)
    settings=MediaSettings(mode="test",storage_root=legacy_media.source_path,providers={"fixture":MediaProvider("1","http://127.0.0.1:9999/v1","fixture-private",{"fixture-image":"upstream"})})
    token=await service.mint(scope,model="fixture-image",capability="image.generate",invocation_key="logout-after-dispatch",mode="test",bindings=settings.bindings)
    seen=[]
    async def handler(request):
        seen.append(request)
        async with db.connection() as c:
            await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id=%s",(scope.session_id,))
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(image()).decode()}]},headers={"x-request-id":"logout-known-delivery"})
    runtime=MediaRuntime(db,service.identity,settings,service=service.raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    await legacy_media.start_runtime(runtime)
    try:
        request={"model":"fixture-image","capability":"image.generate","prompt":"Synthetic accepted image","assets":[],"parameters":{"n":1}}
        # The original HTTP result may be suppressed once the session is revoked,
        # but internal accepted work must retain actual delivery and settlement.
        try:await runtime.submit(token["task_token"],"accepted-once",request)
        except PlatformError:pass
        async with db.connection() as c:
            job=await(await c.execute("SELECT id,status,actual FROM media_jobs")).fetchone()
            artifacts=await(await c.execute("SELECT count(*) n FROM media_artifacts WHERE job_id=%s",(job["id"],))).fetchone()
            evidence=await(await c.execute("SELECT count(*) n FROM media_terminal_evidence WHERE job_id=%s",(job["id"],))).fetchone()
        assert job["status"]=="settled" and job["actual"]==2
        assert artifacts["n"]==evidence["n"]==1 and len(seen)==1
        with pytest.raises(PlatformError):await runtime.submit(token["task_token"],"new-after-logout",request)
        with pytest.raises(PlatformError):await runtime.artifact(scope,"unknown",content=True)
    finally:await runtime.close()
