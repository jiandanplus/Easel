"""Native media HTTP fixtures: no commercial provider requests in this suite."""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import io
import json
import struct
import time
from uuid import uuid4
import zlib

import httpx
from PIL import Image
import pytest

from easel.harnesses.base import RunRequest
from tests.test_central_platform_client import auth


MODEL = "gpt-image-2.5-flare"
ORIGIN = "http://127.0.0.1:8791"


def definition():
    return {"id": MODEL, "name": MODEL, "version": "image-v1", "capability": "image.generate",
            "test_only": False, "readiness": "live",
            "rate": {"version": "one-point", "meters": [
                {"unit": "image", "basis": "verified_delivery_v1", "numerator": "1000000", "denominator": "1"}]},
            "limits": {"max_input_bytes": 65536, "max_assets": 0,
                       "max_artifact_bytes": 20971520, "max_units": {"image": 1}},
            "parameters": {"type": "object", "properties": {
                "n": {"type": "integer", "minimum": 1, "maximum": 1},
                "size": {"type": "string", "enum": ["1024x1024"]}},
                "required": ["n", "size"], "additionalProperties": False}}


def setup(tmp_path, handler):
    from easel.platform.central import CentralClient
    from easel.platform.media import MediaClient, MediaHost
    central = CentralClient(tmp_path, transport=httpx.MockTransport(handler), url=ORIGIN)
    central.save_auth(auth())
    client = MediaClient(tmp_path, central=central)
    request = RunRequest("Generate fixture image", tmp_path, {}, model="deepseek-flash",
                         service_source="subscription", subscription_backend="central", account_id="user",
                         platform_url=ORIGIN, central_scope=auth()["scope"], invocation_key="accepted-turn",
                         session_key="session-one", turn_id="turn-one")
    return client, MediaHost(request, client=client), request


class Fixture:
    def __init__(self):
        self.calls = []
        self.invocation = str(uuid4())
        self.job_id = str(uuid4())
        self.artifact_id = str(uuid4())
        image = Image.new("RGB", (1024, 1024), "blue")
        stream = io.BytesIO(); image.save(stream, format="PNG"); self.raw = stream.getvalue()
        self.artifact = {"id": self.artifact_id, "sha256": sha256(self.raw).hexdigest(), "size": len(self.raw),
                         "mime": "image/png", "properties": {"width": 1024, "height": 1024, "format": "PNG"},
                         "created_at": "2026-10-27T00:00:00+00:00", "expires_at": "2027-10-27T00:00:00+00:00", "purged_at": None}
        self.job = {"id": self.job_id, "model": MODEL, "capability": "image.generate", "status": "settled",
                    "financial_state": "settled", "execution_state": "completed", "delivery_state": "available",
                    "amount": "1000000", "reserved": "1000000", "actual": "1000000", "rate_version": "one-point",
                    "funding_mode": "live", "created_at": "2026-10-27T00:00:00+00:00",
                    "updated_at": "2026-10-27T00:00:01+00:00", "artifacts": [self.artifact]}
        self.timeout = False
        self.corrupt = False
        self.hook = None

    def __call__(self, request):
        self.calls.append(request)
        if self.hook: self.hook(request)
        path = request.url.path
        if path == "/v2/me":
            return httpx.Response(200, json={"account": auth()["account"], "scope": auth()["scope"]})
        if path.endswith("/media-models"):
            return httpx.Response(200, json={"models": [definition()]})
        if path.endswith("/task-tokens"):
            body = json.loads(request.content)
            return httpx.Response(200, json={"task_token": "MEDIA-TASK-TOKEN-SECRET", "invocation_id": self.invocation,
                "expires_at": time.time() + 300, "model": body["model"], "capability": body["capability"],
                "funding_mode": "live", "scope": auth()["scope"]})
        if path == "/v2/media/jobs" and request.method == "POST":
            if self.timeout: raise httpx.ReadTimeout("PRIVATE-UPSTREAM-DETAIL", request=request)
            return httpx.Response(200, json=self.job)
        if path == "/v2/media/operations":
            assert request.url.params["invocation_id"] == self.invocation
            return httpx.Response(200, json={key: value for key, value in self.job.items() if key != "artifacts"})
        if path == "/v2/media/jobs/" + self.job_id:
            return httpx.Response(200, json=self.job)
        if path == "/v2/media/jobs/" + self.job_id + "/cancel":
            return httpx.Response(200, json={**self.job, "execution_state": "cancel_requested",
                "status": "pending_reconciliation", "financial_state": "pending_reconciliation", "actual": None})
        if path == "/v2/media/artifacts/" + self.artifact_id:
            return httpx.Response(200, json=self.artifact)
        if path == "/v2/media/artifacts/" + self.artifact_id + "/content":
            return httpx.Response(200, content=b"corrupt" if self.corrupt else self.raw,
                                  headers={"content-type": "image/png", "etag": '"' + self.artifact["sha256"] + '"'})
        raise AssertionError((request.method, str(request.url)))


