import assert from 'node:assert/strict';
import { test } from 'node:test';
import { dependency, load } from './load.mjs';

const core = load('core.ts'), reconciliation = load('reconciliation.ts', { './core': core });
const jobA = '11111111-1111-4111-8111-111111111111', jobB = '22222222-2222-4222-8222-222222222222', hash = 'a'.repeat(64);
const session = { admin: { id: 'synthetic-operator', role: 'operator', permissions: ['console:read', 'execution:review'] }, csrf_token: 'synthetic-csrf' };
const row = id => ({ id, model: 'synthetic-model', product_id: 'easel', billing_account_id: 'synthetic-wallet', status: 'pending_reconciliation', amount: '9728', actual: null, funding_mode: 'live', created_at: null, updated_at: '2026-10-26T02:00:00Z', rate_version: 'frozen', review_state: 'unreviewed', review_revision: 0 });
const detail = id => ({ job: row(id), review: null, observed_at: '2026-10-26T02:00:00Z', snapshot_hash: hash, usage: { present: false, input_tokens: null, output_tokens: null, diagnostic: 'central_usage_missing' }, history: { items: [], has_more: false } });
const pageData = id => ({ items: [row(id)], has_more: false, limit: 25, offset: 0 });
const settle = () => new Promise(resolve => setImmediate(resolve));
function hooks() {
  const slots = []; let cursor = 0, pending = [];
  const changed = (a, b) => !a || a.length !== b.length || a.some((value, i) => value !== b[i]);
  return {
    begin() { cursor = 0; pending = []; }, flush() { for (const run of pending) run(); }, unmount() { for (const slot of slots) slot?.cleanup?.(); },
    react: { ...dependency('react'),
      useState(initial) { const i = cursor++; if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial }; const slot = slots[i]; slot.set ??= next => { slot.value = typeof next === 'function' ? next(slot.value) : next; }; return [slot.value, slot.set]; },
      useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
      useEffect(fn, deps) { const i = cursor++; if (!slots[i] || changed(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps }; pending.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
    },
  };
}
function find(tree, name) { if (!tree || typeof tree !== 'object') return null; if (typeof tree.type === 'function' && tree.type.name === name) return tree; for (const child of [tree.props?.children].flat(Infinity)) { const result = find(child, name); if (result) return result; } return null; }
function harness(client, registry = new reconciliation.ReviewRegistry()) {
  const driver = hooks();
  const Page = load('ReconciliationPage.tsx', { react: driver.react, './core': core, './reconciliation': reconciliation }).ReconciliationPage;
  const props = { client, session, registry, onFailure: error => error.message, onWallet() {} };
  return { render() { driver.begin(); return Page(props); }, flush: driver.flush, unmount: driver.unmount, registry };
}

test('filter changes abort old reads and stale responses cannot replace a new queue', async () => {
  const requests = [];
  const client = new core.ConsoleClient((url, init) => new Promise(resolve => requests.push({ url, init, resolve }))); client.setSession(session);
  const app = harness(client); app.render(); app.flush();
  const first = requests[0]; find(app.render(), 'ReconciliationQueue').props.onFilters({ financial_status: 'all', review_state: 'disputed', q: 'new', offset: 0 });
  app.render(); app.flush(); assert.equal(first.init.signal.aborted, true);
  requests[1].resolve(Response.json(pageData(jobB))); await settle(); first.resolve(Response.json(pageData(jobA))); await settle();
  const queue = find(app.render(), 'ReconciliationQueue'); assert.equal(queue.props.state.data.items[0].id, jobB); assert.match(requests[1].url, /financial_status=all&review_state=disputed&funding_mode=all&q=new/);
});

test('funding mode control resets pagination while retaining the other queue filters', () => {
  const driver = hooks(), changes = [];
  const Queue = load('ReconciliationPage.tsx', { react: driver.react, './core': core, './reconciliation': reconciliation }).ReconciliationQueue;
  const filters = { financial_status: 'finished', review_state: 'disputed', funding_mode: 'all', product_id: 'easel', q: 'synthetic', offset: 25 };
  driver.begin(); const tree = Queue({ state: { kind: 'ready', data: { items: [], has_more: false, limit: 25, offset: 25 } }, filters, onFilters: value => changes.push(value), onInspect() {}, onRetry() {} });
  function controls(node) { if (!node || typeof node !== 'object') return []; return [node.type === 'select' ? node : null, ...[node.props?.children].flat(Infinity).flatMap(controls)].filter(Boolean); }
  const funding = controls(tree).find(node => node.props.children?.some(option => option?.props?.children === '全部资金模式'));
  assert.ok(funding, 'funding selector must exist'); assert.equal(funding.props.value, 'all');
  assert.deepEqual([...funding.props.children].map(option => option.props.value), ['all', 'test', 'live']);
  funding.props.onChange({ target: { value: 'test' } }); assert.deepEqual(JSON.parse(JSON.stringify(changes)), [{ ...filters, funding_mode: 'test', offset: 0 }]);
});

test('detail fetch is independent, rejects stale completions, and errors never fall back to a list row', async () => {
  const requests = [];
  const client = new core.ConsoleClient((url, init) => url.includes('?') ? Promise.resolve(Response.json(pageData(jobA))) : new Promise(resolve => requests.push({ url, init, resolve }))); client.setSession(session);
  const app = harness(client); app.render(); app.flush(); await settle();
  find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobA)); assert.equal(find(app.render(), 'ReconciliationDetail'), null);
  find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobB)); assert.equal(requests[0].init.signal.aborted, true);
  requests[1].resolve(Response.json(detail(jobB))); await settle(); requests[0].resolve(Response.json(detail(jobA))); await settle();
  assert.equal(find(app.render(), 'ReconciliationDetail').props.data.job.id, jobB);
  find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobA)); requests[2].resolve(Response.json({ error: { code: 'service_unavailable' } }, { status: 503 })); await settle();
  assert.equal(find(app.render(), 'ReconciliationDetail'), null); assert.equal(find(app.render(), 'Loading').props.state.kind, 'error');
});

