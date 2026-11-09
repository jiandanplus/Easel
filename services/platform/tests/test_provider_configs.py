"""Interface settings entered in the console, through the low Console role on a real PostgreSQL."""
import dataclasses
import json
import os
import stat

import httpx
import pytest
from nacl.public import PrivateKey

from platform_service.errors import PlatformError
from platform_service.execution_config import ExecutionProvider, ExecutionSettings
from platform_service.gateway_catalog import ManagedBindings
from platform_service.media_config import MediaProvider, MediaSettings
from platform_service.provider_channels import CHANNELS, PLATFORMS, build_clone_provider, build_provider, clone_provider_id, clean_base_url, clean_key, clean_options, provider_id, public_platforms, seal_scope
from platform_service.provider_secrets import ProviderKeys, generate, seal
from platform_service.voice_catalogs import MAX_VOICES, list_voices
from test_console_auth import console  # noqa: F401 (fixture)
from test_console_owner_allowance import low_catalog
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

import base64

PRIVATE = PrivateKey.generate()
PUBLIC = base64.b64encode(bytes(PRIVATE.public_key)).decode()
KEYS = ProviderKeys(PRIVATE)
SECRET = "sk-supplier-secret-1234"
URL = "https://gateway.example/v1"
TEXT = lambda provider: (None, None, ("text",))
MEDIA = lambda provider: (provider.adapter, provider.protocol_version, provider.capabilities)


async def admin_catalog(console, console_role, role="admin"):
    catalog, queries, principal, rest = await low_catalog(console, console_role, role)
    catalog.public_key = PUBLIC
    return catalog, queries, principal, rest


def page(listing, channel, platform="gateway", account=""):
    row = next(c for c in listing["channels"] if c["channel"] == channel)
    return next((c for c in row["connections"] if (c["platform"], c["account"]) == (platform, account)), None)


def server_entry(listing, channel):
    return next(c for c in listing["channels"] if c["channel"] == channel)["server"]


def exec_settings(**extra):
    provider = ExecutionProvider(version="1", endpoint="https://old.example/v1/responses", secret="old-secret", models={"file-text": "upstream"})
    return ExecutionSettings(mode="live", providers={"file": provider}, **extra)


def media_settings(tmp_path):
    provider = MediaProvider(version="1", base_url="https://old.example/v1", secret="old-secret", models={"file-image": "upstream"}, capabilities=("image.generate",))
    return MediaSettings(mode="live", providers={"file_images": provider}, storage_root=tmp_path)


def test_key_opens_only_for_the_channel_and_address_it_was_typed_for():
    sealed = seal(PUBLIC, "chat", URL, SECRET)
    assert SECRET not in sealed and KEYS.open(sealed, "chat", URL) == SECRET
    assert KEYS.open(sealed, "image", URL) is None
    assert KEYS.open(sealed, "chat", "https://attacker.example/v1") is None
    assert KEYS.open("not-base64", "chat", URL) is None
    assert ProviderKeys(PrivateKey.generate()).open(sealed, "chat", URL) is None


