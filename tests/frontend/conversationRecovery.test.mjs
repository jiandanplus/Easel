import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
const require = createRequire(new URL("../../web/frontend/package.json", import.meta.url));
const ts = require("typescript");
const settle = () => new Promise(resolve => setImmediate(resolve));
const status = (id = "A") => ({ authenticated: true, url: "https://central.example", account: { id }, scope: { user_id: id, tenant_id: "tenant", product_id: "easel", billing_account_id: id, session_id: "auth", authorization_epoch: 1 } });
const row = (state = "settled", extra = {}) => ({ operation_key: state, invocation_id: state, model: `model-${state}`, local_status: "pending", lookup_status: "found", execution: { id: `job-${state}`, status: state, amount: "9007199254740993123456", actual: "14000", result_state: "expired", ...extra } });
function loadApi(fetch) {
  const code = ts.transpileModule(readFileSync(new URL("../../web/frontend/src/lib/api.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const api = {};
  runInNewContext(code, { exports: api, require: requireDeliverables, window: { location: { pathname: "/" } }, fetch, Error, Object, AbortController, TextDecoder, Date, setTimeout, clearTimeout });
  return api;
}
function setup({ read = async () => ({ recoveries: [], truncated: false }), readStatus = async () => status(), readMedia = async () => ({recoveries:[],truncated:false}), timers = { setTimeout, clearTimeout } } = {}) {
  const path = new URL("../../web/frontend/src/components/ConversationRecovery.tsx", import.meta.url);
  assert.ok(existsSync(path), "ConversationRecovery component must exist");
  const code = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const state = [], effects = [], refs = [], exports = {}, calls = [], statusCalls = [], listeners = new Set();
  let timeoutCallback;
  let cursor = 0, effectCursor = 0, refCursor = 0, generation = 0, props = { sessionId: "session-1", turnId: "turn-1" };
  const React = { Fragment: "fragment", createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === "function" ? initial() : initial; return [state[i], value => { state[i] = typeof value === "function" ? value(state[i]) : value; }]; },
    useRef(initial) { const i = refCursor++; return refs[i] ||= { current: initial }; },
    useEffect(fn, deps) { const i = effectCursor++; if (!effects[i] || deps.some((value, j) => value !== effects[i].deps[j])) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; } } };
  const api = { centralMediaTurnRecoveries: readMedia, centralTurnRecoveries: (session, turn, signal) => { calls.push({ session, turn, signal }); return read(signal); }, centralStatus: signal => { statusCalls.push(signal); return readStatus(signal); }, centralAccountSignature: value => value.authenticated && value.scope && value.account ? JSON.stringify([value.url, value.account.id, value.scope]) : null, centralAccountBoundaryVersion: () => generation, subscribeCentralAccountBoundary: fn => { listeners.add(fn); return () => listeners.delete(fn); } };
  runInNewContext(code, { exports, require: name => name === "react" ? React : name.includes("/api") ? api : {}, React, Error, BigInt, AbortController, Promise, setTimeout: (callback, delay) => { timeoutCallback = callback; return timers.setTimeout(callback, delay); }, clearTimeout: timers.clearTimeout });
  return { timeout() { timeoutCallback(); }, calls, statusCalls, render() { cursor = 0; effectCursor = 0; refCursor = 0; return exports.default(props); }, props(value) { props = { ...props, ...value }; }, invalidate() { generation++; listeners.forEach(fn => fn()); }, cleanup() { effects.forEach(effect => effect.cleanup?.()); } };
}
function nodes(tree, type) { return !tree || typeof tree !== "object" ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))]; }
function text(tree) { return tree == null || typeof tree === "boolean" ? "" : typeof tree === "object" ? (tree.children || []).map(text).join("") : String(tree); }
function query(view) { const button = nodes(view.render(), "button").find(node => text(node).includes("查看本次请求状态")); assert.ok(button); assert.ok(!button.props.disabled); button.props.onClick(); view.render(); }