def test_catalog_projects_current_owned_image_models_only(tmp_path):
    fixture = Fixture(); client, _, _ = setup(tmp_path, fixture)
    assert client.catalog() == {"models": [definition()]}
    assert all(request.headers.get("authorization") == "Bearer ACCESS-CENTRAL-SECRET" for request in fixture.calls)


def test_create_records_before_posts_uses_private_task_token_and_one_image(tmp_path):
    fixture = Fixture(); client, host, request = setup(tmp_path, fixture)
    def before(post):
        if post.method == "POST":
            rows = list((tmp_path / "outputs/_harness/central-media/operations").glob("turn-*.json"))
            assert len(rows) == 1
            record = json.loads(rows[0].read_text())
            assert rows[0].stat().st_mode & 0o777 == 0o600
            assert record["local_status"] == ("mint_sending" if post.url.path.endswith("task-tokens") else "job_sending")
            assert "Generate fixture image" not in rows[0].read_text() and "SECRET" not in rows[0].read_text()
    fixture.hook = before
    result = host.execute("create_media", {"model": MODEL, "prompt": "A blue circle"}, "call-1")
    assert result["ok"] is True and result["job"]["actual"] == "1000000"
    posts = [r for r in fixture.calls if r.method == "POST"]
    assert len(posts) == 2
    assert posts[0].headers["authorization"] == "Bearer ACCESS-CENTRAL-SECRET"
    assert posts[1].headers["authorization"] == "Bearer MEDIA-TASK-TOKEN-SECRET"
    assert json.loads(posts[1].content) == {"model": MODEL, "capability": "image.generate", "prompt": "A blue circle",
                                           "assets": [], "parameters": {"n": 1, "size": "1024x1024"}}
    assert "SECRET" not in json.dumps(result) and "PRIVATE-UPSTREAM" not in json.dumps(result)


def test_unknown_repeat_and_restarted_host_only_query_original(tmp_path):
    fixture = Fixture(); client, host, request = setup(tmp_path, fixture); fixture.timeout = True
    first = host.execute("create_media", {"model": MODEL, "prompt": "A blue circle"}, "call-1")
    assert first["ok"] is False and first["local_status"] == "unknown"
    from easel.platform.media import MediaHost
    recovered = MediaHost(request, client=client).execute("create_media", {"model": MODEL, "prompt": "A blue circle"}, "call-1")
    assert recovered["ok"] is True and recovered["job"]["id"] == fixture.job_id
    denied = host.execute("create_media", {"model": MODEL, "prompt": "A different paid image"}, "call-2")
    assert denied["ok"] is False
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path.endswith("task-tokens")]) == 1


