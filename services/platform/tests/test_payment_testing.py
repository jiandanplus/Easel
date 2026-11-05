from datetime import datetime, timedelta, timezone
import json

import pytest

from platform_service.errors import PlatformError
from platform_service.payment_config import PaymentSettings
from platform_service.payments import PaymentOrder, RefundRequest


@pytest.fixture
def fixture_provider(tmp_path):
    from platform_service.payment_testing import TestPaymentProvider

    clock = datetime.now(timezone.utc)
    config = PaymentSettings(
        mode="test",
        test_key=b"private-test-key-" * 4,
        test_state_file=tmp_path / "provider-state.json",
    )
    return (
        TestPaymentProvider("wechat", config, now=lambda: clock),
        config,
        PaymentOrder("a" * 32, 1, "CNY", "模拟套餐", clock + timedelta(minutes=15)),
    )


@pytest.mark.asyncio
async def test_fixture_provider_uses_persistent_operator_state_not_browser_claim(
    fixture_provider,
):
    from platform_service.payment_testing import TestPaymentProvider

    provider, config, order = fixture_provider
    assert (await provider.create(order)).qr_code.startswith("easel-test-payment:")
    assert (await provider.query(order)).status == "pending"
    raw, headers = provider.record(order, status="paid", event_id="fixture-payment-1")
    event = provider.verify_notification(raw, headers).to_event()
    assert event["mode"] == "test" and event["amount_minor"] == 1
    recreated = TestPaymentProvider("wechat", config)
    assert (await recreated.query(order)).status == "paid"
    assert (await recreated.close(order)).status == "paid"
    assert config.test_state_file.stat().st_mode & 0o777 == 0o600


def test_fixture_notification_tampering_and_live_mode_cannot_confirm(fixture_provider):
    from platform_service.payment_testing import TestPaymentProvider

    provider, config, order = fixture_provider
    raw, headers = provider.record(order, status="paid", event_id="fixture-payment-1")
    with pytest.raises(PlatformError):
        provider.verify_notification(raw + b" ", headers)
    with pytest.raises(PlatformError):
        provider.verify_notification(raw, {})
    with pytest.raises(PlatformError):
        TestPaymentProvider("wechat", PaymentSettings(mode="live"))
    with pytest.raises(PlatformError):
        provider.record(
            order,
            status="paid",
            event_id="different-financial-event",
            transaction_id="another-trade",
        )


@pytest.mark.asyncio
async def test_fixture_refund_stays_pending_until_operator_fact(fixture_provider):
    provider, _, order = fixture_provider
    provider.record(order, status="paid", event_id="fixture-payment-1")
    refund = RefundRequest("b" * 32, 1, "模拟全额退款")
    assert (await provider.refund(order, refund)).status == "refund_pending"
    assert (await provider.query_refund(order, refund)).status == "refund_pending"
    raw, headers = provider.record(
        order, status="refunded", event_id="fixture-refund-1", refund=refund
    )
    event = provider.verify_notification(raw, headers).to_event()
    assert event["refund_amount_minor"] == 1 and event["amount_minor"] == 1
    assert (await provider.query_refund(order, refund)).status == "refunded"


def test_fixture_state_file_rejects_symlink_and_preserves_other_file(
    fixture_provider, tmp_path
):
    provider, config, order = fixture_provider
    target = tmp_path / "preserved.json"
    target.write_text(json.dumps({"private": "unchanged"}))
    config.test_state_file.symlink_to(target)
    with pytest.raises(PlatformError):
        provider.record(order, status="paid", event_id="fixture-payment-1")
    assert json.loads(target.read_text()) == {"private": "unchanged"}


@pytest.mark.asyncio
async def test_fixture_notice_and_query_have_distinct_ids_and_retries_preserve_notice(
    fixture_provider,
):
    provider, _, order = fixture_provider
    raw, headers = provider.record(order, status="paid", event_id="fixture-payment-1")
    notice = provider.verify_notification(raw, headers)
    queried = await provider.query(order)
    assert queried.event_id != notice.event_id
    original = provider.now()
    provider.now = lambda: original + timedelta(seconds=30)
    retried, _ = provider.record(order, status="paid", event_id="fixture-payment-1")
    assert retried == raw
