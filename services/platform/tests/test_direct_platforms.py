"""Each supplier's own protocol, checked against the shapes in its documentation."""
import base64
import hashlib
import hmac
import json
import wave
import io
from types import SimpleNamespace

import httpx
import pytest

from platform_service.errors import PlatformError
from platform_service.execution_bridges import AnthropicBridge, ChatBridge, anthropic_request, chat_request
from platform_service.media.adapters.direct_async import DIRECT_ASYNC
from platform_service.media.adapters.direct_speech import DIRECT_SPEECH
from platform_service.media.adapters.audio import AudioProfile, OpenAISpeechAdapter, OpenAITranscriptionsAdapter
from platform_service.media.adapters.newapi_async import make_async_adapter
from platform_service.media_config import MediaProvider


def provider(adapter, base, secret="key-1", **extra):
    capability = "music.generate" if "music" in adapter or "suno" in adapter else "video.generate"
    return MediaProvider(version="console-1", base_url=base, secret=secret, models={"m": "upstream"}, adapter=adapter, capabilities=(capability,), client_correlation=True, **extra)


VIDEO = {"capability": "video.generate", "model": "m", "prompt": "海边", "parameters": {"seconds": 5, "size": "1280x720"}}
MUSIC = {"capability": "music.generate", "model": "m", "prompt": "轻快", "parameters": {"instrumental": True}}


def body(prepared):
    return json.loads(prepared.payload)


def test_dashscope_video_follows_both_parameter_generations():
    old = make_async_adapter(provider("dashscope_video_v1", "https://dashscope.aliyuncs.com/api/v1"), "live", {"model": "m"})
    prepared = old.prepare(VIDEO, [])
    assert prepared.url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert body(prepared)["parameters"] == {"duration": 5, "size": "1280*720"} and old.submit_headers == {"X-DashScope-Async": "enable"}
    new = DIRECT_ASYNC["dashscope_video_v1"](MediaProvider(version="1", base_url="https://d.aliyuncs.com/api/v1", secret="k", models={"m": "wan2.7-t2v"}, adapter="dashscope_video_v1", capabilities=("video.generate",)), "live")
    assert body(new.prepare(VIDEO, []))["parameters"] == {"duration": 5, "resolution": "720P", "ratio": "16:9"}
    assert old.observe({"output": {"task_id": "t", "task_status": "RUNNING"}}, "s", "t").state == "running"
    done = old.observe({"output": {"task_id": "t", "task_status": "SUCCEEDED", "video_url": "https://r.oss-cn-beijing.aliyuncs.com/a.mp4?sig=1"}}, "s", "t")
    assert done.state == "completed" and done.sources[0].kind == "url" and old.query_url("t").endswith("/api/v1/tasks/t")
    with pytest.raises(PlatformError):
        old.observe({"output": {"task_status": "UNKNOWN"}}, "s", "t")


def test_download_hosts_are_limited_to_the_supplier():
    adapter = DIRECT_ASYNC["dashscope_video_v1"](provider("dashscope_video_v1", "https://d.example/api/v1", download_origins=("https://cdn.example",)), "live")
    assert adapter.source_url("https://cdn.example/a.mp4")
    for bad in ("https://evil.example/a.mp4", "http://r.aliyuncs.com/a.mp4", "https://aliyuncs.com.evil.example/a.mp4", None):
        with pytest.raises(PlatformError):
            adapter.source_url(bad)


def test_ark_video():
    adapter = DIRECT_ASYNC["ark_video_v1"](provider("ark_video_v1", "https://ark.cn-beijing.volces.com/api/v3"), "live")
    prepared = adapter.prepare(VIDEO, [])
    assert prepared.url.endswith("/api/v3/contents/generations/tasks") and body(prepared)["ratio"] == "16:9" and body(prepared)["content"][0]["text"] == "海边"
    done = adapter.observe({"id": "cgt-1", "status": "succeeded", "content": {"video_url": "https://x.tos-cn-beijing.volces.com/a.mp4"}}, "s", "cgt-1")
    assert done.state == "completed" and adapter.query_url("cgt-1").endswith("/tasks/cgt-1")
    assert adapter.observe({"id": "cgt-1", "status": "expired"}, "s", "cgt-1").state == "failed"


