import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const model = { id: 'fixture-text-v1', name: '文本测试模型', capability: 'text', test_only: true, readiness: 'test', rate: { version: 'test-rate-1', input: { numerator: '100', denominator: '1' }, output: { numerator: '200', denominator: '1' } } };
function setup({ authenticated = true, models = [model], executions = [], error = false, slow, executionRead } = {}) {
  const source = readFileSync(new URL('../../web/frontend/src/components/CentralExecutionSettings.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const state = [], effects = [], exports = {}, calls = [];
  let cursor = 0, effectCursor = 0, props = { authenticated, revision: 0, scopeKey: 'scope-A' };
  const React = { Fragment: 'fragment', createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }), useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value; }]; }, useEffect(fn, deps) { const i = effectCursor++; if (!effects[i] || deps.some((value, j) => value !== effects[i].deps[j])) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; } } };
  const api = { centralModels: async () => { calls.push('models'); if (error) throw new Error('平台暂不可用'); return slow || { models }; }, centralExecutions: async () => { calls.push('executions'); if (error) throw new Error('平台暂不可用'); return executionRead ? executionRead() : { executions }; } };
  runInNewContext(code, { exports, require: name => name === 'react' ? React : name.includes('/api') ? api : {}, React, Error, Date, BigInt, AbortController, Promise });
  return { calls, render() { cursor = 0; effectCursor = 0; return exports.default(props); }, props(value) { props = { ...props, ...value }; }, cleanup() { effects.forEach(effect => effect.cleanup?.()); } };
}
function nodes(tree, type) { if (!tree || typeof tree !== 'object') return []; return [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))]; }
function text(tree) { return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? (tree.children || []).map(text).join('') : String(tree); }

test('central execution requires login and does not fetch or display a misleading empty catalog', () => {
  const view = setup({ authenticated: false }); const value = text(view.render());
  assert.match(value, /登录后查看/); assert.equal(view.calls.length, 0); assert.doesNotMatch(value, /暂无调用记录/);
});
test('test models expose rational rates with their point unit without supplier credentials', async () => {
  const view = setup(); view.render(); await settle(); const tree = view.render(), value = text(tree);
  assert.match(value, /文本测试模型/); assert.match(value, /测试模型/); assert.match(value, /100\/1/); assert.match(value, /200\/1/); assert.match(value, /test-rate-1/);
  assert.match(value, /点／百万 tokens/); assert.doesNotMatch(value, /图像、视频、音乐、语音能力待接入/);
  assert.doesNotMatch(value, /本阶段支持/);
  assert.equal(nodes(tree, 'input').length, 0); assert.match(value, /暂无调用记录/);
  assert.doesNotMatch(value, /暂不可执行/);
});
test('settled and unknown costs remain distinct and decimal microcredits preserve precision', async () => {
  const executions = [{ id: 'j1', model: model.id, status: 'settled', amount: '99999', actual: '14000', input_tokens: 100, output_tokens: 20, rate_version: 'test-rate-1', funding_mode: 'test' }, { id: 'j2', model: model.id, status: 'pending_reconciliation', amount: '9007199254740993000001', actual: null, input_tokens: null, output_tokens: null, rate_version: 'test-rate-1', funding_mode: 'test' }];
  const view = setup({ executions }); view.render(); await settle(); const value = text(view.render());
  assert.match(value, /已结算/); assert.match(value, /0.014/); assert.match(value, /输入 100/); assert.match(value, /输出 20/);
  assert.match(value, /待核算/); assert.match(value, /9007199254740993.000001/); assert.match(value, /实际扣点未知/);
});
test('waived execution history says customer pays nothing while provider usage remains unknown', async () => {
  const view = setup({ executions: [{ id: 'waived-history', model: model.id, status: 'waived', amount: '9728', actual: null,
    input_tokens: null, output_tokens: null, rate_version: 'original-rate', funding_mode: 'live', customer_charge: '0', resolution: 'platform_absorbed', provider_usage_known: false }] });
  view.render(); await settle(); const value = text(view.render());
  assert.match(value, /平台承担费用/); assert.match(value, /用户不扣点/); assert.match(value, /供应商用量仍未知/);
  assert.match(value, /输入 未知/); assert.doesNotMatch(value, /实际扣点未知|已结算/);
});
test('outage clears prior execution data and never invents zero usage', async () => {
  const view = setup({ error: true }); view.render(); await settle(); const value = text(view.render());
  assert.match(value, /平台暂不可用/); assert.doesNotMatch(value, /暂无调用记录|实际扣点0/);
});
test('logout invalidates in-flight catalog and hides account execution records', async () => {
  let complete;
  const view = setup({ slow: new Promise(resolve => { complete = resolve; }) }); view.render();
  view.props({ authenticated: false }); view.render(); complete({ models: [model] }); await settle();
  assert.doesNotMatch(text(view.render()), /文本测试模型/); assert.match(text(view.render()), /登录后查看/);
});
test('native browser bridge endpoints carry no bearer or task credential', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const api = {}, calls = [];
  runInNewContext(code, { exports: api, require: requireDeliverables, window: { location: { pathname: '/' } }, Error, Object, fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) }; } });
  await api.centralModels(); await api.centralExecutions();
  assert.deepEqual(calls.map(call => call.url), ['/api/platform/central/models', '/api/platform/central/executions']);
  assert.ok(calls.every(call => call.options.cache === 'no-store' && !call.options.headers?.Authorization));
});

test('authenticated scope switch clears loaded rows and ignores late old scope responses', async () => {
  let completeA, reads = 0;
  const view = setup({ executionRead: () => ++reads === 1 ? new Promise(resolve => { completeA = resolve; }) : { executions: [{ id: 'job-B', model: 'model-B', status: 'settled' }] } });
  view.render(); await settle();
  view.props({ scopeKey: 'scope-B' });
  assert.doesNotMatch(text(view.render()), /文本测试模型/);
  await settle();
  completeA({ executions: [{ id: 'job-A', model: 'model-A', status: 'settled' }] }); await settle();
  const value = text(view.render());
  assert.equal(reads, 2); assert.match(value, /job-B/); assert.doesNotMatch(value, /job-A/);
  view.props({ scopeKey: 'scope-C' });
  assert.doesNotMatch(text(view.render()), /job-B/);
});
test('missing readiness never advertises executable availability', async () => {
  const view = setup({ models: [{ ...model, readiness: undefined }] }); view.render(); await settle();
  const cards = nodes(view.render(), 'article');
  assert.match(text(cards[0]), /暂不可执行|状态未知/); assert.doesNotMatch(text(cards[0]), /可用/);
});
