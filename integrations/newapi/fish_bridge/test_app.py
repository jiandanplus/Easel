import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path
import secrets
import stat

import httpx
import msgpack
import pytest

try:
    from integrations.newapi.fish_bridge.app import Config, create_app
except ModuleNotFoundError:
    Config = create_app = None


SECRET_A = secrets.token_urlsafe(32)
SECRET_B = secrets.token_urlsafe(32)
TASK = "task_" + "a" * 32


def config(tmp_path, **values):
    assert Config is not None, "Fish bridge configuration is not implemented"
    return Config(
        storage_dir=tmp_path / "private",
        internal_secrets=(SECRET_A, SECRET_B),
        fish_api_key="fixture-supplier-key",
        allowed_reference_ids=frozenset({"admin_preset"}),
        **values,
    )


def headers(secret=SECRET_A, key=TASK):
    return {"Authorization": "Bearer " + secret, "Idempotency-Key": key}


def body(**values):
    return {"text": "Hello", "model": "s2.1-pro", "format": "mp3", "reference_id": "admin_preset", **values}


@asynccontextmanager
async def running(settings, handler, clock=lambda: 1000):
    assert create_app is not None, "Fish bridge application is not implemented"
    app = create_app(settings, transport=httpx.MockTransport(handler), clock=clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge") as client:
            yield client


def test_missing_or_weak_secrets_prevent_startup(tmp_path, monkeypatch):
    assert Config is not None, "Fish bridge configuration is not implemented"
    for key in ["FISH_API_KEY", "FISH_BRIDGE_INTERNAL_SECRETS"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        Config.from_env()
    with pytest.raises(ValueError):
        config(tmp_path, max_concurrency=0)


def test_authentication_precedes_request_parsing_and_upstream(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            response = await client.post("/v1/tts", content="not json")
            assert response.status_code == 401
        assert not calls

    asyncio.run(scenario())


def test_preset_json_round_trip_head_and_ranges(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        assert str(request.url) == "https://api.fish.audio/v1/tts"
        assert request.headers["Authorization"] == "Bearer fixture-supplier-key"
        assert request.headers["model"] == "s2.1-pro"
        assert request.headers["Content-Type"] == "application/json"
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            response = await client.post("/v1/tts", headers=headers(), json=body())
            assert response.status_code == 200
            result = response.json()
            assert result["state"] == "completed"
            assert result["request_count"] == 1
            assert result["input_characters"] == 5
            assert result["size"] == 8
            path = "/v1/artifacts/" + result["artifact_id"]
            get = await client.get(path, headers=headers())
            assert get.content == b"ID3audio"
            head = await client.head(path, headers=headers())
            assert head.content == b""
            assert head.headers["Content-Length"] == "8"
            sliced = await client.get(path, headers={**headers(), "Range": "bytes=0-2"})
            assert sliced.status_code == 206
            assert sliced.content == b"ID3"
            assert sliced.headers["Content-Range"] == "bytes 0-2/8"
            wrong = await client.get(path, headers=headers(SECRET_B))
            assert wrong.status_code == 404
            assert (await client.get(path)).status_code == 401
        assert len(calls) == 1
        root = tmp_path / "private"
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        for path in root.iterdir():
            assert not path.is_symlink()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    asyncio.run(scenario())


def test_inline_sample_is_messagepack_binary(tmp_path):
    def upstream(request):
        assert request.headers["Content-Type"] == "application/msgpack"
        payload = msgpack.unpackb(request.content, raw=False)
        assert payload["references"] == [{"audio": b"RIFFsample", "text": "sample words"}]
        assert "reference_id" not in payload
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        value = body()
        value.pop("reference_id")
        value["references"] = [{"audio": base64.b64encode(b"RIFFsample").decode(), "text": "sample words"}]
        async with running(config(tmp_path), upstream) as client:
            response = await client.post("/v1/tts", headers=headers(), json=value)
            assert response.status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"reference_id": "foreign_voice"},
    {"url": "https://evil.example/sample"},
    {"model": "../../other"},
    {"stream": True},
    {"text": "x" * 2001},
    {"references": [{"audio": "not base64", "text": "test"}], "reference_id": None},
])
def test_invalid_or_foreign_requests_never_reach_supplier(tmp_path, changes):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            response = await client.post("/v1/tts", headers=headers(), json=body(**changes))
            assert response.status_code in {403, 422}
        assert not calls

    asyncio.run(scenario())


def test_idempotency_replays_exact_completed_json_and_rejects_input_change(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            first = await client.post("/v1/tts", headers=headers(), json=body())
            again = await client.post("/v1/tts", headers=headers(), json=body())
            assert first.json() == again.json()
            assert (await client.post("/v1/tts", headers=headers(), json=body(text="changed"))).status_code == 409
        assert len(calls) == 1
        # The receipt survives process restart without another supplier POST.
        async with running(config(tmp_path), upstream) as client:
            third = await client.post("/v1/tts", headers=headers(), json=body())
            assert third.json() == first.json()
        assert len(calls) == 1

    asyncio.run(scenario())


def test_same_public_task_cannot_recharge_through_another_secret(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            assert (await client.post("/v1/tts", headers=headers(), json=body())).status_code == 200
            second = await client.post("/v1/tts", headers=headers(SECRET_B), json=body())
            assert second.status_code == 409
        assert len(calls) == 1

    asyncio.run(scenario())


def test_concurrent_same_key_has_one_supplier_call(tmp_path):
    calls = []

    async def upstream(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path), upstream) as client:
            responses = await asyncio.gather(*[client.post("/v1/tts", headers=headers(), json=body()) for _ in range(2)])
            assert responses[0].json() == responses[1].json()
            assert all(response.status_code == 200 for response in responses)
        assert len(calls) == 1

    asyncio.run(scenario())


def test_concurrency_admission_precedes_body_parsing(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def upstream(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path, max_concurrency=1), upstream) as client:
            first = asyncio.create_task(client.post("/v1/tts", headers=headers(), json=body()))
            await entered.wait()
            second = await client.post("/v1/tts", headers=headers(key="task_" + "b" * 32), content="invalid large body")
            release.set()
            await first
            assert second.status_code == 429

    asyncio.run(scenario())


def test_restart_removes_orphan_files_without_deleting_receipts(tmp_path):
    def upstream(request):
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        settings = config(tmp_path)
        async with running(settings, upstream):
            pass
        orphan = settings.storage_dir / ("f" * 64 + ".part")
        orphan.write_bytes(b"orphan")
        orphan.chmod(0o600)
        async with running(settings, upstream):
            assert not orphan.exists()

    asyncio.run(scenario())


def test_single_process_store_lock_prevents_unsafe_shared_volume_workers(tmp_path):
    def upstream(request):
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        settings = config(tmp_path)
        async with running(settings, upstream):
            with pytest.raises(ValueError, match="one process"):
                create_app(settings)

    asyncio.run(scenario())


def test_unknown_consumption_stays_pending_across_retries_and_restart(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        raise httpx.ReadTimeout("do not expose this upstream secret")

    async def scenario():
        for _ in range(2):
            async with running(config(tmp_path), upstream) as client:
                response = await client.post("/v1/tts", headers=headers(), json=body())
                assert response.status_code == 202
                assert response.json()["state"] == "pending"
                assert "secret" not in response.text
                status = await client.get("/v1/operations/" + TASK, headers=headers())
                assert status.json()["state"] == "pending"
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b'{"error":"sensitive supplier content"}', headers={"Content-Type": "application/json"}),
    httpx.Response(401, content=b"sensitive supplier content"),
    httpx.Response(200, content=b"x" * 100, headers={"Content-Type": "audio/mpeg"}),
])
def test_upstream_errors_or_oversize_are_sanitized_and_never_reposted(tmp_path, response):
    calls = []

    def upstream(request):
        calls.append(request)
        return response

    async def scenario():
        async with running(config(tmp_path, max_audio_bytes=16), upstream) as client:
            for _ in range(2):
                result = await client.post("/v1/tts", headers=headers(), json=body())
                assert result.status_code == 202
                assert result.json()["state"] == "pending"
                assert "sensitive supplier" not in result.text
        assert len(calls) == 1

    asyncio.run(scenario())


