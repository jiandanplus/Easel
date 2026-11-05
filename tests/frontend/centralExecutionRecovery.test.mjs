import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const execution = { id: 'job-owned', model: 'text-model', status: 'settled', amount: '1000000', actual: '14000', funding_mode: 'test', result_state: 'expired' };
function setup(read = async () => ({ recoveries: [], truncated: false })) {
  const source = readFileSync(new URL('../../web/frontend/src/components/CentralExecutionRecovery.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const state = [], effects = [], exports = {}, calls = [];
  let cursor = 0, effectCursor = 0, props = { authenticated: true, scopeKey: 'scope-A' };
  const React = {
    Fragment: 'fragment', createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value; }]; },
    useEffect(fn, deps) { const i = effectCursor++; if (!effects[i] || deps.some((value, j) => value !== effects[i].deps[j])) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; } },
  };
  const api = { centralExecutionRecoveries: signal => { calls.push(signal); return read(signal); } };
  runInNewContext(code, { exports, require: name => name === 'react' ? React : name.includes('/api') ? api : {}, React, Error, BigInt, AbortController, Promise });
  return { calls, render() { cursor = 0; effectCursor = 0; return exports.default(props); }, props(value) { props = { ...props, ...value }; }, cleanup() { effects.forEach(effect => effect.cleanup?.()); } };
}
function nodes(tree, type) { return !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))]; }
function text(tree) { return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? (tree.children || []).map(text).join('') : String(tree); }
function query(view) { const tree = view.render(); const button = nodes(tree, 'button').find(node => text(node).includes('查询中断请求')); assert.ok(button); assert.ok(!button.props.disabled); button.props.onClick(); view.render(); }

