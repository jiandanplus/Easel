import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const names = ['sendUserAndStream', 'handleResend'];
const declarations = app.body.statements.filter(node => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some(decl => names.includes(decl.name.getText(parsed))));
assert.equal(declarations.length, 2);
const code = ts.transpileModule(declarations.map(node => node.getText(parsed)).join('\n') + '\nhandleResend;', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const central = { service_source: 'subscription', subscription_backend: 'central' };

function setup(read, sourceScope = 'original-source') {
  const original = { id: 'owned', sourceScope, messages: [{ role: 'user', content: '你好' }, { role: 'assistant', content: 'original failure' }] };
  const state = { sessions: [original], notice: null, streams: [], reads: [] };
  const sessionsRef = { current: state.sessions };
  const currentDraftScopeRef = { current: 'original-scope' };
  const retry = runInNewContext(code, {
    useCallback: callback => callback, selectedPersona: '', sessionsRef,
    streamCtl: { current: {} }, resendChecks: { current: new Set() },
    currentDraftScopeRef,
    workbench: { version: 1, source_scope: 'original-source' },
    fetchLastTurn: async id => { state.reads.push(id); return await read(); },
    setResendNotice: value => { state.notice = typeof value === 'function' ? value(state.notice) : value; },
    setSessions: update => { state.sessions = update(state.sessions); sessionsRef.current = state.sessions; },
    saveSessions: () => {}, updateSessionTitle: () => {},
    startStream: (...args) => { state.streams.push(args); },
  });
  return { state, original, retry: () => retry('owned', 0, '你好'),
    replaceSessions(sessions) { state.sessions = sessions; sessionsRef.current = sessions; },
    changeSourceScope(scope) { currentDraftScopeRef.current = scope; } };
}

for (const clean_end of [false, undefined]) {
  test(`central uncertain retry is read-only and preserves all history (clean_end=${clean_end})`, async () => {
    const view = setup(async () => ({ status: 'done', route: central, clean_end }));
    await view.retry();
    assert.equal(view.state.streams.length, 0);
    assert.equal(view.state.sessions[0], view.original);
    assert.equal(view.state.sessions[0].messages.length, 2);
    assert.equal(view.state.reads.length, 1);
    assert.match(view.state.notice?.message || '', /账号与订阅.*中断请求状态/);
    assert.match(view.state.notice?.message || '', /新建对话|不要.*重发/);
  });
}

test('failed previous-turn lookup never starts a new request or truncates messages', async () => {
  const view = setup(async () => { throw Error('private-network-diagnostic'); });
  await view.retry();
  assert.equal(view.state.streams.length, 0);
  assert.equal(view.state.sessions[0], view.original);
  assert.match(view.state.notice?.message || '', /无法确认上一轮/);
  assert.doesNotMatch(view.state.notice?.message || '', /private-network/);
});

test('missing previous-turn metadata blocks regeneration without assuming zero cost', async () => {
  const view = setup(async () => ({ status: 'none', text: '' }));
  await view.retry();
  assert.equal(view.state.streams.length, 0);
  assert.equal(view.state.sessions[0], view.original);
  assert.match(view.state.notice?.message || '', /无法确认上一轮/);
});

test('missing null and invalid previous routes never truncate history or start a task', async () => {
  for (const route of [undefined, null, {}, [], 'invalid-route', { service_source: 'unknown' }]) {
    const view = setup(async () => ({ status: 'done', clean_end: false, route }));
    await view.retry();
    assert.equal(view.state.streams.length, 0);
    assert.equal(view.state.sessions[0], view.original);
    assert.equal(view.state.sessions[0].messages.length, 2);
    assert.match(view.state.notice?.message || '', /无法确认上一轮/);
  }
});

for (const subscription_backend of [undefined, 'unknown-backend']) {
  test(`subscription retry with missing or unknown backend stays read-only (${subscription_backend})`, async () => {
    const view = setup(async () => ({ status: 'done', clean_end: false,
      route: { service_source: 'subscription', subscription_backend } }));
    await view.retry();
    assert.equal(view.state.streams.length, 0);
    assert.equal(view.state.sessions[0], view.original);
    assert.equal(view.state.sessions[0].messages.length, 2);
    assert.match(view.state.notice?.message || '', /无法确认上一轮/);
  });
}

test('late retry lookup cannot replace messages added by a completed ordinary send', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  const pending = view.retry();
  const updated = { ...view.original, messages: [...view.original.messages,
    { role: 'user', content: 'new independent instruction' }, { role: 'assistant', content: 'new completed result' }] };
  view.replaceSessions([updated]);
  complete({ status: 'done', route: { service_source: 'custom' }, clean_end: true });
  await pending;
  assert.equal(view.state.streams.length, 0);
  assert.equal(view.state.sessions[0], updated);
  assert.equal(view.state.sessions[0].messages.length, 4);
});