def test_expiry_removes_audio_but_retains_no_replay_tombstone(tmp_path):
    now = [1000]
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path, artifact_ttl_seconds=10), upstream, clock=lambda: now[0]) as client:
            result = (await client.post("/v1/tts", headers=headers(), json=body())).json()
            now[0] += 11
            assert (await client.get("/v1/artifacts/" + result["artifact_id"], headers=headers())).status_code == 404
            again = await client.post("/v1/tts", headers=headers(), json=body())
            assert again.json()["state"] == "expired"
        assert len(calls) == 1
        assert not list((tmp_path / "private").glob("*.audio"))

    asyncio.run(scenario())


def test_private_store_rejects_symlink_and_full_receipt_capacity(tmp_path):
    def upstream(request):
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path, max_receipts=1), upstream) as client:
            assert (await client.post("/v1/tts", headers=headers(), json=body())).status_code == 200
            different = "task_" + "b" * 32
            assert (await client.post("/v1/tts", headers=headers(key=different), json=body())).status_code == 503
        wrong = tmp_path / "wrong"
        wrong.symlink_to(tmp_path / "private", target_is_directory=True)
        with pytest.raises(ValueError):
            create_app(Config(storage_dir=wrong, internal_secrets=(SECRET_A,), fish_api_key="fixture-supplier-key"))

    asyncio.run(scenario())


def test_http_request_sample_and_range_limits(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})

    async def scenario():
        async with running(config(tmp_path, max_request_bytes=1024, max_sample_bytes=4), upstream) as client:
            large = await client.post("/v1/tts", headers=headers(), content=b"x" * 1025)
            assert large.status_code == 415
            large_json = await client.post("/v1/tts", headers={**headers(), "Content-Type": "application/json"}, content=b"x" * 1025)
            assert large_json.status_code == 413
            value = body()
            value.pop("reference_id")
            value["references"] = [{"audio": base64.b64encode(b"RIFFsample").decode(), "text": "sample"}]
            assert (await client.post("/v1/tts", headers=headers(), json=value)).status_code == 422
            result = (await client.post("/v1/tts", headers=headers(), json=body())).json()
            path = "/v1/artifacts/" + result["artifact_id"]
            assert (await client.get(path, headers={**headers(), "Range": "bytes=" + "9" * 5000 + "-"})).status_code == 416
            assert (await client.get(path, headers={**headers(), "Range": "bytes=0-2,4-5"})).status_code == 416
        assert len(calls) == 1

    asyncio.run(scenario())
