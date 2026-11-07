import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const copyExports = {};
const artifactExports = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/deliverables.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports: artifactExports });
runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/requestFailureCopy.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports: copyExports });
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const names = ['startStream', 'resumePendingTurn', 'handleStopStream'];
const declarations = app.body.statements.filter(node => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some(decl => names.includes(decl.name.getText(parsed))));
const code = ts.transpileModule(declarations.map(node => node.getText(parsed)).join('\n') +
  '\n({ startStream, resumePendingTurn, handleStopStream });', { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function setup(lastTurn = { status: 'none', text: '' }, stopResult = { stopped: true, state: 'stopped' }) {
  const messages = [], calls = [], stops = [], acc = { current: {} }, ctl = { current: {} };
  const state = { sessions: [{ id: 'owned', messages: [{ role: 'user', content: 'test' }], pendingTurnId: 'resumed-123' }], streams: {}, draftRefresh: {} };
  const values = new Map(), draftExports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/composerDrafts.ts', import.meta.url), 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports: draftExports, Map, JSON, Date, Number, Object, Array, Error });
  const draftStore = draftExports.createComposerDraftStore({ getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) });
  const sessionsRef = { current: state.sessions }, currentDraftScopeRef = { current: 'scope' };
  const methods = runInNewContext(code, {
    ...copyExports, ...artifactExports,
    useCallback: value => value, streamAcc: acc, streamCtl: ctl,
    sessionsRef, answeredRef: { current: new Set() },
    draftStore, currentDraftScopeRef, workbench: { source_scope: 'source' },
    setDraftRefresh: fn => { state.draftRefresh = fn(state.draftRefresh); },
    typingBuf: { current: {} }, sessionStorage: { setItem() {}, getItem() {}, removeItem() {} },
    snapshotChatModelSelection: value => value,
    setSessions: fn => { state.sessions = fn(state.sessions); sessionsRef.current = state.sessions; }, saveSessions() {},
    setStreams: fn => { state.streams = fn(state.streams); },
    appendAssistant: (id, message) => { messages.push({ id, message }); },
    clearStream: id => { delete acc.current[id]; delete ctl.current[id]; delete state.streams[id]; },
    startTypingPump() {}, ensureTypingPump() {}, flushTyping() {},
    setTimeout: fn => fn(), stopChat: async (...args) => { stops.push(args); return typeof stopResult === 'function' ? await stopResult() : stopResult; },
    fetchLastTurn: async () => lastTurn,
    streamChat: (...args) => { calls.push(args); return { abort() {} }; },
  });
  return { ...methods, messages, calls, stops, acc, ctl, state, draftStore, currentDraftScopeRef };
}

for (const outcome of ['done', 'error', 'stop']) {
  test(`central turn reference survives ${outcome} and is persisted on assistant message`, async () => {
    const view = setup(); view.startStream('owned', 'test', undefined, [], { model: 'fixture', catalog_revision: 'rev' });
    const args = view.calls[0], turnId = args[9];
    assert.equal(typeof args[16], 'function');
    args[16]({ version: 1, turn_id: turnId });
    view.acc.current.owned.content = 'partial result';
    if (outcome === 'done') args[4]('owned');
    if (outcome === 'error') args[5](new Error('fixture failure'));
    if (outcome === 'stop') await view.handleStopStream('owned');
    assert.equal(view.calls.length, 1);
    assert.equal(view.messages.length, 1);
    assert.equal(view.messages[0].message.centralRecoveryTurnId, turnId);
    assert.match(view.messages[0].message.content, /partial result/);
  });
}

test('custom or old-backend completion has no invented central association', () => {
  const view = setup(); view.startStream('owned', 'test'); view.calls[0][4]('owned');
  assert.equal(view.messages[0].message.centralRecoveryTurnId, undefined);
});

for (const outcome of ['done', 'error', 'stop', 'snapshot']) {
  test(`verified file survives ${outcome} without repairing protected model text`, async () => {
    const view = setup({ status: 'done', text: 'outputs/proof/tool-check.tx[已隐藏]', turn_id: 'resumed-123' });
    if (outcome === 'snapshot') view.resumePendingTurn('owned'); else view.startStream('owned', 'test');
    const args = view.calls[0], turn = args[9];
    assert.equal(typeof args[19], 'function');
    const value = { version: 1, session_id: 'owned', turn_id: turn, path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64) };
    args[19]({ ...value, turn_id: 'foreign' }); args[19]({ ...value, session_id: 'foreign' });
    args[19](value); args[19](value);
    assert.equal(view.acc.current.owned.verifiedArtifacts.length, 1);
    assert.equal(view.state.streams.owned.verifiedArtifacts[0].path, value.path);
    assert.equal(view.state.sessions[0].pendingVerifiedArtifacts[0].path, value.path);
    view.acc.current.owned.content = 'outputs/proof/tool-check.tx[已隐藏]';
    if (outcome === 'done') args[4]('owned');
    if (outcome === 'error') args[5](new Error('fixture'));
    if (outcome === 'stop') await view.handleStopStream('owned');
    if (outcome === 'snapshot') { args[11](); await Promise.resolve(); await Promise.resolve(); }
    assert.equal(view.messages.length, 1);
    assert.equal(view.messages[0].message.turnId, turn);
    assert.equal(view.messages[0].message.verifiedArtifacts.length, 1);
    assert.equal(view.messages[0].message.verifiedArtifacts[0].path, value.path);
    assert.match(view.messages[0].message.content, /tool-check.tx\[已隐藏\]/);
  });
}

