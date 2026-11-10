"""Separate functional acceptance; the historical 200ms test is unchanged."""
import pytest

from b2_legacy_media_fixtures import legacy_media, legacy_db as db
from b2_cancellation_cases import expired_before_dispatch, inflight_deadline_cancellation


@pytest.mark.asyncio
async def test_expired_before_dispatch_becomes_manual_without_http(db, legacy_media, monkeypatch):
    result = await expired_before_dispatch(db, legacy_media, monkeypatch)
    assert result['worker_claims'] == 0 and result['http_gets'] == 0


@pytest.mark.asyncio
async def test_entered_get_is_cancelled_by_original_deadline(db, legacy_media, monkeypatch):
    result = await inflight_deadline_cancellation(db, legacy_media, monkeypatch)
    assert result['worker_claims'] == 1 and result['actual_cancel']