test('recovery is an explicit authenticated GET action and does not query on login', () => {
  const view = setup(); const tree = view.render();
  assert.equal(view.calls.length, 0); assert.ok(nodes(tree, 'button').some(node => /查询中断请求/.test(text(node))));
  view.props({ authenticated: false }); const out = view.render();
  assert.ok(nodes(out, 'button').every(button => button.props.disabled));
  view.props({ authenticated: true }); view.render(); assert.equal(view.calls.length, 0);
});
test('settled billing remains settled when result cache expired and no resend action exists', async () => {
  const view = setup(async () => ({ recoveries: [{ operation_key: 'owned-key', invocation_id: 'owned-inv', model: 'text-model', local_status: 'pending', lookup_status: 'found', execution }], truncated: false }));
  query(view); await settle(); const value = text(view.render());
  assert.equal(view.calls.length, 1); assert.doesNotMatch(value, /本机|中央|缓存|text-model|job-owned/); assert.match(value, /已结算/); assert.match(value, /0\.014/); assert.match(value, /结果已过期/);
  assert.match(value, /文本模型/); assert.doesNotMatch(value, /重新发送|重试模型|已扣 0 点/);
});
test('pending timeout explains unknown cost and distinguishes the hold from a confirmed charge', async () => {
  const pending = { ...execution, status: 'pending_reconciliation', actual: null, amount: '9728', result_state: 'not_available', failure_code: 'upstream_timeout' };
  const view = setup(async () => ({ recoveries: [{ operation_key: 'timeout-key', invocation_id: 'owned-inv', model: 'text-model', local_status: 'pending', lookup_status: 'found', execution: pending }], truncated: false }));
  query(view); await settle(); const value = text(view.render());
  assert.match(value, /模型响应超时/); assert.match(value, /扣点待确认/);
  assert.match(value, /待核算/); assert.match(value, /0\.009728/);
  assert.equal(view.calls.length, 1);
  assert.doesNotMatch(value, /已退款|已扣 0 点|重新发送|重试模型/);
});
test('other pending failures and old servers retain unknown-cost wording without inventing a timeout', async () => {
  for (const failure_code of ['upstream_unavailable', 'upstream_error', 'invalid_upstream_usage', 'request_cancelled', undefined, 'private-provider-diagnostic']) {
    const pending = { ...execution, status: 'pending_reconciliation', actual: null, result_state: 'not_available', failure_code };
    const view = setup(async () => ({ recoveries: [{ operation_key: 'pending-key', invocation_id: 'owned-inv', model: 'text-model', local_status: 'pending', lookup_status: 'found', execution: pending }], truncated: false }));
    query(view); await settle(); const value = text(view.render());
    assert.match(value, /扣点待确认/); assert.match(value, /待核算/);
    assert.doesNotMatch(value, /模型响应超时|private-provider-diagnostic|已退款/);
  }
});
test('settled and released rows never display stale timeout or pending-cost advice', async () => {
  for (const status of ['settled', 'released']) {
    const value = { ...execution, status, actual: status === 'released' ? '0' : '14000', failure_code: 'upstream_timeout' };
    const view = setup(async () => ({ recoveries: [{ operation_key: 'closed-key', invocation_id: 'owned-inv', model: 'text-model', local_status: 'delivered', lookup_status: 'found', execution: value }], truncated: false }));
    query(view); await settle(); const rendered = text(view.render());
    assert.doesNotMatch(rendered, /模型响应超时|费用尚未确认|预留点数不是已确认费用/);
    assert.match(rendered, status === 'settled' ? /已结算/ : /已释放/);
  }
});
test('not found and lookup unavailable never imply zero cost or safe resubmission', async () => {
  const view = setup(async () => ({ recoveries: [{ operation_key: 'k1', invocation_id: 'i', model: 'text', local_status: 'sending', lookup_status: 'not_found' }, { operation_key: 'k2', invocation_id: 'i', model: 'text', local_status: 'pending', lookup_status: 'unavailable' }], truncated: true }));
  query(view); await settle(); const value = text(view.render());
  assert.match(value, /尚未查到/); assert.match(value, /暂不可查询/); assert.match(value, /扣点待确认.*请勿重复提交/); assert.match(value, /最近.*50/);
  assert.doesNotMatch(value, /已扣 0 点|已释放/);
});
test('waived request shows platform absorption without inventing supplier usage or a reply', async () => {
  const waived = { ...execution, status: 'waived', actual: null, input_tokens: null, output_tokens: null,
    result_state: 'not_available', resolution: 'platform_absorbed', customer_charge: '0', provider_usage_known: false };
  const view = setup(async () => ({ recoveries: [{ operation_key: 'waived-key', invocation_id: 'owned-inv', model: 'text-model', local_status: 'pending', lookup_status: 'found', execution: waived }], truncated: false }));
  query(view); await settle(); const value = text(view.render());
  assert.match(value, /平台承担/); assert.match(value, /客户扣点 0 点/); assert.match(value, /服务费用待确认/);
  assert.match(value, /结果暂不可用/); assert.doesNotMatch(value, /实际扣点未知|已结算|重新发送|重试模型/);
  assert.equal(view.calls.length, 1);
});
test('same authenticated scope switch hides old recovery and cancels stale reads', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  query(view); const signal = view.calls[0]; view.props({ scopeKey: 'scope-B' });
  assert.doesNotMatch(text(view.render()), /job-owned/); assert.equal(signal.aborted, true);
  complete({ recoveries: [{ operation_key: 'a', invocation_id: 'i', model: 'old-model', local_status: 'pending', lookup_status: 'found', execution }], truncated: false }); await settle();
  assert.doesNotMatch(text(view.render()), /old-model|job-owned/); assert.equal(view.calls.length, 1);
});
test('logout then relogin does not automatically repeat the prior recovery read', async () => {
  const view = setup(); query(view); await settle(); view.render();
  view.props({ authenticated: false }); view.render(); view.props({ authenticated: true }); view.render();
  assert.equal(view.calls.length, 1);
});
test('lookup failure replaces rows with an error instead of showing zero or empty success', async () => {
  const view = setup(async () => { throw new Error('中央状态暂不可用'); }); query(view); await settle();
  const value = text(view.render()); assert.match(value, /中央状态暂不可用/); assert.doesNotMatch(value, /没有可查询|实际扣点0/);
});
test('browser recovery helper only requests the fixed uncached GET with abort signal', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const api = {}, calls = [];
  runInNewContext(code, { exports: api, require: requireDeliverables, window: { location: { pathname: '/' } }, Error, Object, fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ recoveries: [], truncated: false }) }; } });
  const signal = new AbortController().signal;
  await api.centralExecutionRecoveries(signal);
  assert.equal(calls[0].url, '/api/platform/central/execution-recoveries'); assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.signal, signal); assert.ok(!calls[0].options.headers?.Authorization); assert.ok(!calls[0].options.method || calls[0].options.method === 'GET');
});

for (const status of ['settled', 'released', 'waived', 'pending_reconciliation']) test(`account recovery ${status} retains only the appropriate active hold or released amount`, async () => {
  const actual = status === 'settled' ? '14000' : status === 'released' ? '0' : null;
  const view = setup(async () => ({recoveries: [{operation_key: 'amount-key', invocation_id: 'amount-inv', model: 'internal-id', local_status: 'pending', lookup_status: 'found', execution: {...execution, status, amount: '9728', actual}}], truncated: false}));
  query(view); await settle(); const value = text(view.render());
  if (status === 'pending_reconciliation') {assert.match(value, /预留 0\.009728 点/); assert.match(value, /扣点待确认/); assert.doesNotMatch(value, /已扣 0 点/);}
  else assert.doesNotMatch(value, /预留/);
  if (['released', 'waived'].includes(status)) assert.match(value, /已释放 0\.009728 点/);
  if (status === 'settled') assert.match(value, /已扣 0\.014 点/);
  if (status === 'waived') assert.match(value, /客户扣点 0 点.*服务费用待确认/);
  view.cleanup();
});
