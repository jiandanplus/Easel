"""A native supplier through the real runtime on the current schema: points held, evidence kept, settled once."""
import copy
import json
from uuid import uuid4

import httpx
import pytest

from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.execution_runtime import ExecutionRuntime
from platform_service.media.av_artifacts import AVTools
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.media_runtime import MediaRuntime
from test_execution_core import setup as text_setup
from test_internal_features import local_maintenance
from test_media_av_artifacts import files  # noqa: F401 (fixture)
from test_media_contracts import definition
from test_media_credits import setup as media_setup


async def build(db, tmp_path, files, monkeypatch, handler, *, adapter, capability, rate, limits, parameters, provider_extra, request_parameters):
    import test_media_credits as fixture
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    generation = await local_maintenance(db, root)
    value = definition(capability)
    value["adapter"] = adapter
    value["rate"]["meters"] = [rate]
    value["limits"].update(limits)
    value["parameters"] = parameters
    bindings = copy.deepcopy(fixture.BINDINGS)
    bindings["fixture-image"]["adapter"] = adapter
    monkeypatch.setattr(fixture, "BINDINGS", bindings)
    service, ledger, scope, *_ = await media_setup(db, media_definition=value, maintenance=generation)
    provider = MediaProvider("1", "http://127.0.0.1:19991/v1", "supplier-key", {"fixture-image": "upstream"}, adapter=adapter, capabilities=(capability,), client_correlation=True, **provider_extra)
    settings = MediaSettings(mode="test", storage_root=root, providers={"fixture": provider}, av_tools=AVTools(*files[1]))
    runtime = MediaRuntime(db, service.identity, settings, service=service, maintenance=generation, transport=httpx.MockTransport(handler))
    await runtime.start()
    async with generation.use("mint", str(uuid4()), transient=True):
        token = await service.mint(scope, model="fixture-image", capability=capability, invocation_key="direct", mode="test", bindings=settings.bindings)
    return runtime, ledger, scope, token["task_token"], {"model": "fixture-image", "capability": capability, "prompt": "合成", "assets": [], "parameters": request_parameters}


@pytest.mark.asyncio
async def test_ark_video_submits_once_polls_downloads_and_settles_on_the_real_length(db, tmp_path, files, monkeypatch):
    raw = files[0]["video"]
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            assert json.loads(request.content)["ratio"] == "16:9" and request.headers["authorization"] == "Bearer supplier-key"
            return httpx.Response(200, json={"id": "cgt-1"})
        if request.url.path == "/result/a.mp4":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=raw, headers={"content-type": "binary/octet-stream"})
        return httpx.Response(200, json={"id": "cgt-1", "status": "succeeded", "content": {"video_url": "http://127.0.0.1:19992/result/a.mp4?sig=1"}})

    runtime, ledger, scope, token, request = await build(
        db, tmp_path, files, monkeypatch, handler, adapter="ark_video_v1", capability="video.generate",
        rate={"unit": "video_ms", "basis": "verified_delivery_v1", "numerator": "1", "denominator": "1"},
        limits={"max_units": {"video_ms": 5000}, "max_assets": 0, "output_media": {"mime_types": ["video/mp4"], "max_files": 1, "max_duration_ms": 1000, "max_channels": 2, "max_sample_rate": 48000, "require_audio": False, "max_pixels": 1024}},
        parameters={"type": "object", "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 5}, "size": {"type": "string", "enum": ["1280x720"]}}, "required": ["seconds", "size"], "additionalProperties": False},
        provider_extra={"download_origins": ("http://127.0.0.1:19992",)}, request_parameters={"seconds": 1, "size": "1280x720"})
    try:
        first = await runtime.submit(token, "one", request)
        assert first["actual"] is None and (await ledger.summary(scope))["spent"] == "0"
        async with db.connection() as c:
            await c.execute("UPDATE media_work SET due_at=clock_timestamp()-interval '1 second'")
        await runtime.run_once()
        value = await runtime._view(scope, first["id"])
        assert value["financial_state"] == "settled" and value["actual"] == "500"
        assert (await runtime.artifact(scope, value["artifacts"][0]["id"], content=True))[0] == raw
        assert [item for item in seen if item[0] == "POST"] == [("POST", "/v1/contents/generations/tasks")]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_minimax_speech_settles_with_the_request_id_this_service_sent(db, tmp_path, files, monkeypatch):
    raw = files[0]["wav"]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"audio": raw.hex()}, "base_resp": {"status_code": 0}})

    runtime, ledger, scope, token, request = await build(
        db, tmp_path, files, monkeypatch, handler, adapter="minimax_speech_v1", capability="audio.speech",
        rate={"unit": "unicode_scalar", "basis": "verified_input_v1", "numerator": "1", "denominator": "1"},
        limits={"max_units": {"unicode_scalar": 100}, "max_assets": 0, "max_input_bytes": 1048576, "output_media": {"mime_types": ["audio/wav"], "max_files": 1, "max_duration_ms": 20000, "max_channels": 2, "max_sample_rate": 48000, "require_audio": True}},
        parameters={"type": "object", "properties": {"response_format": {"type": "string", "enum": ["wav"]}, "voice": {"type": "string", "enum": ["v1"]}}, "required": ["response_format"], "additionalProperties": False},
        provider_extra={"audio_profile": {"voices": ["v1"]}}, request_parameters={"response_format": "wav", "voice": "v1"})
    try:
        result = await runtime.submit(token, "once", request)
        assert result["financial_state"] == "settled" and result["actual"] == "2" and len(calls) == 1
        assert (await runtime.artifact(scope, result["artifacts"][0]["id"], content=True))[0] == raw
        async with db.connection() as c:
            evidence = await (await c.execute("SELECT * FROM media_terminal_evidence WHERE job_id=%s", (result["id"],))).fetchone()
        assert evidence["reference_kind"] == "http_request_id" and evidence["http_reference_namespace"] == "easel_client_request_v1"
        assert (await ledger.summary(scope))["spent"] == "2"
    finally:
        await runtime.close()


