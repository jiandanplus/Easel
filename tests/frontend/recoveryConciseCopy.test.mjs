import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function text(tree) {return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? (tree.children || []).map(text).join('') : String(tree);}
function nodes(tree, type) {return !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))];}
test('request cards distinguish waived customer zero and unknown service cost using concise rendered status', async () => {
  const state = [], refs = [], effects = [], exports = {}; let cursor = 0, refCursor = 0, effectCursor = 0;
  const React = {Fragment: 'fragment', createElement: (type, props, ...children) => ({type, props: props || {}, children: children.flat(Infinity)}),
    useState(initial) {const n = cursor++; if (!(n in state)) state[n] = initial; return [state[n], value => {state[n] = typeof value === 'function' ? value(state[n]) : value;}];},
    useRef(initial) {return refs[refCursor++] ||= {current: initial};},
    useEffect(fn, deps) {const n = effectCursor++; if (!effects[n] || deps.some((x, i) => x !== effects[n].deps[i])) effects[n] = {deps, cleanup: fn()};},
  };
  let reads = 0;
  const api = {centralAccountBoundaryVersion: () => 0, subscribeCentralAccountBoundary: () => () => {}, centralStatus: async () => ({authenticated: true}), centralAccountSignature: value => value.authenticated ? 'same-account' : null,
    centralTurnRecoveries: async () => {reads++; return {recoveries: [{operation_key: 'owned', invocation_id: 'invocation', model: 'internal-id', lookup_status: 'found', execution: {status: 'waived', amount: '9728', actual: null, result_state: 'not_available'}}], truncated: false};},
    centralMediaTurnRecoveries: async () => ({recoveries: [], truncated: false}),
  };
  const source = readFileSync(new URL('../../web/frontend/src/components/ConversationRecovery.tsx', import.meta.url), 'utf8');
  runInNewContext(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React}}).outputText, {exports, require: name => name === 'react' ? React : name.includes('/api') ? api : {}, React, AbortController, BigInt, Promise, Error, setTimeout, clearTimeout});
  const render = () => {cursor = refCursor = effectCursor = 0; return exports.default({sessionId: 'session', turnId: 'turn'});};
  nodes(render(), 'button')[0].props.onClick(); await new Promise(resolve => setImmediate(resolve));
  const value = text(render());
  assert.match(value, /平台承担费用/); assert.match(value, /已释放 0\.009728 点/);
  assert.match(value, /客户扣点 0 点/); assert.match(value, /服务费用待确认/); assert.match(value, /结果暂不可用/);
  assert.doesNotMatch(value, /预留|服务费用 0 点|internal-id|本机|中央|缓存|不会重发模型请求或再次扣点|预留点数不是已确认费用/);
  assert.equal(reads, 1); effects.forEach(effect => effect.cleanup?.());
});
