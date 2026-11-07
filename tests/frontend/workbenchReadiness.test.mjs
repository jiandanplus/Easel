import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load() {
  const url = new URL('../../web/frontend/src/lib/workbenchReadiness.ts', import.meta.url);
  assert.ok(existsSync(url), 'shared workbench readiness must exist');
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, Object, Number, Date });
  return exports;
}
const base = () => ({ contractVersion: 1, connected: true, configured: true, sourceKey: 's1', expectedSourceKey: 's1', catalogExpiresAt: 2000, now: 1000,
  selection: { model: 'one', catalog_revision: 'r1', reasoning_effort: 'high' },
  catalog: { ready: true, switchable: true, revision: 'r1', source_revision: 's1', models: [{ id: 'one', reasoning: { status: 'supported', efforts: ['high'] } }] } });
test('a valid current authority and lease allow a submission without provider probing', () => {
  const x = load().resolveWorkbenchReadiness(base());
  assert.equal(x.canSubmit, true); assert.equal(x.state, 'ready'); assert.equal(x.action, 'none');
});
test('missing or unsupported contract blocks execution, retaining a recovery action', () => {
  for (const contractVersion of [undefined, 0, 2, '1']) {
    const x = load().resolveWorkbenchReadiness({ ...base(), contractVersion });
    assert.equal(x.canSubmit, false); assert.equal(x.state, 'incompatible'); assert.equal(x.action, 'reconnect');
  }
});
test('initial loading never reports disconnected or unconfigured', () => {
  const x = load().resolveWorkbenchReadiness({ loading: true });
  assert.equal(x.state, 'checking'); assert.equal(x.canSubmit, false);
});
test('transport failures and expired leases cannot use a stale directory', () => {
  for (const patch of [{ error: true }, { connected: false }, { catalogExpiresAt: 999 }, { catalogExpiresAt: undefined }]) {
    const x = load().resolveWorkbenchReadiness({ ...base(), ...patch });
    assert.equal(x.canSubmit, false); assert.equal(x.action, 'reconnect');
  }
});
test('background refresh permits only an unexpired already-confirmed selection', () => {
  assert.equal(load().resolveWorkbenchReadiness({ ...base(), refreshing: true }).canSubmit, true);
  assert.equal(load().resolveWorkbenchReadiness({ ...base(), refreshing: true, catalogExpiresAt: 1000 }).canSubmit, false);
});
test('source change remains blocked even when the new directory is ready', () => {
  const x = load().resolveWorkbenchReadiness({ ...base(), sourceKey: 'new-account' });
  assert.equal(x.state, 'source-changed'); assert.equal(x.action, 'new-chat'); assert.equal(x.canSubmit, false);
});
test('unconfigured and model confirmation have distinct next actions', () => {
  assert.equal(load().resolveWorkbenchReadiness({ ...base(), configured: false }).action, 'settings');
  for (const selection of [undefined, { model: 'missing', catalog_revision: 'r1' }, { model: 'one', catalog_revision: 'old' }, { model: 'one', catalog_revision: 'r1', reasoning_effort: 'max' }]) {
    const x = load().resolveWorkbenchReadiness({ ...base(), selection });
    assert.equal(x.canSubmit, false); assert.equal(x.action, 'confirm-model');
  }
});
test('native default directories remain valid only while their own lease is valid', () => {
  const x = base(); x.catalog.switchable = false; x.selection = undefined;
  assert.equal(load().resolveWorkbenchReadiness(x).canSubmit, true);
  x.catalog.ready = false;
  assert.equal(load().resolveWorkbenchReadiness(x).canSubmit, false);
});
test('malformed directories fail closed with a reconnect action', () => {
  for (const catalog of [{ ready: true, switchable: true, revision: 'r1' }, { ready: true, switchable: true, revision: 'r1', models: [null] }]) {
    const x = load().resolveWorkbenchReadiness({ ...base(), catalog });
    assert.equal(x.canSubmit, false); assert.equal(x.action, 'reconnect');
  }
});
