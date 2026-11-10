"""Signed synthetic OP through the actual BFF, auth, catalog and real PG."""
from urllib.parse import parse_qs, urlsplit
import re
import time

import httpx
import pytest

from platform_service.console_app import create_console_app
from platform_service.contracts import OperatorIdentity
from test_console_auth import console
from test_membership import seed


@pytest.mark.asyncio
async def test_signed_login_pending_approval_catalog_audit_and_logout(console, db, tmp_path):
    auth, provider = console
    _, _, plan, _ = await seed(db)
    (tmp_path / "index.html").write_text("<!doctype html><title>Central Console</title>")
    app = create_console_app(auth.settings, database=db, auth=auth, static_dir=tmp_path, provider_ids=())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=auth.settings.public_url) as client:
        assert (await client.get("/health")).json()["assets_ready"] is True
        assert (await client.get("/api/session", headers={"Authorization": "Bearer product-only"})).status_code == 401

        async def login():
            provider.overrides = {"auth_time": int(time.time())}
            start = await client.get("/auth/login")
            assert start.status_code == 302
            provider.params = {**parse_qs(urlsplit(start.headers["location"]).query), "product": "console"}
            assert provider.params["max_age"] == ["0"]
            return await client.get("/oidc/console/callback", params={"code": "synthetic-code", "state": provider.params["state"][0]})

        pending = await login()
        assert pending.status_code == 403
        request_id = re.search(r"<code>([a-f0-9]{32})</code>", pending.text).group(1)
        assert (await client.get("/api/users")).status_code == 401
        await auth.approve_request(request_id, "admin", OperatorIdentity("fixture-owner", frozenset({"console:bootstrap"})))
        accepted = await login()
        assert accepted.status_code == 302 and accepted.headers["location"] == "/"
        session = (await client.get("/api/session")).json()
        assert session["admin"]["role"] == "admin"
        headers = {"Origin": auth.settings.public_url, "X-Console-CSRF": session["csrf_token"], "Idempotency-Key": "http-reviewed-001"}
        body = {"definition": {**plan, "id": "http-reviewed", "name": "Reviewed HTTP plan"}, "reason": "Signed HTTP integration fixture"}
        first = await client.post("/api/plans", json=body, headers=headers)
        assert first.status_code == 200
        replay = await client.post("/api/plans", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == first.json()
        audit = (await client.get("/api/audits")).json()
        assert len(audit["items"]) == 1 and audit["items"][0]["target_id"] == "http-reviewed"
        assert (await client.get("/api/wallets/wallet")).status_code == 200
        assert (await client.post("/auth/logout", headers=headers)).status_code == 200
        assert (await client.get("/api/overview")).status_code == 401
