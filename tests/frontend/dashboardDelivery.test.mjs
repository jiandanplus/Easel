import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function setup() {
  const code = ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/DashboardPage.tsx', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exports = {}, state = []; let cursor = 0;
  const React = { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], v => { state[i] = typeof v === 'function' ? v(state[i]) : v; }]; },
    useEffect() {}, useRef: value => ({ current: value }), useSyncExternalStore: () => 'forest' };
  runInNewContext(code, { exports, React, Date, Map, Set, localStorage: { getItem: () => null }, require: name => name === 'react' ? React : name.includes('whoami') ? { getWhoamiCache: () => ({}) } : {} });
  return () => { cursor = 0; return exports.default({ persona: '', gatewayStatus: 'connected', apiDirect: false, agentRuntime: 'pi', runtimeDetail: 'CLI diagnostic must stay private', onNavigate() {}, onUseTopic() {}, onStartCreate() {}, composer: React.createElement('draft-composer', {}) }); };
}
function flatten(node) { return !node || typeof node !== 'object' ? [] : [node, ...(node.children || []).flatMap(flatten)]; }
function text(node) { return node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : (node.children || []).map(text).join(''); }
test('dashboard uses the guarded shared composer before statistics', () => {
  const tree = setup()(), nodes = flatten(tree);
  const composer = nodes.findIndex(n => n.type === 'draft-composer');
  const stats = nodes.findIndex(n => n.props.className === 'dash-stats');
  assert.ok(composer >= 0); assert.ok(composer < stats);
});
test('dashboard has no runtime implementation explanation and distinguishes loading from empty', () => {
  const tree = setup()();
  assert.doesNotMatch(text(tree), /CLI diagnostic|需配置代理|一站式|请发送消息验证/);
  assert.match(text(tree), /加载中/);
});