test('late retry lookup cannot dispatch for a deleted session', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  const pending = view.retry();
  view.replaceSessions([]);
  complete({ status: 'done', route: central, clean_end: true });
  await pending;
  assert.equal(view.state.streams.length, 0);
  assert.equal(view.state.sessions.length, 0);
});

test('late retry lookup also ignores an updated session that retained its messages array', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  const pending = view.retry();
  const updated = { ...view.original, persona: 'new-persona' };
  view.replaceSessions([updated]);
  complete({ status: 'done', route: central, clean_end: true });
  await pending;
  assert.equal(view.state.streams.length, 0);
  assert.equal(view.state.sessions[0], updated);
  assert.equal(view.state.sessions[0].messages, view.original.messages);
});

test('account or channel changes during retry lookup preserve old history without a new dispatch', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  const pending = view.retry();
  view.changeSourceScope('other-account-scope');
  complete({ status: 'done', route: central, clean_end: true }); await pending;
  assert.equal(view.state.streams.length, 0); assert.equal(view.state.sessions[0], view.original);
  assert.equal(view.state.sessions[0].messages.length, 2);
});

test('custom retry still dispatches after the read and uses the existing resend path', async () => {
  const view = setup(async () => ({ status: 'done', route: { service_source: 'custom' }, clean_end: false }));
  await view.retry();
  assert.equal(view.state.reads.length, 1);
  assert.equal(view.state.streams.length, 1);
  assert.equal(view.state.sessions[0].messages.length, 3);
  assert.equal(view.state.sessions[0].messages[0].content, '你好');
  assert.equal(view.state.sessions[0].messages[1].content, 'original failure');
  assert.equal(view.state.sessions[0].messages[2].content, '你好');
});

test('explicit legacy subscription retains its existing regeneration path', async () => {
  const view = setup(async () => ({ status: 'done', clean_end: false,
    route: { service_source: 'subscription', subscription_backend: 'legacy' } }));
  await view.retry();
  assert.equal(view.state.reads.length, 1);
  assert.equal(view.state.streams.length, 1);
  assert.equal(view.state.sessions[0].messages.length, 3);
  assert.equal(view.state.sessions[0].messages[1].content, 'original failure');
});

test('confirmed central completion may regenerate as an explicitly new charged task', async () => {
  const view = setup(async () => ({ status: 'done', route: central, clean_end: true }));
  await view.retry();
  assert.equal(view.state.streams.length, 1);
  assert.equal(view.state.sessions[0].messages.length, 3);
  assert.equal(view.state.sessions[0].messages[1].content, 'original failure');
  assert.match(view.state.notice?.message || '', /新任务.*另.*计点/);
});

test('a retry from an already foreign-bound conversation is rejected before lookup or history changes', async () => {
  const view = setup(async () => ({ status: 'done', route: central, clean_end: true }), 'another-source');
  await view.retry();
  assert.equal(view.state.streams.length, 0); assert.equal(view.state.reads.length, 0);
  assert.equal(view.state.sessions[0], view.original); assert.equal(view.state.sessions[0].messages.length, 2);
});

