import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(name, imports = {}, globals = {}) {
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, require: id => imports[id], window: { location: { pathname: '/' } }, ...globals });
  return exports;
}
const helpers = load('deliverables');
const artifact = (changes = {}) => ({ version: 1, session_id: 'owned', turn_id: 'turn-1',
  path: 'outputs/proof/tool-check.txt', name: 'tool-check.txt', size: 25, sha256: 'a'.repeat(64), ...changes });
function chunkedResponse(text) {
  return new Response(new ReadableStream({ start(controller) {
    const bytes = new TextEncoder().encode(text);
    for (let offset = 0; offset < bytes.length; offset += 7) controller.enqueue(bytes.slice(offset, offset + 7));
    controller.close();
  } }));
}

test('verified artifact uses exact public host path despite .txt tail redaction', () => {
  assert.equal(typeof helpers.verifiedArtifact, 'function');
  const value = helpers.verifiedArtifact(artifact(), 'owned', 'turn-1');
  assert.equal(value.path, 'outputs/proof/tool-check.txt');
  const reference = helpers.verifiedArtifactReference(value, 'owned', 'turn-1');
  assert.equal(reference.path, 'proof/tool-check.txt');
  assert.equal(helpers.deliverableDownloadUrl(reference), '/api/media/proof/tool-check.txt');
  const { linkifyOutputs } = load('linkifyOutputs', { './api': { mediaUrl: p => '/api/media/' + p }, './deliverables': helpers });
  for (const body of ['outputs/proof/tool-check.tx[已隐藏]', '`outputs/proof/tool-check.tx[已隐藏]`',
    '[下载](outputs/proof/tool-check.tx[已隐藏])', '[下载](/api/media/proof/tool-check.tx[已隐藏])']) {
    assert.doesNotMatch(linkifyOutputs(body), /\]\((?:\/api\/media|#\/outputs|outputs)\//);
  }
  assert.match(linkifyOutputs('outputs/proof/tool-check.txt'), /\/api\/media\/proof\/tool-check.txt/);
});

test('strict host artifact schema rejects private, malformed, foreign and oversized references', () => {
  assert.equal(typeof helpers.verifiedArtifact, 'function');
  for (const patch of [{ version: 2 }, { session_id: 'foreign' }, { turn_id: 'foreign' }, { path: 'proof/a.txt' },
    { path: 'outputs/_private/a.txt' }, { path: 'outputs/a/../tool-check.txt' }, { path: 'outputs/a\\tool-check.txt' },
    { path: 'outputs/a\u0000/tool-check.txt' }, { path: 'outputs/a/tool-check.tx[已隐藏]' },
    { name: 'wrong.txt' }, { size: -1 }, { size: 262145 }, { size: 0.5 }, { size: true },
    { sha256: 'A'.repeat(64) }, { extra: true }, { path: 'outputs/outputs/../tool-check.txt' }])
    assert.equal(helpers.verifiedArtifact(artifact(patch), 'owned', 'turn-1'), null, JSON.stringify(patch));
  assert.equal(helpers.verifiedArtifact(JSON.stringify(artifact()), 'owned', 'turn-1'), null);
  assert.ok(helpers.verifiedArtifact(artifact({ size: 0 }), 'owned', 'turn-1'));
  assert.ok(helpers.verifiedArtifact(artifact({ size: 262144 }), 'owned', 'turn-1'));
});

test('streaming redacted Markdown links are disabled without linkifying incomplete normal paths', () => {
  const links = load('linkifyOutputs', { './api': { mediaUrl: p => '/api/media/' + p } });
  assert.equal(typeof links.disableRedactedOutputLinks, 'function');
  const input = '[下载](/api/media/proof/tool-check.tx[已隐藏]) outputs/unfinished.t';
  const protectedText = links.disableRedactedOutputLinks(input);
  assert.doesNotMatch(protectedText, /\]\(\/api\/media\//);
  assert.ok(protectedText.endsWith('outputs/unfinished.t'));
});

test('replay deduplicates and updates same path, retaining only last 16 safe references', () => {
  assert.equal(typeof helpers.mergeVerifiedArtifacts, 'function');
  let values = helpers.mergeVerifiedArtifacts([], [artifact(), artifact()], 'owned', 'turn-1');
  assert.equal(values.length, 1);
  values = helpers.mergeVerifiedArtifacts(values, [artifact({ size: 26, sha256: 'b'.repeat(64) })], 'owned', 'turn-1');
  assert.equal(values.length, 1); assert.equal(values[0].size, 26);
  values = helpers.mergeVerifiedArtifacts(values, Array.from({ length: 20 }, (_, i) => artifact({ path: `outputs/${i}.txt`, name: `${i}.txt` })), 'owned', 'turn-1');
  assert.equal(values.length, 16); assert.equal(values[0].name, '4.txt');
});

test('sessions refresh preserves exact same-turn artifacts and filters corrupt or foreign persisted references', () => {
  const values = new Map(); const localStorage = { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) };
  const store = load('store', { './deliverables': helpers }, { localStorage });
  store.saveSessions([{ id: 'owned', pendingTurnId: 'turn-1', pendingVerifiedArtifacts: [artifact()],
    messages: [{ role: 'assistant', content: 'outputs/proof/tool-check.tx[已隐藏]', turnId: 'turn-1', verifiedArtifacts: [artifact(), artifact({ turn_id: 'foreign' })] }] }]);
  const loaded = store.loadSessions()[0];
  assert.equal(loaded.messages[0].content, 'outputs/proof/tool-check.tx[已隐藏]');
  assert.equal(loaded.messages[0].verifiedArtifacts.length, 1);
  assert.equal(loaded.pendingVerifiedArtifacts.length, 1);
});

test('chunked live/replay SSE only dispatches valid artifact events at appended callback index19', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('api.ts', source, ts.ScriptTarget.Latest, true);
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'streamChat');
  for (const resume of [false, true]) {
    const seen = [];
    let finish; const done = new Promise(resolve => { finish = resolve; });
    const streamChat = runInNewContext(ts.transpileModule(declaration.getText(parsed).replace('export ', '') + '\nstreamChat;',
      { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, {
      ...helpers, AbortController, TextDecoder, Date, Error, setTimeout, BASE: '',
      fetch: async () => chunkedResponse('event: artifact\ndata: ' + JSON.stringify(artifact()) + '\n\n' +
        'event: artifact\ndata: ' + JSON.stringify(artifact()) + '\n\n' +
        'event: artifact\ndata: ' + JSON.stringify(artifact({ session_id: 'foreign' })) + '\n\n' +
        'event: artifact\ndata: ' + JSON.stringify(JSON.stringify(artifact())) + '\n\n' +
        'event: token\ndata: "outputs/proof/tool-check.tx[已隐藏]"\n\nevent: done\ndata: {}\n\n'),
    });
    streamChat('', undefined, 'owned', () => {}, finish, e => { throw e; }, undefined, undefined, undefined,
      'turn-1', resume, undefined, [], undefined, undefined, undefined, undefined, undefined, undefined, value => seen.push(value));
    await done; assert.equal(seen.length, 1); assert.equal(seen[0].path, artifact().path);
  }
});

test('message bubble renders verified card separately from protected original text', () => {
  const exports = {}, jsx = (type, props) => ({ type, props: props || {} });
  const source = readFileSync(new URL('../../web/frontend/src/components/MessageBubble.tsx', import.meta.url), 'utf8');
  runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText,
    { exports, DOMParser: class { parseFromString() { return { querySelectorAll: () => [] }; } },
      require: id => id === 'react' ? { useMemo: fn => fn(), useState: v => [v, () => {}] } : id === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
      : id.endsWith('/deliverables') ? helpers : id.endsWith('/linkifyOutputs') ? { linkifyOutputs: x => x, externalizeMediaLinks: x => x }
      : id.endsWith('/sanitize') ? { renderMarkdown: x => x } : id.endsWith('/artifacts') ? { extractArtifacts: html => ({ html, artifacts: [], archives: [] }) }
      : id.endsWith('/DeliverableActions') ? { __esModule: true, default: 'actions' } : {} });
  const content = 'outputs/proof/tool-check.tx[已隐藏]';
  const tree = exports.default({ message: { role: 'assistant', content, turnId: 'turn-1', verifiedArtifacts: [artifact()] }, sessionId: 'owned' });
  const nodes = value => !value || typeof value !== 'object' ? [] : [value, ...[value.props?.children].flat(Infinity).flatMap(nodes)];
  assert.equal(nodes(tree).find(n => n.type === 'actions')?.props.reference.path, 'proof/tool-check.txt');
  assert.equal(nodes(tree).find(n => n.props?.dangerouslySetInnerHTML)?.props.dangerouslySetInnerHTML.__html, content);
});