test("mount and target changes never query; explicit click reads exact turn between status checks", async () => {
  const view = setup(); view.render(); assert.equal(view.calls.length, 0); assert.equal(view.statusCalls.length, 0);
  query(view); assert.match(text(view.render()), /正在查询/); await settle();
  assert.equal(view.calls.length, 1); assert.equal(view.statusCalls.length, 2); assert.equal(view.calls[0].session, "session-1"); assert.equal(view.calls[0].turn, "turn-1");
  assert.match(text(view.render()), /未找到关联记录/); assert.doesNotMatch(text(view.render()), /未扣点|未执行/); view.cleanup();
});

function virtualTimers() {
  let now = 0, next = 0;
  const pending = new Map();
  const timers = {
    setTimeout(callback, delay) { const id = ++next; pending.set(id, { at: now + delay, callback }); return id; },
    clearTimeout(id) { pending.delete(id); },
    sleep(delay, result) { return new Promise(resolve => timers.setTimeout(() => resolve(result), delay)); },
    async advance(duration) {
      const end = now + duration;
      for (;;) {
        const first = [...pending].sort((a, b) => a[1].at - b[1].at)[0];
        if (!first || first[1].at > end) break;
        now = first[1].at; pending.delete(first[0]); first[1].callback();
        for (let i = 0; i < 12; i++) await Promise.resolve();
      }
      now = end; for (let i = 0; i < 12; i++) await Promise.resolve();
    },
    pending: () => pending.size,
  };
  return timers;
}

test('one bounded GUI deadline permits the full 45s authenticated recovery chain', async () => {
  const timers = virtualTimers();
  const view = setup({ timers, readStatus: () => timers.sleep(10000, status()),
    read: () => timers.sleep(25000, { recoveries: [row()], truncated: false }),
    readMedia: () => timers.sleep(15000, { recoveries: [], truncated: false }) });
  query(view);
  await timers.advance(20001);
  assert.match(text(view.render()), /正在查询/); assert.doesNotMatch(text(view.render()), /查询超时/);
  assert.equal(view.calls[0].signal.aborted, false);
  await timers.advance(24999);
  assert.match(text(view.render()), /已结算/); assert.doesNotMatch(text(view.render()), /查询超时|正在查询/);
  assert.equal(view.statusCalls.length, 2); assert.equal(view.calls.length, 1);
  assert.equal(timers.pending(), 0); view.cleanup();
});

