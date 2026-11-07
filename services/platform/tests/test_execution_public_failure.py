"""Failure categories are a bounded public projection, never provider diagnostics."""

import pytest

from platform_service.execution.core import public


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("upstream_timeout", "upstream_timeout"),
        ("upstream_unavailable", "upstream_unavailable"),
        ("upstream_uncertain", "upstream_unavailable"),
        ("lease_expired", "upstream_unavailable"),
        ("upstream_error", "upstream_error"),
        ("invalid_upstream_response", "upstream_error"),
        ("upstream_response_too_large", "upstream_error"),
        ("upstream_event_too_large", "upstream_error"),
        ("invalid_upstream_usage", "invalid_upstream_usage"),
        ("usage_exceeds_reservation", "invalid_upstream_usage"),
        ("cancelled_after_dispatch", "request_cancelled"),
    ],
)
def test_pending_public_failure_is_a_whitelisted_category(reason, expected):
    row = {
        "id": "job",
        "status": "pending_reconciliation",
        "reason": reason,
        "amount": 9728,
        "actual": None,
        "model_definition": {"rate": {"version": "rate-v1"}},
    }
    value = public(row)
    assert value["failure_code"] == expected
    assert value["actual"] is None and value["amount"] == "9728"
    assert "reason" not in value


@pytest.mark.parametrize("reason", [None, "private_provider_diagnostic", "new_unknown_code", {"private": True}])
def test_unknown_reason_never_becomes_a_public_string(reason):
    value = public({"status": "pending_reconciliation", "reason": reason,
                    "model_definition": {"rate": {"version": "v1"}}})
    assert "failure_code" not in value and "reason" not in value


@pytest.mark.parametrize("status", ["reserved", "dispatched", "settled", "released"])
def test_non_pending_status_cannot_inherit_a_failure_category(status):
    value = public({"status": status, "reason": "upstream_timeout",
                    "model_definition": {"rate": {"version": "v1"}}})
    assert "failure_code" not in value and "reason" not in value
