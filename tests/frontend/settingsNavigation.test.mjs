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
const names = ['applyStatus', 'refreshWorkbench', 'openSettings', 'closeSettings', 'navigateToPage'];
const handlers = app.body.statements.filter(node => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some(decl => names.includes(decl.name.getText(parsed))));
const code = ts.transpileModule(handlers.map(node => node.getText(parsed)).join('\n') +
  '\n({ openSettings, closeSettings, navigateToPage });', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

const bootstrapEffect = app.body.statements.find(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
  && node.expression.expression.getText(parsed) === 'useEffect' && node.expression.arguments[0]?.getText(parsed).includes('const bootstrap = fetch'));
const bootstrapCode = ts.transpileModule(`(${bootstrapEffect.expression.arguments[0].getText(parsed)});`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function setup(open = true, fail = false, read, initial = {}) {
  const state = { settingsOpen: open, historyOpen: true, recommend: false, needModelSetup: initial.needModelSetup ?? true, sectionRequest: 2, page: 'chat', runtimeDetail: 'stale runtime detail', checking: false, error: false };
  let fetches = 0;
  const context = { settingsOpen: open, useCallback: value => value, statusSequence: { current: 0 }, initialDraftScope: 'pending', currentDraftScopeRef: { current: 'pending' },
    dashboardSession: { id: 'dashboard', messages: [] }, draftStore: {}, loadSessions: () => [], applyStatusRef: { current: null },
    fetchStatus: async () => {
    fetches++;
    if (fail) throw Error('offline');
    if (read) return await read();
    return { transport: 'api', runtime: 'codex', gateway: true, runtime_detail: '当前执行引擎状态已刷新' };
  } };
  for (const [setter, key] of Object.entries({ setSettingsOpen: 'settingsOpen', setHistoryOpen: 'historyOpen', setNeedModelSetup: 'needModelSetup',
    setSettingsSection: 'section', setSettingsSectionRequest: 'sectionRequest', setCurrentPage: 'page',
    setApiDirect: 'apiDirect', setAgentRuntime: 'runtime', setGatewayStatus: 'gateway', setRuntimeDetail: 'runtimeDetail',
    setShowRecommend: 'recommend', setPersonas: 'personas', setWorkbench: 'workbench', setWorkbenchChecking: 'checking', setWorkbenchError: 'error' })) {
    context[setter] = value => { state[key] = typeof value === 'function' ? value(state[key]) : value; };
  }
  const api = runInNewContext(code.replace('({ openSettings, closeSettings, navigateToPage })', '({ openSettings, closeSettings, navigateToPage, applyStatus })'), context);
  context.applyStatusRef.current = api.applyStatus;
  context.fetch = async () => ({ ok: true, json: async () => initial.bootstrap ? await initial.bootstrap() : (initial.boot === undefined ? { modelConfigured: true } : initial.boot) });
  context.onboardingSeen = () => initial.seen ?? true;
  return { state, api, fetches: () => fetches, startInitialStatus: () => runInNewContext(bootstrapCode, context)() };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('closing settings refreshes runtime and clears first-model banner', async () => {
  const view = setup();
  view.api.closeSettings();
  await settle();
  assert.equal(view.fetches(), 1);
  assert.equal(view.state.settingsOpen, false);
  assert.equal(view.state.needModelSetup, false);
  assert.equal(view.state.apiDirect, true);
  assert.equal(view.state.runtime, 'codex');
  assert.equal(view.state.gateway, 'connected');
  assert.equal(view.state.runtimeDetail, '当前执行引擎状态已刷新');
});
test('page navigation from settings performs the same refresh', async () => {
  const view = setup();
  view.api.navigateToPage('chat');
  await settle();
  assert.equal(view.fetches(), 1);
  assert.equal(view.state.needModelSetup, false);
  assert.equal(view.state.page, 'chat');
  assert.equal(view.state.historyOpen, false);
  assert.equal(view.state.runtimeDetail, '当前执行引擎状态已刷新');
});
test('normal page navigation does not fetch settings runtime', () => {
  const view = setup(false);
  view.api.navigateToPage('outputs');
  assert.equal(view.fetches(), 0);
  assert.equal(view.state.page, 'outputs');
  assert.equal(view.state.historyOpen, false);
  assert.equal(view.state.runtimeDetail, 'stale runtime detail');
});
test('failed exit refresh leaves settings closed and reports disconnected', async () => {
  const view = setup(true, true);
  view.api.closeSettings();
  await settle();
  assert.equal(view.state.settingsOpen, false);
  assert.equal(view.state.needModelSetup, false);
  assert.equal(view.state.gateway, 'disconnected');
  assert.equal(view.state.error, true);
  assert.equal(view.state.checking, false);
});
test('account entry and repeated settings requests each issue fresh section navigation', () => {
  const view = setup();
  for (const section of ['account', 'theme', 'account', 'account', 'model', 'model']) {
    const before = view.state.sectionRequest;
    view.state.historyOpen = true;
    view.api.openSettings(section);
    assert.equal(view.state.section, section);
    assert.equal(view.state.sectionRequest, before + 1);
    assert.equal(view.state.settingsOpen, true);
    assert.equal(view.state.historyOpen, false);
  }
  assert.equal(view.fetches(), 0);
});
test('a late initial status cannot replace the newer account refresh', async () => {
  const pending=[];
  const view=setup(true,false,()=>new Promise(resolve=>pending.push(resolve)),{boot:{modelConfigured:false},seen:false});
  view.startInitialStatus();assert.equal(pending.length,1);
  view.api.closeSettings();assert.equal(pending.length,2);
  pending[1]({transport:'api',runtime:'pi',gateway:true,runtime_detail:'new account'});await settle();
  assert.equal(view.state.runtimeDetail,'new account');
  pending[0]({transport:'api',runtime:'codex',gateway:false,runtime_detail:'stale account',personas:[]});await settle();
  assert.equal(view.state.runtimeDetail,'new account');assert.equal(view.state.runtime,'pi');assert.equal(view.state.gateway,'connected');
  assert.equal(view.state.settingsOpen,false);assert.equal(view.state.needModelSetup,false);assert.equal(view.state.recommend,false);
});

for (const runtime_detail of ['请先登录中央账号', '刷新状态待确认', '目录暂不可用']) {
  test(`configured unavailable subscription keeps the current page without onboarding: ${runtime_detail}`, async () => {
    const view = setup(false, false, async () => ({ gateway: false, runtime: 'pi', runtime_detail, personas: [] }),
      { boot: { modelConfigured: true }, seen: false, needModelSetup: false });
    view.startInitialStatus(); await settle();
    assert.equal(view.state.settingsOpen, false); assert.equal(view.state.needModelSetup, false);
    assert.equal(view.state.recommend, false); assert.equal(view.state.page, 'chat');
    assert.equal(view.state.gateway, 'disconnected'); assert.equal(view.state.runtimeDetail, runtime_detail);
  });
}

test('missing custom provider still opens model setup before persona onboarding', async () => {
  const view = setup(false, false, async () => ({ gateway: false, runtime: 'pi', personas: [] }),
    { boot: { modelConfigured: false }, seen: false, needModelSetup: false });
  view.startInitialStatus(); await settle();
  assert.equal(view.state.settingsOpen, true); assert.equal(view.state.needModelSetup, true);
  assert.equal(view.state.recommend, false);
});

for (const [gateway, seen, personas, expected] of [[true, false, [], true], [true, true, [], false], [true, false, [{ id: 'saved' }], false], [false, false, [], false]]) {
  test(`persona onboarding follows settled connection, seen state and profiles: ${JSON.stringify([gateway, seen, personas])}`, async () => {
    const view = setup(false, false, async () => ({ gateway, personas }),
      { boot: { modelConfigured: true }, seen, needModelSetup: false });
    view.startInitialStatus(); await settle();
    assert.equal(view.state.recommend, expected); assert.equal(view.state.settingsOpen, false);
  });
}

for (const outcome of ['null', 'failure']) {
  test(`unavailable status with ${outcome} bootstrap does not force setup or persona onboarding`, async () => {
    const view = setup(false, false, async () => ({ gateway: false, personas: [] }),
      { seen: false, needModelSetup: false, bootstrap: async () => { if (outcome === 'failure') throw Error('offline'); return null; } });
    view.startInitialStatus(); await settle();
    assert.equal(view.state.settingsOpen, false); assert.equal(view.state.needModelSetup, false);
    assert.equal(view.state.recommend, false);
  });
}

for (const first of ['status', 'bootstrap']) {
  test(`unavailable subscription waits for both responses when ${first} resolves first`, async () => {
    let resolveStatus, resolveBootstrap;
    const view = setup(false, false, () => new Promise(resolve => { resolveStatus = resolve; }),
      { seen: false, needModelSetup: false, bootstrap: () => new Promise(resolve => { resolveBootstrap = resolve; }) });
    view.startInitialStatus(); await settle();
    const responses = { status: () => resolveStatus({ gateway: false, personas: [] }), bootstrap: () => resolveBootstrap({ modelConfigured: true }) };
    responses[first](); await settle();
    assert.equal(view.state.settingsOpen, false); assert.equal(view.state.recommend, false);
    responses[first === 'status' ? 'bootstrap' : 'status'](); await settle();
    assert.equal(view.state.settingsOpen, false); assert.equal(view.state.recommend, false);
    assert.equal(view.state.gateway, 'disconnected');
  });
}
