"""Internal environment may require fresh hosted login; old clients stay unchanged."""
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
import pytest
from test_identity import identity


def test_oidc_force_login_is_optional_strict_boolean():
    from platform_service.config import OIDCClient
    assert OIDCClient("client", "secret").force_login is False
    assert OIDCClient("client", "secret", force_login=True).force_login is True
    for value in (1, "true", None):
        with pytest.raises(ValueError):
            OIDCClient("client", "secret", force_login=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("force_login", [False, True])
async def test_force_login_authorize_parameter_and_exact_session_provenance(identity, force_login):
    from platform_service.config import OIDCClient
    from test_identity import handoff
    service, _ = identity
    original = service.settings.oidc_clients["easel"]
    service.settings.oidc_clients["easel"] = OIDCClient(original.client_id, original.client_secret, force_login=force_login)
    original_authorize = service.authorize
    seen=[]
    async def observe(*args, **kwargs):
        value=await original_authorize(*args, **kwargs)
        seen.append(parse_qs(urlsplit(value["url"]).query))
        return value
    service.authorize=observe
    credentials=await service.redeem(await handoff(identity))
    assert seen[0].get("prompt") == (["login"] if force_login else None)
    async with service.db.connection() as c:
        row=await (await c.execute("SELECT s.source_transaction_id,h.transaction_id FROM identity_sessions s JOIN identity_handoffs h ON h.transaction_id=s.source_transaction_id WHERE s.id=%s AND h.consumed",(credentials["session_id"],))).fetchone()
        assert row and row['source_transaction_id']==row['transaction_id']
