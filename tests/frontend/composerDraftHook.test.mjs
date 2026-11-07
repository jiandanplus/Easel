import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function module(name, requireModule = () => ({})) {
  const path = new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url);
  assert.ok(existsSync(path), `${name} exists`);
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, require: requireModule, Date, Map, JSON, Error, Number, setTimeout, clearTimeout, window: { addEventListener() {}, removeEventListener() {} } });
  return exports;
}
function setup() {
  const values = new Map(), store = module('composerDrafts').createComposerDraftStore({ getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) });
  const states = [], refs = [], effects = []; let cursor, refCursor, effectCursor;
  const React = {
    useState(initial) { const i = cursor++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial; return [states[i], v => states[i] = typeof v === 'function' ? v(states[i]) : v]; },
    useRef(initial) { return refs[refCursor++] ||= { current: initial }; },
    useEffect(fn, deps) { const i = effectCursor++; if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; } },
    useCallback(fn) { return fn; },
  };
  const hook = module('useComposerDraft', n => n === 'react' ? React : module('composerDrafts')).useComposerDraft;
  let refresh = 0;
  return { store, values, refresh() { refresh++; }, render() { cursor = refCursor = effectCursor = 0; return hook('scope', 'session', store, refresh); }, cleanup() { effects.forEach(e => e.cleanup?.()); } };
}
test('draft survives unmount with text and exact attachment references', () => {
  const view = setup();
  view.render().update({ text: '三个要求', attachments: [{ id: 'a', name: '材料.txt', path: 'outputs/inbox/session/a.txt' }] });
  view.cleanup();
  const value = view.store.load('scope', 'session');
  assert.equal(value.text, '三个要求'); assert.equal(value.attachments[0].id, 'a'); assert.equal(value.persisted, true);
});
test('submission is not cleared before accepted; late confirmation preserves next revision', () => {
  const view = setup(); view.render().update({ text: '第一稿' });
  const snapshot = view.render().prepare('turn-1');
  assert.equal(view.render().draft.text, '第一稿');
  view.render().update({ text: '下一条修改' });
  view.store.acknowledge('scope', 'session', 'turn-1', snapshot.submission.submittedRevision);
  view.refresh(); view.render();
  assert.equal(view.render().draft.text, '下一条修改');
  assert.equal(view.render().draft.submission, undefined); view.cleanup();
});
test('accepted unchanged revision clears both text and attachment exactly once', () => {
  const view = setup(); view.render().update({ text: '第一稿', attachments: [{ id: 'a', name: 'a', path: 'outputs/a' }] });
  const snapshot = view.render().prepare('turn-1');
  view.store.acknowledge('scope', 'session', 'turn-1', snapshot.submission.submittedRevision);
  view.refresh(); view.render();
  assert.equal(view.render().draft.text, ''); assert.equal(view.render().draft.attachments.length, 0); view.cleanup();
});
