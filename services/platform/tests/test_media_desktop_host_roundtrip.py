"""Desktop host → actual central HTTP → PostgreSQL → verified file.

Only the provider transport is synthetic. Never reads production credentials.
"""
import asyncio
import base64
from dataclasses import asdict
import hashlib
import io
import json
import time

import httpx
from PIL import Image
import pytest
from b2_legacy_media_fixtures import legacy_media, legacy_db as db, legacy_dsn as test_dsn

from platform_service.app import create_app
from platform_service.config import Settings
from platform_service.media_config import MediaSettings, MediaProvider
from platform_service.media_runtime import MediaRuntime
from test_media_credits import setup
from test_media_contracts import definition


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "timeout", "wrong_size"])
async def test_desktop_media_owned_http_postgres_exact_charge_or_unknown_once(db, tmp_path, case,legacy_media):
    from easel.harnesses.base import RunRequest
    from easel.platform.central import CentralClient
    from easel.platform.media import MediaClient, MediaHost, MediaError

    configured = definition()
    configured["limits"].update(max_assets=0, max_artifact_bytes=20971520, max_units={"image": 1})
    configured["parameters"] = {"type": "object", "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 1},
        "size": {"type": "string", "enum": ["1024x1024"]}},
        "required": ["n", "size"], "additionalProperties": False}
    configured["rate"] = {"version": "synthetic-one-point", "meters": [
        {"unit": "image", "basis": "verified_delivery_v1", "numerator": "1000000", "denominator": "1"}]}
    service, ledger, scope, *_ = await legacy_media.setup(db, media_definition=configured)
    product_token = "synthetic-desktop-product-access-token"
    async with db.connection() as c:
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,%s,'access',now()+interval '1 hour')",
                        (hashlib.sha256(product_token.encode()).hexdigest(), scope.session_id))
    uncertain = case != "success"
    image = io.BytesIO(); Image.new("RGB", (7, 4) if case == "wrong_size" else (1024, 1024), "blue").save(image, format="PNG"); raw = image.getvalue()
    provider_calls = []
    async def provider(request):
        provider_calls.append(request)
        if case == "timeout": raise httpx.ReadTimeout("synthetic provider unknown", request=request)
        return httpx.Response(200, json={"created": 1793052744, "data": [{"b64_json": base64.b64encode(raw).decode()}]},
                              headers={"X-Oneapi-Request-Id": "20261027021224728516470abcdefghijklmnop"})
    settings = MediaSettings(mode="test", storage_root=legacy_media.source_path, providers={
        "fixture": MediaProvider("1", "http://127.0.0.1:9999/v1", "synthetic-provider-secret",
                                 {"fixture-image": "synthetic-upstream"}, response_profile="newapi_images_v1", capabilities=("image.generate",))})
    runtime = MediaRuntime(db, service.identity, settings, service=service.raw, maintenance=legacy_media.generation, transport=httpx.MockTransport(provider))
    await legacy_media.start_runtime(runtime)
    app = create_app(Settings("postgresql://localhost/unused", "https://accounts.example"),
                     database=db, identity=service.identity, media_runtime=runtime)
    loop = asyncio.get_running_loop()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://accounts.example") as server:
            central_calls = []
            safe_responses = []
            async def forward(request):
                central_calls.append((request.method, request.url.path))
                response = await server.request(request.method, request.url.path + ("?" + request.url.query.decode() if request.url.query else ""),
                                                headers=dict(request.headers), content=request.content)
                if request.url.path == "/v2/media/jobs":
                    value = response.json()
                    safe_responses.append({"status": response.status_code, "body_keys": sorted(value),
                        "error_code": (value.get("error") or {}).get("code"),
                        "job": {k: value[k] for k in ("id", "status", "financial_state", "execution_state", "delivery_state", "actual", "reserved", "artifacts") if k in value}})
                return httpx.Response(response.status_code, headers=response.headers, content=response.content, request=request)
            def bridge(request): return asyncio.run_coroutine_threadsafe(forward(request), loop).result(30)
            workspace = tmp_path / "desktop"; workspace.mkdir()
            central = CentralClient(workspace, transport=httpx.MockTransport(bridge), url="https://accounts.example")
            central.save_auth({"access_token": product_token, "refresh_token": "synthetic-refresh-private",
                "expires_at": time.time()+600, "refresh_expires_at": time.time()+1200,
                "session_id": scope.session_id, "scope": asdict(scope),
                "account": {"id": scope.user_id, "email": None, "email_verified": False}})
            client = MediaClient(workspace, central=central)
            request = RunRequest("Synthetic image acceptance", workspace, {}, model="fixture-text", service_source="subscription",
                subscription_backend="central", account_id=scope.user_id, platform_url="https://accounts.example",
                central_scope=asdict(scope), invocation_key="accepted-desktop-turn", session_key="desktop-session", turn_id="desktop-turn")
            host = MediaHost(request, client=client)
            before = await ledger.summary(scope)
            assert (await asyncio.to_thread(client.catalog))["models"][0]["id"] == "fixture-image"
            arguments = {"model": "fixture-image", "prompt": "One synthetic blue circle"}
            first = await asyncio.to_thread(host.execute, "create_media", arguments, "image-tool-call")
            if uncertain:
                assert not first["ok"] and first["local_status"] == "unknown"
            else:
                assert first["ok"] and first["job"]["actual"] == "1000000", (first, central_calls, len(provider_calls), safe_responses)
                artifact = first["job"]["artifacts"][0]
                downloaded = await asyncio.to_thread(host.execute, "download_media_artifact", {
                    "job_id": first["job"]["id"], "artifact_id": artifact["id"], "path": "outputs/verified.png"}, "download-call")
                assert downloaded["ok"], json.dumps({"result": downloaded, "routes": central_calls}, sort_keys=True)
                assert (workspace / downloaded["path"]).read_bytes() == raw
            repeated = await asyncio.to_thread(MediaHost(request, client=client).execute, "create_media", arguments, "image-tool-call")
            assert repeated["ok"]
            job = repeated["job"]
            assert job["actual"] == (None if uncertain else "1000000")
            assert job["financial_state"] == ("pending_reconciliation" if uncertain else "settled")
            if case == "wrong_size": assert job["artifacts"] == []
            blocked = await asyncio.to_thread(host.execute, "create_media", {**arguments, "prompt": "another paid request"}, "new-tool-call")
            assert not blocked["ok"]
            assert len(provider_calls) == 1 and central_calls.count(("POST", "/v2/media/jobs")) == 1
            assert central_calls.count(("POST", "/v2/media/task-tokens")) == 1
            recovered = await asyncio.to_thread(client.recoveries, "desktop-session", "desktop-turn")
            assert recovered["recoveries"][0]["job"]["id"] == job["id"]
            after = await ledger.summary(scope)
            assert int(after["spent"]) - int(before["spent"]) == (0 if uncertain else 1000000)
            assert after["reserved"] == ("1000000" if uncertain else "0")
            async with db.connection() as c:
                assert (await (await c.execute("SELECT count(*) n FROM media_provider_attempts")).fetchone())["n"] == 1
                if not uncertain:
                    evidence = await (await c.execute("SELECT quantities,provider_response_id,reference_kind FROM media_terminal_evidence WHERE job_id=%s", (job["id"],))).fetchone()
                    assert evidence["quantities"] == {"image": 1} and evidence["provider_response_id"] is None
                    assert evidence["reference_kind"] == "http_request_id"
                await c.execute("UPDATE identity_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (scope.session_id,))
            with pytest.raises(MediaError): await asyncio.to_thread(client.recoveries, "desktop-session", "desktop-turn")
            assert len(provider_calls) == 1
            records = list((workspace / "outputs/_harness/central-media/operations").glob("turn-*.json"))
            assert len(records) == 1
            private = records[0].read_text()
            assert product_token not in private and "One synthetic blue circle" not in private and "synthetic-provider-secret" not in private
    finally:
        await runtime.close()
        await app.state.execution_runtime.close()