def test_same_tool_id_with_changed_content_cannot_reuse_operation(tmp_path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    assert host.execute("create_media", {"model": MODEL, "prompt": "one"}, "call-1")["ok"]
    assert not host.execute("create_media", {"model": MODEL, "prompt": "two"}, "call-1")["ok"]
    assert len([r for r in fixture.calls if r.method == "POST"]) == 2


@pytest.mark.parametrize("arguments", [{"model": MODEL, "prompt": "x", "api_key": "x"},
    {"model": MODEL, "prompt": "x", "parameters": {"n": 2}}, {"model": MODEL, "prompt": "x", "assets": ["file.png"]},
    {"model": "foreign", "prompt": "x"}])
def test_unpublished_parameters_or_private_fields_do_not_dispatch(tmp_path, arguments):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    assert host.execute("create_media", arguments, "call-1")["ok"] is False
    assert not any(r.method == "POST" for r in fixture.calls)


def test_central_identity_file_lock_is_not_held_across_media_http(tmp_path, monkeypatch):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture); locked = False
    original = client.central.store.locked
    @contextmanager
    def tracking():
        nonlocal locked
        with original():
            locked = True
            try: yield
            finally: locked = False
    monkeypatch.setattr(client.central.store, "locked", tracking)
    fixture.hook = lambda _: (_ for _ in ()).throw(AssertionError("credential lock held")) if locked else None
    assert host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]


def test_logout_during_generation_preserves_original_record_suppresses_late_result(tmp_path):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture)
    def logout(request):
        if request.url.path == "/v2/media/jobs" and request.method == "POST":
            with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN})
    fixture.hook = logout
    result = host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    assert result["ok"] is False and "job" not in result
    record = json.loads(next((tmp_path / "outputs/_harness/central-media/operations").glob("turn-*.json")).read_text())
    assert record["local_status"] in ("job_sending", "unknown")
    assert record["invocation_id"] == fixture.invocation


def test_same_owner_new_session_can_recover_but_cannot_start_old_accepted_turn(tmp_path):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture)
    assert host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
    fresh = auth(); fresh["scope"]["session_id"] = fresh["session_id"] = "new-session"
    with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN, "auth": fresh})
    def current(request):
        if request.url.path == "/v2/me": return httpx.Response(200, json={"account": fresh["account"], "scope": fresh["scope"]})
        return fixture(request)
    client.central.transport = httpx.MockTransport(current)
    recovered = client.recoveries("session-one", "turn-one")
    assert recovered["recoveries"][0]["job"]["id"] == fixture.job_id
    assert not host.execute("create_media", {"model": MODEL, "prompt": "new"}, "call-2")["ok"]
    assert len([r for r in fixture.calls if r.method == "POST"]) == 2


