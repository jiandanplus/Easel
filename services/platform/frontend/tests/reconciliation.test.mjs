import assert from 'node:assert/strict';
import { test } from 'node:test';
import { dependency, load } from './load.mjs';

const core = load('core.ts');
const r = load('reconciliation.ts', { './core': core });
const implemented = name => assert.equal(typeof r[name], 'function', name + ' must exist');
const job = '11111111-1111-4111-8111-111111111111';
const hash = 'a'.repeat(64);
const session = (role = 'operator') => ({ admin: { id: 'operator-a', role, permissions: role === 'viewer' ? ['console:read'] : ['console:read', 'execution:review'] }, csrf_token: 'synthetic-csrf' });
const body = { state: 'investigating', reason: '核对已有记录', expected_revision: 0, snapshot_hash: hash };
const result = () => ({ operation_id: 'operation-a', job_id: job, review: { state: body.state, revision: 1, actor_id: 'operator-a', reason: body.reason, snapshot_hash: hash, updated_at: '2026-10-26T02:00:00Z' } });
const detail = () => ({ job: { id: job, model: 'synthetic-model', product_id: 'easel', billing_account_id: 'wallet-a', status: 'pending_reconciliation', amount: '9728', actual: null, funding_mode: 'live', rate_version: 'frozen-v1', created_at: null, updated_at: '2026-10-26T01:00:00Z', review_state: 'unreviewed', review_revision: 0 }, review: null, observed_at: '2026-10-26T02:00:00Z', snapshot_hash: hash, usage: { present: false, input_tokens: null, output_tokens: null, diagnostic: 'central_usage_missing' }, history: { items: [], has_more: false } });

test('queue filters encode the fixed pending/finished/all and review-state contract', () => {
  implemented('reconciliationListPath');
  assert.equal(r.reconciliationListPath({}), '/api/reconciliation-cases?limit=25&offset=0&financial_status=pending&review_state=all&funding_mode=all');
  assert.equal(r.reconciliationListPath({ financial_status: 'finished', review_state: 'awaiting_evidence', funding_mode: 'live', product_id: 'easel', q: 'a&b', offset: 25 }), '/api/reconciliation-cases?limit=25&offset=25&financial_status=finished&review_state=awaiting_evidence&funding_mode=live&product_id=easel&q=a%26b');
  for (const filters of [{ financial_status: 'settled' }, { review_state: 'cleared' }, { funding_mode: 'internal_experience' }, { funding_mode: '' }, { q: 'x'.repeat(129) }, { offset: -1 }, { limit: 101 }]) assert.throws(() => r.reconciliationListPath(filters));
});

test('funding filter explicitly encodes all, test and live without changing financial scope', () => {
  for (const funding_mode of ['all', 'test', 'live']) {
    const path = r.reconciliationListPath({ funding_mode });
    const params = new URL('http://localhost' + path).searchParams;
    assert.equal(params.get('funding_mode'), funding_mode); assert.equal(params.get('financial_status'), 'pending');
  }
});

test('detail is an independent GET, validates its target, and retains unknown fees', async () => {
  implemented('readReconciliationDetail'); const calls = [];
  const client = new core.ConsoleClient(async (url, init) => { calls.push({ url, init }); return Response.json(detail()); });
  const data = await r.readReconciliationDetail(client, job);
  assert.equal(calls[0].url, '/api/reconciliation-cases/' + job); assert.equal(calls[0].init.method, undefined);
  assert.equal(data.job.actual, null); assert.equal(core.formatCredits(data.job.actual), '未知');
  const bad = new core.ConsoleClient(async () => Response.json({ ...detail(), job: { ...detail().job, id: 'different-job' } }));
  await assert.rejects(() => r.readReconciliationDetail(bad, job));
});

test('authorized token counts preserve decimal strings beyond JavaScript safe integers', async () => {
  implemented('readReconciliationDetail');
  const source = { ...detail(), usage: { present: true, input_tokens: '9007199254740993', output_tokens: '0', diagnostic: 'usage_without_settlement' } };
  const client = new core.ConsoleClient(async () => Response.json(source));
  const data = await r.readReconciliationDetail(client, job);
  assert.equal(data.usage.input_tokens, '9007199254740993'); assert.equal(data.usage.output_tokens, '0');
});

