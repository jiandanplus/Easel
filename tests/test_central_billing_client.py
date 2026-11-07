import json
import httpx
import pytest
from easel.harnesses.base import HarnessError
from tests.test_central_platform_client import auth, client

ORDER = "A" * 32
BODY = {"offer_id": "fixture-cny-v1", "channel": "wechat", "idempotency_key": "checkout-1"}


def test_billing_exact_allowlist_and_native_bearer(tmp_path):
    seen = []
    def handle(r):
        seen.append(r)
        return httpx.Response(200, json={"id": ORDER, "amount_minor": "9007199254740993", "status": "pending", "credit_state": "pending"})
    c = client(tmp_path, handle)
    c.call("GET", "/v2/billing/catalog?product_id=easel", public=True)
    assert "authorization" not in seen[0].headers
    with pytest.raises(HarnessError):
        c.call("POST", "/v2/orders", BODY)
    c.save_auth(auth())
    for method, path, body in [("POST", "/v2/orders", BODY), ("GET", "/v2/orders", None), ("GET", "/v2/orders/"+ORDER, None), ("POST", "/v2/orders/"+ORDER+"/refresh", None), ("POST", "/v2/orders/"+ORDER+"/close", None)]:
        assert c.call(method, path, body)["amount_minor"] == "9007199254740993"
        assert seen[-1].headers["authorization"] == "Bearer ACCESS-CENTRAL-SECRET"
    for method, path, body in [("POST", "/v2/orders", {**BODY, "amount_minor": "1"}), ("POST", "/v2/orders", {**BODY, "wallet": "other"}), ("POST", "/v2/orders/"+ORDER+"/paid", None), ("POST", "/v2/orders/"+ORDER+"/refund", None), ("GET", "/v2/orders/../wallet", None), ("GET", "/v2/orders/"+ORDER+"?url=https://evil.test", None), ("POST", "/v2/orders/"+ORDER+"/refresh", {"paid": True})]:
        before = len(seen)
        with pytest.raises(HarnessError): c.call(method, path, body)
        assert len(seen) == before
    with pytest.raises(HarnessError): c.call("GET", "/v2/orders", public=True)
    with pytest.raises(HarnessError): c.authorize("model")


@pytest.mark.parametrize("code", ["payments_disabled", "offer_unavailable", "channel_unavailable", "idempotency_conflict", "order_not_found", "order_not_pending", "invalid_request", "reconciliation_required", "payments_unavailable", "offer_not_found", "offer_scope_denied"])
def test_billing_safe_error_mapping_preserves_session(tmp_path, code):
    c = client(tmp_path, lambda r: httpx.Response(409, json={"error": {"code": code, "message": "PRIVATE SIGNATURE"}}))
    c.save_auth(auth())
    before = c.store.path.read_bytes()
    with pytest.raises(HarnessError) as caught: c.call("GET", "/v2/orders")
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)
    assert c.store.path.read_bytes() == before

@pytest.mark.parametrize("field", ["access_token", "refresh_token", "api_key", "api_v3_key", "private_key", "signature"])
def test_billing_never_returns_provider_secrets(tmp_path, field):
    c = client(tmp_path, lambda r: httpx.Response(200, json={"checkout": {field: "PRIVATE-PROVIDER-VALUE"}}))
    c.save_auth(auth())
    with pytest.raises(HarnessError): c.call("GET", "/v2/orders")