def test_private_key_file_must_be_owner_only(tmp_path):
    path = tmp_path / "provider.key"
    public = generate(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    keys = ProviderKeys.from_env({"CENTRAL_PROVIDER_KEY_FILE": str(path)})
    assert keys.open(seal(public, "chat", URL, SECRET), "chat", URL) == SECRET
    assert ProviderKeys.from_env({}) is None
    os.chmod(path, 0o640)
    with pytest.raises(ValueError):
        ProviderKeys.from_env({"CENTRAL_PROVIDER_KEY_FILE": str(path)})


@pytest.mark.parametrize("bad", ["http://gateway.example/v1", "https://user:pw@gateway.example/v1", "https://gateway.example/v1?x=1",
                                 "https://169.254.169.254/v1", "https://10.0.0.1/v1", "ftp://gateway.example", "x"])
def test_unsafe_addresses_are_refused(bad):
    with pytest.raises(PlatformError):
        clean_base_url(bad)


def test_trailing_slash_is_normalized():
    assert clean_base_url(URL + "/") == URL


FIELD_VALUES = {"voices": ["alloy"], "download_origins": ["https://cdn.example"], "download_path_prefixes": ["/audio/"], "group_id": "g1", "poll_url": "", "auth_header": "api-key", "api_version": "2025-01-01"}
ALL_PLATFORMS = [(channel, platform) for channel, table in PLATFORMS.items() for platform in table]


def options_for(channel, platform):
    return {field["key"]: FIELD_VALUES[field["key"]] for field in PLATFORMS[channel][platform].get("fields", [])}


@pytest.mark.parametrize("channel,platform", ALL_PLATFORMS)
def test_every_platform_builds_a_provider_the_server_accepts(channel, platform, tmp_path):
    provider = build_provider(channel, platform, URL, SECRET, clean_options(channel, platform, options_for(channel, platform)))
    if CHANNELS[channel]["kind"] == "execution":
        assert provider.models_url == URL + "/models"
        assert provider.endpoint.rsplit("/", 1)[-1] == {"responses": "responses", "chat": "completions", "anthropic": "messages"}[provider.protocol]
        ExecutionSettings(mode="live", providers={provider_id(channel, platform): provider})
    else:
        settings = MediaSettings(mode="live", providers={provider_id(channel, platform): provider}, storage_root=tmp_path)
        models = dataclasses.replace(provider, models={"m": "u"})
        assert dataclasses.replace(settings, providers={provider_id(channel, platform): models}).bindings["m"]["provider_version"] == "console-1"
        assert provider.client_correlation is True
    if PLATFORMS[channel][platform]["probe"] != "bearer" or options_for(channel, platform).get("auth_header"):
        assert provider.preflight_models is False
    if PLATFORMS[channel][platform].get("clone_adapter"):
        clone = build_clone_provider(channel, platform, URL, SECRET, options_for(channel, platform))
        MediaSettings(mode="live", providers={clone_provider_id(channel, platform): dataclasses.replace(clone, models={"c": "up"})}, storage_root=tmp_path)
        assert clone.capabilities == ("voice.enroll",) and clone_provider_id(channel, platform).endswith("-clone")


def test_only_platforms_with_a_model_list_are_probed_and_named():
    assert provider_id("video") == "console-video" and provider_id("video", "kling") == "console-video-kling"
    assert seal_scope("video") == "video" and seal_scope("video", "kling") == "video/kling"
    assert provider_id("video", "kling", "abcd1234") == "console-video-kling-abcd1234" and provider_id("video", "gateway", "abcd1234") == "console-video-gateway-abcd1234"
    assert seal_scope("video", "kling", "abcd1234") == "video/kling/abcd1234" and clone_provider_id("speech", "minimax", "abcd1234") == "console-speech-minimax-abcd1234-clone"
    assert {p["id"]: p["second_key"] for p in public_platforms("video")}["kling"] == "Secret Key"
    with pytest.raises(PlatformError):
        clean_key("only-one", parts=2)
    assert clean_key("ak|sk", parts=2) == "ak|sk"


def test_page_options_are_strict():
    for channel, platform, bad in (("speech", "gateway", {}), ("speech", "gateway", {"voices": []}), ("speech", "gateway", {"voices": ["bad/voice"]}), ("chat", "gateway", {"x": 1}),
                                   ("music", "gateway", {"download_origins": [], "download_path_prefixes": ["/a/"]}),
                                   ("music", "gateway", {"download_origins": ["https://c.example"], "download_path_prefixes": ["/"]}),
                                   ("image", "gateway", {"download_origins": ["ftp://x"]}), ("speech", "minimax", {"voices": ["a"], "group_id": "bad id"}),
                                   ("video", "nope", {})):
        with pytest.raises(PlatformError):
            clean_options(channel, platform, bad)


@pytest.mark.asyncio
async def test_saved_key_is_sealed_write_only_and_needs_admin(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    result = await catalog.save_provider_config(principal, "chat", "gateway", URL + "/", SECRET, False, {}, "接入", "cfg-1")
    assert result["configured"] is True
    state = page(await queries.provider_configs(principal), "chat")
    assert state["base_url"] == URL and state["key_hint"] == "1234" and state["status"] is None
    assert SECRET not in json.dumps(state)
    async with db.connection() as c:
        for table, column in (("provider_configs", "sealed_secret"), ("console_operations", "result"), ("console_audit_events", "reason")):
            rows = await (await c.execute(f"SELECT {column}::text AS v FROM {table}")).fetchall()
            assert rows and all(SECRET not in row["v"] for row in rows)
        sealed = (await (await c.execute("SELECT sealed_secret FROM provider_configs")).fetchone())["sealed_secret"]
    assert KEYS.open(sealed, "chat", URL) == SECRET

    replay = await catalog.save_provider_config(principal, "chat", "gateway", URL, SECRET, False, {}, "接入", "cfg-1")
    assert replay == result
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "chat", "gateway", URL, "another-key", False, {}, "接入", "cfg-1")
    assert error.value.code == "console_idempotency_conflict"


@pytest.mark.asyncio
async def test_only_admins_can_change_interfaces(db, console, console_role):
    await seed(db)
    operator, queries, who, _ = await admin_catalog(console, console_role, "operator")
    assert "providers:manage" not in who.permissions
    with pytest.raises(PlatformError):
        await operator.save_provider_config(who, "chat", "gateway", URL, SECRET, False, {}, "越权", "cfg-op")


@pytest.mark.asyncio
async def test_moving_an_address_needs_the_key_again_and_keep_or_clear_work(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "video", "gateway", URL, None, False, {}, "无 Key", "k0")
    assert error.value.code == "console_provider_key_required"
    await catalog.save_provider_config(principal, "video", "gateway", URL, SECRET, False, {}, "接入", "k1")
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "video", "gateway", "https://other.example/v1", None, False, {}, "搬家", "k2")
    assert error.value.code == "console_provider_key_required"
    kept = await catalog.save_provider_config(principal, "video", "gateway", URL, None, False, {}, "重新检测", "k3")
    assert kept["configured"] is True
    cleared = await catalog.save_provider_config(principal, "video", "gateway", URL, None, True, {}, "停用", "k4")
    assert cleared["configured"] is False
    assert page(await queries.provider_configs(principal), "video") is None
    with pytest.raises(PlatformError):
        await catalog.save_provider_config(principal, "nope", "gateway", URL, SECRET, False, {}, "x", "k5")
    catalog.public_key = None
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "video", "gateway", URL, SECRET, False, {}, "x", "k6")
    assert error.value.code == "console_provider_key_unconfigured"


