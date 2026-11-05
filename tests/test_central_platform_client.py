import json
import stat
import time

import httpx
import pytest

from easel.harnesses.base import HarnessError


def auth(**changes):
    scope = dict(
        user_id="user",
        product_id="easel",
        tenant_id="tenant",
        billing_account_id="wallet",
        session_id="device",
        authorization_epoch=0,
    )
    return dict(
        access_token="ACCESS-CENTRAL-SECRET",
        refresh_token="REFRESH-CENTRAL-SECRET",
        session_id="device",
        expires_at=time.time() + 600,
        refresh_expires_at=time.time() + 1200,
        scope=scope,
        account=dict(id="user", email="x@example.test", email_verified=True),
        **changes,
    )


def client(root, handler, **kwargs):
    from easel.platform.central import CentralClient

    return CentralClient(
        root,
        transport=httpx.MockTransport(handler),
        url="http://127.0.0.1:8791",
        **kwargs,
    )


def test_private_session_v2_and_legacy_isolation(tmp_path):
    from easel.platform.session import SessionStore

    legacy = SessionStore(tmp_path)
    with legacy.locked():
        legacy.write(dict(version=1, url="http://127.0.0.1:8790"))
    original = legacy.path.read_bytes()
    c = client(tmp_path, lambda r: httpx.Response(200, json={"ok": True}))
    c.save_auth(auth())
    assert c.store.path != legacy.path
    assert json.loads(c.store.path.read_text())["version"] == 2
    assert stat.S_IMODE(c.store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(c.store.directory.stat().st_mode) == 0o700
    assert "SECRET" not in json.dumps(c.status(verify=False))
    assert legacy.path.read_bytes() == original
    from easel.platform.privacy import sensitive_values

    assert "ACCESS-CENTRAL-SECRET" in sensitive_values(tmp_path)
    with pytest.raises(HarnessError):
        c.authorize("model")


def test_refresh_rotation_and_logout_revoke_private_session(tmp_path):
    calls = []

    def handler(r):
        calls.append((r.url.path, r.headers.get("authorization")))
        if r.url.path.endswith("refresh"):
            value = auth()
            value["access_token"] = "ROTATED-CENTRAL-SECRET"
            return httpx.Response(200, json=value)
        if r.url.path.endswith("me"):
            return httpx.Response(
                200, json={"account": auth()["account"], "scope": auth()["scope"]}
            )
        return httpx.Response(200, json={"ok": True})

    c = client(tmp_path, handler)
    value = auth()
    value["expires_at"] = 0
    c.save_auth(value)
    assert c.status()["authenticated"]
    assert calls[0][0] == "/v2/auth/refresh"
    assert c.logout()["authenticated"] is False
    assert ("/v2/auth/logout", "Bearer ROTATED-CENTRAL-SECRET") in calls
    assert "auth" not in c.store.read()


def test_central_store_symlink_and_scope_rejection(tmp_path):
    c = client(tmp_path, lambda r: None)
    invalid = auth()
    invalid["scope"]["product_id"] = "other"
    with pytest.raises(HarnessError):
        c.save_auth(invalid)
    c.store.directory.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.write_text("preserved")
    c.store.path.symlink_to(outside)
    with pytest.raises(HarnessError):
        c.save_auth(auth())
    assert outside.read_text() == "preserved"


def test_response_credentials_and_errors_do_not_leave_client(tmp_path):
    c = client(
        tmp_path,
        lambda r: httpx.Response(200, json={"access_token": "ACCESS-CENTRAL-SECRET"}),
    )
    c.save_auth(auth())
    with pytest.raises(HarnessError):
        c.call("GET", "/v2/wallet")


def test_iso_dates_from_central_are_normalized(tmp_path):
    from datetime import datetime, timezone

    c = client(tmp_path, lambda r: None)
    value = auth()
    value["expires_at"] = datetime.fromtimestamp(
        value["expires_at"], timezone.utc
    ).isoformat()
    value["refresh_expires_at"] = datetime.fromtimestamp(
        value["refresh_expires_at"], timezone.utc
    ).isoformat()
    c.save_auth(value)
    assert c.status(verify=False)["authenticated"]


def test_scope_contains_only_six_identity_fields(tmp_path):
    c = client(tmp_path, lambda r: None)
    value = auth()
    value["scope"]["private_extra"] = "opaque"
    with pytest.raises(HarnessError):
        c.save_auth(value)


def test_central_refresh_serializes_across_native_clients(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    calls = []

    def handler(r):
        if r.url.path.endswith("refresh"):
            calls.append(r.url.path)
            value = auth()
            value["access_token"] = "ROTATED-CENTRAL-SECRET"
            return httpx.Response(200, json=value)
        assert r.headers["authorization"] == "Bearer ROTATED-CENTRAL-SECRET"
        return httpx.Response(200, json={"ok": True})

    a = client(tmp_path, handler)
    value = auth()
    value["expires_at"] = 0
    a.save_auth(value)
    b = client(tmp_path, handler)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda native: native.call("GET", "/v2/wallet"), [a, b]))
    assert calls == ["/v2/auth/refresh"]


