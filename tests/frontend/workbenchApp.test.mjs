import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = parsed.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'App');
const selected = app.body.statements.filter(n => ts.isVariableStatement(n) && n.declarationList.declarations.some(d => ['startStream', 'handleStopStream'].includes(d.name.getText(parsed))));
const code = ts.transpileModule(selected.map(n => n.getText(parsed)).join('\n') + '\n({startStream,handleStopStream});', { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
function setup(stop = async () => ({ stopped: false, state: 'stopping' })) {
  const calls = [], accepted = [], messages = [], ctl = { current: {} }, acc = { current: {} };
  const sessions = [{ id: 's', messages: [] }], streams = {};
  const methods = runInNewContext(code, { useCallback: f => f, snapshotChatModelSelection: v => v,
    draftStore: { acknowledge: (...args) => accepted.push(args) }, setDraftRefresh: () => {},
    workbench: { source_scope: 'source' }, sessionsRef: { current: sessions },
    streamCtl: ctl, streamAcc: acc, setSessions: fn => fn(sessions), saveSessions() {},
    sessionStorage: { setItem() {}, removeItem() {} }, setStreams: fn => Object.assign(streams, fn(streams)),
    typingBuf: { current: {} }, startTypingPump() {}, ensureTypingPump() {}, flushTyping() {},
    streamChat: (...args) => { calls.push(args); return { abort() { calls.push('aborted'); } }; },
    appendAssistant: (_s, m) => messages.push(m), clearStream: s => { delete acc.current[s]; delete ctl.current[s]; },
    stopChat: stop, setTimeout: f => f(), setResendNotice() {},
  });
  return { ...methods, calls, accepted, messages, acc, ctl, streams };
}
test('only a matching accepted receipt clears the captured submitted revision', () => {
  const view = setup();
  view.startStream('s', 'text', undefined, [], undefined, { scopeKey: 'original-scope', revision: 7, turnId: 'original-turn' });
  const args = view.calls[0];
  assert.equal(args[9], 'original-turn');
  assert.equal(view.accepted.length, 0);
  assert.equal(typeof args[17], 'function');
  args[17]({ version: 1, session_id: 'other', turn_id: 'original-turn', receipt_id: 'b'.repeat(64), request_hash: 'a'.repeat(64) });
  assert.equal(view.accepted.length, 0);
  args[17]({ version: 1, session_id: 's', turn_id: 'original-turn', receipt_id: 'b'.repeat(64), request_hash: 'a'.repeat(64) });
  assert.deepEqual(view.accepted[0], ['original-scope', 's', 'original-turn', 7]);
});
test('a pending stop keeps stream and original request instead of claiming stopped', async () => {
  const view = setup(); view.startStream('s', 'text'); view.acc.current.s.content = 'partial';
  await view.handleStopStream('s');
  assert.ok(view.ctl.current.s); assert.equal(view.messages.length, 0);
  assert.equal(view.streams.s.phase, 'stopping');
  assert.ok(!view.calls.includes('aborted'));
});
test('a completed task racing stop is never relabeled stopped', async () => {
  const view = setup(async () => ({ stopped: true, state: 'completed' }));
  view.startStream('s', 'text'); view.acc.current.s.content = 'finished';
  await view.handleStopStream('s');
  assert.equal(view.messages.length, 0); assert.ok(view.ctl.current.s);
});
test('done preserves a confirmed stop terminal state', () => {
  const view = setup(); view.startStream('s', 'text');
  view.acc.current.s.content = 'partial';
  view.calls[0][4]('s', { version: 1, session_id: 's', turn_id: view.calls[0][9], state: 'stopped', clean_end: false, stop_reason: 'user_stopped' });
  assert.equal(view.messages[0].taskState, 'stopped');
});