async def save_row(db, channel, base_url=URL, secret=SECRET, options=None, config_id="a" * 32, platform="gateway"):
    async with db.connection() as c:
        await c.execute("INSERT INTO provider_configs(id,channel,platform,base_url,sealed_secret,key_hint,options,operator_id) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,'op')",
                        (config_id, channel, platform, base_url, None if secret is None else seal(PUBLIC, seal_scope(channel, platform), base_url, secret), None if secret is None else secret[-4:], json.dumps(options or {})))


@pytest.mark.asyncio
async def test_saved_interface_takes_over_its_page_without_a_restart(db):
    await seed(db)
    binder = ManagedBindings(db, "execution", exec_settings(), TEXT, keys=KEYS)
    before = binder.bindings["file-text"]["binding_hash"]
    await save_row(db, "chat")
    settings, bindings = await binder.refresh()
    assert settings.providers["file"].endpoint == URL + "/responses" and settings.providers["file"].secret == SECRET
    assert bindings["file-text"]["binding_hash"] != before and bindings["file-text"]["provider_version"] == "1"
    assert settings.providers["console-chat"].managed_models is True
    async with db.connection() as c:
        rows = await (await c.execute("SELECT provider_id,provider_version,base_url FROM provider_catalog WHERE kind='execution' ORDER BY provider_id")).fetchall()
    assert [(r["provider_id"], r["base_url"]) for r in rows] == [("console-chat", URL), ("file", URL)]

    await save_row(db, "chat", secret=None, config_id="b" * 32)
    binder._checked = 0
    settings, bindings = await binder.refresh()
    assert settings.providers["file"].secret == "old-secret" and "console-chat" not in settings.providers
    assert bindings["file-text"]["binding_hash"] == before


@pytest.mark.asyncio
async def test_new_models_bind_to_a_saved_video_page(db, tmp_path):
    await seed(db)
    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS)
    await save_row(db, "video")
    async with db.connection() as c:
        await c.execute("INSERT INTO managed_model_bindings(kind,model_id,provider_id,upstream_model,operator_id) VALUES ('media','my-video','console-video','sora-2','op')")
    settings, bindings = await binder.refresh()
    assert bindings["my-video"]["adapter"] == "newapi_videos_v1" and settings.providers["console-video"].models == {"my-video": "sora-2"}
    assert settings.providers["file_images"].secret == "old-secret"


@pytest.mark.asyncio
async def test_a_bad_setting_is_reported_and_leaves_others_alone(db):
    await seed(db)
    binder = ManagedBindings(db, "execution", exec_settings(), TEXT, keys=KEYS)
    await save_row(db, "chat", base_url="http://127.0.0.1:9/v1")  # Not https in live mode.
    settings, _ = await binder.refresh()
    assert settings.providers["file"].secret == "old-secret" and "console-chat" not in settings.providers
    async with db.connection() as c:
        row = await (await c.execute("SELECT state FROM provider_config_status WHERE channel='chat'")).fetchone()
    assert row["state"] == "invalid"