@pytest.mark.parametrize(
    "reason", ["session_invalid", "refresh_replayed", "natural_expiry"]
)
def test_terminal_central_auth_clears_private_state_and_allows_new_login(
    tmp_path, reason
):
    requests = []

    def handler(r):
        requests.append(r.url.path)
        if r.url.path.endswith("transactions"):
            return httpx.Response(
                200,
                json={
                    "transaction_id": "new-transaction",
                    "authorize_url": "http://127.0.0.1:8791/oidc/easel/authorize?transaction_id=new",
                    "expires_at": time.time() + 300,
                },
            )
        return httpx.Response(401, json={"error": {"code": reason}})

    c = client(tmp_path, handler)
    value = auth()
    if reason == "refresh_replayed":
        value["expires_at"] = 0
    if reason == "natural_expiry":
        value["refresh_expires_at"] = 0
    c.save_auth(value)
    status = c.status()
    expected = "invalid_authentication" if reason == "natural_expiry" else reason
    assert status["error"]["code"] == expected
    assert (
        not status["authenticated"]
        and status["account"] is None
        and status["scope"] is None
    )
    assert "auth" not in c.store.read()
    started = c.begin(
        "http://127.0.0.1:9876/platform-auth/callback", "challenge", "state"
    )
    assert started["transaction_id"] == "new-transaction"
    c.save_auth(auth())
    assert c.status(verify=False)["authenticated"]


@pytest.mark.parametrize(
    "reason", ["session_invalid", "refresh_replayed", "natural_expiry"]
)
def test_logout_terminal_central_session_always_recovers(tmp_path, reason):
    c = client(
        tmp_path, lambda r: httpx.Response(401, json={"error": {"code": reason}})
    )
    value = auth()
    if reason == "refresh_replayed":
        value["expires_at"] = 0
    if reason == "natural_expiry":
        value["refresh_expires_at"] = 0
    c.save_auth(value)
    assert not c.logout()["authenticated"]
    assert "auth" not in c.store.read()


@pytest.mark.parametrize("failure", ["network", "server", "unknown_401"])
def test_nonterminal_central_failures_preserve_private_session(tmp_path, failure):
    def handler(r):
        if failure == "network":
            raise httpx.ConnectError("PRIVATE-REMOTE-DETAIL", request=r)
        return httpx.Response(
            503 if failure == "server" else 401,
            json={"error": {"code": "PRIVATE-REMOTE-DETAIL"}},
        )

    c = client(tmp_path, handler)
    c.save_auth(auth())
    before = c.store.path.read_bytes()
    status = c.status()
    assert (
        not status["authenticated"]
        and status["error"]["code"] == "platform_unavailable"
    )
    assert "PRIVATE-REMOTE-DETAIL" not in json.dumps(status)
    assert c.store.path.read_bytes() == before
    with pytest.raises(HarnessError):
        c.logout()
    assert c.store.path.read_bytes() == before


def test_authenticated_call_clears_only_explicit_session_invalid(tmp_path):
    c = client(
        tmp_path,
        lambda r: httpx.Response(401, json={"error": {"code": "session_invalid"}}),
    )
    c.save_auth(auth())
    with pytest.raises(HarnessError) as caught:
        c.call("GET", "/v2/wallet")
    assert caught.value.code == "session_invalid"
    assert "auth" not in c.store.read()
