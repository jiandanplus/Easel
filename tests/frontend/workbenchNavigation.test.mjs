import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load() {
  const url = new URL('../../web/frontend/src/lib/workbenchNavigation.ts', import.meta.url);
  assert.ok(existsSync(url), 'stable workbench navigation must exist'); const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, Set, URLSearchParams, encodeURIComponent, decodeURIComponent });
  return exports;
}
const plain = x => JSON.parse(JSON.stringify(x));
test('page and stable session round trip without content or absolute paths', () => {
  const lib = load(), location = { page: 'chat', sessionId: 'chat-1' };
  assert.deepEqual(plain(lib.parseWorkbenchHash(lib.hashForWorkbenchLocation(location))), location);
  assert.equal(lib.hashForWorkbenchLocation({ page: 'outputs', artifactId: 'artifact_123' }), '#/outputs?artifact=artifact_123');
});
test('legacy outputs link is decoded only into its separate compatibility field', () => {
  assert.deepEqual(plain(load().parseWorkbenchHash('#/outputs/outputs%2Fnotes%2F%E6%96%87%E6%A1%88.md')), { page: 'outputs', legacyOutputPath: 'notes/文案.md' });
  assert.deepEqual(plain(load().parseWorkbenchHash('#/outputs/notes%2F%E6%96%87%E6%A1%88.md')), { page: 'outputs', legacyOutputPath: 'notes/文案.md' });
});
test('unknown page, duplicates, malformed escapes and traversal are rejected', () => {
  for (const hash of ['#/secret', '#/chat?session=one&session=two', '#/outputs/%ZZ', '#/outputs/../secret', '#/outputs/%2Fetc%2Fpasswd', '#/chat?session=..%2Fprivate', '#/outputs?artifact=..%2Fsecret'])
    assert.equal(load().parseWorkbenchHash(hash), null, hash);
});
test('canonical routes strip unrelated payload and reject identifiers containing paths', () => {
  assert.equal(load().hashForWorkbenchLocation({ page: 'chat', sessionId: 'safe', token: 'secret', text: 'prompt', path: '/private' }), '#/chat?session=safe');
  assert.equal(load().hashForWorkbenchLocation({ page: 'chat', sessionId: '/private/secret' }), '#/chat');
  assert.equal(load().hashForWorkbenchLocation({ page: 'outputs', artifactId: 'https://secret' }), '#/outputs');
});
test('all existing navigation pages are supported', () => {
  for (const page of ['dashboard','chat','trends','ideas','calendar','publish','breakdown','skills','outputs','accounts','profile'])
    assert.deepEqual(plain(load().parseWorkbenchHash(`#/${page}`)), { page });
});