@pytest.mark.parametrize("response,state,models", [
    (httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]}), "ok", ["a", "b"]),
    (httpx.Response(401, json={}), "auth_failed", []),
    (httpx.Response(404, text="nope"), "no_model_list", []),
    (None, "unreachable", []),
])
@pytest.mark.asyncio
async def test_saved_interface_is_tried_with_the_free_model_list(db, response, state, models):
    await seed(db)
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["authorization"]))
        if response is None:
            raise httpx.ConnectError("down")
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    binder = ManagedBindings(db, "execution", exec_settings(), TEXT, keys=KEYS, client=client)
    await save_row(db, "chat")
    await binder.refresh()
    await binder._probe_task
    assert seen == [(URL + "/models", "Bearer " + SECRET)] * (3 if response is None else 1)
    async with db.connection() as c:
        row = await (await c.execute("SELECT config_id,state,models FROM provider_config_status WHERE channel='chat'")).fetchone()
    assert (row["config_id"], row["state"], row["models"]) == ("a" * 32, state, models)
    binder._checked = 0
    settings, _ = await binder.refresh()
    assert settings.providers["console-chat"].preflight_models is (state != "no_model_list")
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["chat", "video"])
async def test_changing_the_connection_waits_for_running_calls(db, console, console_role, monkeypatch, channel):
    await seed(db)
    catalog, _, principal, _ = await admin_catalog(console, console_role)
    await catalog.save_provider_config(principal, channel, "gateway", URL, SECRET, False, {}, "接入", "b1")
    # The real count runs under the low console role and finds nothing running.
    await catalog.save_provider_config(principal, channel, "gateway", URL, "second-key-5678", False, {}, "换 Key", "b2")
    original = catalog._running_calls

    async def busy(conn, name):
        assert await original(conn, name) == 0
        return 1

    monkeypatch.setattr(catalog, "_running_calls", busy)
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, channel, "gateway", URL, "third-key-9012", False, {}, "换 Key", "b3")
    assert error.value.code == "console_provider_busy"
    kept = await catalog.save_provider_config(principal, channel, "gateway", URL, None, False, {}, "重新检测", "b4")
    assert kept["configured"] is True


@pytest.mark.asyncio
async def test_platforms_on_one_page_stay_separate_and_leave_server_models_alone(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    await catalog.save_provider_config(principal, "video", "kling", "https://api.klingai.com", "access|secret-key", False, {"download_origins": []}, "接入", "p1")
    await catalog.save_provider_config(principal, "video", "dashscope", "https://dashscope.aliyuncs.com/api/v1", SECRET, False, {"download_origins": []}, "接入", "p2")
    listing = await queries.provider_configs(principal)
    assert page(listing, "video", "kling")["key_hint"] == "-key" and page(listing, "video", "dashscope")["key_hint"] == "1234"
    video = next(c for c in listing["channels"] if c["channel"] == "video")
    assert page(listing, "video") is None and next(p for p in video["platforms"] if p["id"] == "kling")["lists_models"] is False
    with pytest.raises(PlatformError):
        await catalog.save_provider_config(principal, "video", "kling", "https://api.klingai.com", "missing-second", False, {"download_origins": []}, "x", "p3")
    with pytest.raises(PlatformError):
        await catalog.save_provider_config(principal, "video", "nope", URL, SECRET, False, {}, "x", "p4")


@pytest.mark.asyncio
async def test_a_native_platform_adds_models_without_touching_the_file_provider(db, tmp_path):
    await seed(db)
    seen = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200, json={"data": []})))
    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS, client=client)
    await save_row(db, "video", base_url="https://api.klingai.com", secret="ak|sk", platform="kling", options={"download_origins": []})
    async with db.connection() as c:
        await c.execute("INSERT INTO managed_model_bindings(kind,model_id,provider_id,upstream_model,operator_id) VALUES ('media','my-kling','console-video-kling','kling-v2','op')")
    settings, bindings = await binder.refresh()
    assert bindings["my-kling"]["adapter"] == "kling_video_v1" and settings.providers["file_images"].secret == "old-secret"
    await binder._probe_task
    assert not any("klingai" in url for url in seen)  # Kling has no model list to ask.
    async with db.connection() as c:
        row = await (await c.execute("SELECT channel,platform,state FROM provider_config_status")).fetchone()
    assert (row["channel"], row["platform"], row["state"]) == ("video", "kling", "no_model_list")
    await client.aclose()