test('review body has only approved fields and reason permits 1-500 single-line characters', () => {
  implemented('buildReviewBody');
  assert.deepEqual(JSON.parse(JSON.stringify(r.buildReviewBody('disputed', ' 核查说明 ', detail()))), { state: 'disputed', reason: '核查说明', expected_revision: 0, snapshot_hash: hash });
  assert.equal(r.buildReviewBody('investigating', '字'.repeat(500), detail()).reason.length, 500);
  for (const reason of ['', 'x'.repeat(501), 'line\nline', '\u0000']) assert.throws(() => r.buildReviewBody('investigating', reason, detail()));
  assert.throws(() => r.buildReviewBody('settled', '说明', detail()));
});

test('double click and unknown result send one POST; original-key GET false remains unknown', async () => {
  implemented('ReviewRegistry'); const calls = []; let resolvePost;
  const client = new core.ConsoleClient(async (url, init) => { calls.push({ url, init }); if (init.method === 'POST') return new Promise(resolve => { resolvePost = resolve; }); return Response.json({ found: false, result: null }); }, () => 'original-key');
  client.setSession(session()); const registry = new r.ReviewRegistry(); const submission = registry.get(client, job);
  const first = submission.send(body); await assert.rejects(() => submission.send(body));
  assert.equal(calls.length, 1); resolvePost(Response.json({ error: { code: 'service_unavailable' } }, { status: 503 }));
  await assert.rejects(() => first); assert.equal(submission.status, 'unknown');
  await assert.rejects(() => submission.send(body)); await submission.lookup();
  assert.equal(submission.status, 'unknown'); assert.equal(calls.filter(call => call.init.method === 'POST').length, 1);
  assert.equal(calls[1].url, '/api/reconciliation-cases/' + job + '/review-operations');
  assert.equal(calls[1].init.headers.get('Idempotency-Key'), 'original-key');
  assert.equal(calls[0].init.headers.get('X-Console-CSRF'), 'synthetic-csrf');
  assert.deepEqual(JSON.parse(calls[0].init.body), body);
  assert.equal(registry.get(client, job), submission, 'same instance survives closing and navigation');
  assert.throws(() => registry.startAfterRefresh(client, job));
});

test('a malformed successful POST is unknown and original result lookup restores confirmation', async () => {
  implemented('ReviewRegistry'); let calls = 0;
  const client = new core.ConsoleClient(async (_url, init) => { calls++; return Response.json(init.method === 'POST' ? { ok: true } : { found: true, result: result() }); });
  client.setSession(session()); const submission = new r.ReviewRegistry().get(client, job);
  await assert.rejects(() => submission.send(body)); assert.equal(submission.status, 'unknown');
  await submission.lookup(); assert.equal(submission.status, 'confirmed'); assert.equal(submission.result.review.revision, 1); assert.equal(calls, 2);
});

test('conflict retains reason and requires explicit refresh; a replaced session blocks old POST and lookup', async () => {
  implemented('ReviewRegistry'); let calls = 0;
  const client = new core.ConsoleClient(async () => { calls++; return Response.json({ error: { code: 'snapshot_conflict' } }, { status: 409 }); });
  client.setSession(session()); const registry = new r.ReviewRegistry(), submission = registry.get(client, job);
  await assert.rejects(() => submission.send(body)); assert.equal(submission.status, 'conflict'); assert.equal(submission.body.reason, body.reason);
  await assert.rejects(() => submission.send(body)); assert.equal(calls, 1);
  const replacement = registry.startAfterRefresh(client, job); assert.notEqual(replacement, submission); assert.equal(replacement.reason, body.reason);
  client.setSession(session()); await assert.rejects(() => replacement.send(body)); await assert.rejects(() => submission.lookup()); assert.equal(calls, 1);
});

