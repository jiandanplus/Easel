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
async def test_operator_adds_models_sets_prices_and_needs_a_real_call_to_enable(db, console, console_role):
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
    with pytest.raises(PlatformError) as error:
        await catalog.set_model_state(principal, "my-text", True, "上线", "e1", updated)
    assert error.value.code == "console_model_needs_trial"
    with pytest.raises(PlatformError) as error:
        await catalog.set_media_state(principal, "my-image", True, "上线", "e2")
    assert error.value.code == "console_model_needs_trial"
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