test('double click while the read is pending never creates two new tasks', async () => {
  let complete;
  const view = setup(() => new Promise(resolve => { complete = resolve; }));
  const first = view.retry(); const second = view.retry();
  assert.equal(view.state.streams.length, 0); assert.equal(view.state.reads.length, 1);
  complete({ status: 'done', route: central, clean_end: true });
  await Promise.all([first, second]);
  assert.equal(view.state.streams.length, 1);
});

test('chat displays a blocking retry notice inline and removes regeneration actions', () => {
  const componentSource = readFileSync(new URL('../../web/frontend/src/components/ChatPage.tsx', import.meta.url), 'utf8');
  const componentCode = ts.transpileModule(componentSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exports = {};
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
    useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}], useRef: value => ({ current: value }), useEffect: () => {}, useCallback: value => value,
  };
  const modules = new Map();
  const load = name => {
    if (modules.has(name)) return modules.get(name);
    const result = {}; modules.set(name, result);
    runInNewContext(ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText, { exports: result, require: path => path === 'react' ? React : load('composerDrafts'), Map, Number, Array, Object, Date, JSON, Error, setTimeout, clearTimeout });
    return result;
  };
  const store = load('composerDrafts').createComposerDraftStore();
  runInNewContext(componentCode, { exports, React,
    require: name => name === 'react' ? React : name === './MessageBubble' ? { __esModule: true, default: 'MessageBubble' }
      : name.endsWith('/useComposerDraft') ? load('useComposerDraft') : {},
  });
  const tree = exports.default({ apiDirect: false,
    draftScopeKey: 'scope', draftStore: store,
    session: { id: 'owned', messages: [{ role: 'user', content: '你好' }, { role: 'assistant', content: 'old error' }] },
    retryNotice: { blocked: true, message: '请到账号与订阅查询中断请求状态。' },
    onResend: () => { throw Error('must not resend'); },
  });
  const flatten = value => !value || typeof value !== 'object' ? [] : [value, ...(value.children || []).flatMap(flatten)];
  const nodes = flatten(tree);
  const notice = nodes.find(node => node.props.role === 'status');
  assert.ok(notice); assert.match(notice.children.join(''), /账号与订阅/);
  const messages = nodes.filter(node => node.type === 'MessageBubble');
  assert.equal(messages.length, 2);
  assert.ok(messages.every(node => !node.props.actions?.onRetry));
});

test('a bound conversation rejects sends under another source before appending or dispatching', () => {
  const statement = app.body.statements.find(node => ts.isVariableStatement(node)
    && node.declarationList.declarations.some(decl => decl.name.getText(parsed) === 'handleSendMessage'));
  const code = ts.transpileModule(statement.getText(parsed) + '\nhandleSendMessage;', { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const dispatched = [], released = [], notices = [];
  const send = runInNewContext(code, { useCallback: value => value,
    currentDraftScopeRef: { current: 'new-scope' }, workbenchChecking: false, workbenchError: false, workbench: { version: 1, source_scope: 'new-account' },
    sessionsRef: { current: [{ id: 'owned', sourceScope: 'original-account', messages: [{ role: 'user', content: 'original' }] }] },
    draftStore: { abandonSubmission: (...args) => released.push(args) }, setDraftRefresh: () => {},
    setResendNotice: value => notices.push(value), sendUserAndStream: (...args) => dispatched.push(args),
  });
  send('owned', 'new draft', [], undefined, { scopeKey: 'new-scope', turnId: 'new-turn', revision: 1 });
  assert.equal(dispatched.length, 0); assert.deepEqual(released[0], ['new-scope', 'owned', 'new-turn']);
  assert.equal(notices.at(-1).blocked, true); assert.match(notices.at(-1).message, /账号或渠道已变化/);
});
