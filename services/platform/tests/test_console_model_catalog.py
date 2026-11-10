"""Console model catalog (migration 027) through the low Console role on a real PostgreSQL."""
import re

import pytest

from platform_service.errors import PlatformError
from platform_service.console_catalog import text_price_ok
from platform_service.execution.rates import cost as cost_text, normalize as normalize_text_rate
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


def test_text_prices_are_points_per_million_tokens():
    """The form asks for points per million tokens; a rate is microcredits per token, so 1 point/M = 1/token."""
    rate = build_rate("text", "v", {"input": "1", "output": "0.5"})
    assert rate == {"version": "v", "input": {"numerator": "1", "denominator": "1"}, "output": {"numerator": "1", "denominator": "2"}}
    assert cost_text(normalize_text_rate(rate), 16384, 1024) == 16896  # about 0.017 points for the largest call
    text_price_ok(normalize_text_rate(rate), {"max_input_tokens": 16384, "max_output_tokens": 1024})
    with pytest.raises(PlatformError) as refused:
        text_price_ok(normalize_text_rate(build_rate("text", "v", {"input": "6000", "output": "6000"})), {"max_input_tokens": 16384, "max_output_tokens": 1024})
    assert refused.value.code == "console_price_too_high"


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
    assert rows["my-text"]["custom_price"] is True and rows["my-text"]["rate"]["input"] == {"numerator": "8", "denominator": "1"}
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
@pytest.mark.parametrize("names", [[], ["a"] * 2, ["two  spaces"], [" lead"], ["tab\tname"], ["wide　space"], ["c1\x85"], [f"m{i}" for i in range(101)]])
async def test_pulled_model_lists_are_checked(db, console, console_role, names):
    await seed(db)
    await providers(db)
    catalog, _, principal, _ = await low_catalog(console, console_role, "admin")
    with pytest.raises(PlatformError):
        await catalog.import_models(principal, "text", "gateway", names, TEXT_PRICES, True, "拉取", "bad-1")


@pytest.mark.asyncio
async def test_supplier_names_with_spaces_and_chinese_are_added(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "admin")
    names = ["Midjourney v8.2", "gpt-image-2.0", "gpt-image-2.0-不超分", "gpt-image-2.0-原生", "完全无限制-Z-Image", "极速生图"]
    result = await catalog.import_models(principal, "image.generate", "images", names, {"image": "2"}, True, "拉取", "zh-1")
    added = result["added"]
    assert len(added) == len(set(added)) == 6 and not result["skipped"]
    assert added[:2] == ["Midjourney-v8.2", "gpt-image-2.0"]
    assert re.fullmatch(r"gpt-image-2\.0-[0-9a-f]{6}", added[2]) and added[2] != added[3]
    assert re.fullmatch(r"Z-Image-[0-9a-f]{6}", added[4]) and re.fullmatch(r"model-[0-9a-f]{6}", added[5])
    rows = {row["id"]: row for row in (await queries.model_catalog(principal))["items"]}
    assert [rows[model]["upstream_model"] for model in added] == names
    assert rows["Midjourney-v8.2"]["name"] == "Midjourney v8.2" and rows["Midjourney-v8.2"]["enabled"] is True


@pytest.mark.asyncio
async def test_one_bad_model_leaves_nothing_behind(db, console, console_role):
    await seed(db)
    await providers(db)
    catalog, queries, principal, _ = await low_catalog(console, console_role, "admin")
    with pytest.raises(PlatformError):
        await catalog.import_models(principal, "image.edit", "images", ["a", "b"], {"image": "2"}, True, "拉取", "bad-2")  # This interface cannot edit.
    assert not [row for row in (await queries.model_catalog(principal))["items"] if row["managed"]]


@pytest.mark.asyncio
async def test_migration_037_divides_console_text_prices_by_a_million(db, console, console_role):
    """Prices saved before the unit fix were a million times too high; 037 appends the corrected price."""
    from pathlib import Path
    import platform_service
    from platform_service.pricing import current_rate
    await seed(db)
    await providers(db)
    catalog, _, principal, _ = await low_catalog(console, console_role, "operator")
    await catalog.create_managed_model(principal, "old-b", "旧文本", "text", "gateway", "u-b", TEXT_PRICES, "新增", "g2")
    async with db.connection() as c:
        # Re-create the stored shapes the old code produced: an inflated definition and an inflated reprice.
        await c.execute("""INSERT INTO execution_models(id,definition,operator_id) SELECT 'old-a', jsonb_set(jsonb_set(definition,'{id}','"old-a"'), '{rate}',
            '{"version":"console-aaaaaaaaaaaa","input":{"numerator":"1000000","denominator":"1"},"output":{"numerator":"3000000","denominator":"2"}}'), 'op'
            FROM execution_models WHERE id='old-b'""")
        await c.execute("""INSERT INTO model_prices(id,kind,model_id,rate,reason,operator_id) VALUES
            (md5('x'),'execution','old-b','{"version":"price-bbbbbbbbbbbb","input":{"numerator":"500000","denominator":"1"},"output":{"numerator":"2000000","denominator":"1"}}','调价','op')""")
        await c.execute("""INSERT INTO execution_models(id,definition,operator_id) SELECT 'internal', jsonb_set(jsonb_set(definition,'{id}','"internal"'),'{verification_ref}','"internal"'), 'op' FROM execution_models WHERE id='old-b'""")
        await c.execute((Path(platform_service.__file__).parent / "migrations/037_text_price_unit.sql").read_text())
        a = await current_rate(c, "execution", "old-a", None)
        b = await current_rate(c, "execution", "old-b", None)
        internal = await current_rate(c, "execution", "internal", "untouched")
    assert a["input"] == {"numerator": "1", "denominator": "1"} and a["output"] == {"numerator": "3", "denominator": "2"}
    assert b["input"] == {"numerator": "1", "denominator": "2"} and b["output"] == {"numerator": "2", "denominator": "1"}
    assert a["version"].startswith("unit-fix-") and internal == "untouched"