def test_kling_signs_a_token_and_limits_duration():
    adapter = DIRECT_ASYNC["kling_video_v1"](provider("kling_video_v1", "https://api.klingai.com", secret="ak-1|sk-1"), "live")
    head, claims, signature = adapter._auth()["Authorization"].removeprefix("Bearer ").split(".")
    assert json.loads(base64.urlsafe_b64decode(claims + "==="))["iss"] == "ak-1"
    expected = hmac.new(b"sk-1", f"{head}.{claims}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64encode(expected).rstrip(b"=").decode() == signature
    prepared = adapter.prepare(VIDEO, [])
    assert body(prepared) == {"model_name": "upstream", "prompt": "海边", "duration": "5", "aspect_ratio": "16:9"} and prepared.url.endswith("/v1/videos/text2video")
    with pytest.raises(PlatformError):
        adapter.prepare({**VIDEO, "parameters": {"seconds": 7, "size": "1280x720"}}, [])
    done = adapter.observe({"code": 0, "data": {"task_id": "9", "task_status": "succeed", "task_result": {"videos": [{"url": "https://v15.klingai.com/a.mp4"}]}}}, "s", "9")
    assert done.state == "completed"
    with pytest.raises(PlatformError):
        adapter.observe({"code": 1200, "data": {"task_status": "succeed"}}, "s", "9")


def test_agnes_polls_with_the_upstream_model():
    adapter = make_async_adapter(provider("agnes_video_v1", "https://apihub.agnes-ai.com/v1"), "live", {"model": "m"})
    assert adapter.query_url("v1") == "https://apihub.agnes-ai.com/agnesapi?video_id=v1&model_name=upstream"
    assert adapter.observe({"status": "completed", "url": "https://a.agnes-ai.com/x.mp4"}, "s", "v1").state == "completed"
    assert body(adapter.prepare(VIDEO, []))["mode"] == "text"


def test_suno_style_answers_are_read_inside_their_envelope():
    adapter = DIRECT_ASYNC["suno_direct_v1"](provider("suno_direct_v1", "https://s.example/api"), "live")
    prepared = adapter.prepare(MUSIC, [])
    assert prepared.url == "https://s.example/api/generate" and body(prepared)["make_instrumental"] is True
    assert adapter._job_id({"data": [{"id": "s1"}]}, submit=True) == "s1" and adapter.query_url("s1").endswith("/feed/s1")
    assert adapter.observe({"data": {"status": "streaming"}}, "s", "s1").state == "running"
    done = adapter.observe({"data": [{"status": "complete", "audio_url": "https://cdn1.suno.ai/a.mp3"}]}, "s", "s1")
    assert done.state == "completed" and done.sources[0].location.endswith("a.mp3")
    assert adapter.observe({"status": "failed"}, "s", "s1").state == "failed"


def test_dashscope_music():
    adapter = DIRECT_ASYNC["dashscope_music_v1"](provider("dashscope_music_v1", "https://dashscope.aliyuncs.com/api/v1"), "live")
    prepared = adapter.prepare({**MUSIC, "parameters": {"lyrics": "第一句歌词"}}, [])
    assert prepared.url.endswith("/services/audio/music/generation") and body(prepared)["input"] == {"prompt": "轻快", "is_instrumental": False, "lyrics": "第一句歌词"}
    done = adapter.observe({"output": {"task_status": "SUCCEEDED", "finish_reason": "stop", "audio": {"url": "https://r.oss.aliyuncs.com/a.mp3"}}, "usage": {"duration": 30}}, "s", "t")
    assert done.state == "completed" and done.quantities == {"audio_ms": 30000}


@pytest.mark.asyncio
async def test_object_store_downloads_keep_a_checked_type_and_send_no_key():
    adapter = DIRECT_ASYNC["ark_video_v1"](provider("ark_video_v1", "https://a.volces.com/api/v3"), "live")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"video", headers={"content-type": "binary/octet-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = adapter.url_source("https://x.volces.com/a.mp4")
        assert await adapter.retrieve(client, source, max_bytes=100) == (b"video", "video/mp4")
    assert seen["auth"] is None


SPEECH = {"capability": "audio.speech", "model": "m", "prompt": "你好", "parameters": {"voice": "v1", "response_format": "mp3", "speed_percent": 150}}


def speaker(adapter, base, **extra):
    return DIRECT_SPEECH[adapter](MediaProvider(version="console-1", base_url=base, secret="key-1", models={"m": "upstream"}, adapter=adapter, capabilities=("audio.speech",), audio_profile={"voices": ["v1"]}, client_correlation=True, **extra), "live", AudioProfile(voices=("v1",)))


async def run(adapter, prepared, handler):
    headers = {}

    async def on_headers(**values):
        headers.update(values)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await adapter.submit(client, prepared, request_id="req-local-1", max_bytes=1_000_000, on_headers=on_headers)
    assert headers == {"http_status": 200, "http_request_id": "req-local-1"} and result.correlation.namespace == "easel_client_request_v1"
    return result


@pytest.mark.asyncio
async def test_minimax_speech_decodes_hex():
    adapter = speaker("minimax_speech_v1", "https://api.minimax.chat", platform_options={"group_id": "g1"})
    prepared = adapter.prepare(SPEECH, [])
    assert prepared.url == "https://api.minimax.chat/v1/t2a_v2?GroupId=g1" and body(prepared)["voice_setting"] == {"voice_id": "v1", "speed": 1.5}
    result = await run(adapter, prepared, lambda r: httpx.Response(200, json={"data": {"audio": "aabb"}, "base_resp": {"status_code": 0}}))
    assert result.payload == b"\xaa\xbb" and result.mime == "audio/mpeg"
    with pytest.raises(PlatformError):
        await run(adapter, prepared, lambda r: httpx.Response(200, json={"base_resp": {"status_code": 1004}}))


@pytest.mark.asyncio
async def test_cosyvoice_downloads_only_from_alibaba_storage():
    adapter = speaker("dashscope_speech_v1", "https://dashscope.aliyuncs.com")
    prepared = adapter.prepare(SPEECH, [])
    assert prepared.url.endswith("/api/v1/services/audio/tts/SpeechSynthesizer") and body(prepared)["input"]["voice"] == "v1"

    def handler(request):
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"mp3-bytes")
        return httpx.Response(200, json={"output": {"audio": {"url": "https://a.oss-cn-beijing.aliyuncs.com/x.mp3"}, "finish_reason": "stop"}})

    assert (await run(adapter, prepared, handler)).payload == b"mp3-bytes"
    with pytest.raises(PlatformError):
        await run(adapter, prepared, lambda r: httpx.Response(200, json={"output": {"audio": {"url": "https://evil.example/x.mp3"}}}))


@pytest.mark.asyncio
async def test_fish_returns_bytes_and_names_the_voice():
    adapter = speaker("fish_speech_v1", "https://api.fish.audio")
    prepared = adapter.prepare(SPEECH, [])
    assert body(prepared)["reference_id"] == "v1"

    def handler(request):
        assert request.headers["model"] == "upstream" and request.url.path == "/v1/tts"
        return httpx.Response(200, content=b"audio")

    assert (await run(adapter, prepared, handler)).payload == b"audio"


@pytest.mark.asyncio
async def test_gemini_wraps_raw_audio_as_wav():
    adapter = speaker("gemini_speech_v1", "https://generativelanguage.googleapis.com/v1beta")
    with pytest.raises(PlatformError):
        adapter.prepare(SPEECH, [])  # Gemini cannot produce mp3.
    prepared = adapter.prepare({**SPEECH, "parameters": {"voice": "v1", "response_format": "wav"}}, [])
    assert prepared.url.endswith("/models/upstream:generateContent")

    def handler(request):
        assert request.headers["x-goog-api-key"] == "key-1" and "authorization" not in request.headers
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=16000", "data": base64.b64encode(b"\0\0" * 100).decode()}}]}}]})

    result = await run(adapter, prepared, handler)
    with wave.open(io.BytesIO(result.payload)) as audio:
        assert audio.getframerate() == 16000 and audio.getnframes() == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,expected", [(True, "easel_client_request_v1"), (False, None)])
async def test_openai_compatible_audio_no_longer_requires_openai_request_ids(flag, expected):
    settings = MediaProvider(version="1", base_url="https://api.siliconflow.cn/v1", secret="k", models={"m": "u"}, adapter="openai_speech_v1", capabilities=("audio.speech",), audio_profile={"voices": ["v1"]}, client_correlation=flag)
    adapter = OpenAISpeechAdapter(settings, "live", AudioProfile(voices=("v1",)))
    prepared = adapter.prepare(SPEECH, [])
    assert ("stream_format" in body(prepared)) is (not flag)

    async def on_headers(**values):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x", headers={"content-type": "audio/mpeg", "x-request-id": "not-openai"}))) as client:
        if not flag:
            with pytest.raises(PlatformError):
                await adapter.submit(client, prepared, request_id="local-1", max_bytes=100, on_headers=on_headers)
            return
        result = await adapter.submit(client, prepared, request_id="local-1", max_bytes=100, on_headers=on_headers)
    assert result.correlation.namespace == expected


TEXT = {"model": "up", "stream": True, "max_output_tokens": 100, "instructions": "规则",
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "查天气"}]},
                  {"type": "reasoning", "summary": []},
                  {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "好的"}]},
                  {"type": "function_call", "call_id": "c1", "name": "get", "arguments": "{\"a\":1}"},
                  {"type": "function_call_output", "call_id": "c1", "output": "晴"}],
        "tools": [{"type": "function", "name": "get", "description": "d", "parameters": {"type": "object", "properties": {}}}], "tool_choice": "required"}


