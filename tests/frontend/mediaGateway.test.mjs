import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(path, dependencies = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/${path}`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => dependencies[name] || {}, React: dependencies.react, Error, URL, JSON });
  return exports;
}
const stored = () => ({ baseUrl: 'https://gateway.invalid/prefix', keyConfigured: true, keyMasked: '••••', modes: { image: 'custom', video: 'custom', music: 'custom', voice: 'custom' }, image: { model: 'image-test' }, video: { model: 'video-test', profile: 'standard' }, music: { provider: 'dashscope', model: '' }, voice: { provider: 'dashscope', model: '', voiceId: '' }, geminiTtsApi: 'generate-content', configured: { image: false, video: false, music: false, voice: false }, managed: { image: false, video: false, music: false, voice: false } });
const settle = () => new Promise(resolve => setImmediate(resolve));
function nodes(tree, type) { return !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(child => nodes(child, type))]; }
function text(tree) { return typeof tree === 'string' ? tree : !tree || typeof tree !== 'object' ? '' : (tree.children || []).map(text).join(''); }
test('gateway payload preserves key and unrelated choices when switching capability modes', () => {
  const model = load('components/mediaGatewayState.ts');
  const initial = stored(); const draft = model.gatewayDraft(initial);
  draft.modes.image = 'new-api';
  const payload = model.gatewayPayload(draft);
  assert.equal(payload.key, ''); assert.equal(payload.clearKey, false);
  assert.equal(payload.modes.video, 'custom'); assert.equal(initial.modes.image, 'custom');
  assert.equal(payload.voice.provider, 'dashscope'); assert.equal(payload.video.model, 'video-test');
  assert.equal(model.gatewayValidation(draft, initial), '');
  assert.equal('keyMasked' in payload, false);
});
test('gateway protects endpoint replacement and explicit clear while retaining protocol prefixes', () => {
  const model = load('components/mediaGatewayState.ts'); const initial = stored(); const draft = model.gatewayDraft(initial);
  draft.baseUrl = 'https://new.invalid/proxy/v1';
  assert.match(model.gatewayValidation(draft, initial), /重新填写.*密钥/);
  draft.key = 'synthetic'; assert.equal(model.gatewayValidation(draft, initial), '');
  assert.equal(model.gatewayPayload(draft).baseUrl, 'https://new.invalid/proxy/v1');
  draft.key = ''; draft.baseUrl = initial.baseUrl; draft.clearKey = true; draft.modes.voice = 'new-api';
  assert.match(model.gatewayValidation(draft, initial), /全部.*自定义/);
  draft.modes.voice = 'custom'; assert.equal(model.gatewayValidation(draft, initial), '');
});
test('gateway validates enabled model and provider-specific voice fields', () => {
  const model = load('components/mediaGatewayState.ts'); const initial = stored(); const draft = model.gatewayDraft(initial);
  draft.modes.image = 'new-api'; draft.image.model = '';
  assert.match(model.gatewayValidation(draft, initial), /图片.*模型/);
  draft.modes.image = 'custom'; draft.modes.voice = 'new-api'; draft.voice.provider = 'gemini';
  assert.match(model.gatewayValidation(draft, initial), /配音.*模型/);
  draft.voice.model = 'tts-test'; assert.equal(model.gatewayValidation(draft, initial), '');
  assert.equal(model.VIDEO_PROFILES.length, 5); assert.equal(model.VOICE_PROVIDERS.length, 5);
  assert.deepEqual(Array.from(model.MUSIC_PROVIDERS, item => item.value), ['dashscope', 'suno-compatible']);
});
function view({ error = '', settings = stored() } = {}) {
  const state = []; let cursor = 0; let mounted = false; const saves = []; const changes = [];
  const model = load('components/mediaGatewayState.ts');
  const component = load('components/MediaGatewaySettings.tsx', {
    react: { useState: initial => { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; }, useEffect: action => { if (!mounted) { mounted = true; action(); } }, createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }) },
    './mediaGatewayState': model,
    '../lib/api': { fetchMediaGateway: async () => settings, saveMediaGateway: async payload => { saves.push(payload); if (error) throw new Error(error); return { ...settings, ...payload, key: undefined, keyConfigured: !payload.clearKey }; } },
  }).default;
  return { saves, changes, render() { cursor = 0; return component({ onChange: value => changes.push(value) }); } };
}
test('gateway card loads independently, saves nested choices and clears plaintext after save', async () => {
  const v = view(); v.render(); await settle(); let tree = v.render();
  assert.equal(nodes(tree, 'input').find(n => n.props.type === 'password').props.value, '');
  nodes(tree, 'select').find(n => n.props.id === 'media-image-mode').props.onChange({ target: { value: 'new-api' } });
  tree = v.render(); nodes(tree, 'input').find(n => n.props.type === 'password').props.onChange({ target: { value: 'synthetic-key' } });
  tree = v.render(); nodes(tree, 'form')[0].props.onSubmit({ preventDefault() {} }); await settle(); tree = v.render();
  assert.equal(v.saves[0].modes.image, 'new-api'); assert.equal(v.saves[0].key, 'synthetic-key');
  assert.equal(nodes(tree, 'input').find(n => n.props.type === 'password').props.value, '');
  assert.match(text(tree), /已保存/); assert.equal(v.changes.at(-1).modes.image, 'new-api');
});
test('gateway save failures preserve edited choices and report error while clearing plaintext', async () => {
  const v = view({ error: '当前任务运行中' }); v.render(); await settle(); let tree = v.render();
  nodes(tree, 'input').find(n => n.props.type === 'password').props.onChange({ target: { value: 'synthetic-key' } });
  nodes(v.render(), 'select').find(n => n.props.id === 'media-video-mode').props.onChange({ target: { value: 'new-api' } });
  nodes(v.render(), 'form')[0].props.onSubmit({ preventDefault() {} }); await settle(); tree = v.render();
  assert.match(text(tree), /保存失败：当前任务运行中/);
  assert.equal(nodes(tree, 'select').find(n => n.props.id === 'media-video-mode').props.value, 'new-api');
  assert.equal(nodes(tree, 'input').find(n => n.props.type === 'password').props.value, '');
  assert.equal(v.changes.length, 1);
});
test('media gateway API uses uncached GET and nested PUT without browser credential storage', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  const exports = {}; const calls = [];
  runInNewContext(outputText, { exports, require: requireDeliverables, Error, Object, window: { location: { pathname: '/easel/' } }, fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => stored() }; } });
  await exports.fetchMediaGateway();
  const model = load('components/mediaGatewayState.ts'); const payload = model.gatewayPayload(model.gatewayDraft(stored()));
  payload.key = 'synthetic-key'; await exports.saveMediaGateway(payload);
  assert.equal(calls[0].url, '/easel/api/settings/media-gateway'); assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[1].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[1].options.body), JSON.parse(JSON.stringify(payload)));
  assert.equal(calls[1].options.headers.Authorization, undefined);
});
test('managed gateway keeps retained provider table editable while selftest stays blocked', () => {
  const source = readFileSync(new URL('../../web/frontend/src/components/SettingsPanel.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('settings.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'SettingsPanel');
  const output = ts.transpileModule(declaration.getText(parsed).replace('export default ', '') + '\nSettingsPanel;', { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
  const states = []; let cursor = 0;
  const component = runInNewContext(output, {
    React: { Fragment: 'fragment', createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }) },
    useState: initial => { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial === true ? false : initial; return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
    useEffect() {}, useCallback: callback => callback, useRef: value => ({ current: value }),
    CHANNELS: [{ id: 'image', label: '生图' }], SETTINGS_GROUPS: [], NAV_ICONS: {}, MediaGatewaySettings: 'media-gateway', HarnessSettings: 'harness', ThemeSettings: 'theme', PlatformAccountSettings: 'account', EnvBoard: 'env', IconSlidersHorizontal: 'icon', IconPackage: 'icon', IconEllipsis: 'icon',
  });
  const render = () => { cursor = 0; return component({ onClose() {} }); };
  nodes(render(), 'button').find(node => text(node) === '生图').props.onClick();
  let tree = render();
  assert.equal(nodes(tree, 'button').find(node => text(node) === '保存配置').props.disabled, false);
  const value = stored(); value.modes.image = 'new-api'; value.configured.image = true; value.managed.image = true;
  nodes(tree, 'media-gateway')[0].props.onChange(value); tree = render();
  assert.match(text(tree), /当前通道使用统一网关.*配置完整/);
  assert.equal(nodes(tree, 'button').find(node => text(node) === '保存配置').props.disabled, false);
  assert.equal(nodes(tree, 'button').find(node => text(node) === '全部自测').props.disabled, true);
  assert.equal(nodes(tree, 'div').some(node => node.props.className === 'board'), true);
  value.modes.image = 'custom'; value.managed.image = false; nodes(tree, 'media-gateway')[0].props.onChange({ ...value }); tree = render();
  assert.doesNotMatch(text(tree), /当前通道使用统一网关/);
  assert.equal(nodes(tree, 'button').find(node => text(node) === '保存配置').props.disabled, false);
  assert.equal(nodes(tree, 'div').some(node => node.props.className === 'board'), true);
});

test('choosing Suno sends its native provider ID accepted by the settings API', () => {
  const model = load('components/mediaGatewayState.ts');
  const draft = model.gatewayDraft(stored());
  draft.modes.music = 'new-api';
  draft.music.provider = model.MUSIC_PROVIDERS.find(item => item.label === 'Suno').value;
  assert.equal(model.gatewayPayload(draft).music.provider, 'suno-compatible');
});

test('unchanged legacy adapter bypasses unified model checks until explicitly adopted', () => {
  const model = load('components/mediaGatewayState.ts');
  const initial = stored(); initial.baseUrl = ''; initial.keyConfigured = false; initial.modes.image = 'new-api'; initial.image.model = '';
  const draft = model.gatewayDraft(initial);
  assert.equal(model.gatewayLegacyPreserved(draft, initial, 'image'), true);
  assert.equal(model.gatewayValidation(draft, initial), '');
  draft.adopt = ['image'];
  assert.equal(model.gatewayLegacyPreserved(draft, initial, 'image'), false);
  assert.match(model.gatewayValidation(draft, initial), /网关根地址/);
});

test('legacy source is explicit and selecting unified gateway requests adoption', async () => {
  const settings = stored(); settings.modes.image = 'new-api'; settings.image.model = ''; settings.baseUrl = ''; settings.keyConfigured = false;
  const v = view({ settings }); v.render(); await settle();
  let tree = v.render();
  assert.equal(nodes(tree, 'select').find(node => node.props.id === 'media-image-mode').props.value, 'legacy');
  assert.match(text(tree), /保留旧版 New API/);
  assert.equal(nodes(tree, 'input').some(node => node.props.id === 'media-image-model'), false);
  nodes(tree, 'select').find(node => node.props.id === 'media-image-mode').props.onChange({ target: { value: 'new-api' } });
  tree = v.render();
  assert.equal(nodes(tree, 'input').some(node => node.props.id === 'media-image-model'), true);
  nodes(tree, 'input').find(node => node.props.id === 'media-gateway-url').props.onChange({ target: { value: 'https://gateway.invalid' } });
  nodes(v.render(), 'input').find(node => node.props.id === 'media-gateway-key').props.onChange({ target: { value: 'unit-key' } });
  nodes(v.render(), 'input').find(node => node.props.id === 'media-image-model').props.onChange({ target: { value: 'new-image' } });
  nodes(v.render(), 'form')[0].props.onSubmit({ preventDefault() {} }); await settle();
  assert.deepEqual(Array.from(v.saves[0].adopt), ['image']);
});
