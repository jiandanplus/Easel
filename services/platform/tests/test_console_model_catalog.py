"""Console model catalog (migration 027) through the low Console role on a real PostgreSQL."""
import pytest

from platform_service.errors import PlatformError
from platform_service.model_templates import build_rate, points_to_part
from test_console_auth import console  # noqa: F401 (fixture)
from test_console_owner_allowance import low_catalog
from test_console_permissions import console_role  # noqa: F401 (fixture)
from test_membership import seed

TEXT_PRICES = {"input": "4", "output": "16"}


async def providers(db):
    async with db.connection() as c:
        await c.execute("""INSERT INTO provider_catalog(kind,provider_id,provider_version,adapter,protocol_version,capabilities) VALUES
            ('execution','gateway','v1',NULL,NULL,'["text"]'),('media','images','v1','openai_images_v1','1','["image.generate"]')""")


def test_prices_are_entered_in_points_and_stored_exactly():
    assert points_to_part("4", 1) == {"numerator": "4000000", "denominator": "1"}
    assert points_to_part("0.08", 1) == {"numerator": "80000", "denominator": "1"}
    assert points_to_part("2", 1000) == {"numerator": "2000", "denominator": "1"}
    rate = build_rate("audio.speech", "v", {"unicode_scalar": "1"})
    assert rate["meters"][0]["numerator"] == "100" and rate["meters"][0]["basis"] == "verified_input_v1"
    for bad in ("abc", "-1", 4):
        with pytest.raises(PlatformError):
            points_to_part(bad, 1)


@pytest.mark.asyncio
async def test_operator_adds_models_sets_prices_and_enables_them(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "operator")
    created = await catalog.create_managed_model(principal, "my-text", "我的文本", "text", "gateway", "upstream-text", TEXT_PRICES, "新增", "m1")
    assert created == {"id": "my-text", "kind": "execution", "enabled": False}
    media = await catalog.create_managed_model(principal, "my-image", "我的生图", "image.generate", "images", "upstream-image", {"image": "2"}, "新增", "m2")
    assert media["kind"] == "media"
    listing = await queries.model_catalog(principal)
    rows = {row["id"]: row for row in listing["items"]}
    assert rows["my-text"]["upstream_model"] == "upstream-text" and rows["my-text"]["enabled"] is False
    assert {p["provider_id"] for p in listing["providers"]} == {"gateway", "images"} and listing["templates"]
    await catalog.set_model_price(principal, "execution", "my-text", {"input": "8", "output": "32"}, "调价", "p1")
    rows = {row["id"]: row for row in (await queries.model_catalog(principal))["items"]}
    assert rows["my-text"]["custom_price"] is True and rows["my-text"]["rate"]["input"]["numerator"] == "8000000"
    updated = rows["my-text"]["updated_at"]
    assert (await catalog.set_model_state(principal, "my-text", True, "上线", "e1", updated))["enabled"] is True
    assert (await catalog.set_media_state(principal, "my-image", True, "上线", "e2"))["enabled"] is True
    trial = await catalog.set_model_trial(principal, "execution", "my-text", "u", True, "内测", "t1")
    assert trial["wallet"] == "wallet"
    rows = {row["id"]: row for row in (await queries.model_catalog(principal))["items"]}
    assert rows["my-text"]["trial_wallets"] == 1
    await catalog.set_model_trial(principal, "execution", "my-text", "u", False, "结束", "t2")
    with pytest.raises(PlatformError):
        await catalog.create_managed_model(principal, "bad", "x", "text", "missing-provider", "u", TEXT_PRICES, "r", "m3")
    with pytest.raises(PlatformError):
        await catalog.create_managed_model(principal, "bad2", "x", "video.generate", "images", "u", {"video_ms": "1"}, "r", "m4")


@pytest.mark.asyncio
async def test_plan_with_model_access_and_multiplier_publishes_and_lists(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "admin")
    await catalog.create_managed_model(principal, "my-text", "我的文本", "text", "gateway", "upstream", TEXT_PRICES, "新增", "m1")
    base = dict(id="pro-v1", name="专业版", version="1", included_products=["easel"], features=["text"], credits="50000000", days=30, concurrency=2, test_only=False)
    pricing = {"default": {"numerator": "4", "denominator": "5"}, "models": {"my-text": {"numerator": "1", "denominator": "2"}}}
    await catalog.publish_plan(principal, {**base, "models": ["my-text"], "pricing": pricing}, "发布", "plan-1")
    row = next(item for item in (await queries.plans(principal))["items"] if item["id"] == "pro-v1")
    assert row["models"] == ["my-text"] and row["pricing"] == pricing
    with pytest.raises(PlatformError) as error:
        await catalog.publish_plan(principal, {**base, "id": "pro-v2", "name": "x", "models": ["ghost"]}, "发布", "plan-2")
    assert error.value.code == "console_plan_model_unknown"


@pytest.mark.asyncio
async def test_pulled_models_are_added_and_enabled_in_one_step(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "admin")
    result = await catalog.import_models(principal, "text", "gateway", ["vendor/model-a", "model b"[:5] + "b"], TEXT_PRICES, True, "拉取", "i1")
    assert result == {"added": ["vendor-model-a", "modelb"], "skipped": [], "enabled": True}
    rows = {row["id"]: row for row in (await queries.model_catalog(principal))["items"]}
    assert rows["vendor-model-a"]["enabled"] is True and rows["vendor-model-a"]["upstream_model"] == "vendor/model-a" and rows["modelb"]["name"] == "modelb"
    assert await catalog.import_models(principal, "text", "gateway", ["vendor/model-a", "modelb"][:2], TEXT_PRICES, True, "拉取", "i1b") == {"added": [], "skipped": ["vendor/model-a", "modelb"], "enabled": True}

    media = await catalog.import_models(principal, "image.generate", "images", ["vendor/model-a"], {"image": "2"}, False, "拉取", "i2")
    assert media["added"] == ["images:vendor-model-a"]  # The id was taken by the text model.
    rows = {row["id"]: row for row in (await queries.model_catalog(principal))["items"]}
    assert rows["images:vendor-model-a"]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [[], ["a"] * 2, ["has space"], [f"m{i}" for i in range(101)]])
async def test_pulled_model_lists_are_checked(db, console, console_role, names):
    await seed(db)
    await providers(db)
    catalog, _, principal, _ = await low_catalog(console, console_role, "admin")
    with pytest.raises(PlatformError):
        await catalog.import_models(principal, "text", "gateway", names, TEXT_PRICES, True, "拉取", "bad-1")


@pytest.mark.asyncio
async def test_one_bad_model_leaves_nothing_behind(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "admin")
    with pytest.raises(PlatformError):
        await catalog.import_models(principal, "image.edit", "images", ["a", "b"], {"image": "2"}, True, "拉取", "bad-2")  # This interface cannot edit.
    assert not [row for row in (await queries.model_catalog(principal))["items"] if row["managed"]]
