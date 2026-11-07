import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load() {
  const url = new URL('../../web/frontend/src/lib/composerDrafts.ts', import.meta.url);
  assert.ok(existsSync(url), 'scoped revisioned drafts must exist');
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, Object, Number, Date, Map, Set, JSON, encodeURIComponent });
  return exports;
}
function storage() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }; }
const plain = x => JSON.parse(JSON.stringify(x));
const attachment = () => ({ id: 'file1', name: '活动.md', path: 'outputs/inbox/one.md', available: true });
test('a sent request fingerprint persists without clearing a newer draft', () => {
  const lib = load(), disk = storage(), repo = lib.createComposerDraftStore(disk);
  repo.update('scope', 'chat', { text: 'original' }); repo.prepareSubmission('scope', 'chat', 'turn-1');
  repo.update('scope', 'chat', { text: 'next revision' });
  assert.equal(typeof repo.bindSubmission, 'function');
  const bound = repo.bindSubmission('scope', 'chat', 'turn-1', 'a'.repeat(64));
  assert.equal(bound.text, 'next revision'); assert.equal(bound.revision, 2);
  assert.equal(lib.createComposerDraftStore(disk).load('scope', 'chat').submission.requestHash, 'a'.repeat(64));
  repo.bindSubmission('scope', 'chat', 'another-turn', 'b'.repeat(64));
  assert.equal(repo.load('scope', 'chat').submission.requestHash, 'a'.repeat(64));
});
test('draft text and structured attachments survive a new store instance', () => {
  const lib = load(), disk = storage(), scope = lib.draftScopeKey('workspace', 'account');
  const first = lib.createComposerDraftStore(disk, () => 100);
  const d = first.update(scope, 'chat1', { text: '时间、地点、长度', attachments: [attachment()] });
  assert.equal(d.persisted, true); assert.equal(d.revision, 1);
  const restored = lib.createComposerDraftStore(disk).load(scope, 'chat1');
  assert.equal(restored.text, '时间、地点、长度'); assert.deepEqual(plain(restored.attachments), [attachment()]);
});
test('workspace, authority and session boundaries do not share drafts', () => {
  const lib = load(), disk = storage(), repo = lib.createComposerDraftStore(disk);
  const one = lib.draftScopeKey('workspace1', 'account1');
  repo.update(one, 'same', { text: 'private draft' });
  for (const [scope, session] of [[lib.draftScopeKey('workspace2', 'account1'), 'same'], [lib.draftScopeKey('workspace1', 'account2'), 'same'], [one, 'other']])
    assert.equal(repo.load(scope, session).text, '');
  assert.equal(repo.load(one, 'same').text, 'private draft');
});
test('late acceptance for revision r never erases revision r+1', () => {
  const repo = load().createComposerDraftStore(storage(), () => 100);
  repo.update('scope', 'chat', { text: 'r', attachments: [attachment()] });
  const sent = repo.prepareSubmission('scope', 'chat', 'turn1');
  repo.update('scope', 'chat', { text: 'r+1', attachments: [] });
  const result = repo.acknowledge('scope', 'chat', 'turn1', sent.revision);
  assert.equal(result.text, 'r+1'); assert.equal(result.revision, sent.revision + 1); assert.equal(result.submission, undefined);
});
test('exact accepted snapshot clears text and refs, a foreign receipt never does', () => {
  const repo = load().createComposerDraftStore(storage());
  repo.update('scope', 'chat', { text: 'keep', attachments: [attachment()] });
  const sent = repo.prepareSubmission('scope', 'chat', 'turn1');
  assert.equal(repo.acknowledge('scope', 'chat', 'turn-other', sent.revision).text, 'keep');
  assert.equal(repo.acknowledge('scope', 'chat', 'turn1', sent.revision + 1).text, 'keep');
  const accepted = repo.acknowledge('scope', 'chat', 'turn1', sent.revision);
  assert.equal(accepted.text, ''); assert.equal(accepted.attachments.length, 0); assert.equal(accepted.submission, undefined);
});
test('a submission snapshot persists until explicitly accepted', () => {
  const lib = load(), disk = storage(), repo = lib.createComposerDraftStore(disk);
  repo.update('scope', 'chat', { text: 'not yet accepted' });
  repo.prepareSubmission('scope', 'chat', 'turn1');
  const restored = lib.createComposerDraftStore(disk).load('scope', 'chat');
  assert.equal(restored.text, 'not yet accepted'); assert.equal(restored.submission.clientTurnId, 'turn1');
  assert.equal(restored.submission.state, 'confirming');
});
test('quota failure preserves in-memory content and reports unsaved', () => {
  const disk = { getItem() { throw Error('private failure'); }, setItem() { throw Error('quota'); } };
  const repo = load().createComposerDraftStore(disk);
  const result = repo.update('scope', 'chat', { text: 'in memory' });
  assert.equal(result.persisted, false); assert.equal(repo.load('scope', 'chat').text, 'in memory');
});
test('attachment invalidation retains the chip and never silently drops a reference', () => {
  const repo = load().createComposerDraftStore(storage());
  repo.update('scope', 'chat', { text: '', attachments: [attachment()] });
  const result = repo.setAttachmentAvailability('scope', 'chat', { file1: false });
  assert.equal(result.attachments.length, 1); assert.equal(result.attachments[0].available, false); assert.equal(result.attachments[0].name, '活动.md');
});
test('persisted allowlist strips credentials and unrelated attachment fields', () => {
  const disk = storage(), repo = load().createComposerDraftStore(disk);
  repo.update('scope', 'chat', { text: 'plain', token: 'SECRET', attachments: [{ ...attachment(), access_token: 'SECRET', headers: { Authorization: 'SECRET' } }] });
  const raw = [...disk.values.values()].join('');
  assert.doesNotMatch(raw, /SECRET|access_token|Authorization|headers/);
});
test('malformed, wrong-scope and future-version disk data are not restored', () => {
  const lib = load(), disk = storage(), repo = lib.createComposerDraftStore(disk);
  repo.update('scope', 'chat', { text: 'source' });
  const [key, raw] = [...disk.values.entries()][0];
  for (const value of ['{broken', JSON.stringify({ ...JSON.parse(raw), scopeKey: 'other' }), JSON.stringify({ ...JSON.parse(raw), version: 2 }), JSON.stringify({ ...JSON.parse(raw), revision: -1 })]) {
    disk.setItem(key, value); assert.equal(lib.createComposerDraftStore(disk).load('scope', 'chat').text, '');
  }
});
test('callers cannot mutate a stored draft through returned objects', () => {
  const repo = load().createComposerDraftStore(storage());
  const draft = repo.update('scope', 'chat', { text: 'source', attachments: [attachment()] });
  draft.attachments[0].name = 'changed'; draft.text = 'changed';
  assert.equal(repo.load('scope', 'chat').text, 'source'); assert.equal(repo.load('scope', 'chat').attachments[0].name, '活动.md');
});
test('deferred updates write memory immediately and disk only when flushed', () => {
  const disk = storage(), repo = load().createComposerDraftStore(disk);
  const draft = repo.update('scope', 'chat', { text: 'pending' }, { persist: false });
  assert.equal(draft.persisted, false); assert.equal(disk.values.size, 0); assert.equal(repo.load('scope', 'chat').text, 'pending');
  assert.equal(repo.persist('scope', 'chat').persisted, true); assert.equal(disk.values.size, 1);
});
test('new submission cannot replace an uncertain original snapshot', () => {
  const repo = load().createComposerDraftStore(storage());
  repo.update('scope', 'chat', { text: 'original' });
  repo.prepareSubmission('scope', 'chat', 'turn1');
  repo.update('scope', 'chat', { text: 'new draft' });
  assert.throws(() => repo.prepareSubmission('scope', 'chat', 'turn2'), /pending/);
  const same = repo.prepareSubmission('scope', 'chat', 'turn1');
  assert.equal(same.submission.text, 'original'); assert.equal(same.text, 'new draft');
});
