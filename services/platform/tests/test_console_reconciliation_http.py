"""Actual BFF + real sessions + real PostgreSQL for reconciliation routes."""
import json

import httpx
import pytest

from platform_service.console_app import create_console_app
from test_console_reconciliation import env, admin, body, counts, financial_snapshot, live_pending


def server(env, db, tmp_path):
    _, auth, *_ = env
    (tmp_path / 'index.html').write_text('<!doctype html><title>Console fixture</title>')
    return create_console_app(auth.settings, database=db, auth=auth, static_dir=tmp_path, provider_ids=())


@pytest.mark.asyncio
async def test_http_queue_detail_review_replay_and_original_key_lookup(db, env, tmp_path):
    _, auth, p, token, _, _, job = env
    before = await financial_snapshot(db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server(env, db, tmp_path)), base_url=auth.settings.public_url, cookies={'easel_console_session': token}) as client:
        response = await client.get('/api/reconciliation-cases')
        assert response.status_code == 200
        assert response.json()['items'][0]['id'] == job['id']
        detail = await client.get('/api/reconciliation-cases/' + job['id'])
        assert detail.status_code == 200 and detail.json()['job']['actual'] is None
        headers = {'Origin': auth.settings.public_url, 'X-Console-CSRF': p.csrf_token, 'Idempotency-Key': 'http-review-001'}
        value = body(detail.json())
        response = await client.post('/api/reconciliation-cases/' + job['id'] + '/reviews', json=value, headers=headers)
        assert response.status_code == 200
        first = response.json()
        assert (await client.post('/api/reconciliation-cases/' + job['id'] + '/reviews', json=value, headers=headers)).json() == first
        response = await client.get('/api/reconciliation-cases/' + job['id'] + '/review-operations', headers={'Idempotency-Key': 'http-review-001'})
        assert response.json() == {'found': True, 'result': first}
        response = await client.get('/api/reconciliation-cases/' + job['id'] + '/review-operations', headers={'Idempotency-Key': 'missing-http-001'})
        assert response.status_code == 200 and response.json() == {'found': False, 'result': None}
        assert 'no-store' in response.headers['cache-control']
        assert (await client.get('/reconciliation')).status_code == 200
    assert await counts(db) == [1, 1, 1] and await financial_snapshot(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['product_bearer', 'viewer', 'origin', 'csrf', 'missing_key', 'extra_actor', 'duplicate_field', 'oversize', 'invalid_uuid'])
async def test_http_review_strict_boundary_never_writes(db, env, tmp_path, failure):
    api, auth, p, token, _, _, job = env
    value = body(await api.detail(p, job['id']))
    headers = {'Origin': auth.settings.public_url, 'X-Console-CSRF': p.csrf_token, 'Idempotency-Key': 'boundary-review-001'}
    url = '/api/reconciliation-cases/' + job['id'] + '/reviews'
    if failure == 'viewer':
        _, viewer, token = await admin(db, 'viewer')
        headers['X-Console-CSRF'] = viewer.csrf_token
    if failure == 'origin': headers['Origin'] = 'https://evil.invalid'
    if failure == 'csrf': headers['X-Console-CSRF'] = 'invalid'
    if failure == 'missing_key': headers.pop('Idempotency-Key')
    if failure == 'extra_actor': value['actor_id'] = 'injected'
    if failure == 'oversize': value['reason'] = 'x' * 65536
    if failure == 'invalid_uuid': url = '/api/reconciliation-cases/not-uuid/reviews'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server(env, db, tmp_path)), base_url=auth.settings.public_url) as client:
        if failure == 'product_bearer': headers['Authorization'] = 'Bearer product-only'
        else: client.cookies.set('easel_console_session', token)
        if failure == 'duplicate_field':
            headers['Content-Type'] = 'application/json'
            response = await client.post(url, content=json.dumps(value)[:-1] + ',"reason":"injected"}', headers=headers)
        else:
            response = await client.post(url, json=value, headers=headers)
        assert response.status_code in {400, 401, 403, 413, 422}
        assert set(response.json()) == {'error'} and 'PRIVATE_RAW_REASON' not in response.text
    assert await counts(db) == [0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['limit=101', 'financial_status=settled', 'review_state=closed', 'funding_mode=production', 'funding_mode=', 'funding_mode=test&funding_mode=live', 'limit=1&limit=2', 'unexpected=value'])
async def test_http_queue_rejects_invalid_or_ambiguous_filters(db, env, tmp_path, query):
    _, auth, _, token, *_ = env
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server(env, db, tmp_path)), base_url=auth.settings.public_url, cookies={'easel_console_session': token}) as client:
        response = await client.get('/api/reconciliation-cases?' + query)
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_http_queue_funding_filter_has_positive_live_and_test_results(db, env, tmp_path):
    _, auth, _, token, _, _, test_job = env
    live_job = await live_pending(db, env)
    before = await financial_snapshot(db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server(env, db, tmp_path)), base_url=auth.settings.public_url, cookies={'easel_console_session': token}) as client:
        for mode, ids in (('all', {test_job['id'], live_job['id']}), ('test', {test_job['id']}), ('live', {live_job['id']})):
            response = await client.get('/api/reconciliation-cases', params={'funding_mode': mode})
            assert response.status_code == 200
            assert {row['id'] for row in response.json()['items']} == ids
    assert await financial_snapshot(db) == before and await counts(db) == [0, 0, 0]