test('viewer cannot allocate a write operation or issue an operation lookup', async () => {
  implemented('ReviewRegistry'); let calls = 0;
  const client = new core.ConsoleClient(async () => { calls++; return Response.json(result()); }); client.setSession(session('viewer'));
  const submission = new r.ReviewRegistry().get(client, job);
  await assert.rejects(() => submission.send(body)); await assert.rejects(() => submission.lookup()); assert.equal(calls, 0);
});

test('safe detail separates finance, review, evidence and truncated history without making dates or amounts', () => {
  const views = load('ReconciliationPage.tsx'); assert.equal(typeof views.ReconciliationDetail, 'function');
  const React = dependency('react'), { renderToStaticMarkup } = dependency('react-dom/server');
  const render = (data, permissions = ['console:read']) => renderToStaticMarkup(React.createElement(views.ReconciliationDetail, { data, permissions, submission: { status: 'idle', reason: '', state: 'investigating', busy: false }, onSubmit() {}, onLookup() {}, onRefresh() {}, onWallet() {}, onDraft() {} }));
  const html = render({ ...detail(), history: { items: [], has_more: true } });
  assert.match(html, /实际费用（点）[\s\S]*?未知/); assert.match(html, /待处理/); assert.match(html, /尚无中央终态用量记录/); assert.match(html, /历史记录已截断/); assert.match(html, /未记录/);
  assert.doesNotMatch(html, /登记处理进度<\/button>|强制结算|释放预留|重跑请求|供应商可信证据 hash/);
  const terminal = render({ ...detail(), job: { ...detail().job, status: 'settled', actual: '5000' } }, ['execution:review']);
  assert.match(terminal, /财务已结束/); assert.doesNotMatch(terminal, /登记处理进度<\/button>/);
  assert.match(render(detail(), ['execution:review']), /登记处理进度<\/button>/);
});

test('an independently finished financial job still permits original operation lookup and prevents a new POST form', () => {
  const views = load('ReconciliationPage.tsx'), React = dependency('react'), { renderToStaticMarkup } = dependency('react-dom/server');
  const html = renderToStaticMarkup(React.createElement(views.ReconciliationDetail, { data: { ...detail(), job: { ...detail().job, status: 'settled', actual: '5000' } }, permissions: ['execution:review'], submission: { status: 'unknown', state: 'investigating', reason: body.reason, busy: false, body }, onLookup() {}, onSubmit() {}, onRefresh() {}, onWallet() {}, onDraft() {} }));
  assert.match(html, /财务已结束/); assert.match(html, /查询原操作<\/button>/); assert.doesNotMatch(html, /登记处理进度<\/button>/);
});

test('waived financial detail preserves unknown provider usage and offers no new financial or review action', async () => {
  const data = { ...detail(), job: { ...detail().job, status: 'waived', actual: null,
    customer_charge: '0', resolution: 'platform_absorbed', provider_usage_known: false },
    usage: { present: false, input_tokens: null, output_tokens: null, diagnostic: 'waived_usage_unknown' } };
  const client = new core.ConsoleClient(async () => Response.json(data));
  const parsed = await r.readReconciliationDetail(client, job);
  assert.equal(parsed.job.actual, null);
  const views = load('ReconciliationPage.tsx'), React = dependency('react'), { renderToStaticMarkup } = dependency('react-dom/server');
  const html = renderToStaticMarkup(React.createElement(views.ReconciliationDetail, { data: parsed, permissions: ['execution:review'], submission: { status: 'idle', state: 'investigating', reason: '', busy: false }, onLookup() {}, onSubmit() {}, onRefresh() {}, onWallet() {}, onDraft() {} }));
  assert.match(html, /财务已结束/); assert.match(html, /平台承担/); assert.match(html, /用户不扣点/);
  assert.match(html, /供应商用量.*未知/); assert.match(html, /实际费用（点）[\s\S]*?未知/);
  assert.doesNotMatch(html, /登记处理进度<\/button>|强制结算|释放预留<\/button>|重跑请求/);
  for (const changes of [{actual:'0'},{customer_charge:'1'},{provider_usage_known:true},{resolution:'settled'}]) {
    const malformed = new core.ConsoleClient(async () => Response.json({ ...data, job: { ...data.job, ...changes } }));
    await assert.rejects(() => r.readReconciliationDetail(malformed, job));
  }
});