def test_chat_request_keeps_the_conversation_order():
    value = chat_request(TEXT)
    assert [m["role"] for m in value["messages"]] == ["system", "user", "assistant", "tool"]
    assert value["messages"][2]["content"] == "好的" and value["messages"][2]["tool_calls"][0]["function"]["name"] == "get"
    assert value["max_tokens"] == 100 and value["stream_options"] == {"include_usage": True} and value["tool_choice"] == "required"
    assert value["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}
    with pytest.raises(PlatformError):
        chat_request({**TEXT, "tools": [{"type": "custom", "name": "x"}]})


def test_anthropic_request_merges_turns_and_maps_tools():
    value = anthropic_request(TEXT)
    assert value["system"] == "规则" and [m["role"] for m in value["messages"]] == ["user", "assistant", "user"]
    assert value["messages"][1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "get", "input": {"a": 1}}
    assert value["messages"][2]["content"][0]["tool_use_id"] == "c1" and value["tool_choice"] == {"type": "any"}
    assert value["tools"][0]["input_schema"] == {"type": "object", "properties": {}}


async def chunks(lines):
    for line in lines:
        yield line.encode()


async def collect(generator):
    events = []
    async for raw in generator:
        events.append(json.loads(raw.decode().split("data: ", 1)[1]))
    return events


def line(value):
    return "data: " + json.dumps(value) + "\n\n"


@pytest.mark.asyncio
async def test_chat_stream_becomes_responses_events_with_usage():
    events = await collect(ChatBridge.stream(chunks([
        line({"choices": [{"delta": {"content": "你"}}]}), line({"choices": [{"delta": {"content": "好"}}]}),
        line({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get", "arguments": "{\"a\""}}]}}]}),
        line({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]}, "finish_reason": "tool_calls"}]}),
        line({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}), "data: [DONE]\n\n"]), "public"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "response.created" and kinds[-1] == "response.completed" and "response.function_call_arguments.delta" in kinds
    final = events[-1]["response"]
    assert final["usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10} and final["model"] == "public"
    assert final["output"][0]["content"][0]["text"] == "你好" and final["output"][1]["arguments"] == "{\"a\":1}"


@pytest.mark.asyncio
async def test_a_chat_stream_without_usage_can_never_settle():
    with pytest.raises(PlatformError) as error:
        await collect(ChatBridge.stream(chunks([line({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}), "data: [DONE]\n\n"]), "m"))
    assert error.value.code == "invalid_upstream_usage"


@pytest.mark.asyncio
async def test_length_limit_is_incomplete():
    events = await collect(ChatBridge.stream(chunks([line({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})]), "m"))
    assert events[-1]["type"] == "response.incomplete" and events[-1]["response"]["status"] == "incomplete"


def typed(kind, **fields):
    return "event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n"


@pytest.mark.asyncio
async def test_anthropic_stream_counts_cached_input():
    events = await collect(AnthropicBridge.stream(chunks([
        typed("message_start", message={"usage": {"input_tokens": 5, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 1}}),
        typed("content_block_start", index=0, content_block={"type": "text", "text": ""}), typed("ping"),
        typed("content_block_delta", index=0, delta={"type": "text_delta", "text": "嗨"}), typed("content_block_stop", index=0),
        typed("content_block_start", index=1, content_block={"type": "tool_use", "id": "t1", "name": "get"}),
        typed("content_block_delta", index=1, delta={"type": "input_json_delta", "partial_json": "{}"}),
        typed("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 4}), typed("message_stop")]), "m"))
    final = events[-1]["response"]
    assert events[-1]["type"] == "response.completed" and final["usage"]["input_tokens"] == 16 and final["usage"]["output_tokens"] == 4
    assert final["output"][1]["call_id"] == "t1"


def test_plain_json_replies_convert_too():
    chat = ChatBridge.response({"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}, "m")
    assert chat["status"] == "completed" and chat["usage"]["total_tokens"] == 3
    claude = AnthropicBridge.response({"content": [{"type": "text", "text": "好"}], "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}}, "m")
    assert claude["output"][0]["content"][0]["text"] == "好"
    with pytest.raises(PlatformError):
        ChatBridge.response({"choices": [{"message": {"content": "好"}}]}, "m")