def test_download_verifies_owned_artifact_and_publishes_exclusively(tmp_path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    assert host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
    arguments = {"job_id": fixture.job_id, "artifact_id": fixture.artifact_id, "path": "outputs/image.png"}
    result = host.execute("download_media_artifact", arguments, "download-1")
    assert result["ok"] and result["path"] == "outputs/image.png"
    assert (tmp_path / "outputs/image.png").read_bytes() == fixture.raw
    assert not host.execute("download_media_artifact", arguments, "download-2")["ok"]
    fixture.corrupt = True
    assert not host.execute("download_media_artifact", {**arguments, "path": "outputs/broken.png"}, "download-3")["ok"]
    assert not (tmp_path / "outputs/broken.png").exists()


@pytest.mark.parametrize("path", ["../escape.png", "/tmp/escape.png", "outputs/_harness/private.png", ".env", "image.png"])
def test_download_rejects_unsafe_output_before_content_http(tmp_path, path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    assert not host.execute("download_media_artifact", {"job_id": fixture.job_id, "artifact_id": fixture.artifact_id, "path": path}, "d")["ok"]
    assert not any(r.url.path.endswith("/content") for r in fixture.calls)


def test_cancel_preserves_unknown_finances_and_does_not_regenerate(tmp_path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    result = host.execute("cancel_media_job", {"job_id": fixture.job_id}, "cancel-1")
    assert result["job"]["actual"] is None and result["job"]["execution_state"] == "cancel_requested"
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1


def test_read_only_or_unaccepted_turn_has_no_paid_media_tools(tmp_path):
    fixture = Fixture(); client, _, request = setup(tmp_path, fixture)
    from easel.platform.media import MediaHost
    assert MediaHost(replace(request, sandbox="read-only"), client=client).models() == []
    assert MediaHost(replace(request, turn_id=""), client=client).models() == []
    assert MediaHost(replace(request, service_source="custom"), client=client).models() == []
    assert not any(r.method == "POST" for r in fixture.calls)


def test_human_model_names_can_include_chinese_and_spaces(tmp_path):
    fixture = Fixture()
    def handler(request):
        if request.url.path.endswith("/media-models"):
            return httpx.Response(200, json={"models": [{**definition(), "name": MODEL + " · 内部体验"}]})
        return fixture(request)
    client, _, _ = setup(tmp_path, handler)
    assert client.catalog()["models"][0]["name"] == MODEL + " · 内部体验"


def test_stopped_host_does_not_submit_or_publish_after_abort(tmp_path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    host.stop()
    assert not host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
    assert not any(r.method == "POST" for r in fixture.calls)


@pytest.mark.parametrize("change", [{"service_source": "custom"}, {"sandbox": "read-only"}, {"turn_id": ""}])
def test_unaccepted_context_cannot_cancel_or_download_even_known_ids(tmp_path, change):
    fixture = Fixture(); client, _, request = setup(tmp_path, fixture)
    from easel.platform.media import MediaHost
    host = MediaHost(replace(request, **change), client=client)
    for name, arguments in (("cancel_media_job", {"job_id": fixture.job_id}),
            ("download_media_artifact", {"job_id": fixture.job_id, "artifact_id": fixture.artifact_id, "path": "outputs/x.png"})):
        assert not host.execute(name, arguments, "test")["ok"]
    assert not fixture.calls


def test_logout_after_download_before_atomic_publication_cannot_save_late_image(tmp_path, monkeypatch):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture)
    import easel.platform.media as media
    host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    save = media._save_image
    def logout_then_save(*args, **kwargs):
        with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN})
        return save(*args, **kwargs)
    monkeypatch.setattr(media, "_save_image", logout_then_save)
    result = host.execute("download_media_artifact", {"job_id": fixture.job_id, "artifact_id": fixture.artifact_id,
                                                    "path": "outputs/late.png"}, "d")
    assert not result["ok"] and not (tmp_path / "outputs/late.png").exists()


def test_malformed_huge_image_does_not_escape_as_unhandled_decoder_failure(tmp_path):
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    header = struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + header
    fixture.raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    for kind, value in ((b"IDAT", zlib.compress(b"\0")), (b"IEND", b"")):
        fixture.raw += struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    fixture.artifact.update(size=len(fixture.raw), sha256=sha256(fixture.raw).hexdigest())
    host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    result = host.execute("download_media_artifact", {"job_id": fixture.job_id, "artifact_id": fixture.artifact_id,
                                                    "path": "outputs/bomb.png"}, "d")
    assert not result["ok"] and not (tmp_path / "outputs/bomb.png").exists()


def test_mint_timeout_is_distinct_and_never_remints_or_generates_on_replay(tmp_path):
    fixture = Fixture()
    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/task-tokens"):
            fixture.calls.append(request)
            raise httpx.ReadTimeout("synthetic mint lost", request=request)
        return fixture(request)
    client, host, _ = setup(tmp_path, handler)
    arguments = {"model": MODEL, "prompt": "x"}
    first = host.execute("create_media", arguments, "call-1")
    assert not first["ok"]
    assert not host.execute("create_media", arguments, "call-1")["ok"]
    row = client.recoveries("session-one", "turn-one")["recoveries"][0]
    assert row["local_status"] == "mint_unknown" and row["lookup_status"] == "not_sent_or_unknown"
    assert len([r for r in fixture.calls if r.method == "POST"]) == 1


def test_record_partial_or_symlink_is_never_replaced_by_a_new_paid_call(tmp_path):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture)
    host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")
    record = next((tmp_path / "outputs/_harness/central-media/operations").glob("turn-*.json"))
    original = record.read_bytes(); record.write_bytes(b"{")
    assert not host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
    assert record.read_bytes() == b"{"
    record.unlink(); outside = tmp_path / "private-original"; outside.write_bytes(original); record.symlink_to(outside)
    assert not host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
    assert outside.read_bytes() == original and record.is_symlink()
    assert len([r for r in fixture.calls if r.method == "POST"]) == 2