test('the same 90s GUI deadline aborts a stalled chain and discards late results', async () => {
  const timers = virtualTimers(); let complete;
  const view = setup({ timers, read: () => new Promise(resolve => { complete = resolve; }) });
  query(view); await timers.advance(0); await timers.advance(89999);
  assert.match(text(view.render()), /正在查询/); assert.equal(view.calls[0].signal.aborted, false);
  await timers.advance(1); assert.match(text(view.render()), /查询超时/); assert.equal(view.calls[0].signal.aborted, true);
  complete({ recoveries: [row()], truncated: false }); await timers.advance(0);
  assert.doesNotMatch(text(view.render()), /已结算/); assert.equal(view.statusCalls.length, 1); view.cleanup();
});
test("separate requests preserve exact reservations, confirmed charges and result availability", async () => {
  const view = setup({ read: async () => ({ recoveries: [row(), row("pending_reconciliation", { actual: null, result_state: "available", failure_code: "upstream_timeout" })], truncated: true }) });
  query(view); await settle(); const value = text(view.render());
  assert.equal(nodes(view.render(), "article").length, 2); assert.match(value, /9007199254740993\.123456/); assert.match(value, /已扣 0\.014 点/); assert.match(value, /结果已过期/); assert.match(value, /已结算/); assert.match(value, /结果已过期/); assert.match(value, /待核算/); assert.match(value, /扣点待确认/); assert.match(value, /结果可用/); assert.match(value, /50/); assert.equal(view.calls.length, 1); assert.doesNotMatch(value, /本机|中央|缓存|model-settled|model-pending|重新发送|重试模型/); view.cleanup();
});
test("waived and unresolved operations do not invent zero supplier usage or safe reruns", async () => {
  const view = setup({ read: async () => ({ recoveries: [row("waived", { actual: null, customer_charge: "0", provider_usage_known: false, result_state: "not_available" }), { ...row(), lookup_status: "not_found" }, { ...row(), lookup_status: "unavailable" }, row("unknown")], truncated: false }) });
  query(view); await settle(); const value = text(view.render());
  assert.match(value, /平台承担/); assert.match(value, /客户扣点 0 点/); assert.match(value, /服务费用待确认/); assert.match(value, /尚未查到/); assert.match(value, /暂不可查询/); assert.match(value, /状态待确认/); assert.match(value, /扣点待确认/); assert.match(value, /结果暂不可用/); assert.equal(view.calls.length, 1); assert.doesNotMatch(value, /重新发送|重跑工具/); view.cleanup();
});
test("lookup errors are explicit without empty success", async () => {
  const view = setup({ read: async () => { throw new Error("中央暂不可用"); } }); query(view); await settle();
  assert.match(text(view.render()), /中央暂不可用/); assert.doesNotMatch(text(view.render()), /没有.*关联/); view.cleanup();
});
for (const change of ["target", "logout", "unmount"]) test(`${change} aborts and rejects stale reads without repeating query`, async () => {
  let complete; const view = setup({ read: () => new Promise(resolve => { complete = resolve; }) }); query(view); await settle();
  const signal = view.calls[0].signal;
  if (change === "target") { view.props({ turnId: "turn-2" }); view.render(); } else if (change === "logout") view.invalidate(); else view.cleanup();
  assert.equal(signal.aborted, true); complete({ recoveries: [row()], truncated: false }); await settle();
  if (change !== "unmount") assert.doesNotMatch(text(view.render()), /已结算/); assert.equal(view.calls.length, 1); view.cleanup();
});
for (const change of ["account", "wallet", "origin", "logout"]) test(`changed ${change} at final authentication check rejects the entire result`, async () => {
  let n = 0; const other = status(); if (change === "account") other.account.id = "B"; if (change === "wallet") other.scope.billing_account_id = "B"; if (change === "origin") other.url = "https://other.example"; if (change === "logout") other.authenticated = false;
  const view = setup({ read: async () => ({ recoveries: [row()], truncated: false }), readStatus: async () => ++n === 1 ? status() : other });
  query(view); await settle(); const value = text(view.render()); assert.doesNotMatch(value, /已结算/); assert.match(value, /账号|身份|登录/); view.cleanup();
});
test("fixed turn GET validates identifiers and uses no-store and caller abort", async () => {
  const calls = []; const api = loadApi(async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ recoveries: [], truncated: false }) }; });
  assert.equal(typeof api.centralTurnRecoveries, "function"); const signal = new AbortController().signal;
  await api.centralTurnRecoveries("web:session", "turn_1", signal); assert.equal(calls[0].url, "/api/platform/central/execution-recoveries/turn?session_id=web%3Asession&turn_id=turn_1"); assert.equal(calls[0].options.signal, signal); assert.equal(calls[0].options.cache, "no-store");
  for (const invalid of [undefined, null, 123, {}, "", " space", "a/b", "turn\n", "turn\r", "x".repeat(161)]) await assert.rejects(async () => api.centralTurnRecoveries("session", invalid)); assert.equal(calls.length, 1);
});
test("account boundary observes changes silently at first and invalidates both mutation edges", async () => {
  let value = status(), release; const api = loadApi(async url => ({ ok: true, json: async () => url.endsWith("/status") ? value : new Promise(resolve => { release = resolve; }) }));
  assert.equal(typeof api.subscribeCentralAccountBoundary, "function"); let changed = 0; api.subscribeCentralAccountBoundary(() => changed++);
  await api.centralStatus(); assert.equal(changed, 0); await api.centralStatus(); assert.equal(changed, 0); value = status("B"); await api.centralStatus(); assert.equal(changed, 1);
  for (const name of ["centralStartLogin", "centralCancelLogin", "centralLogout", "centralRevoke"]) { const before = changed, pending = api[name]("device"); assert.equal(changed, before + 1); await settle(); release({}); await pending; assert.equal(changed, before + 2); }
});
for (const replay of [false, true]) test(`central recovery hints validate exact turn on ${replay ? "replay" : "live"} SSE`, async () => {
  const hints = []; let finish; const done = new Promise(resolve => { finish = resolve; });
  const values = [{version:1,turn_id:"turn-1"},{version:1,turn_id:"turn-1"},{version:2,turn_id:"turn-1"},{version:1,turn_id:"other"},{version:1,turn_id:"bad/turn"},null,"invalid",{version:1,turn_id:"turn-1",scope:{secret:true}}];
  const frames = values.map(value => `event: central_recovery\ndata: ${JSON.stringify(value)}\n\n`).join("") + "event: done\ndata: {}\n\n";
  const api = loadApi(async () => new Response(frames));
  api.streamChat("hello", undefined, "session", () => {}, finish, error => { throw error; }, undefined, undefined, undefined, "turn-1", replay, undefined, [], undefined, undefined, undefined, hint => hints.push(hint)); await done;
  assert.equal(hints.length, 3); assert.deepEqual(JSON.parse(JSON.stringify(hints)), Array.from({length:3}, () => ({version:1,turn_id:"turn-1"})));
});

