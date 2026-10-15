import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const file = new URL('../../web/frontend/src/lib/theme.ts', import.meta.url);
const source = existsSync(file) ? readFileSync(file, 'utf8') : '';
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});
function setup(saved, blocked = false) {
  const entries = new Map(saved === undefined ? [] : [['easel_theme', saved]]);
  const listeners = new Set();
  const root = { dataset: {}, style: {} };
  const storage = {
    getItem: key => { if (blocked) throw Error('blocked'); return entries.get(key) ?? null; },
    setItem: (key, value) => { if (blocked) throw Error('blocked'); entries.set(key, value); },
  };
  const exports = {};
  runInNewContext(outputText, { exports, document: { documentElement: root }, window: {
    get localStorage() { if (blocked) throw Error('blocked'); return storage; },
    addEventListener: (_, cb) => listeners.add(cb), removeEventListener: (_, cb) => listeners.delete(cb),
  } });
  return { api: exports, root, entries, listeners, storage };
}
test('theme module exposes the preference initializer', () => {
  assert.equal(typeof setup().api.initializeTheme, 'function');
});
for (const saved of [undefined, 'light', 'dark', 'broken']) {
  test(`initializes ${String(saved)} preference as paper and persists migration`, () => {
    const view = setup(saved);
    view.api.initializeTheme();
    assert.equal(view.api.getTheme(), 'paper');
    assert.equal(view.root.dataset.theme, 'paper');
    assert.equal(view.root.style.colorScheme, 'light');
    assert.equal(view.entries.get('easel_theme'), 'paper');
  });
}
const themes = ['paper', 'minimal', 'forest', 'pastel', 'swiss'];
for (const theme of themes) {
  test(`restores ${theme} and its correct native color scheme`, () => {
    const view = setup(theme);
    view.api.initializeTheme();
    assert.equal(view.api.getTheme(), theme);
    assert.equal(view.root.dataset.theme, theme);
    assert.equal(view.root.style.colorScheme, theme === 'forest' ? 'dark' : 'light');
    assert.equal(view.entries.get('easel_theme'), theme);
  });
  test(`selecting ${theme} persists it and notifies the current tab`, () => {
    const view = setup();
    view.api.initializeTheme();
    let updates = 0;
    view.api.subscribeTheme(() => updates++);
    assert.equal(view.api.setTheme(theme), true);
    assert.equal(view.root.dataset.theme, theme);
    assert.equal(view.entries.get('easel_theme'), theme);
    assert.equal(view.root.style.colorScheme, theme === 'forest' ? 'dark' : 'light');
    assert.equal(updates, 1);
  });
}
test('all five approved themes are available with their exact names', () => {
  const configured = setup().api.THEMES;
  assert.equal(configured.filter(theme => theme.available).length, 5);
  assert.equal(configured.map(theme => theme.name).join('|'), 'A · 纸感编辑室|B · 极简效率台|C · 深色创作舱|D · 柔和彩色工作室|E · 瑞士网格系统');
});
test('unavailable storage still applies and selects paper', () => {
  const view = setup('dark', true);
  assert.doesNotThrow(() => view.api.initializeTheme());
  assert.equal(view.root.dataset.theme, 'paper');
  assert.equal(view.api.setTheme('paper'), true);
  assert.equal(view.api.setTheme('forest'), true);
  assert.equal(view.root.dataset.theme, 'forest');
  assert.equal(view.root.style.colorScheme, 'dark');
});
test('unknown and malformed themes cannot be activated or persisted', () => {
  const view = setup();
  view.api.initializeTheme();
  for (const value of ['unknown', 'light', 'dark', '__proto__', null, {}, 2]) {
    assert.equal(view.api.setTheme(value), false);
    assert.equal(view.api.getTheme(), 'paper');
    assert.equal(view.entries.get('easel_theme'), 'paper');
  }
});
test('cross-tab selection preserves valid themes and switches native form colors', () => {
  const view = setup();
  view.api.initializeTheme();
  const receive = [...view.listeners][0];
  for (const theme of ['forest', 'pastel', 'minimal', 'swiss', 'paper']) {
    view.entries.set('easel_theme', theme);
    receive({ key: 'easel_theme', storageArea: view.storage, newValue: theme });
    assert.equal(view.api.getTheme(), theme);
    assert.equal(view.root.dataset.theme, theme);
    assert.equal(view.root.style.colorScheme, theme === 'forest' ? 'dark' : 'light');
    assert.equal(view.entries.get('easel_theme'), theme);
  }
});
test('same-tab subscribers receive selection updates and can unsubscribe', () => {
  const view = setup();
  let updates = 0;
  const off = view.api.subscribeTheme(() => updates++);
  view.api.setTheme('paper');
  assert.equal(updates, 1);
  off();
  view.api.setTheme('paper');
  assert.equal(updates, 1);
});
test('cross-tab changes normalize legacy values, ignore other storage and clean up listener', () => {
  const view = setup();
  const cleanup = view.api.initializeTheme();
  let updates = 0;
  view.api.subscribeTheme(() => updates++);
  const receive = [...view.listeners][0];
  receive({ key: 'other', storageArea: view.storage, newValue: 'dark' });
  receive({ key: 'easel_theme', storageArea: {}, newValue: 'dark' });
  assert.equal(updates, 0);
  view.entries.set('easel_theme', 'dark');
  receive({ key: 'easel_theme', storageArea: view.storage, newValue: 'dark' });
  assert.equal(view.root.dataset.theme, 'paper');
  assert.equal(view.entries.get('easel_theme'), 'paper');
  assert.equal(updates, 1);
  receive({ key: null, storageArea: view.storage, newValue: null });
  assert.equal(updates, 2);
  cleanup();
  assert.equal(view.listeners.size, 0);
});
test('before-paint bootstrap matches runtime for every valid and legacy preference', () => {
  const html = readFileSync(new URL('../../web/frontend/index.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  for (const value of [...themes, 'dark', 'light', 'invalid', null]) {
    const root = { dataset: {}, style: {} };
    runInNewContext(script, { localStorage: { getItem: () => value, setItem() {} }, document: { documentElement: root }, window: {} });
    const expected = themes.includes(value) ? value : 'paper';
    assert.equal(root.dataset.theme, expected);
    assert.equal(root.style.colorScheme, expected === 'forest' ? 'dark' : 'light');
  }
  const root = { dataset: {}, style: {} };
  runInNewContext(script, { get localStorage() { throw Error('blocked'); }, document: { documentElement: root } });
  assert.equal(root.dataset.theme, 'paper');
});