@pytest.mark.asyncio
async def test_a_page_on_server_files_can_list_its_models_and_a_blip_keeps_the_list(db, console, console_role, monkeypatch):
    import platform_service.gateway_catalog as module
    await seed(db)
    seen, failing = [], []

    def handler(request):
        seen.append((str(request.url), request.headers["authorization"]))
        if failing:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"data": [{"id": "m2"}, {"id": "m1"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    binder = ManagedBindings(db, "execution", exec_settings(), TEXT, keys=KEYS, client=client)
    await binder.refresh()
    await binder._probe_task
    assert seen == [("https://old.example/v1/models", "Bearer old-secret")]
    _, queries, principal, _ = await admin_catalog(console, console_role)
    state = server_entry(await queries.provider_configs(principal), "chat")
    assert state["base_url"] == "https://old.example/v1" and state["status"]["models"] == ["m1", "m2"]

    failing.append(True)
    binder._probed.clear()
    await binder.probe_due()
    assert len(seen) == 1 + module.PROBE_ATTEMPTS
    state = server_entry(await queries.provider_configs(principal), "chat")
    assert state["status"]["state"] == "unreachable" and state["status"]["models"] == ["m1", "m2"]

    failing.clear()
    binder._probed.clear()
    await binder.probe_due()
    assert server_entry(await queries.provider_configs(principal), "chat")["status"]["state"] == "ok"
    await client.aclose()


VOICES = {"voices": ["alloy"], "group_id": ""}
MINIMAX = "https://api.minimax.chat"


async def two_accounts(catalog, principal):
    first = await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, "first-key-1111", False, VOICES, "接入", "acct-1", account=None, name="主账号")
    second = await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, "second-key-2222", False, VOICES, "接入", "acct-2", account=None, name="备用账号")
    return first, second


@pytest.mark.asyncio
async def test_one_platform_can_hold_several_accounts_with_their_own_keys(db, console, console_role, tmp_path):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    first, second = await two_accounts(catalog, principal)
    assert first["account"] != second["account"] and first["provider_id"] != second["provider_id"]
    assert await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, "first-key-1111", False, VOICES, "接入", "acct-1", account=None, name="主账号") == first

    listing = await queries.provider_configs(principal)
    assert [(c["name"], c["key_hint"]) for c in next(c for c in listing["channels"] if c["channel"] == "speech")["connections"]] == [("主账号", "1111"), ("备用账号", "2222")]

    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS)
    settings, _ = await binder.refresh()
    assert settings.providers[first["provider_id"]].secret == "first-key-1111" and settings.providers[second["provider_id"]].secret == "second-key-2222"
    assert settings.providers[second["provider_id"] + "-clone"].secret == "second-key-2222"
    assert settings.providers["file_images"].secret == "old-secret"
    async with db.connection() as c:
        await c.execute("INSERT INTO managed_model_bindings(kind,model_id,provider_id,upstream_model,operator_id) VALUES ('media','voice-a','%s','speech-hd','op'),('media','voice-b','%s','speech-hd','op')" % (first["provider_id"], second["provider_id"]))
    binder._checked = 0
    settings, bindings = await binder.refresh()
    assert bindings["voice-a"]["provider_id"] == first["provider_id"] and bindings["voice-b"]["provider_id"] == second["provider_id"]


@pytest.mark.asyncio
async def test_a_key_cannot_move_between_accounts_of_one_platform(db, console, console_role):
    await seed(db)
    catalog, _, principal, _ = await admin_catalog(console, console_role)
    first, _ = await two_accounts(catalog, principal)
    async with db.connection() as c:
        sealed = (await (await c.execute("SELECT sealed_secret FROM provider_configs WHERE account=%s", (first["account"],))).fetchone())["sealed_secret"]
    assert KEYS.open(sealed, seal_scope("speech", "minimax", first["account"]), MINIMAX) == "first-key-1111"
    assert KEYS.open(sealed, seal_scope("speech", "minimax", "00000000"), MINIMAX) is None
    assert KEYS.open(sealed, seal_scope("speech", "minimax"), MINIMAX) is None


@pytest.mark.asyncio
async def test_each_account_is_tried_and_reported_on_its_own(db, console, console_role, tmp_path):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    url = "https://api.openai-like.example/v1"
    first = await catalog.save_provider_config(principal, "transcribe", "siliconflow", url, "aaaa-key", False, {}, "接入", "t1", account=None, name="甲")
    second = await catalog.save_provider_config(principal, "transcribe", "siliconflow", url, "bbbb-key", False, {}, "接入", "t2", account=None, name="乙")

    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "a-model"}]}) if request.headers["authorization"] == "Bearer aaaa-key" else httpx.Response(401, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS, client=client)
    await binder.refresh()
    await binder._probe_task
    listing = await queries.provider_configs(principal)
    one, two = page(listing, "transcribe", "siliconflow", first["account"]), page(listing, "transcribe", "siliconflow", second["account"])
    assert (one["status"]["state"], one["status"]["models"]) == ("ok", ["a-model"]) and two["status"]["state"] == "auth_failed"
    await client.aclose()