for (const [state, label] of [["reserved", "已预留"], ["dispatched", "执行中"], ["released", "已释放"]]) test(`central ${state} has a distinct financial label`, async () => {
  const view = setup({ read: async () => ({ recoveries: [row(state, { actual: state === "released" ? "0" : null })], truncated: false }) });
  query(view); await settle(); assert.ok(text(view.render()).includes(label)); assert.doesNotMatch(text(view.render()), /费用尚未确认/); view.cleanup();
});
test("completed financial results clear on account boundary and remain idle", async () => {
  const view = setup({ read: async () => ({ recoveries: [row()], truncated: false }) }); query(view); await settle(); assert.match(text(view.render()), /已结算/);
  view.invalidate(); assert.doesNotMatch(text(view.render()), /已结算/); assert.equal(view.calls.length, 1); view.cleanup();
});
test("bounded timeout aborts the exact read and ignores a late result", async () => {
  let complete; const view = setup({ read: () => new Promise(resolve => { complete = resolve; }) }); query(view); await settle(); view.timeout();
  assert.equal(view.calls[0].signal.aborted, true); assert.match(text(view.render()), /查询超时/);
  complete({ recoveries: [row()], truncated: false }); await settle(); assert.doesNotMatch(text(view.render()), /已结算/); view.cleanup();
});
test("unauthenticated status never reaches the financial lookup", async () => {
  const view = setup({ readStatus: async () => ({ ...status(), authenticated: false }) }); query(view); await settle(); assert.equal(view.calls.length, 0); assert.match(text(view.render()), /登录/); view.cleanup();
});

test("media and text financial states are distinct in one turn",async()=>{
 const view=setup({read:async()=>({recoveries:[row()],truncated:false}),readMedia:async()=>({recoveries:[{operation_key:'image-op',model:'gpt-image-2.5-flare',capability:'image.generate',local_status:'created',lookup_status:'found',job:{id:'image-job',status:'settled',actual:'1000000',amount:'1000000',delivery_state:'available',artifacts:[{id:'blob'}]}}],truncated:false})});
 query(view);await settle();const value=text(view.render());assert.match(value,/已结算/);assert.match(value,/图片模型/);assert.match(value,/已扣 1 点/);assert.match(value,/图片可用/);view.cleanup();
});
test("media lookup failure preserves known text costs and does not fake zero media cost",async()=>{
 const view=setup({read:async()=>({recoveries:[row()],truncated:false}),readMedia:async()=>{throw new Error('supplier-private')}});query(view);await settle();const value=text(view.render());assert.match(value,/已结算/);assert.match(value,/媒体状态暂不可查询/);assert.doesNotMatch(value,/supplier-private/);view.cleanup();
});

test("processing media preserves unknown cost and does not claim no associated records",async()=>{
 const view=setup({readMedia:async()=>({recoveries:[{operation_key:'pending-image',model:'flare',capability:'image.generate',lookup_status:'found',job:{financial_state:'dispatched',amount:'1000000',actual:null,delivery_state:'processing'}}],truncated:false})});query(view);await settle();const value=text(view.render());assert.match(value,/生成中/);assert.match(value,/扣点待确认/);assert.doesNotMatch(value,/未找到关联记录|图片状态未知/);view.cleanup();
});

