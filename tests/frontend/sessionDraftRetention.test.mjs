import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(storage) {
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/store.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports, require: requireDeliverables, localStorage: storage, Date, Math });
  return exports;
}
test('multiple unsent conversations with drafts survive history pruning', () => {
  let saved; const api = load({ setItem: (_key, value) => saved = value, getItem: () => saved ?? null });
  api.saveSessions([{ id: 'blank', messages: [] }, { id: 'draft1', messages: [], hasDraft: true }, { id: 'draft2', messages: [], hasDraft: true }, { id: 'old-blank', messages: [] }]);
  assert.deepEqual(JSON.parse(saved).map(session => session.id), ['blank', 'draft1', 'draft2']);
});
test('blocked browser storage does not crash the live conversation state', () => {
  const api = load({ setItem() { throw Error('quota'); }, getItem() { throw Error('blocked'); } });
  assert.doesNotThrow(() => api.saveSessions([{ id: 'draft', messages: [], hasDraft: true }]));
});
