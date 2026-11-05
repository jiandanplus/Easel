import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { requireDeliverables } from './deliverablesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const declaration = app.body.statements.find(node => ts.isVariableStatement(node)
  && node.declarationList.declarations.some(value => value.name.getText(ast) === 'queryOriginalTurn'));
assert.ok(declaration, 'exercise the actual App callback rather than a mirrored implementation');
const callbackCode = ts.transpileModule(declaration.getText(ast) + '\nqueryOriginalTurn;', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const plain = value => JSON.parse(JSON.stringify(value));
function load(name, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  runInNewContext(code, { exports, require: requireDeliverables, Map, Set, Object, Number, Date, JSON, Error, AbortController,
    window: { location: { pathname: '/' } }, ...globals });
  return exports;
}
const sessionId = 'synthetic-session';
const turnId = 'synthetic-pending-turn';
const hash = 'a'.repeat(64);
const attachment = { id: 'f'.repeat(64), name: '合成附件.md', path: 'synthetic/attachment.md', available: true };
function setup({ state = 'accepted', responseHash = hash, localHash = hash, newer = false, deferred = false,
  pending = true, queriedTurn = turnId } = {}) {
  const lib = load('composerDrafts'), values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const store = lib.createComposerDraftStore(storage, () => 1000);
  const scope = lib.draftScopeKey('b'.repeat(64), 'c'.repeat(64));
  store.update(scope, sessionId, { text: '保留原合成正文', attachments: [attachment] });
  if (pending) {
    store.prepareSubmission(scope, sessionId, turnId);
    if (localHash) store.bindSubmission(scope, sessionId, turnId, localHash);
  }
  if (newer) store.update(scope, sessionId, { text: '保留尚未提交的下一版', attachments: [{ ...attachment, name: '下一版附件.md' }] });
  const before = plain(store.load(scope, sessionId));
  const calls = { fetch: [], checked: 0, acknowledge: [], append: [], resume: [], refresh: [], notices: [] };
  let finish;
  const wait = deferred ? new Promise(resolve => { finish = resolve; }) : undefined;
  const response = { version: 1, session_id: sessionId, turn_id: queriedTurn, state,
    accepted: state !== 'rejected', request_hash: responseHash, receipt_id: 'd'.repeat(64),
    ...(state === 'rejected' ? { not_dispatched: true } : {}),
    ...(state === 'completed' ? { text: '原任务的合成结果', clean_end: true } : {}) };
  const api = load('api', { fetch: async (url, options = {}) => {
    calls.fetch.push({ url, method: options.method || 'GET' });
    assert.equal(url, `/api/chat/turns/${sessionId}/${queriedTurn}`);
    assert.equal(options.method || 'GET', 'GET', 'query must not dispatch a POST');
    if (wait) await wait;
    return { ok: true, json: async () => plain(response) };
  } });
  const acknowledge = store.acknowledge;
  store.acknowledge = (...args) => { calls.acknowledge.push(args); return acknowledge(...args); };
  const scopeRef = { current: scope };
  const query = runInNewContext(callbackCode, {
    useCallback: fn => fn, currentDraftScopeRef: scopeRef, draftStore: store,
    fetchChatTurn: async (...args) => { const value = await api.fetchChatTurn(...args); calls.checked++; return value; },
    setResendNotice: value => calls.notices.push(plain(value)),
    setDraftRefresh: fn => calls.refresh.push(plain(fn({}))),
    sessionsRef: { current: [{ id: sessionId, pendingTurnId: queriedTurn, messages: [{ role: 'user', content: '合成历史' }] }] },
    streamCtl: { current: {} }, appendAssistant: (...args) => calls.append.push(plain(args)),
    resumePendingTurn: async id => { calls.resume.push(id); },
  });
  return { before, calls, scopeRef, finish, query: () => query(sessionId, queriedTurn),
    snapshot: () => plain(store.load(scope, sessionId)),
    persisted: () => plain(lib.createComposerDraftStore(storage).load(scope, sessionId)) };
}
function assertUnconfirmed(view, { checked = 1 } = {}) {
  assert.deepEqual(view.snapshot(), view.before, 'retain text, attachments, revision, original turn/hash and confirming metadata');
  assert.deepEqual(view.persisted(), view.before, 'a fresh real store must retain the same persisted snapshot');
  assert.equal(view.calls.checked, checked);
  assert.equal(view.calls.acknowledge.length, 0);
  assert.equal(view.calls.append.length, 0, 'do not append a false completion');
  assert.equal(view.calls.resume.length, 0, 'do not resume a different content fingerprint');
  assert.equal(view.calls.refresh.length, 0);
  assert.deepEqual(view.calls.notices.at(-1), { sessionId, blocked: true, message: '请求状态待确认' });
}

for (const state of ['accepted', 'completed']) test(`matching ${state} acknowledges the original submitted revision`, async () => {
  const view = setup({ state }); await view.query();
  assert.equal(view.snapshot().text, ''); assert.equal(view.snapshot().attachments.length, 0);
  assert.equal(view.snapshot().submission, undefined); assert.equal(view.calls.checked, 1);
  assert.equal(view.calls.acknowledge.length, 1);
  assert.equal(view.calls.append.length, state === 'completed' ? 1 : 0);
  assert.equal(view.calls.resume.length, state === 'accepted' ? 1 : 0);
});
test('matching completed receipt retains a newer revision while releasing the confirmed submission', async () => {
  const view = setup({ state: 'completed', newer: true }); await view.query();
  assert.equal(view.snapshot().text, view.before.text); assert.deepEqual(view.snapshot().attachments, view.before.attachments);
  assert.equal(view.snapshot().revision, view.before.revision); assert.equal(view.snapshot().submission, undefined);
  assert.equal(view.calls.append.length, 1);
});
for (const state of ['accepted', 'completed']) for (const newer of [false, true])
  test(`different request_hash in ${state} preserves ${newer ? 'newer revision and original confirming binding' : 'the original pending snapshot'}`, async () => {
    const view = setup({ state, newer, responseHash: 'e'.repeat(64) }); await view.query(); assertUnconfirmed(view);
  });
test('a local confirming submission without requestHash is not acknowledged by an accepted response', async () => {
  const view = setup({ localHash: null }); await view.query(); assertUnconfirmed(view);
});
for (const responseHash of [null, 'invalid-hash']) test(`actual API rejects ${responseHash === null ? 'missing' : 'malformed'} receipt hash before acknowledgement`, async () => {
  const view = setup({ responseHash }); await view.query(); assertUnconfirmed(view, { checked: 0 });
});
test('a matching durable rejection releases only submission metadata and preserves the unsent draft', async () => {
  const view = setup({ state: 'rejected' }); await view.query();
  const after = view.snapshot(); assert.equal(after.text, view.before.text); assert.deepEqual(after.attachments, view.before.attachments);
  assert.equal(after.revision, view.before.revision); assert.equal(after.submission, undefined);
  assert.equal(view.calls.acknowledge.length, 0); assert.equal(view.calls.resume.length, 0);
  assert.deepEqual(view.calls.notices.at(-1), { sessionId, blocked: false, message: '未提交' });
});
test('a rejection with another request hash leaves the original confirmation pending', async () => {
  const view = setup({ state: 'rejected', responseHash: 'e'.repeat(64) }); await view.query(); assertUnconfirmed(view);
});
test('a late matching completion after the source scope changes does not mutate the old draft', async () => {
  const view = setup({ state: 'completed', deferred: true }); const result = view.query();
  view.scopeRef.current = JSON.stringify(['b'.repeat(64), '9'.repeat(64)]); view.finish(); await result;
  assert.deepEqual(view.snapshot(), view.before); assert.deepEqual(view.persisted(), view.before);
  assert.equal(view.calls.checked, 1); assert.equal(view.calls.acknowledge.length, 0);
  assert.equal(view.calls.append.length, 0); assert.equal(view.calls.resume.length, 0);
});
test('querying another original turn never clears the current pending submission', async () => {
  const view = setup({ state: 'completed', queriedTurn: 'synthetic-other-original', responseHash: 'e'.repeat(64) });
  await view.query(); assert.deepEqual(view.snapshot(), view.before); assert.deepEqual(view.persisted(), view.before);
  assert.equal(view.calls.acknowledge.length, 0); assert.equal(view.calls.append.length, 1);
});
test('a historical query without a local submission still presents the validated completed result', async () => {
  const view = setup({ state: 'completed', pending: false }); await view.query();
  assert.deepEqual(view.snapshot(), view.before); assert.equal(view.calls.acknowledge.length, 0);
  assert.equal(view.calls.append.length, 1); assert.equal(view.calls.resume.length, 0);
  assert.deepEqual(view.calls.notices.at(-1), { sessionId, blocked: false, message: '已完成' });
});