@pytest.mark.asyncio
async def test_new_gateway_connections_leave_the_server_models_alone(db, console, console_role):
    await seed(db)
    catalog, _, principal, _ = await admin_catalog(console, console_role)
    extra = await catalog.save_provider_config(principal, "chat", "gateway", URL, SECRET, False, {}, "接入", "g1", account=None, name="第二个网关")
    binder = ManagedBindings(db, "execution", exec_settings(), TEXT, keys=KEYS)
    settings, _ = await binder.refresh()
    assert settings.providers["file"].secret == "old-secret" and settings.providers[extra["provider_id"]].secret == SECRET
    assert extra["provider_id"] == "console-chat-gateway-" + extra["account"]


@pytest.mark.asyncio
async def test_names_are_required_new_and_unique_and_edits_keep_the_key(db, console, console_role):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    first, second = await two_accounts(catalog, principal)
    for name, code in (("", "console_provider_invalid"), ("主账号", "console_provider_name_taken"), ("主\n账号", "console_provider_invalid")):
        with pytest.raises(PlatformError) as error:
            await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, "third-key-3333", False, VOICES, "接入", "bad-" + str(len(name)) + code, account=None, name=name)
        assert error.value.code == code
    # Another platform on the page cannot take the name either.
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "speech", "fish", "https://api.fish.audio", "fish-key-4444", False, {"voices": ["v"]}, "接入", "bad-fish", account=None, name="备用账号")
    assert error.value.code == "console_provider_name_taken"

    renamed = await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, False, VOICES, "改名", "edit-1", account=second["account"], name="备用二号")
    state = page(await queries.provider_configs(principal), "speech", "minimax", second["account"])
    assert renamed["account"] == second["account"] and state["name"] == "备用二号" and state["key_hint"] == "2222"
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, False, VOICES, "改名", "edit-2", account="00000000", name="不存在")
    assert error.value.code == "console_provider_not_found"
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, False, VOICES, "x", "edit-3", account="NOT-HEX!", name="x")
    assert error.value.code == "console_provider_invalid"


@pytest.mark.asyncio
async def test_a_connection_with_enabled_models_cannot_be_removed(db, console, console_role, tmp_path):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    _, second = await two_accounts(catalog, principal)
    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS)
    await binder.refresh()
    prices = {"unicode_scalar": "1"}
    result = await catalog.import_models(principal, "audio.speech", second["provider_id"], ["speech-hd"], prices, True, "添加", "imp-1")
    model = result["added"][0]
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, True, VOICES, "删除", "del-1", account=second["account"])
    assert error.value.code == "console_provider_in_use"
    await catalog.set_media_state(principal, model, False, "停用", "off-1")
    assert (await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, True, VOICES, "删除", "del-2", account=second["account"]))["configured"] is False
    assert page(await queries.provider_configs(principal), "speech", "minimax", second["account"]) is None
    with pytest.raises(PlatformError) as error:
        await catalog.save_provider_config(principal, "speech", "minimax", MINIMAX, None, False, VOICES, "复活", "del-3", account=second["account"], name="x")
    assert error.value.code == "console_provider_not_found"
    binder._checked = 0
    settings, _ = await binder.refresh()
    assert second["provider_id"] not in settings.providers


@pytest.mark.asyncio
async def test_the_same_model_on_two_accounts_is_told_apart_by_name(db, console, console_role, tmp_path):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    first, second = await two_accounts(catalog, principal)
    await ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS).refresh()
    prices = {"unicode_scalar": "1"}
    one = await catalog.import_models(principal, "audio.speech", first["provider_id"], ["speech-hd"], prices, True, "添加", "twin-1")
    two = await catalog.import_models(principal, "audio.speech", second["provider_id"], ["speech-hd"], prices, True, "添加", "twin-2")
    names = {row["id"]: row["name"] for row in (await queries.model_catalog(principal))["items"]}
    assert one["added"] == ["speech-hd"] and names["speech-hd"] == "speech-hd"
    assert two["added"][0].startswith(second["provider_id"] + ":") and names[two["added"][0]] == "speech-hd（备用账号）"


