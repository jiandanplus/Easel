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
from platform_service.provider_channels import CHANNELS, PLATFORMS, build_provider, clean_base_url, clean_key, clean_options, provider_id, public_platforms, seal_scope
from platform_service.provider_secrets import ProviderKeys, generate, seal
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


def page(listing, channel, platform="gateway"):
    row = next(c for c in listing["channels"] if c["channel"] == channel)
    return next(p for p in row["platforms"] if p["id"] == platform)


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


FIELD_VALUES = {"voices": ["alloy"], "download_origins": ["https://cdn.example"], "download_path_prefixes": ["/audio/"], "group_id": "g1", "poll_url": ""}
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
    if PLATFORMS[channel][platform]["probe"] != "bearer":
        assert provider.preflight_models is False


def test_only_platforms_with_a_model_list_are_probed_and_named():
    assert provider_id("video") == "console-video" and provider_id("video", "kling") == "console-video-kling"
    assert seal_scope("video") == "video" and seal_scope("video", "kling") == "video/kling"
    assert {p["id"]: p["second_key"] for p in public_platforms("video")}["kling"] == "Secret Key"
    with pytest.raises(PlatformError):
        clean_key("only-one", parts=2)
    assert clean_key("ak|sk", parts=2) == "ak|sk"


def test_page_options_are_strict():
    for channel, platform, bad in (("speech", "gateway", {}), ("speech", "gateway", {"voices": []}), ("speech", "gateway", {"voices": ["bad voice"]}), ("chat", "gateway", {"x": 1}),
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
    assert state["config"]["base_url"] == URL and state["config"]["key_hint"] == "1234" and state["status"] is None
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
    state = page(await queries.provider_configs(principal), "video")
    assert state["config"]["key_hint"] is None
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
    assert seen == [(URL + "/models", "Bearer " + SECRET)]
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
    assert page(listing, "video", "kling")["config"]["key_hint"] == "-key" and page(listing, "video", "dashscope")["config"]["key_hint"] == "1234"
    assert page(listing, "video")["config"] is None and page(listing, "video", "kling")["lists_models"] is False
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
    assert seen == []  # Kling has no model list to ask.
    async with db.connection() as c:
        row = await (await c.execute("SELECT channel,platform,state FROM provider_config_status")).fetchone()
    assert (row["channel"], row["platform"], row["state"]) == ("video", "kling", "no_model_list")
    await client.aclose()
