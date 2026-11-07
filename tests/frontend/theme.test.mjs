import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/theme.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});
const KEY = 'easel_appearance';
const themes = ['paper', 'minimal', 'forest', 'pastel', 'swiss'];
function setup({ saved, legacy, dark = false, blocked = false, noMedia = false, oldMedia = false } = {}) {
  const entries = new Map();
  if (saved !== undefined) entries.set(KEY, typeof saved === 'string' ? saved : JSON.stringify(saved));
  if (legacy !== undefined) entries.set('easel_theme', legacy);
  const listeners = new Set();
  const mediaListeners = new Set();
  const root = { dataset: {}, style: {} };
  const storage = {
    getItem(key) { if (blocked) throw Error('blocked'); return entries.get(key) ?? null; },
    setItem(key, value) { if (blocked) throw Error('blocked'); entries.set(key, value); },
  };
  const media = { matches: dark };
  if (oldMedia) {
    media.addListener = cb => mediaListeners.add(cb);
    media.removeListener = cb => mediaListeners.delete(cb);
  } else {
    media.addEventListener = (_, cb) => mediaListeners.add(cb);
    media.removeEventListener = (_, cb) => mediaListeners.delete(cb);
  }
  const win = {
    get localStorage() { if (blocked) throw Error('blocked'); return storage; },
    addEventListener: (_, cb) => listeners.add(cb), removeEventListener: (_, cb) => listeners.delete(cb),
    ...(noMedia ? {} : { matchMedia(query) { assert.equal(query, '(prefers-color-scheme: dark)'); return media; } }),
  };
  const exports = {};
  runInNewContext(outputText, { exports, document: { documentElement: root }, window: win });
  return { api: exports, root, entries, listeners, storage, win, mediaListeners,
    systemChange(value) { media.matches = value; [...mediaListeners].forEach(cb => cb({ matches: value })); },
    receive(value, key = KEY, area = storage) {
      const serialized = value == null ? null : typeof value === 'string' ? value : JSON.stringify(value);
      if (serialized == null) entries.delete(key); else entries.set(key, serialized);
      [...listeners].forEach(cb => cb({ key, newValue: serialized, storageArea: area }));
    },
  };
}
for (const dark of [false, true]) {
  for (const legacy of [undefined, ...themes, 'light', 'dark', 'broken']) {
    test(`first run defaults to system ${dark ? 'night/C' : 'day/B'} despite legacy ${legacy}`, () => {
      const view = setup({ dark, legacy }); view.api.initializeTheme();
      assert.equal(view.api.getAppearanceMode(), 'system');
      assert.equal(view.api.getTheme(), dark ? 'forest' : 'minimal');
      assert.equal(view.root.dataset.theme, dark ? 'forest' : 'minimal');
      assert.equal(view.root.style.colorScheme, dark ? 'dark' : 'light');
      assert.equal(JSON.parse(view.entries.get(KEY)).mode, 'system');
    });
  }
}
for (const theme of themes) {
  test(`manual ${theme} persists and restores independently of system`, () => {
    const view = setup({ dark: theme !== 'forest' }); view.api.initializeTheme();
    let updates = 0; view.api.subscribeTheme(() => updates++);
    assert.equal(view.api.setTheme(theme), true);
    assert.equal(view.api.getTheme(), theme);
    assert.equal(view.api.getAppearanceMode(), 'custom');
    assert.equal(view.root.style.colorScheme, theme === 'forest' ? 'dark' : 'light');
    assert.equal(updates, 1);
    const restored = setup({ saved: view.entries.get(KEY), dark: theme !== 'forest' }); restored.api.initializeTheme();
    assert.equal(restored.api.getTheme(), theme); assert.equal(restored.api.getAppearanceMode(), 'custom');
  });
}
test('all five approved themes remain available with exact names', () => {
  const configured = setup().api.THEMES;
  assert.equal(configured.filter(theme => theme.available).length, 5);
  assert.equal(configured.map(theme => theme.name).join('|'), 'A · 纸感编辑室|B · 极简效率台|C · 深色创作舱|D · 柔和彩色工作室|E · 瑞士网格系统');
});
for (const [mode, expected] of [['light', 'minimal'], ['dark', 'forest']]) {
  test(`explicit ${mode} selects ${expected}, ignores system and survives reload`, () => {
    const view = setup({ dark: mode === 'light' }); view.api.initializeTheme();
    assert.equal(view.api.setAppearanceMode(mode), true); assert.equal(view.api.getTheme(), expected);
    view.systemChange(false); view.systemChange(true); assert.equal(view.api.getTheme(), expected);
    const restored = setup({ saved: view.entries.get(KEY), dark: mode === 'light' }); restored.api.initializeTheme();
    assert.equal(restored.api.getAppearanceMode(), mode); assert.equal(restored.api.getTheme(), expected);
  });
}
test('system changes live; custom mode stays put; returning to system uses latest OS state', () => {
  const view = setup(); view.api.initializeTheme();
  view.systemChange(true); assert.equal(view.api.getTheme(), 'forest');
  view.systemChange(false); assert.equal(view.api.getTheme(), 'minimal');
  view.api.setTheme('pastel'); view.systemChange(true); assert.equal(view.api.getTheme(), 'pastel');
  view.api.setAppearanceMode('system'); assert.equal(view.api.getTheme(), 'forest');
  view.systemChange(false); assert.equal(view.api.getTheme(), 'minimal');
});
test('cross-tab appearance is atomic and legacy mirror events cannot override it', () => {
  const view = setup({ dark: true }); view.api.initializeTheme();
  for (const theme of themes) {
    view.receive({ mode: 'custom', theme }); assert.equal(view.api.getTheme(), theme);
    view.receive('paper', 'easel_theme'); assert.equal(view.api.getTheme(), theme);
  }
  view.receive({ mode: 'light', theme: 'forest' }); assert.equal(view.api.getTheme(), 'minimal');
  view.receive({ mode: 'system', theme: 'pastel' }); assert.equal(view.api.getTheme(), 'forest');
  view.receive(null); assert.equal(view.api.getAppearanceMode(), 'system');
  view.receive(null, null); assert.equal(view.api.getTheme(), 'forest');
});
test('storage area and unrelated keys are ignored; unsubscribe and cleanup release listeners', () => {
  const view = setup(); const cleanup = view.api.initializeTheme();
  let updates = 0; const off = view.api.subscribeTheme(() => updates++);
  view.receive({ mode: 'dark' }, 'other'); view.receive({ mode: 'dark' }, KEY, {}); assert.equal(updates, 0);
  view.api.setAppearanceMode('dark'); assert.equal(updates, 1);
  off(); view.api.setAppearanceMode('light'); assert.equal(updates, 1);
  cleanup(); assert.equal(view.listeners.size, 0); assert.equal(view.mediaListeners.size, 0);
});
test('queued stale storage and clear events cannot undo a newer saved local choice', () => {
  const view = setup({ dark: true }); view.api.initializeTheme();
  const receive = [...view.listeners][0];
  view.api.setAppearanceMode('dark');
  receive({ key: KEY, newValue: JSON.stringify({ mode: 'light', theme: 'paper' }), storageArea: view.storage });
  assert.equal(view.api.getAppearanceMode(), 'dark');
  assert.equal(view.api.getTheme(), 'forest');
  receive({ key: null, newValue: null, storageArea: view.storage });
  assert.equal(view.api.getAppearanceMode(), 'dark');
  view.entries.clear();
  receive({ key: null, newValue: null, storageArea: view.storage });
  assert.equal(view.api.getAppearanceMode(), 'system');
  assert.equal(view.api.getTheme(), 'forest');
});
test('blocked storage keeps live OS following and manual selection usable', () => {
  const view = setup({ blocked: true, dark: true });
  assert.doesNotThrow(() => view.api.initializeTheme()); assert.equal(view.api.getTheme(), 'forest');
  view.systemChange(false); assert.equal(view.api.getTheme(), 'minimal');
  view.api.setTheme('minimal'); assert.equal(view.api.getTheme(), 'minimal');
});
test('missing media API falls back to day but explicit night still works', () => {
  const view = setup({ noMedia: true }); const cleanup = view.api.initializeTheme();
  assert.equal(view.api.getTheme(), 'minimal'); view.api.setAppearanceMode('dark'); assert.equal(view.api.getTheme(), 'forest'); cleanup();
});
test('older MediaQueryList listeners are supported and cleaned up', () => {
  const view = setup({ oldMedia: true }); const cleanup = view.api.initializeTheme();
  view.systemChange(true); assert.equal(view.api.getTheme(), 'forest'); cleanup(); assert.equal(view.mediaListeners.size, 0);
});
test('invalid preferences normalize safely and invalid actions leave state unchanged', () => {
  for (const saved of ['{bad', 'null', '[]', '"dark"', { mode: 'bad', theme: 'forest' }]) {
    const view = setup({ saved, dark: true }); view.api.initializeTheme();
    assert.equal(view.api.getAppearanceMode(), 'system'); assert.equal(view.api.getTheme(), 'forest');
  }
  const view = setup(); view.api.initializeTheme();
  for (const value of ['unknown', '__proto__', null, {}, 2]) {
    assert.equal(view.api.setAppearanceMode(value), false); assert.equal(view.api.setTheme(value), false);
    assert.equal(view.api.getTheme(), 'minimal');
  }
});
test('before-paint bootstrap matches runtime for all modes, malformed data, and OS appearances', () => {
  const html = readFileSync(new URL('../../web/frontend/index.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const cases = [undefined, '{bad', 'null', { mode: ['dark'], theme: 'forest' }, { mode: 'invalid', theme: 'forest' }, { mode: 'custom', theme: 'invalid' }];
  for (const mode of ['system', 'light', 'dark', 'custom']) for (const theme of themes) cases.push({ mode, theme });
  for (const dark of [false, true]) for (const saved of cases) {
    const view = setup({ saved, dark }); const root = { dataset: {}, style: {} };
    runInNewContext(script, { localStorage: view.storage, document: { documentElement: root }, window: view.win });
    view.api.initializeTheme();
    assert.equal(root.dataset.theme, view.api.getTheme()); assert.equal(root.dataset.appearanceMode, view.api.getAppearanceMode());
    assert.equal(root.style.colorScheme, view.root.style.colorScheme);
  }
  const root = { dataset: {}, style: {} };
  runInNewContext(script, { get localStorage() { throw Error('blocked'); }, document: { documentElement: root }, window: { matchMedia: () => ({ matches: true }) } });
  assert.equal(root.dataset.theme, 'forest');
});
