"""Live model bindings from the catalog, and a free check before money is at risk."""
import dataclasses
import time

import httpx

from .errors import PlatformError

REFRESH_SECONDS = 5
PREFLIGHT_SECONDS = 30


class ManagedBindings:
    """Merge console-managed models into the file-configured providers."""

    def __init__(self, db, kind, settings, describe):
        self.db, self.kind, self.base, self.describe = db, kind, settings, describe
        self.settings, self.bindings = settings, settings.bindings
        self._checked, self._written, self._seen = 0.0, False, None

    async def _publish_providers(self):
        async with self.db.connection() as c:
            for name, provider in self.base.providers.items():
                adapter, protocol, capabilities = self.describe(provider)
                await c.execute(
                    """INSERT INTO provider_catalog(kind,provider_id,provider_version,adapter,protocol_version,capabilities)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (kind,provider_id) DO UPDATE SET provider_version=EXCLUDED.provider_version,
                    adapter=EXCLUDED.adapter,protocol_version=EXCLUDED.protocol_version,capabilities=EXCLUDED.capabilities,updated_at=clock_timestamp()""",
                    (self.kind, name, provider.version, adapter, protocol, __import__("json").dumps(list(capabilities))))
        self._written = True

    async def refresh(self):
        if self.base.mode == "disabled" or time.monotonic() < self._checked:
            return self.settings, self.bindings
        if not self._written:
            await self._publish_providers()
        async with self.db.connection() as c:
            rows = await (await c.execute(
                "SELECT model_id,provider_id,upstream_model FROM managed_model_bindings WHERE kind=%s ORDER BY model_id",
                (self.kind,))).fetchall()
        self._checked = time.monotonic() + REFRESH_SECONDS
        seen = [tuple(row.values()) for row in rows]
        if seen == self._seen:
            return self.settings, self.bindings
        fixed = {model for provider in self.base.providers.values() for model in provider.models}
        providers = dict(self.base.providers)
        for model, name, upstream in seen:
            if model not in fixed and name in providers:
                providers[name] = dataclasses.replace(providers[name], models={**providers[name].models, model: upstream})
        self.settings = dataclasses.replace(self.base, providers=providers)
        self.bindings, self._seen = self.settings.bindings, seen
        return self.settings, self.bindings


class GatewayPreflight:
    """Ask the gateway whether it can serve a model before the call is marked as sent.

    A missing channel then releases the points at once instead of leaving a job whose
    outcome is unknown. Listing models costs nothing at the supplier.
    """

    def __init__(self, client, unavailable):
        self.client, self.unavailable, self._cache = client, unavailable, {}

    async def check(self, models_url, secret, upstream):
        cached = self._cache.get(models_url)
        if cached and cached[0] > time.monotonic():
            names = cached[1]
        else:
            try:
                response = await self.client.get(models_url, headers={"Authorization": "Bearer " + secret}, timeout=10)
                data = response.json().get("data") if response.status_code == 200 else None
                if not isinstance(data, list):
                    raise ValueError
                names = {item.get("id") for item in data if isinstance(item, dict)}
            except (httpx.HTTPError, ValueError, AttributeError):
                raise PlatformError(503, self.unavailable) from None
            self._cache[models_url] = (time.monotonic() + PREFLIGHT_SECONDS, names)
        if upstream not in names:
            raise PlatformError(503, self.unavailable)