async def text_runtime(db, handler, *, protocol, endpoint):
    service, ledger, scope, _ = await text_setup(db)
    provider = ExecutionProvider("1", endpoint, "supplier-key", {"fixture-text": "upstream"}, protocol=protocol)
    settings = ExecutionSettings(mode="test", providers={"fixture": provider})
    runtime = ExecutionRuntime(db, service.identity, settings, service=service, transport=httpx.MockTransport(handler))
    credential = await runtime.mint(scope, {"model": "fixture-text", "invocation_key": "bridge-" + protocol})
    return runtime, ledger, scope, credential["task_token"]


async def body_of(response):
    return b"".join([chunk async for chunk in response.body_iterator])


def data(value):
    return "data: " + json.dumps(value) + "\n\n"


@pytest.mark.asyncio
async def test_chat_platform_streams_as_responses_and_settles_on_reported_tokens(db):
    def handler(request):
        sent = json.loads(request.content)
        assert request.url.path.endswith("/chat/completions") and sent["model"] == "upstream" and sent["messages"][-1] == {"role": "user", "content": "你好"}
        stream = data({"choices": [{"delta": {"content": "嗨"}}]}) + data({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + data({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}) + "data: [DONE]\n\n"
        return httpx.Response(200, content=stream.encode(), headers={"content-type": "text/event-stream"})

    runtime, ledger, scope, token = await text_runtime(db, handler, protocol="chat", endpoint="http://127.0.0.1:1/v1/chat/completions")
    try:
        response = await runtime.respond(token, "stream-1", {"model": "fixture-text", "input": "你好", "stream": True})
        text = (await body_of(response)).decode()
        assert "response.output_text.delta" in text and "response.completed" in text
        summary = await ledger.summary(scope)
        assert summary["spent"] != "0" and summary["reserved"] == "0"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_anthropic_platform_uses_its_own_key_header_and_refunds_a_broken_stream(db):
    def handler(request):
        assert request.headers["x-api-key"] == "supplier-key" and "authorization" not in request.headers and request.url.path.endswith("/messages")
        stream = "event: message_start\n" + data({"type": "message_start", "message": {"usage": {"input_tokens": 1}}})
        return httpx.Response(200, content=stream.encode(), headers={"content-type": "text/event-stream"})

    runtime, ledger, scope, token = await text_runtime(db, handler, protocol="anthropic", endpoint="http://127.0.0.1:1/v1/messages")
    try:
        response = await runtime.respond(token, "stream-2", {"model": "fixture-text", "input": "你好", "stream": True})
        text = (await body_of(response)).decode()
        assert "invalid_upstream_usage" in text and (await ledger.summary(scope))["spent"] == "0"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_a_tool_type_the_supplier_cannot_take_is_refused_before_any_call(db):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    runtime, ledger, scope, token = await text_runtime(db, handler, protocol="chat", endpoint="http://127.0.0.1:1/v1/chat/completions")
    try:
        from platform_service.errors import PlatformError
        with pytest.raises(PlatformError):
            await runtime.respond(token, "tool-1", {"model": "fixture-text", "input": "x", "tools": [{"type": "custom", "name": "patch"}]})
        summary = await ledger.summary(scope)
        assert not calls and summary["reserved"] == "0" and summary["spent"] == "0"
    finally:
        await runtime.close()