def test_concurrent_same_turn_claim_cannot_dispatch_twice(tmp_path):
    import concurrent.futures
    import threading
    fixture = Fixture(); _, host, _ = setup(tmp_path, fixture)
    entered, release = threading.Event(), threading.Event()
    def hook(request):
        if request.method == "POST" and request.url.path.endswith("/task-tokens"):
            entered.set(); assert release.wait(3)
    fixture.hook = hook
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(host.execute, "create_media", {"model": MODEL, "prompt": "x"}, "call-1")
        assert entered.wait(3)
        try: assert not host.execute("create_media", {"model": MODEL, "prompt": "x"}, "call-1")["ok"]
        finally: release.set()
        assert future.result(3)["ok"]
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1


def test_slow_json_stream_has_an_absolute_deadline(tmp_path):
    import asyncio
    from easel.platform.media import MediaError
    fixture = Fixture()
    class Drip(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            for _ in range(20):
                time.sleep(.005)
                yield b" "
            yield b"{}"
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(.005)
                yield b" "
            yield b"{}"
    client, _, _ = setup(tmp_path, lambda _: httpx.Response(200, stream=Drip()))
    started = time.monotonic()
    with pytest.raises(MediaError):
        client._fetch({"url": ORIGIN, "token": "synthetic-access"}, "GET", "/v2/me", timeout=.02)
    assert time.monotonic() - started < .08


def test_stalled_read_is_cancelled_at_the_absolute_deadline(tmp_path):
    import asyncio
    from easel.platform.media import MediaError
    class Stall(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            time.sleep(.2)
            yield b"{}"
        async def __aiter__(self):
            await asyncio.sleep(.2)
            yield b"{}"
    client, _, _ = setup(tmp_path, lambda _: httpx.Response(200, stream=Stall()))
    started = time.monotonic()
    with pytest.raises(MediaError):
        client._fetch({"url": ORIGIN, "token": "synthetic-access"}, "GET", "/v2/me", timeout=.02)
    assert time.monotonic() - started < .08


def test_session_refresh_obeys_the_same_absolute_recovery_deadline(tmp_path):
    import asyncio
    from easel.platform.client import PlatformError
    class StallRefresh(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            time.sleep(.2)
            yield json.dumps(auth()).encode()
        async def __aiter__(self):
            await asyncio.sleep(.2)
            yield json.dumps(auth()).encode()
    client, _, _ = setup(tmp_path, lambda _: httpx.Response(200, stream=StallRefresh()))
    expired = auth(); expired["expires_at"] = 0
    with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN, "auth": expired})
    before = client.central.store.path.read_bytes()
    started = time.monotonic()
    with pytest.raises(PlatformError): client._snapshot(deadline=time.monotonic() + .02)
    assert time.monotonic() - started < .08
    saved=client.central.store.read()
    assert saved["auth"] == json.loads(before)["auth"]
    assert saved["refresh_guard"]["state"] in {"sending","uncertain"}


@pytest.mark.parametrize("changed", [False, True])
def test_serialized_media_refresh_preserves_scope_and_uses_new_access_token(tmp_path, changed):
    fixture = Fixture(); fresh = auth(); fresh["access_token"] = "FRESH-CENTRAL-SECRET"
    if changed: fresh["scope"]["authorization_epoch"] += 1
    def handler(request):
        if request.url.path == "/v2/auth/refresh":
            fixture.calls.append(request)
            assert "authorization" not in request.headers
            assert json.loads(request.content) == {"refresh_token": "REFRESH-CENTRAL-SECRET"}
            return httpx.Response(200, json=fresh)
        assert request.headers["authorization"] == "Bearer FRESH-CENTRAL-SECRET"
        return fixture(request)
    client, _, _ = setup(tmp_path, handler)
    expired = auth(); expired["expires_at"] = 0
    with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN, "auth": expired})
    before = client.central.store.path.read_bytes()
    from easel.platform.media import MediaError
    if changed:
        with pytest.raises(MediaError): client.catalog()
        saved=client.central.store.read()
        assert saved["auth"] == json.loads(before)["auth"]
        assert saved["refresh_guard"]["state"] in {"sending","uncertain"}
        assert len(fixture.calls) == 1
    else:
        assert client.catalog()["models"][0]["id"] == MODEL
        assert client.central.store.read()["auth"]["access_token"] == "FRESH-CENTRAL-SECRET"
        assert len([r for r in fixture.calls if r.method == "POST"]) == 1


