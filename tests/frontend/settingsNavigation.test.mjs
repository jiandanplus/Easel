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
const names = ['openSettings', 'closeSettings', 'navigateToPage'];
const handlers = app.body.statements.filter(node => ts.isVariableStatement(node) &&
  node.declarationList.declarations.some(decl => names.includes(decl.name.getText(parsed))));
const code = ts.transpileModule(handlers.map(node => node.getText(parsed)).join('\n') +
  '\n({ openSettings, closeSettings, navigateToPage });', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(open = true, fail = false) {
  const state = { settingsOpen: open, needModelSetup: true, sectionRequest: 2, page: 'chat' };
  let fetches = 0;
  const context = { settingsOpen: open, fetchStatus: async () => {
    fetches++;
    if (fail) throw Error('offline');
    return { transport: 'api', runtime: 'codex', gateway: true };
  } };
  for (const [setter, key] of Object.entries({ setSettingsOpen: 'settingsOpen', setNeedModelSetup: 'needModelSetup',
    setSettingsSection: 'section', setSettingsSectionRequest: 'sectionRequest', setCurrentPage: 'page',
    setApiDirect: 'apiDirect', setAgentRuntime: 'runtime', setGatewayStatus: 'gateway' })) {
    context[setter] = value => { state[key] = typeof value === 'function' ? value(state[key]) : value; };
  }
  return { state, api: runInNewContext(code, context), fetches: () => fetches };
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
});
test('page navigation from settings performs the same refresh', async () => {
  const view = setup();
  view.api.navigateToPage('chat');
  await settle();
  assert.equal(view.fetches(), 1);
  assert.equal(view.state.needModelSetup, false);
  assert.equal(view.state.page, 'chat');
});
test('normal page navigation does not fetch settings runtime', () => {
  const view = setup(false);
  view.api.navigateToPage('outputs');
  assert.equal(view.fetches(), 0);
  assert.equal(view.state.page, 'outputs');
});
test('failed exit refresh leaves settings closed and reports disconnected', async () => {
  const view = setup(true, true);
  view.api.closeSettings();
  await settle();
  assert.equal(view.state.settingsOpen, false);
  assert.equal(view.state.needModelSetup, false);
  assert.equal(view.state.gateway, 'disconnected');
});
test('repeated appearance and model requests each issue fresh section navigation', () => {
  const view = setup();
  for (const section of ['theme', 'theme', 'model', 'model']) {
    const before = view.state.sectionRequest;
    view.api.openSettings(section);
    assert.equal(view.state.section, section);
    assert.equal(view.state.sectionRequest, before + 1);
    assert.equal(view.state.settingsOpen, true);
  }
  assert.equal(view.fetches(), 0);
});