test('unknown review instance survives detail close and page remount, with lookup false keeping POST locked', async () => {
  const calls = []; let rejectPost;
  const client = new core.ConsoleClient((url, init) => { calls.push({ url, init }); if (init.method === 'POST') return new Promise((_resolve, reject) => { rejectPost = reject; }); if (url.endsWith('/review-operations')) return Promise.resolve(Response.json({ found: false, result: null })); return Promise.resolve(Response.json(url.includes('?') ? pageData(jobA) : detail(jobA))); }); client.setSession(session);
  const registry = new reconciliation.ReviewRegistry(); let app = harness(client, registry); app.render(); app.flush(); await settle();
  find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobA)); await settle();
  find(app.render(), 'ReconciliationDetail').props.onDraft('awaiting_evidence', '等待精确凭据');
  const form = find(app.render(), 'ReconciliationDetail'); form.props.onSubmit(); form.props.onSubmit();
  find(app.render(), 'Modal').props.onClose(); rejectPost(new TypeError('network interrupted')); await settle();
  assert.equal(calls.filter(call => call.init.method === 'POST').length, 1); app.unmount();
  app = harness(client, registry); app.render(); app.flush(); await settle(); find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobA)); await settle();
  const reopened = find(app.render(), 'ReconciliationDetail'); assert.equal(reopened.props.submission.status, 'unknown'); assert.equal(reopened.props.submission.reason, '等待精确凭据');
  reopened.props.onLookup(); await settle(); const current = find(app.render(), 'ReconciliationDetail'); assert.equal(current.props.submission.status, 'unknown'); assert.match(current.props.notice, /结果仍未知/); assert.equal(calls.filter(call => call.init.method === 'POST').length, 1);
});

test('an old successful operation refresh cannot put its confirmation notice over another job detail', async () => {
  let resolveRefresh, readsA = 0;
  const review = { state: 'investigating', reason: '核查说明', revision: 1, actor_id: session.admin.id, snapshot_hash: hash, updated_at: '2026-10-26T02:00:00Z' };
  const client = new core.ConsoleClient((url, init) => {
    if (init.method === 'POST') return Promise.resolve(Response.json({ operation_id: 'synthetic-operation', job_id: jobA, review }));
    if (url.includes('?')) return Promise.resolve(Response.json(pageData(jobA)));
    if (url.endsWith(jobA) && ++readsA === 2) return new Promise(resolve => { resolveRefresh = resolve; });
    return Promise.resolve(Response.json(detail(url.endsWith(jobB) ? jobB : jobA)));
  }); client.setSession(session);
  const app = harness(client); app.render(); app.flush(); await settle(); find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobA)); await settle();
  find(app.render(), 'ReconciliationDetail').props.onDraft('investigating', '核查说明'); find(app.render(), 'ReconciliationDetail').props.onSubmit(); await settle();
  find(app.render(), 'ReconciliationQueue').props.onInspect(row(jobB)); await settle(); resolveRefresh(Response.json({ ...detail(jobA), review })); await settle();
  const current = find(app.render(), 'ReconciliationDetail'); assert.equal(current.props.data.job.id, jobB); assert.equal(current.props.notice, '');
});