def test_media_verification_forgets_only_explicit_current_authentication_failure(tmp_path):
    from easel.platform.media import MediaError
    client, _, _ = setup(tmp_path, lambda _: httpx.Response(401, json={"error": {"code": "invalid_authentication"}}))
    with pytest.raises(MediaError): client.catalog()
    assert "auth" not in client.central.store.read()


def test_late_old_auth_failure_does_not_remove_new_same_owner_session(tmp_path):
    from easel.platform.media import MediaError
    holder = {}
    def handler(request):
        central = holder["client"].central
        newer = auth(); newer["access_token"] = "NEW-ACCESS-CENTRAL-SECRET"
        with central.store.locked(): central.store.write({"version": 2, "url": ORIGIN, "auth": newer})
        return httpx.Response(401, json={"error": {"code": "invalid_authentication"}})
    client, _, _ = setup(tmp_path, handler); holder["client"] = client
    with pytest.raises(MediaError): client.catalog()
    assert client.central.store.read()["auth"]["access_token"] == "NEW-ACCESS-CENTRAL-SECRET"


def test_wrong_size_completed_image_is_not_reported_as_delivered(tmp_path):
    fixture = Fixture(); client, host, _ = setup(tmp_path, fixture)
    out = io.BytesIO(); Image.new("RGB", (16, 16), "blue").save(out, format="PNG"); fixture.raw = out.getvalue()
    fixture.artifact.update(size=len(fixture.raw), sha256=sha256(fixture.raw).hexdigest(),
                            properties={"width": 16, "height": 16, "format": "PNG"})
    result = host.execute("create_media", {"model": MODEL, "prompt": "one image"}, "call-1")
    assert not result["ok"] and result["local_status"] == "unknown"
    assert len([r for r in fixture.calls if r.method == "POST" and r.url.path == "/v2/media/jobs"]) == 1


def test_final_save_rejects_verified_but_wrong_sized_image(tmp_path):
    from contextlib import nullcontext
    from easel.platform.media import _save_image
    out = io.BytesIO(); Image.new("RGB", (16, 16), "blue").save(out, format="PNG"); raw = out.getvalue()
    metadata = {"mime": "image/png", "size": len(raw), "sha256": sha256(raw).hexdigest(),
                "properties": {"width": 16, "height": 16, "format": "PNG"}}
    with pytest.raises(ValueError): _save_image(tmp_path, "outputs/wrong.png", raw, metadata, publish_guard=nullcontext)
    assert not (tmp_path / "outputs/wrong.png").exists()


