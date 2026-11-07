import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const panelUrl = new URL('../../web/frontend/src/components/CentralAccountSettings.tsx', import.meta.url);
const settle = () => new Promise(resolve => setImmediate(resolve));
const nodes = (tree, type) => !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(node => nodes(node, type))];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : !tree || typeof tree !== 'object' ? '' : (tree.children || []).map(text).join('');
const authenticated = { configured: true, url: 'https://accounts.example.invalid', authenticated: true, account: { id: 'u1', email: 'one@example.invalid', email_verified: true }, scope: { user_id: 'u1', product_id: 'easel', tenant_id: 't1', billing_account_id: 'b1', session_id: 's1', authorization_epoch: 0 }, login: { pending: false } };
const wallet = { unit: 'microcredit', microcredits_per_point: '1000000', available: '9007199254740993000001', reserved: '1', spent: '1000000', expired: '0', issued: '9007199254740994000002', pending_reconciliation: 2, test_only: true };

function setup(overrides = {}) {
  assert.ok(existsSync(panelUrl), 'central account panel must exist');
  const source = readFileSync(panelUrl, 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const states = [], refs = [], effects = [], pendingEffects = [], timers = new Map();
  let cursor = 0, refCursor = 0, effectCursor = 0, timerId = 0, mounted = true;
  const calls = { status: 0, start: 0, cancel: 0, logout: 0, revoke: [] };
  let statusValue = overrides.status || authenticated;
  const api = {
    centralAccountSignature: value => value.authenticated ? JSON.stringify([value.url, value.account?.id, value.scope]) : null,
    centralStatus: async () => { calls.status++; return overrides.statusRead ? overrides.statusRead() : statusValue; },
    centralStartLogin: async () => { calls.start++; return { authorization_url: overrides.authorizationUrl || 'https://accounts.example.invalid/v2/auth/authorize/tx', expires_at: Date.now() / 1000 + 60 }; },
    centralCancelLogin: async () => { calls.cancel++; if (overrides.cancelError) throw new Error('取消暂不可用'); statusValue = { ...statusValue, login: { pending: false } }; return statusValue; },
    centralLogout: async () => { calls.logout++; statusValue = { ...statusValue, authenticated: false, account: null, scope: null }; return statusValue; },
    centralPlans: async () => ({ plans: [{ id: 'test-v1', name: '共享测试套餐', version: 'v1', included_products: ['easel', 'fixture-product'], features: ['text'], credits: '1000000', days: 30, concurrency: 2, test_only: true }] }),
    centralWallet: async () => { if (overrides.walletError) throw new Error('钱包暂不可用'); return overrides.walletPromise || { ...wallet, ...overrides.wallet }; },
    centralEntitlements: async () => ({ active: true, product_id: 'easel', features: ['text'], concurrency: 2, memberships: [{ id: 'm1', plan_id: 'test-v1', expires_at: 2000000000 }], test_only: true, ...overrides.entitlements }),
    centralUsage: async () => ({ usage: overrides.usage || [{ id: 'usage-1', operation: 'fixture', product_id: 'fixture-product', amount: '1000000', actual: null, status: 'pending', rate_version: 'fixture-v1', created_at: 1700000000, updated_at: 1700000000 }] }),
    centralDevices: async () => ({ sessions: [{ id: 's1', client_id: 'easel-desktop', product_id: 'easel', current: true, expires_at: '2030-05-01T00:00:00+00:00' }, { id: 's2', client_id: 'easel-desktop-second', product_id: 'easel', current: false, expires_at: '2030-06-01T00:00:00+00:00' }] }),
    centralRevoke: async id => { calls.revoke.push(id); return { ok: true }; },
  };
  const React = { Fragment: 'fragment', createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial; return [states[index], value => { if (mounted) states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
    useRef(initial) { const index = refCursor++; return refs[index] ||= { current: initial }; },
    useEffect(callback, deps) { const index = effectCursor++; if (!effects[index] || deps.some((value, offset) => !Object.is(value, effects[index].deps[offset]))) { pendingEffects.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: callback() }; }); } },
  };
  const exports = {};
  runInNewContext(code + '\nexports.formatMicrocredits = formatMicrocredits;', { exports, require: name => name === 'react' ? { ...React, default: React } : name.endsWith('/api') ? api : name.endsWith('/CentralExecutionSettings') ? { __esModule: true, default: 'execution-settings' } : {}, React, Error, Date, Promise, URL, BigInt, AbortController, setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id) });
  const render = () => { cursor = 0; refCursor = 0; effectCursor = 0; const tree = exports.default(); pendingEffects.splice(0).forEach(effect => effect()); return tree; };
  return { calls, api, render, format: exports.formatMicrocredits, timers, tick: async () => { const scheduled = [...timers.values()]; timers.clear(); scheduled.forEach(fn => fn()); await settle(); }, unmount() { mounted = false; effects.forEach(effect => effect?.cleanup?.()); }, setStatus(value) { statusValue = value; } };
}