test('refresh resume restores only persisted artifact references bound to pending turn', () => {
  const view = setup();
  const value = { version: 1, session_id: 'owned', turn_id: 'resumed-123', path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64) };
  view.state.sessions[0].pendingVerifiedArtifacts = [value, { ...value, turn_id: 'foreign' }];
  view.resumePendingTurn('owned'); view.calls[0][4]('owned');
  assert.equal(view.messages[0].message.verifiedArtifacts.length, 1);
  assert.equal(view.messages[0].message.turnId, 'resumed-123');
});

test('snapshot additive artifacts must belong to this exact session and turn', async () => {
  const value = { version: 1, session_id: 'owned', turn_id: 'resumed-123', path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64) };
  const view = setup({ status: 'done', text: 'protected', turn_id: 'resumed-123', artifacts: [value, { ...value, session_id: 'foreign' }] });
  view.resumePendingTurn('owned'); view.calls[0][11](); await Promise.resolve(); await Promise.resolve();
  assert.equal(view.messages[0].message.verifiedArtifacts.length, 1);
});

test('foreign-turn hint cannot attach another operation to the message', () => {
  const view = setup(); view.startStream('owned', 'test');
  assert.equal(typeof view.calls[0][16], 'function');
  view.calls[0][16]({ version: 1, turn_id: 'another-turn' }); view.calls[0][4]('owned');
  assert.equal(view.messages[0].message.centralRecoveryTurnId, undefined);
});

test('resumed event log preserves the exact original turn without new submission', () => {
  const view = setup(); view.resumePendingTurn('owned'); const args = view.calls[0];
  assert.equal(args[10], true); assert.equal(args[9], 'resumed-123');
  assert.equal(typeof args[16], 'function');
  args[16]({ version: 1, turn_id: 'resumed-123' }); args[4]('owned');
  assert.equal(view.messages[0].message.centralRecoveryTurnId, 'resumed-123');
  assert.equal(view.calls.length, 1);
});

test('hint-only stop retains the original turn even before any text or activity', async () => {
  const view = setup(); view.startStream('owned', 'test'); const args = view.calls[0];
  args[16]({ version: 1, turn_id: args[9] }); await view.handleStopStream('owned');
  assert.equal(view.messages.length, 1);
  assert.equal(view.messages[0].message.centralRecoveryTurnId, args[9]);
});

test('stop waits for a confirmed stopped response and preserves the original operation meanwhile', async () => {
  let resolve;
  const view = setup(undefined, () => new Promise(done => { resolve = done; }));
  view.startStream('owned', 'test'); view.acc.current.owned.content = 'partial';
  const pending = view.handleStopStream('owned');
  assert.equal(view.state.streams.owned.phase, 'stopping');
  assert.equal(view.messages.length, 0); assert.ok(view.acc.current.owned); assert.ok(view.ctl.current.owned);
  assert.equal(view.stops.length, 1); assert.equal(view.stops[0][1], view.calls[0][9]);
  resolve({ stopped: true, state: 'stopped' }); await pending;
  assert.equal(view.messages.length, 1); assert.equal(view.messages[0].message.taskState, 'stopped');
  assert.equal(view.state.streams.owned, undefined);
});

test('stopping and unknown responses never become a stopped terminal or clear an accepted task', async () => {
  for (const state of ['stopping', 'unknown']) {
    const view = setup(undefined, { stopped: false, state }); view.startStream('owned', 'test');
    await view.handleStopStream('owned');
    assert.equal(view.messages.length, 0); assert.ok(view.acc.current.owned); assert.ok(view.ctl.current.owned);
    assert.equal(view.state.streams.owned.phase, 'stopping'); assert.equal(view.state.sessions[0].pendingTurnId, view.calls[0][9]);
  }
});

test('a failed stop request retains partial output and exposes unknown state without resending', async () => {
  const view = setup(undefined, async () => { throw Error('network'); }); view.startStream('owned', 'test');
  view.acc.current.owned.content = 'partial'; await view.handleStopStream('owned');
  assert.equal(view.messages.length, 0); assert.equal(view.acc.current.owned.content, 'partial');
  assert.equal(view.state.streams.owned.phase, 'unknown'); assert.equal(view.calls.length, 1);
});

