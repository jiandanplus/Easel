"""Live model bindings and interface settings from the catalog, and a free check before money is at risk."""
import asyncio
import dataclasses
import json
import re
import time

import httpx

from .errors import PlatformError
from .pricing import catalog_ready
from .voice_catalogs import list_voices
from .provider_channels import CHANNELS, GATEWAY, apply_connection, build_clone_provider, build_provider, channel_of, clone_provider_id, provider_id, seal_scope, spec_of

REFRESH_SECONDS = 5
PREFLIGHT_SECONDS = 30
PROBE_SECONDS = 300
MAX_LISTED_MODELS = 500
SERVER_FILE = "server-file"
PROBE_ATTEMPTS = 3


class ManagedBindings:
    """Merge console-managed interfaces and models into the file-configured providers."""

    def __init__(self, db, kind, settings, describe, *, keys=None, client=None):
        self.db, self.kind, self.base, self.describe, self.keys, self.client = db, kind, settings, describe, keys, client
        self.settings, self.bindings = settings, settings.bindings
        self._checked, self._published, self._seen = 0.0, None, None
        self._channels = {name for name, spec in CHANNELS.items() if spec["kind"] == kind}
        self._targets, self._probed, self._probe_task = {}, {}, None

    async def _publish(self, providers):
        """Tell the console what this service can reach. Addresses only, never keys."""
        def row(name, provider):
            adapter, protocol, capabilities = self.describe(provider)
            base = None if self.keys is None else provider.base_url if self.kind == "media" else re.sub(r"/(?:responses|chat/completions|messages)$", "", provider.endpoint)
            return (self.kind, name, provider.version, adapter, protocol, json.dumps(list(capabilities)), base)
        rows = [row(name, provider) for name, provider in providers.items()]
        if rows == self._published:
            return
        async with self.db.connection() as c:
            has_address = self.keys is not None
            for value in rows:
                if has_address:
                    await c.execute(
                        """INSERT INTO provider_catalog(kind,provider_id,provider_version,adapter,protocol_version,capabilities,base_url)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (kind,provider_id) DO UPDATE SET provider_version=EXCLUDED.provider_version,
                        adapter=EXCLUDED.adapter,protocol_version=EXCLUDED.protocol_version,capabilities=EXCLUDED.capabilities,base_url=EXCLUDED.base_url,updated_at=clock_timestamp()""", value)
                else:
                    await c.execute(
                        """INSERT INTO provider_catalog(kind,provider_id,provider_version,adapter,protocol_version,capabilities)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (kind,provider_id) DO UPDATE SET provider_version=EXCLUDED.provider_version,
                        adapter=EXCLUDED.adapter,protocol_version=EXCLUDED.protocol_version,capabilities=EXCLUDED.capabilities,updated_at=clock_timestamp()""", value[:-1])
        self._published = rows

    async def _configs(self, conn):
        if self.keys is None:
            return []
        rows = await (await conn.execute(
            """SELECT DISTINCT ON (c.channel,c.platform,c.account) c.id,c.channel,c.platform,c.account,c.base_url,c.sealed_secret,c.options,s.config_id AS status_config,s.state
            FROM provider_configs c LEFT JOIN provider_config_status s ON s.channel=c.channel AND s.platform=c.platform AND s.account=c.account
            WHERE c.channel=ANY(%s) ORDER BY c.channel,c.platform,c.account,c.created_at DESC,c.id DESC""", (sorted(self._channels),))).fetchall()
        return [row for row in rows if row["sealed_secret"] is not None]

    def _connection(self, providers, row, invalid):
        channel, platform, account, base_url = row["channel"], row["platform"], row["account"], row["base_url"]
        secret = self.keys.open(row["sealed_secret"], seal_scope(channel, platform, account), base_url)
        # A setting that does not validate is reported and skipped, never fatal to the rest.
        try:
            if secret is None:
                raise ValueError
            target = (row["id"], base_url, secret, row["options"].get("auth_header"), row["options"])
            if channel == "speech" and not row["options"].get("voices"):
                # Nothing to serve until voices are picked, but the supplier can be asked for its list.
                self._targets[(channel, platform, account)] = target
                return
            current = row["status_config"] == row["id"]
            changed = {provider_id(channel, platform, account): build_provider(channel, platform, base_url, secret, row["options"], preflight=not (current and row["state"] == "no_model_list"))}
            if platform == GATEWAY and not account:  # Everything already on this page moves to the saved address and key too.
                changed.update({name: apply_connection(item, base_url, secret) for name, item in self.base.providers.items() if channel_of(item) == channel})
            if spec_of(channel, platform).get("clone_adapter"):
                changed[clone_provider_id(channel, platform, account)] = build_clone_provider(channel, platform, base_url, secret, row["options"])
            dataclasses.replace(self.base, providers=changed)
        except (ValueError, TypeError, KeyError, PlatformError):
            invalid.append(row)
            return
        providers.update(changed)
        self._targets[(channel, platform, account)] = target

    def _add_server_targets(self):
        """Pages still on the server files can list their models too."""
        if self.keys is None:
            return
        for name, item in self.base.providers.items():
            channel = channel_of(item)
            if channel is not None and (channel, GATEWAY, "") not in self._targets:
                base = item.base_url.rstrip("/") if self.kind == "media" else re.sub(r"/(?:responses|chat/completions|messages)$", "", item.endpoint)
                self._targets[(channel, GATEWAY, "")] = (SERVER_FILE, base, item.secret, None, {})

    async def refresh(self):
        if self.base.mode == "disabled" or time.monotonic() < self._checked:
            return self.settings, self.bindings
        async with self.db.connection() as c:
            if not await catalog_ready(c):  # A database still on an earlier migration keeps its file settings.
                self._checked = time.monotonic() + REFRESH_SECONDS
                return self.settings, self.bindings
            models = await (await c.execute(
                "SELECT model_id,provider_id,upstream_model FROM managed_model_bindings WHERE kind=%s ORDER BY model_id",
                (self.kind,))).fetchall()
            configs = await self._configs(c)
        self._checked = time.monotonic() + REFRESH_SECONDS
        seen = ([tuple(row.values()) for row in models], [(row["id"], row["status_config"], row["state"]) for row in configs])
        if seen != self._seen:
            providers, invalid = dict(self.base.providers), []
            self._targets = {}
            for row in configs:
                self._connection(providers, row, invalid)
            self._add_server_targets()
            fixed = {model for provider in self.base.providers.values() for model in provider.models}
            for model, name, upstream in seen[0]:
                if model not in fixed and name in providers:
                    providers[name] = dataclasses.replace(providers[name], models={**providers[name].models, model: upstream})
            await self._publish(providers)
            for row in invalid:
                await self._report(row["channel"], row["platform"], row["account"], row["id"], "invalid", [])
            self.settings = dataclasses.replace(self.base, providers=providers)
            self.bindings, self._seen = self.settings.bindings, seen
        self._start_probes()
        return self.settings, self.bindings

    def _start_probes(self):
        if self.client is None or not self._targets or (self._probe_task is not None and not self._probe_task.done()):
            return
        if any(self._due(key, target[0]) for key, target in self._targets.items()):
            self._probe_task = asyncio.create_task(self.probe_due())

    def _due(self, key, config_id):
        last = self._probed.get(key)
        return last is None or last[0] != config_id or time.monotonic() >= last[1]

    async def probe_due(self):
        for key, (config_id, base_url, secret, custom_header, options) in list(self._targets.items()):
            if self._due(key, config_id):
                spec, voices = spec_of(*key[:2]), []
                state, names = await self._try(None if custom_header else spec["probe"], base_url, secret, spec.get("models_query", ""))
                if spec.get("voice_list"):
                    failure, voices = await list_voices(self.client, spec["voice_list"], base_url, secret, options)
                    state = failure or state
                await self._report(*key, config_id, state, names, voices)
                self._probed[key] = (config_id, time.monotonic() + PROBE_SECONDS)

    async def _try(self, probe, base_url, secret, query=""):
        """List models, which costs nothing at the supplier, and say what happened."""
        if probe is None:  # This supplier has no list to ask; the first real call decides.
            return "no_model_list", []
        headers = {"bearer": {"Authorization": "Bearer " + secret}, "anthropic": {"x-api-key": secret, "anthropic-version": "2023-06-01"},
                   "gemini": {"x-goog-api-key": secret}}[probe]
        for attempt in range(PROBE_ATTEMPTS):
            try:
                response = await self.client.get(base_url + "/models" + query, headers=headers, timeout=10)
                break
            except httpx.HTTPError:
                if attempt == PROBE_ATTEMPTS - 1:
                    return "unreachable", []
        if response.status_code in (401, 403):
            return "auth_failed", []
        try:
            body = response.json() if response.status_code == 200 else None
            data = body.get("data", body.get("models")) if isinstance(body, dict) else None
        except ValueError:
            data = None
        if not isinstance(data, list):
            return "no_model_list", []
        ids = (item.get("id") or str(item.get("name") or "").removeprefix("models/") for item in data if isinstance(item, dict))
        return "ok", sorted({name for name in ids if isinstance(name, str) and 0 < len(name) <= 128})[:MAX_LISTED_MODELS]

    async def _report(self, channel, platform, account, config_id, state, names, voices=()):
        async with self.db.connection() as c:
            await c.execute(
                """INSERT INTO provider_config_status(channel,platform,account,config_id,state,models,voices) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT (channel,platform,account) DO UPDATE SET config_id=EXCLUDED.config_id,state=EXCLUDED.state,checked_at=clock_timestamp(),
                models=CASE WHEN EXCLUDED.state='unreachable' AND provider_config_status.config_id=EXCLUDED.config_id THEN provider_config_status.models ELSE EXCLUDED.models END,
                voices=CASE WHEN EXCLUDED.state='unreachable' AND provider_config_status.config_id=EXCLUDED.config_id THEN provider_config_status.voices ELSE EXCLUDED.voices END""",
                (channel, platform, account, config_id, state, json.dumps(names), json.dumps(list(voices))))

    async def close(self):
        if self._probe_task is not None and not self._probe_task.done():
            self._probe_task.cancel()
            await asyncio.gather(self._probe_task, return_exceptions=True)


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