test('central account is default and old test account requires explicit expansion', () => {
  const source = readFileSync(new URL('../../web/frontend/src/components/PlatformAccountSettings.tsx', import.meta.url), 'utf8');
  assert.match(source, /function LegacyPlatformAccountSettings/);
  const parsed = ts.createSourceFile('wrapper.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'PlatformAccountSettings');
  const code = ts.transpileModule(declaration.getText(parsed).replace('export default ', '') + '\nPlatformAccountSettings;', { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
  let expanded = false;
  const component = runInNewContext(code, { React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) }, useState: () => [expanded, value => { expanded = value; }], CentralAccountSettings: 'central', LegacyPlatformAccountSettings: 'legacy' });
  assert.equal(nodes(component(), 'central').length, 1);
  assert.equal(nodes(component(), 'legacy').length, 0);
  nodes(component(), 'button').find(node => /旧测试账号/.test(text(node))).props.onClick();
  assert.equal(nodes(component(), 'legacy').length, 1);
});

test('missing product preset disables login without exposing a supplier URL or key field', async () => {
  const view = setup({ status: { ...authenticated, configured: false, authenticated: false, account: null, scope: null, error: { code: 'not_configured', message: '运营者尚未配置平台入口' } } });
  view.render(); await settle(); const tree = view.render();
  assert.match(text(tree), /运营者尚未配置平台入口/);
  assert.match(text(tree), /账号服务暂不可用/);
  assert.doesNotMatch(text(tree), /订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
  assert.equal(nodes(tree, 'input').length, 0);
  assert.ok(nodes(tree, 'button').find(node => /登录／注册/.test(text(node))).props.disabled);
  assert.equal(view.timers.size, 0);
});

test('delayed account status shows loading without declaring subscription availability, then reveals the open account and wallet', async () => {
  let complete;
  const view = setup({ statusRead: () => new Promise(resolve => { complete = resolve; }) });
  for (let render = 0; render < 3; render++) {
    const tree = view.render(); await settle();
    assert.match(text(tree), /正在读取账号状态…/);
    assert.doesNotMatch(text(tree), /订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
    assert.ok(nodes(tree, 'button').find(node => text(node) === '登录／注册').props.disabled);
    assert.equal(nodes(tree, 'section').some(node => node.props['aria-label'] === '共享钱包'), false);
  }
  complete({ ...authenticated, capabilities: { subscription_execution_enabled: true, test_execution_enabled: false } });
  await settle(); view.render(); await settle(); const tree = view.render();
  assert.match(text(tree), /文本订阅已开放/);
  assert.doesNotMatch(text(tree), /正在读取账号状态…|订阅暂未开放/);
  assert.match(text(tree), /one@example.invalid/);
  assert.match(text(tree), /可用点数9007199254740993.000001/);
});

test('confirmed closed and test-only subscriptions retain their distinct availability labels', async () => {
  for (const [capabilities, label] of [
    [{ subscription_execution_enabled: false, test_execution_enabled: false }, '订阅暂未开放'],
    [{ subscription_execution_enabled: false, test_execution_enabled: true }, '文本订阅 · 测试点数'],
  ]) {
    const view = setup({ status: { ...authenticated, capabilities } });
    view.render(); await settle(); const tree = view.render();
    assert.match(text(tree), new RegExp(label));
    assert.doesNotMatch(text(tree), /正在读取账号状态…|文本订阅已开放/);
  }
});

test('a failed initial account read reports failure without claiming subscription availability', async () => {
  const view = setup({ statusRead: async () => { throw new Error('连接失败'); } });
  view.render(); await settle(); const tree = view.render();
  assert.match(text(tree), /账号状态暂不可用：连接失败/);
  assert.doesNotMatch(text(tree), /正在读取账号状态…|订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
  assert.ok(nodes(tree, 'button').find(node => text(node) === '登录／注册').props.disabled);
});

test('an account service error preserves its message without declaring subscriptions closed', async () => {
  const view = setup({ status: { ...authenticated, authenticated: false, account: null, scope: null, error: { code: 'unavailable', message: '账号服务连接失败' } } });
  view.render(); await settle(); const tree = view.render();
  assert.match(text(tree), /账号服务连接失败/);
  assert.doesNotMatch(text(tree), /订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
});

test('refresh loading and failure hide an earlier availability conclusion while retaining the account', async () => {
  let rejectRefresh, reads = 0;
  const view = setup({ statusRead: () => ++reads === 1
    ? { ...authenticated, capabilities: { subscription_execution_enabled: true } }
    : new Promise((_, reject) => { rejectRefresh = reject; }) });
  view.render(); await settle(); view.render(); await settle();
  assert.match(text(view.render()), /文本订阅已开放/);
  nodes(view.render(), 'button').find(node => text(node) === '刷新账号状态').props.onClick();
  view.render(); const loading = view.render();
  assert.match(text(loading), /正在读取账号状态…/);
  assert.doesNotMatch(text(loading), /订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
  rejectRefresh(new Error('刷新失败')); await settle(); const failed = view.render();
  assert.match(text(failed), /账号状态暂不可用：刷新失败/);
  assert.match(text(failed), /one@example.invalid/);
  assert.doesNotMatch(text(failed), /订阅暂未开放|文本订阅已开放|文本订阅 · 测试点数/);
});

test('microcredits preserve integer precision and distinguish tiny zero negative and invalid values', () => {
  const { format } = setup();
  assert.equal(format('9007199254740993000001'), '9007199254740993.000001');
  assert.equal(format('1'), '0.000001');
  assert.equal(format('0'), '0');
  assert.equal(format('-1'), '-0.000001');
  assert.equal(format('1.5'), '数据无效');
  assert.equal(format(undefined), '未知');
});
test('waived wallet usage does not present customer payment as unresolved or supplier usage as zero', async () => {
  const view = setup({ usage: [{ id: 'waived-usage', operation: 'execution', product_id: 'easel', amount: '9728', actual: null,
    status: 'waived', rate_version: 'old-rate', created_at: 1700000000, updated_at: 1700000000 }] });
  view.render(); await settle(); view.render(); await settle(); const value = text(view.render());
  assert.match(value, /平台承担费用/); assert.match(value, /用户不扣点/); assert.match(value, /供应商用量仍未知/);
  assert.doesNotMatch(value, /实际扣点未知|状态 waived/);
});

test('wallet unknown and failure never erase authenticated account or turn into zero', async () => {
  let resolve;
  const view = setup({ walletPromise: new Promise(done => { resolve = done; }) });
  view.render(); await settle(); view.render(); await settle();
  let tree = view.render();
  assert.match(text(tree), /one@example.invalid/);
  assert.match(text(tree), /可用点数未知/);
  assert.doesNotMatch(text(tree), /可用点数0/);
  resolve({ ...wallet, available: '0' }); await settle(); tree = view.render();
  assert.match(text(tree), /可用点数0/);
  const failed = setup({ walletError: true }); failed.render(); await settle(); failed.render(); await settle(); tree = failed.render();
  assert.match(text(tree), /one@example.invalid/); assert.match(text(tree), /钱包暂不可用/); assert.match(text(tree), /可用点数未知/);
});

test('wallet separates active requests from pending count and shows the server pending limit', async () => {
  const view = setup({ wallet: { active_requests: 0, pending_reconciliation: 2, pending_reconciliation_limit: 3 } });
  view.render(); await settle(); view.render(); await settle();
  const value = text(view.render());
  assert.match(value, /执行中请求（不含待核算）0/);
  assert.match(value, /待核算请求2/); assert.match(value, /待核算上限3/);
  assert.match(value, /预留点数0\.000001/);
});

test('old wallet servers leave separate active count and pending limit unknown', async () => {
  const view = setup(); view.render(); await settle(); view.render(); await settle();
  const value = text(view.render());
  assert.match(value, /执行中请求（不含待核算）未知/); assert.match(value, /待核算上限未知/);
  assert.doesNotMatch(value, /执行中请求（不含待核算）0|待核算上限3/);
});

test('pending facts above the new admission threshold remain visible without clamping', async () => {
  const view = setup({ wallet: { active_requests: 0, pending_reconciliation: 4, pending_reconciliation_limit: 3 } });
  view.render(); await settle(); view.render(); await settle();
  const value = text(view.render());
  assert.match(value, /执行中请求（不含待核算）0/);
  assert.match(value, /待核算请求4/); assert.match(value, /待核算上限3/);
  assert.doesNotMatch(value, /待核算请求3/);
});

test('each optional wallet counter independently preserves known zero and unknown values', async () => {
  for (const [fields, active, limit] of [
    [{ active_requests: 0 }, '0', '未知'],
    [{ pending_reconciliation_limit: 3 }, '未知', '3'],
    [{ active_requests: false, pending_reconciliation_limit: -1 }, '未知', '未知'],
  ]) {
    const view = setup({ wallet: fields }); view.render(); await settle(); view.render(); await settle();
    const value = text(view.render());
    assert.ok(value.includes(`执行中请求（不含待核算）${active}`));
    assert.ok(value.includes(`待核算上限${limit}`));
  }
});

test('test catalog wallet entitlement usage and devices retain distinct meaning', async () => {
  const view = setup(); view.render(); await settle(); view.render(); await settle(); const tree = view.render();
  const value = text(tree);
  assert.match(value, /可用点数9007199254740993.000001/);
  assert.match(value, /预留点数0.000001/); assert.match(value, /已过期点数0/); assert.match(value, /待核算请求2/);
  assert.match(value, /共享测试套餐/); assert.match(value, /fixture-product/); assert.match(value, /fixture-v1/); assert.match(value, /实际扣点未知/);
  assert.match(value, /测试点数钱包/); assert.match(value, /测试权益/); assert.match(value, /订阅暂未开放/);
  assert.equal(nodes(tree, 'button').some(node => /购买|续费|已付款/.test(text(node))), false);
  nodes(tree, 'button').filter(node => text(node) === '撤销此设备')[1].props.onClick(); await settle();
  assert.deepEqual(view.calls.revoke, ['s2']);
});

test('internal experience allowance stays distinct from payments and test funding', async () => {
  const view = setup({
    wallet: { available: '9876543', test_only: false, funding_mode: 'live', live_available: '9876543', experience_available: '9876543', experience_issued: '10000000', experience_expires_at: '2030-05-01T00:00:00Z' },
    entitlements: { test_only: false, experience_active: true, memberships: [{ id: 'gift-1', plan_id: 'easel-internal-experience-v1', funding_source: 'internal_experience', expires_at: '2030-05-01T00:00:00Z' }] },
  });
  view.render(); await settle(); view.render(); await settle();
  const value = text(view.render());
  assert.match(value, /内部体验可用点数9\.876543/);
  assert.match(value, /内部体验累计发放10/);
  assert.match(value, /内部体验额度 · 非付款/);
  assert.doesNotMatch(value, /内部体验额度.*已付款/);
  assert.match(value, /体验额度到期/);
});

test('zero or malformed issuance never invents an experience grant; exhausted grant stays visible', async () => {
  for (const issued of [undefined, '0', 'invalid']) {
    const view = setup({ wallet: { experience_issued: issued } });
    view.render(); await settle(); view.render(); await settle();
    assert.equal(nodes(view.render(), 'aside').some(node => node.props['aria-label'] === '内部体验额度'), false);
  }
  const view = setup({ wallet: { experience_issued: '10000000', experience_available: '0', experience_expires_at: null } });
  view.render(); await settle(); view.render(); await settle();
  assert.match(text(view.render()), /内部体验可用点数0内部体验累计发放10体验额度到期未知/);
});

test('login returns a user-clicked browser link and polls only pending state', async () => {
  const view = setup({ status: { ...authenticated, authenticated: false, account: null, scope: null } });
  view.render(); await settle(); view.render(); await settle();
  assert.equal(view.timers.size, 0);
  nodes(view.render(), 'button').find(node => /登录／注册/.test(text(node))).props.onClick(); await settle();
  let tree = view.render();
  const link = nodes(tree, 'a').find(node => /打开统一账号站/.test(text(node)));
  assert.equal(link.props.href, 'https://accounts.example.invalid/v2/auth/authorize/tx'); assert.equal(link.props.target, '_blank'); assert.match(link.props.rel, /noopener/);
  assert.equal(view.timers.size, 1);
  view.setStatus(authenticated); await view.tick(); tree = view.render(); await settle();
  assert.match(text(view.render()), /one@example.invalid/); assert.equal(view.timers.size, 0);
});

test('cancelling pending login ignores an older in-flight poll and unmount stops timers', async () => {
  let resolvePoll;
  let reads = 0;
  const pending = { ...authenticated, authenticated: false, account: null, scope: null, login: { pending: true } };
  const view = setup({ status: pending, statusRead: () => ++reads === 1 ? pending : new Promise(resolve => { resolvePoll = resolve; }) });
  view.render(); await settle(); view.render(); assert.equal(view.timers.size, 1);
  await view.tick(); assert.equal(view.timers.size, 0);
  nodes(view.render(), 'button').find(node => /取消登录/.test(text(node))).props.onClick(); await settle(); view.render();
  resolvePoll(authenticated); await settle(); const tree = view.render();
  assert.doesNotMatch(text(tree), /one@example.invalid/); assert.equal(view.calls.cancel, 1); assert.equal(view.timers.size, 0);
  const other = setup({ status: pending }); other.render(); await settle(); other.render(); assert.equal(other.timers.size, 1); other.unmount(); assert.equal(other.timers.size, 0);
});

test('unsafe authorization URL is rejected instead of exposed as a link', async () => {
  const view = setup({ status: { ...authenticated, authenticated: false, account: null, scope: null }, authorizationUrl: 'javascript:alert(1)' });
  view.render(); await settle(); view.render();
  nodes(view.render(), 'button').find(node => /登录／注册/.test(text(node))).props.onClick(); await settle();
  assert.equal(nodes(view.render(), 'a').length, 0); assert.match(text(view.render()), /登录地址无效/);
});

test('failed cancellation resumes serialized login polling and exposes retry error', async () => {
  const pending = { ...authenticated, authenticated: false, account: null, scope: null, login: { pending: true } };
  const view = setup({ status: pending, cancelError: true });
  view.render(); await settle(); view.render();
  nodes(view.render(), 'button').find(node => /取消登录/.test(text(node))).props.onClick(); await settle();
  assert.match(text(view.render()), /取消暂不可用/);
  const before = view.calls.status;
  await view.tick(); view.render(); await view.tick(); view.render();
  assert.ok(view.calls.status >= before + 2, 'failed cancel must not leave pending login frozen');
});

test('a slow pending status never overlaps polls and unmount ignores its response', async () => {
  let completePoll, reads = 0;
  const pending = { ...authenticated, authenticated: false, account: null, scope: null, login: { pending: true } };
  const view = setup({ statusRead: () => ++reads === 1 ? pending : new Promise(resolve => { completePoll = resolve; }) });
  view.render(); await settle(); view.render(); await view.tick();
  assert.equal(reads, 2); await view.tick(); assert.equal(reads, 2); assert.equal(view.timers.size, 0);
  view.unmount(); completePoll(authenticated); await settle(); assert.equal(view.timers.size, 0);
});

test('central browser API uses isolated uncached routes with no credentials in browser headers', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const api = {}, requests = [], controller = new AbortController();
  runInNewContext(code, { exports: api, require: requireDeliverables, window: { location: { pathname: '/easel/' } }, Error, Object, fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => ({}) }; } });
  for (const method of ['centralStatus', 'centralPlans', 'centralWallet', 'centralEntitlements', 'centralUsage', 'centralDevices']) await api[method](controller.signal);
  await api.centralStartLogin(); await api.centralCancelLogin(); await api.centralLogout(); await api.centralRevoke('other/device');
  assert.deepEqual(requests.map(request => request.url), ['status', 'plans', 'wallet', 'entitlements', 'usage', 'devices', 'auth/start', 'auth/cancel', 'logout', 'devices/other%2Fdevice'].map(path => `/easel/api/platform/central/${path}`));
  for (const request of requests) { assert.equal(request.options.cache, 'no-store'); assert.equal(request.options.headers?.Authorization, undefined); assert.equal(request.options.body, undefined); }
  assert.ok(requests.slice(0, 6).every(request => request.options.signal === controller.signal));
  assert.ok(requests.slice(6, 9).every(request => request.options.method === 'POST'));
  assert.equal(requests[9].options.method, 'DELETE');
});

test('backend structured login failure renders through real React without object child crash', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const source = readFileSync(panelUrl, 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const status = { ...authenticated, authenticated: false, account: null, scope: null, login: { pending: false, error: { code: 'login_expired', message: '登录已过期，请重试' } } };
  let cursor = 0;
  const hooks = { ...React, useState(initial) { const index = cursor++; return [index === 0 ? status : index === 1 ? false : typeof initial === 'function' ? initial() : initial, () => {}]; }, useEffect() {}, useRef(value) { return { current: value }; } };
  const exports = {};
  runInNewContext(code, { exports, require: name => name === 'react' ? hooks : name.includes('/api') ? {centralAccountSignature:()=>null} : /Central(Billing|Execution|Media)Settings/.test(name) ? { __esModule: true, default: () => null } : {}, React, Error, Date, Promise, URL, BigInt, AbortController });
  const html = renderToStaticMarkup(React.createElement(exports.default));
  assert.match(html, /登录已过期，请重试/);
  assert.doesNotMatch(html, /\[object Object\]/);
  assert.match(html, /登录／注册/);
});

test('central devices display actual client expires_at and current marker contract', async () => {
  const view = setup(); view.render(); await settle(); view.render(); await settle();
  const tree = view.render(), cards = nodes(tree, 'article');
  const current = cards.find(node => text(node).includes('easel-desktop') && !text(node).includes('easel-desktop-second'));
  const other = cards.find(node => text(node).includes('easel-desktop-second'));
  assert.ok(current); assert.ok(other);
  assert.match(text(current), /当前设备/); assert.match(text(current), /2030/);
  assert.doesNotMatch(text(current), /未知|未命名/);
  assert.doesNotMatch(text(other), /当前设备/); assert.match(text(other), /2030/);
  assert.match(text(tree), /版本 v1/);
});

test('unverified retained local session exposes explicit logout and recovers to fresh login', async () => {
  const view = setup({ status: { ...authenticated, authenticated: false, error: { code: 'platform_unavailable', message: '中央平台暂不可用' } } });
  view.render(); await settle(); view.render();
  const logout = nodes(view.render(), 'button').find(node => text(node) === '退出本机账号');
  assert.ok(logout, 'a retained session must have an explicit recovery action'); assert.equal(logout.props.disabled, false);
  logout.props.onClick(); await settle(); view.render(); await settle();
  assert.equal(view.calls.logout, 1);
  assert.equal(nodes(view.render(), 'button').some(node => text(node) === '退出本机账号'), false);
  const login = nodes(view.render(), 'button').find(node => text(node) === '登录／注册');
  assert.equal(login.props.disabled, false); login.props.onClick(); await settle();
  assert.equal(view.calls.start, 1);
});

 test('wallet clearly separates mixed test and live funding and refunded frozen buckets', async () => {
  const view = setup({ wallet: { funding_mode: 'mixed', test_only: false, test_available: '1000000', live_available: '2000000', frozen: '3000000', refunded: '4000000' } });
  view.render(); await settle(); view.render(); await settle(); const value = text(view.render());
  assert.match(value, /测试和正式点数混合/); assert.match(value, /测试可用点数1/); assert.match(value, /正式可用点数2/); assert.match(value, /冻结点数3/); assert.match(value, /已退款点数4/);
});

test('parent supplies complete execution identity across authenticated account scope switches', async () => {
  const view = setup(); view.render(); await settle();
  const before = nodes(view.render(), 'execution-settings')[0].props;
  assert.ok(before.scopeKey, 'complete scope must be supplied to execution child');
  for (const key of Object.keys(authenticated.scope)) {
    const status = { ...authenticated, scope: { ...authenticated.scope, [key]: key === 'authorization_epoch' ? 1 : 'different' } };
    view.setStatus(status);
    nodes(view.render(), 'button').find(node => text(node) === '刷新账号状态').props.onClick();
    view.render(); await settle();
    const after = nodes(view.render(), 'execution-settings')[0].props;
    assert.equal(after.authenticated, true); assert.notEqual(after.scopeKey, before.scopeKey, key);
    assert.deepEqual(JSON.parse(after.scopeKey), [status.url, status.account.id, status.scope]);
  }
  const status = { ...authenticated, url: 'https://other.invalid', account: { ...authenticated.account, id: 'other-account' } };
  view.setStatus(status);
  nodes(view.render(), 'button').find(node => text(node) === '刷新账号状态').props.onClick();
  view.render(); await settle();
  assert.deepEqual(JSON.parse(nodes(view.render(), 'execution-settings')[0].props.scopeKey), [status.url, status.account.id, status.scope]);
});
