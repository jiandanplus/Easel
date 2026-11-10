import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load() {
  const url = new URL('../../web/frontend/src/lib/taskLifecycle.ts', import.meta.url);
  assert.ok(existsSync(url), 'task lifecycle permissions must exist'); const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports }); return exports;
}
test('uncertain submissions can query, never automatically resend or claim failure', () => {
  for (const phase of ['submitting','confirming','disconnected','unknown']) {
    const x = load().taskLifecycle({ phase });
    assert.equal(x.canSend, false); assert.equal(x.canQuery, true); assert.equal(x.canRerun, false);
  }
});
test('stop remains pending until the backend confirms a stopped terminal', () => {
  const pending = load().taskLifecycle({ phase: 'stopping' });
  assert.equal(pending.label, '正在停止'); assert.equal(pending.canSend, false); assert.equal(pending.canStop, false);
  assert.equal(load().taskLifecycle({ phase: 'stopped' }).canSend, true);
});
test('accepted/running can stop and reconnect but cannot submit a concurrent turn', () => {
  for (const phase of ['accepted','running']) {
    const x = load().taskLifecycle({ phase });
    assert.equal(x.canStop, true); assert.equal(x.canReconnect, true); assert.equal(x.canSend, false);
  }
});
test('done with an unclean end remains uncertain, not a successful execution', () => {
  const x = load().taskLifecycle({ phase: 'done', cleanEnd: false });
  assert.equal(x.phase, 'unknown'); assert.equal(x.canQuery, true); assert.equal(x.canRerun, false);
});
test('completed content and pending finances have independent status', () => {
  const x = load().taskLifecycle({ phase: 'done', cleanEnd: true, financialState: 'pending_reconciliation', resultState: 'cached' });
  assert.equal(x.label, '已完成'); assert.equal(x.financialLabel, '费用待核对'); assert.equal(x.canSend, true); assert.equal(x.canQuery, true);
});
test('platform absorption never becomes an invented successful model reply', () => {
  const x = load().taskLifecycle({ phase: 'failed', financialState: 'waived', resultState: 'not_available' });
  assert.equal(x.label, '未完成'); assert.equal(x.financialLabel, '平台承担'); assert.equal(x.canRerun, false);
});
test('only explicit confirmed-not-sent permits a new execution action', () => {
  assert.equal(load().taskLifecycle({ phase: 'failed' }).canRerun, false);
  assert.equal(load().taskLifecycle({ phase: 'failed', confirmedNotSent: true }).canRerun, true);
});
test('explicit stop and failure reasons remain visible even if a stream closed cleanly', () => {
  assert.equal(load().taskLifecycle({ phase: 'done', cleanEnd: true, stopReason: 'user_stopped' }).phase, 'stopped');
  assert.equal(load().taskLifecycle({ phase: 'done', cleanEnd: true, stopReason: 'timeout' }).phase, 'failed');
});
test('only an exact versioned durable acceptance receipt acknowledges a draft', () => {
  const lib = load();
  const value = { version: 1, session_id: 'chat', turn_id: 'turn', request_hash: 'a'.repeat(64), receipt_id: 'b'.repeat(64) };
  assert.equal(lib.isAcceptedTaskReceipt(value, 'chat', 'turn'), true);
  for (const patch of [{ version: 2 }, { session_id: 'foreign' }, { turn_id: 'foreign' }, { request_hash: 'private' }, { receipt_id: undefined }])
    assert.equal(lib.isAcceptedTaskReceipt({ ...value, ...patch }, 'chat', 'turn'), false);
  assert.equal(lib.isAcceptedTaskReceipt(null, 'chat', 'turn'), false);
});
