import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/SettingsPanel.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
const settle = () => new Promise(resolve => setImmediate(resolve));
function nodes(tree, type) { return !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))]; }
function text(tree) { return tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? (tree.children || []).map(text).join('') : String(tree); }
function setup() {
  const state = [], effects = [], callbacks = [], exports = {};
  let cursor = 0, effectCursor = 0, callbackCursor = 0;
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = { value: typeof initial === 'function' ? initial() : initial, set(value) { this.value = typeof value === 'function' ? value(this.value) : value; } }; return [state[i].value, state[i].set.bind(state[i])]; },
    useRef(initial) { const i = cursor++; if (!(i in state)) state[i] = { current: initial }; return state[i]; },
    useCallback(fn, deps) { const i = callbackCursor++; if (!callbacks[i] || deps.some((value, j) => value !== callbacks[i].deps[j])) callbacks[i] = { fn, deps }; return callbacks[i].fn; },
    useEffect(fn, deps) { const i = effectCursor++; if (!effects[i] || deps.some((value, j) => value !== effects[i].deps[j])) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; } },
  };
  const api = { fetchModelChannels: async () => ({ channels: { chat: { rows: [] }, transcribe: { rows: [] } } }) };
  runInNewContext(code, { exports, require: name => name === 'react' ? React : name.includes('/api') ? api : name.includes('settingsNavIcons') ? { NAV_ICONS: {} } : { __esModule: true, default: name.split('/').at(-1) }, React, localStorage: { getItem: () => null }, window: { addEventListener() {}, removeEventListener() {} }, setTimeout, clearTimeout, Date, Error, Promise });
  return { render() { cursor = 0; effectCursor = 0; callbackCursor = 0; return exports.default({ onClose() {}, banner: '请先配置模型，再开始创作。' }); }, cleanup() { effects.forEach(effect => effect.cleanup?.()); } };
}

for (const backend of ['central', 'legacy']) {
  test(`${backend} subscription updates parent copy immediately after loading or saving without provider keys`, async () => {
    const view = setup();
    let tree = view.render();
    const harness = nodes(tree, 'HarnessSettings')[0];
    assert.equal(typeof harness.props.onSettingsChange, 'function');
    harness.props.onChange('codex'); harness.props.onSelect('codex');
    harness.props.onSettingsChange({ runtime: 'codex', service_source: 'subscription', subscription_backend: backend, subscription_model: 'selected-text' });
    view.render(); await settle(); tree = view.render();
    const copy = text(tree);
    assert.match(copy, backend === 'central' ? /中央订阅/ : /旧版订阅/);
    assert.match(copy, /订阅与账单/);
    assert.match(copy, /新建对话/);
    assert.doesNotMatch(copy, /请先配置模型|自定义供应商地址|API Key/);
    assert.match(copy, /selected-text/);
    for (const label of ['保存配置', '全部自测']) {
      const button = nodes(tree, 'button').find(node => text(node) === label);
      assert.equal(button.props.disabled, true);
      assert.match(button.props.title, /订阅/);
      assert.doesNotMatch(button.props.title, /API Key|供应商地址/);
    }
    // A later successful custom save restores the independent custom setup instructions.
    nodes(tree, 'HarnessSettings')[0].props.onSettingsChange({ runtime: 'codex', service_source: 'custom' });
    tree = view.render();
    assert.match(text(tree), /请先配置模型|自定义供应商地址/);
    assert.match(nodes(tree, 'button').find(node => text(node) === '全部自测').props.title, /自测暂不可用/);
    assert.equal(nodes(tree, 'div').some(node => node.props.className === 'settings-foot'), false);
    view.cleanup();
  });
}

test('unversioned saved subscription keeps legacy copy and does not imply central account ownership', () => {
  const view = setup(); const tree = view.render(); const harness = nodes(tree, 'HarnessSettings')[0];
  assert.equal(typeof harness.props.onSettingsChange, 'function');
  harness.props.onChange('codex'); harness.props.onSelect('codex');
  harness.props.onSettingsChange({ runtime: 'codex', service_source: 'subscription' });
  const copy = text(view.render());
  assert.match(copy, /旧版订阅/); assert.doesNotMatch(copy, /中央订阅|请先配置模型|API Key/);
  view.cleanup();
});

for (const runtime of ['pi', 'codex']) {
  test(`${runtime} custom setup retains separate save controls and unavailable self-test status`, async () => {
    const view = setup(); let tree = view.render();
    const harness = nodes(tree, 'HarnessSettings')[0];
    harness.props.onChange(runtime); harness.props.onSelect(runtime);
    harness.props.onSettingsChange({ runtime, service_source: 'custom' });
    view.render(); await settle(); tree = view.render();
    for (const label of ['保存配置', '全部自测']) {
      const button = nodes(tree, 'button').find(node => text(node) === label);
      assert.equal(button.props.disabled, true);
      assert.match(button.props.title, label === '保存配置' ? /保存底座.*保存供应商/ : /自测暂不可用/);
    }
    assert.equal(nodes(tree, 'div').some(node => node.props.className === 'settings-foot'), false);
    view.cleanup();
  });
}