@pytest.mark.asyncio
async def test_the_desktop_never_gets_more_models_than_it_accepts(monkeypatch):
    import types

    import platform_service.media_runtime as module

    async def catalog(scope, mode, bindings):
        return {"models": [{"id": f"m{i}", "capability": "image.generate"} for i in range(101)]}

    async def sync():
        return None

    runtime = module.MediaRuntime.__new__(module.MediaRuntime)
    runtime.service, runtime.settings, runtime.bindings = types.SimpleNamespace(catalog=catalog), types.SimpleNamespace(mode="live", providers={}), {}
    runtime._sync, runtime._provider = sync, lambda model, capability: types.SimpleNamespace(adapter="x")
    monkeypatch.setattr(module, "supported_metering", lambda row, provider: True)
    assert len((await runtime.catalog({}))["models"]) == module.MAX_PUBLISHED_MODELS == 100


def test_voices_may_wait_for_a_pull_only_where_a_supplier_can_list_them():
    assert clean_options("speech", "fish", {"voices": []}) == {"voices": []}
    assert clean_options("speech", "minimax", {"voices": ["Chinese (Mandarin)_Reliable_Executive"], "group_id": ""})["voices"] == ["Chinese (Mandarin)_Reliable_Executive"]
    assert clean_options("speech", "fish", {"voices": [], "voice_query": " 旁白 "}) == {"voice_query": "旁白", "voices": []}
    for platform, bad in (("gateway", {"voices": []}), ("gemini", {"voices": []}), ("minimax", {"voices": [], "group_id": "", "voice_query": "x"}), ("fish", {"voices": [], "voice_query": "x" * 65}), ("fish", {"voices": [], "voice_query": 1}), ("fish", {"voices": [" a"]})):
        with pytest.raises(PlatformError):
            clean_options("speech", platform, bad)
    assert {p["id"]: (p["lists_voices"], p["voice_search"]) for p in public_platforms("speech")} == {"gateway": (False, False), "dashscope": (True, False), "minimax": (True, False), "fish": (True, True), "gemini": (False, False)}


def voice_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fish_lists_own_voices_page_by_page_then_chinese_or_searched_public_ones():
    seen = []

    def handler(request):
        params = dict(request.url.params)
        seen.append(params)
        assert request.headers["authorization"] == "Bearer k"
        if "self" not in params:
            return httpx.Response(200, json={"items": [{"_id": "pub1", "title": "公开旁白", "state": "trained"}, {"_id": "abc", "title": "重复"}], "has_more": False})
        page = int(params["page_number"])
        return httpx.Response(200, json={"items": [{"_id": f"own{page}", "title": f"声音{page}", "state": "trained"}, {"_id": "abc", "title": "训练中", "state": "training"}, {"_id": "bad/id", "title": "x"}], "has_more": page == 1})

    async with voice_client(handler) as client:
        failure, voices = await list_voices(client, "fish", "https://api.fish.audio", "k", {})
        assert failure is None and [(v["id"], v["name"], v["kind"]) for v in voices] == [("own1", "声音1", "mine"), ("own2", "声音2", "mine"), ("pub1", "公开旁白", "public"), ("abc", "重复", "public")]
        assert [p.get("self") for p in seen] == ["true", "true", None] and seen[-1]["language"] == "zh" and "title" not in seen[-1]
        seen.clear()
        _, voices = await list_voices(client, "fish", "https://api.fish.audio", "k", {"voice_query": "旁白"})
    assert [v["kind"] for v in voices] == ["mine", "mine", "public", "public"] and seen[-1]["title"] == "旁白" and "self" not in seen[-1] and "language" not in seen[-1]


@pytest.mark.asyncio
async def test_minimax_lists_every_kind_and_a_bad_key_says_so():
    def handler(request):
        assert request.method == "POST" and json.loads(request.content) == {"voice_type": "all"} and request.url.params["GroupId"] == "g1"
        return httpx.Response(200, json={"base_resp": {"status_code": 0}, "system_voice": [{"voice_id": "Chinese (Mandarin)_Lady", "voice_name": "知性女声"}], "voice_cloning": [{"voice_id": "my-clone"}], "voice_generation": []})

    async with voice_client(handler) as client:
        failure, voices = await list_voices(client, "minimax", "https://api.minimax.chat", "k", {"group_id": "g1"})
    assert failure is None and [(v["id"], v["name"], v["kind"]) for v in voices] == [("my-clone", "my-clone", "cloned"), ("Chinese (Mandarin)_Lady", "知性女声", "system")]
    async with voice_client(lambda request: httpx.Response(200, json={"base_resp": {"status_code": 2049}})) as client:
        assert await list_voices(client, "minimax", "https://api.minimax.chat", "k", {}) == ("auth_failed", [])