@pytest.mark.parametrize("status,cleared", [(401, True), (503, False)])
def test_refresh_replayed_clears_only_explicit_401(tmp_path, status, cleared):
    from easel.platform.media import MediaError
    client, _, _ = setup(tmp_path, lambda _: httpx.Response(status, json={"error": {"code": "refresh_replayed"}}))
    expired = auth(); expired["expires_at"] = 0
    with client.central.store.locked(): client.central.store.write({"version": 2, "url": ORIGIN, "auth": expired})
    with pytest.raises(MediaError): client.catalog()
    assert ("auth" not in client.central.store.read()) is cleared


def test_media_reads_require_a_complete_accepted_scope_before_network(tmp_path):
    fixture = Fixture(); client, _, request = setup(tmp_path, fixture)
    from easel.platform.media import MediaHost
    scope = dict(request.central_scope); del scope["session_id"]
    host = MediaHost(replace(request, central_scope=scope), client=client)
    assert not host.execute("get_media_job", {"job_id": fixture.job_id}, "read-call")["ok"]
    assert fixture.calls == []


def test_paid_record_lookup_is_independent_of_display_pagination(tmp_path):
    from easel.platform.media_records import MediaRecords, digest
    records=MediaRecords(tmp_path); owner=digest('owner'); first=None
    for index in range(51):
        row,_=records.claim(owner=owner,scope={},session_id='session',turn_id=f'turn-{index}',
                            tool_id='call',model=MODEL,body_hash=digest(index))
        row=records.update(row,job_id=str(uuid4()),local_status='known')
        if first is None:first=row
    assert len(records.rows(owner)[0])==50
    assert records.owned(owner,job_id=first['job_id'])['operation_key']==first['operation_key']
    assert records.owned(owner,operation_key=first['operation_key'])['job_id']==first['job_id']
    assert records.rows(owner,first['turn_key'])[0]==[first]
    with pytest.raises(ValueError):records.owned(digest('other'),job_id=first['job_id'])


def test_record_capacity_blocks_new_mint_but_keeps_original_claim_and_exact_recovery(tmp_path,monkeypatch):
    import easel.platform.media_records as module
    monkeypatch.setattr(module,'MAX_RECORDS',1)
    records=module.MediaRecords(tmp_path); kwargs=dict(owner=module.digest('owner'),scope={},session_id='session',
        turn_id='old-turn',tool_id='call',model=MODEL,body_hash=module.digest('body'))
    first,fresh=records.claim(**kwargs);assert fresh
    repeated,fresh=records.claim(**kwargs);assert repeated==first and not fresh
    with pytest.raises(module.MediaRecordError,match='capacity'):
        records.claim(**{**kwargs,'turn_id':'new-turn'})
    assert records.owned(kwargs['owner'],operation_key=first['operation_key'])==first


def test_incomplete_lookup_never_misreports_a_paid_operation_as_not_found(tmp_path,monkeypatch):
    import easel.platform.media_records as module
    records=module.MediaRecords(tmp_path);owner=module.digest('owner')
    row,_=records.claim(owner=owner,scope={},session_id='session',turn_id='old',tool_id='call',model=MODEL,body_hash=module.digest('body'))
    monkeypatch.setattr(module,'MAX_SCAN',0)
    with pytest.raises(module.MediaRecordError,match='incomplete'):records.owned(owner,operation_key=row['operation_key'])
    assert records.rows(owner,row['turn_key'])[0]==[row]


def test_capacity_error_is_actionable_and_never_sends_a_new_paid_post(tmp_path,monkeypatch):
    import easel.platform.media_records as module
    monkeypatch.setattr(module,'MAX_RECORDS',0)
    fixture=Fixture();_,host,_=setup(tmp_path,fixture)
    result=host.execute('create_media',{'model':MODEL,'prompt':'blue circle'},'one')
    assert result['ok'] is False and result['text']=='本机生图记录已满'
    assert not any(r.method=='POST' for r in fixture.calls)
