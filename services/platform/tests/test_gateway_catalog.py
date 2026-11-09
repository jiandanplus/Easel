"""Console-managed bindings reach the runtime, and a missing channel is refused before any spend."""
import dataclasses

import httpx
import pytest

from platform_service import gateway_catalog
from platform_service.errors import PlatformError
from platform_service.gateway_catalog import GatewayPreflight, ManagedBindings


@dataclasses.dataclass(frozen=True)
class Provider:
    version: str
    models: dict


@dataclasses.dataclass(frozen=True)
class Settings:
    mode: str
    providers: dict

    @property
    def bindings(self):
        return {model: name for name, provider in self.providers.items() for model in provider.models}


class Db:
    def __init__(self):
        self.rows, self.statements = [], []

    def connection(self):
        db = self

        class Context:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, sql, params=()):
                db.statements.append(sql)

                class Result:
                    async def fetchone(self):
                        return {"ready": True}

                    async def fetchall(self):
                        return db.rows
                return Result()
        return Context()


def managed(db, mode="live"):
    settings = Settings(mode, {"gateway": Provider("1", {"fixed": "upstream-fixed"})})
    return ManagedBindings(db, "media", settings, lambda provider: ("adapter", "1", ("image.generate",)))


@pytest.mark.asyncio
async def test_new_binding_reaches_the_provider_and_keeps_file_models(monkeypatch):
    db, clock = Db(), [100.0]
    monkeypatch.setattr(gateway_catalog.time, "monotonic", lambda: clock[0])
    bindings = managed(db)
    settings, _ = await bindings.refresh()
    assert settings.providers["gateway"].models == {"fixed": "upstream-fixed"}
    db.rows = [{"model_id": "new", "provider_id": "gateway", "upstream_model": "upstream-new"}]
    settings, _ = await bindings.refresh()
    assert "new" not in settings.providers["gateway"].models, "reads are throttled"
    clock[0] += gateway_catalog.REFRESH_SECONDS + 1
    settings, table = await bindings.refresh()
    assert settings.providers["gateway"].models == {"fixed": "upstream-fixed", "new": "upstream-new"}
    assert table["new"] == "gateway" and sum("provider_catalog" in s for s in db.statements) == 1


@pytest.mark.asyncio
async def test_managed_row_cannot_override_a_file_model_or_invent_a_provider(monkeypatch):
    db = Db()
    db.rows = [{"model_id": "fixed", "provider_id": "gateway", "upstream_model": "other"},
               {"model_id": "stray", "provider_id": "missing", "upstream_model": "x"}]
    settings, table = await managed(db).refresh()
    assert settings.providers["gateway"].models == {"fixed": "upstream-fixed"} and set(table) == {"fixed"}


@pytest.mark.asyncio
async def test_disabled_mode_touches_nothing():
    db = Db()
    await managed(db, "disabled").refresh()
    assert db.statements == []


def preflight(handler):
    return GatewayPreflight(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "media_unavailable")


@pytest.mark.asyncio
async def test_listed_model_passes_and_the_list_is_cached():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "served"}]})
    check = preflight(handler)
    await check.check("http://gateway/v1/models", "secret", "served")
    await check.check("http://gateway/v1/models", "secret", "served")
    assert len(calls) == 1 and calls[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(200, json={"data": [{"id": "other"}]}), httpx.Response(500),
                                      httpx.Response(200, json={"data": "x"}), httpx.Response(200, content=b"<html>")])
async def test_unserved_model_or_bad_answer_is_refused(response):
    with pytest.raises(PlatformError) as caught:
        await preflight(lambda request: response).check("http://gateway/v1/models", "secret", "served")
    assert caught.value.status == 503 and caught.value.code == "media_unavailable"


@pytest.mark.asyncio
async def test_unreachable_gateway_is_refused():
    def handler(request):
        raise httpx.ConnectError("down")
    with pytest.raises(PlatformError):
        await preflight(handler).check("http://gateway/v1/models", "secret", "served")