test('durable accepted callback clears only the matching submitted revision', () => {
  const view = setup(); view.draftStore.update('scope', 'owned', { text: 'first' });
  const draft = view.draftStore.prepareSubmission('scope', 'owned', 'turn-accepted');
  view.startStream('owned', 'first', undefined, [], undefined, { scopeKey: 'scope', turnId: 'turn-accepted', revision: draft.submission.submittedRevision });
  view.draftStore.update('scope', 'owned', { text: 'next draft' });
  const receipt = { version: 1, session_id: 'owned', turn_id: 'turn-accepted', request_hash: 'a'.repeat(64), receipt_id: 'b'.repeat(64) };
  view.calls[0][17](receipt);
  const current = view.draftStore.load('scope', 'owned');
  assert.equal(current.text, 'next draft'); assert.equal(current.submission, undefined);
  assert.equal(view.state.streams.owned.phase, 'running');
});

test('a verified pre-dispatch rejection releases only submission metadata, retaining text and attachments', () => {
  const view = setup(), attachment = { id: 'a', name: '材料.txt', path: '_inbox/owned/a.txt' };
  view.draftStore.update('scope', 'owned', { text: 'original draft', attachments: [attachment] });
  const pending = view.draftStore.prepareSubmission('scope', 'owned', 'rejected-turn');
  view.startStream('owned', 'original draft', undefined, [attachment], undefined,
    { scopeKey: 'scope', turnId: 'rejected-turn', revision: pending.submission.submittedRevision });
  const error = new Error('请选择模型');
  error.rejection = { version: 1, session_id: 'owned', turn_id: 'rejected-turn', request_hash: 'a'.repeat(64), not_dispatched: true };
  view.calls[0][5](error);
  const draft = view.draftStore.load('scope', 'owned');
  assert.equal(draft.text, 'original draft'); assert.equal(draft.attachments[0].path, attachment.path); assert.equal(draft.submission, undefined);
  assert.equal(view.messages[0].message.taskState, 'failed'); assert.equal(view.calls.length, 1);
});

test('an uncertain failure or foreign rejection cannot release an original draft submission', () => {
  for (const marker of [undefined, { version: 1, session_id: 'foreign', turn_id: 'uncertain-turn', not_dispatched: true },
    { version: 1, session_id: 'owned', turn_id: 'foreign', not_dispatched: true }]) {
    const view = setup(); view.draftStore.update('scope', 'owned', { text: 'retain uncertain' });
    const pending = view.draftStore.prepareSubmission('scope', 'owned', 'uncertain-turn');
    view.startStream('owned', 'retain uncertain', undefined, [], undefined,
      { scopeKey: 'scope', turnId: 'uncertain-turn', revision: pending.submission.submittedRevision });
    const error = new Error('connection lost'); if (marker) error.rejection = marker;
    view.calls[0][5](error);
    assert.equal(view.draftStore.load('scope', 'owned').submission.clientTurnId, 'uncertain-turn'); assert.equal(view.calls.length, 1);
  }
});

test('a stopped durable terminal after the initial stop response remains stopped in the saved message', async () => {
  const view = setup(undefined, { stopped: false, state: 'stopping' }); view.startStream('owned', 'test');
  view.acc.current.owned.content = 'partial result'; await view.handleStopStream('owned'); assert.equal(view.messages.length, 0);
  const args = view.calls[0]; args[4]('owned', { version: 1, session_id: 'owned', turn_id: args[9], state: 'stopped', clean_end: false, stop_reason: 'user_stopped' });
  assert.equal(view.messages.length, 1); assert.equal(view.messages[0].message.taskState, 'stopped'); assert.equal(view.messages[0].message.content, 'partial result');
});

test('old central snapshot without the new recovery marker never invents association', async () => {
  const view = setup({ status: 'done', text: 'old result', turn_id: 'resumed-123',
    route: { service_source: 'subscription', subscription_backend: 'central' } });
  view.resumePendingTurn('owned'); view.calls[0][11](); await Promise.resolve(); await Promise.resolve();
  assert.equal(view.messages[0].message.centralRecoveryTurnId, undefined);
});

test('snapshot fallback consumes only the new matching versioned recovery marker', async () => {
  for (const marker of [{ version: 1, turn_id: 'resumed-123' }, { version: 2, turn_id: 'resumed-123' }, { version: 1, turn_id: 'another-turn' }]) {
    const view = setup({ status: 'done', text: 'new result', turn_id: 'resumed-123',
      route: { service_source: 'subscription', subscription_backend: 'central' }, central_recovery: marker });
    view.resumePendingTurn('owned'); view.calls[0][11](); await Promise.resolve(); await Promise.resolve();
    assert.equal(view.messages[0].message.centralRecoveryTurnId, marker.version === 1 && marker.turn_id === 'resumed-123' ? 'resumed-123' : undefined);
  }
});