@pytest.mark.asyncio
async def test_dashscope_lists_finished_clones_and_supplier_failures_are_named():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization" and body["input"]["action"] == "list_voice"
        return httpx.Response(200, json={"output": {"voice_list": [{"voice_id": "ready", "status": "OK"}, {"voice_id": "busy", "status": "DEPLOYING"}]}})

    async with voice_client(handler) as client:
        assert (await list_voices(client, "dashscope", "https://dashscope.aliyuncs.com", "k", {}))[1] == [{"id": "ready", "name": "ready", "kind": "cloned"}]
    async with voice_client(lambda request: httpx.Response(401, json={})) as client:
        assert await list_voices(client, "fish", "https://api.fish.audio", "k", {}) == ("auth_failed", [])
    async with voice_client(lambda request: httpx.Response(500, text="oops")) as client:
        assert await list_voices(client, "dashscope", "https://dashscope.aliyuncs.com", "k", {}) == (None, [])

    def down(request):
        raise httpx.ConnectError("down")

    async with voice_client(down) as client:
        assert await list_voices(client, "minimax", "https://api.minimax.chat", "k", {}) == ("unreachable", [])
    big = {"base_resp": {"status_code": 0}, "system_voice": [{"voice_id": f"v{i}"} for i in range(MAX_VOICES + 5)]}
    async with voice_client(lambda request: httpx.Response(200, json=big)) as client:
        assert len((await list_voices(client, "minimax", "https://api.minimax.chat", "k", {}))[1]) == MAX_VOICES


@pytest.mark.asyncio
async def test_a_connection_can_be_saved_without_voices_then_picked_from_the_pulled_list(db, console, console_role, tmp_path):
    await seed(db)
    catalog, queries, principal, _ = await admin_catalog(console, console_role)
    url = "https://api.fish.audio"
    created = await catalog.save_provider_config(principal, "speech", "fish", url, "fish-key-1234", False, {"voices": []}, "接入", "v1", account=None, name="Fish test")
    down = []

    def handler(request):
        if down:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"items": [{"_id": "voice1", "title": "温柔女声", "state": "trained"}] if "self" in request.url.params else [], "has_more": False})

    client = voice_client(handler)
    binder = ManagedBindings(db, "media", media_settings(tmp_path), MEDIA, keys=KEYS, client=client)
    settings, _ = await binder.refresh()
    assert created["provider_id"] not in settings.providers
    await binder._probe_task
    state = page(await queries.provider_configs(principal), "speech", "fish", created["account"])
    assert state["status"]["state"] == "no_model_list" and state["status"]["voices"] == [{"id": "voice1", "name": "温柔女声", "kind": "mine"}]

    down.append(True)
    binder._probed.clear()
    await binder.probe_due()
    state = page(await queries.provider_configs(principal), "speech", "fish", created["account"])
    assert state["status"]["state"] == "unreachable" and [v["id"] for v in state["status"]["voices"]] == ["voice1"]

    await catalog.save_provider_config(principal, "speech", "fish", url, None, False, {"voices": ["voice1"]}, "选择音色", "v2", account=created["account"], name="Fish test")
    binder._checked = 0
    settings, _ = await binder.refresh()
    assert settings.providers[created["provider_id"]].audio_profile["voices"] == ["voice1"] and settings.providers[created["provider_id"]].secret == "fish-key-1234"
    await client.aclose()


def test_the_agent_is_told_which_voices_a_model_accepts():
    import types

    import platform_service.media_runtime as module

    parameters = {"type": "object", "required": ["voice"], "additionalProperties": False, "properties": {"voice": {"type": "string", "maxLength": 128}}}
    speech = {"capability": "audio.speech", "parameters": parameters}
    provider = types.SimpleNamespace(audio_profile={"voices": ["a", "b"]}, adapter="fish_speech_v1", capabilities=("audio.speech",))
    assert module._with_voices(speech, provider)["parameters"]["properties"]["voice"] == {"type": "string", "maxLength": 128, "enum": ["a", "b"]}
    assert parameters["properties"]["voice"] == {"type": "string", "maxLength": 128}
    other = {"capability": "image.generate", "parameters": {"properties": {}}}
    assert module._with_voices(other, provider) is other
    fixed = {"capability": "audio.speech", "parameters": {"properties": {"voice": {"type": "string", "enum": ["narrator"]}}}}
    assert module._with_voices(fixed, provider) is fixed
    assert module._with_voices(speech, types.SimpleNamespace(audio_profile=None)) is speech
