"""Loopback media reads delegate to the host without exposing credentials."""
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from easel.platform.central_web import create_router
from easel.platform.client import PlatformError


@pytest.mark.asyncio
async def test_catalog_and_recovery_reads_only_and_loopback(tmp_path, monkeypatch):
    calls=[]
    class Media:
        def __init__(self, root, **kwargs):
            assert root == tmp_path
        def catalog(self):
            calls.append(('catalog', {})); return {'models': []}
        def recoveries(self, **kwargs):
            calls.append(('recoveries', kwargs)); return {'recoveries': [], 'truncated': False}
    monkeypatch.setitem(sys.modules, 'easel.platform.media', SimpleNamespace(MediaClient=Media))
    app=FastAPI(); app.include_router(create_router(tmp_path, lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12)), base_url='http://local') as c:
        assert (await c.get('/api/platform/central/media-models')).json() == {'models': []}
        assert (await c.get('/api/platform/central/media-recoveries')).status_code == 200
        assert (await c.get('/api/platform/central/media-recoveries/turn?session_id=session-1&turn_id=turn-1')).status_code == 200
        assert (await c.post('/api/platform/central/media-models')).status_code == 405
        assert (await c.get('/api/platform/central/media-recoveries/turn?session_id=a&session_id=b&turn_id=t')).status_code == 400
        assert (await c.get('/api/platform/central/media-recoveries/turn?session_id=a&turn_id=../other')).status_code == 400
        assert (await c.get('/api/platform/central/media-recoveries/turn?session_id=a&turn_id=t&wallet=other')).status_code == 400
    assert calls == [('catalog', {}), ('recoveries', {}), ('recoveries', {'session_id':'session-1','turn_id':'turn-1'})]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('203.0.113.10', 12)), base_url='http://local') as c:
        assert (await c.get('/api/platform/central/media-models')).status_code == 403


@pytest.mark.asyncio
async def test_safe_media_auth_failure_uses_native_error(tmp_path, monkeypatch):
    class Media:
        def __init__(self, *args, **kwargs): pass
        def catalog(self): raise PlatformError('invalid_authentication', 401)
    monkeypatch.setitem(sys.modules, 'easel.platform.media', SimpleNamespace(MediaClient=Media))
    app=FastAPI(); app.include_router(create_router(tmp_path, lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 12)), base_url='http://local') as c:
        r=await c.get('/api/platform/central/media-models')
        assert r.status_code == 401 and r.json()['detail']['code'] == 'invalid_authentication'