for (const [state, actual, expected] of [['pending_reconciliation', null, '扣点待确认'], ['released', '0', '已扣 0 点'], ['waived', null, '客户扣点 0 点'], ['unknown', null, '扣点待确认']]) test(`concise ${state} retains null-versus-zero cost and live funding is not labelled paid`, async () => {
  const view = setup({read: async () => ({recoveries: [row(state, {actual, funding_mode: 'live', result_state: 'not_available'})], truncated: false})});
  query(view); await settle(); const value = text(view.render());
  assert.ok(value.includes(expected)); assert.ok(value.includes(`${state === 'pending_reconciliation' ? '预留' : ['released', 'waived'].includes(state) ? '已释放' : '预算'} 9007199254740993.123456 点`));
  assert.doesNotMatch(value, /正式点数|本机|中央|缓存|model-/);
  if (actual === null && state !== 'waived') assert.doesNotMatch(value, /已扣 0 点|客户扣点 0 点/);
  if (state === 'waived') assert.match(value, /平台承担.*客户扣点 0 点.*服务费用待确认/);
  view.cleanup();
});

for (const state of ['settled', 'released', 'waived', 'pending_reconciliation', 'reserved', 'dispatched', 'unknown']) test(`text and image ${state} describe current holds rather than calling old budgets reserved`, async () => {
  const actual = state === 'settled' ? '14000' : state === 'released' ? '0' : null;
  const view = setup({read: async () => ({recoveries: [row(state, {amount: '9728', actual})], truncated: false}), readMedia: async () => ({recoveries: [{operation_key: 'image-operation', model: 'internal-image-id', capability: 'image.generate', lookup_status: 'found', job: {financial_state: state, amount: '9728', actual, delivery_state: 'unavailable'}}], truncated: false})});
  query(view); await settle();
  for (const article of nodes(view.render(), 'article')) {
    const value = text(article);
    if (['settled', 'released', 'waived'].includes(state)) assert.doesNotMatch(value, /预留/);
    if (['pending_reconciliation', 'reserved', 'dispatched'].includes(state)) assert.match(value, /预留 0\.009728 点/);
    if (['released', 'waived'].includes(state)) assert.match(value, /已释放 0\.009728 点/);
    if (state === 'settled') assert.match(value, /已扣 0\.014 点/);
    if (state === 'unknown') {assert.match(value, /预算 0\.009728 点/); assert.doesNotMatch(value, /预留|已释放/);}
    if (state === 'waived') assert.match(value, /客户扣点 0 点.*服务费用待确认/);
    else if (actual === null) {assert.match(value, /扣点待确认/); assert.doesNotMatch(value, /已扣 0 点/);}
  }
  assert.equal(nodes(view.render(), 'article').length, 2); view.cleanup();
});

for (const [capability, jobCapability, heading, delivery, result] of [['music.generate', undefined, '音乐请求 · 音乐模型', 'processing', '生成中'], [undefined, 'video.generate', '视频请求 · 视频模型', 'available', '结果可用'], ['audio.speech', undefined, '配音请求 · 语音模型', 'expired', '结果已过期'], [undefined, undefined, '媒体请求 · 媒体模型', 'unavailable', '结果暂不可用']]) test(`media recovery uses explicit capability ${capability || jobCapability || 'missing'} without guessing from image-looking model IDs`, async () => {
  const view = setup({readMedia: async () => ({recoveries: [{operation_key: 'media-owned', model: 'gpt-image-looking-internal', capability, lookup_status: 'found', job: {capability: jobCapability, financial_state: 'pending_reconciliation', amount: '9728', actual: null, delivery_state: delivery}}], truncated: false})});
  query(view); await settle(); const value = text(view.render());
  assert.ok(value.includes(heading)); assert.ok(value.includes(result)); assert.match(value, /预留 0\.009728 点.*扣点待确认/);
  assert.doesNotMatch(value, /生图请求|图片模型|图片可用|图片已过期|gpt-image-looking-internal|已扣 0 点/);
  assert.equal(view.calls.length, 1); view.cleanup();
});
test('older recovery without a reservation amount retains unknown rather than zero', async () => {
  const view = setup({read: async () => ({recoveries: [row('pending_reconciliation', {amount: undefined, actual: null})], truncated: false})});
  query(view); await settle(); const value = text(view.render()); assert.match(value, /预留 未知 点.*扣点待确认/); assert.doesNotMatch(value, /预留 0 点|已扣 0 点/); view.cleanup();
});
