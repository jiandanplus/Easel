import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { createHash, webcrypto } from 'node:crypto';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(fetch) {
  const code = ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const api = {};
  runInNewContext(code, { exports: api, require: requireDeliverables, fetch, window: { location: { pathname: '/' } },
    Error, AbortController, TextDecoder, TextEncoder, crypto: webcrypto, Date, setTimeout, clearTimeout });
  return api;
}
const valid = { version: 1, session_id: 'session-1', turn_id: 'turn-1', request_hash: 'a'.repeat(64), receipt_id: 'b'.repeat(64) };

test('only exact accepted receipts acknowledge a submitted draft', async () => {
  const hints = [], values = [valid, { ...valid, session_id: 'other' }, { ...valid, turn_id: 'other' },
    { ...valid, version: 2 }, { ...valid, receipt_id: '' }, { ...valid, request_hash: 'bad' }, { ...valid, secret: 'omit' }];
  const frames = values.map(value => `event: accepted\ndata: ${JSON.stringify(value)}\n\n`).join('') + 'event: done\ndata: {}\n\n';
  const api = load(async () => new Response(frames));
  await new Promise((resolve, reject) => api.streamChat('draft', undefined, 'session-1', () => {}, resolve, reject,
    undefined, undefined, undefined, 'turn-1', false, undefined, [], undefined, undefined, undefined, undefined, hint => hints.push(hint)));
  assert.deepEqual(JSON.parse(JSON.stringify(hints)), [valid, valid]);
});

test('exact turn lookup never sends a POST and rejects identifiers before network', async () => {
  const calls = [];
  const api = load(async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ ...valid, state: 'completed', accepted: true }) }; });
  assert.equal(typeof api.fetchChatTurn, 'function');
  const controller = new AbortController();
  const result = await api.fetchChatTurn('session-1', 'turn-1', controller.signal);
  assert.equal(result.turn_id, 'turn-1');
  assert.equal(calls[0].url, '/api/chat/turns/session-1/turn-1');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.signal, controller.signal);
  assert.ok(!calls[0].options.method || calls[0].options.method === 'GET');
  for (const bad of ['', '../escape', 'a/b', 'a\n', 'x'.repeat(161), null, 123])
    await assert.rejects(async () => api.fetchChatTurn('session-1', bad));
  assert.equal(calls.length, 1);
});

test('turn lookup rejects a response for a different request', async () => {
  const api = load(async () => ({ ok: true, json: async () => ({ ...valid, turn_id: 'other', state: 'completed', accepted: true }) }));
  assert.equal(typeof api.fetchChatTurn, 'function');
  await assert.rejects(() => api.fetchChatTurn('session-1', 'turn-1'));
});

test('stop binds exact turn and preserves stopping instead of inventing completion', async () => {
  let body;
  const api = load(async (_url, options) => { body = JSON.parse(options.body); return { ok: true, json: async () => ({ stopped: false, state: 'stopping' }) }; });
  const value = await api.stopChat('session-1', 'turn-1');
  assert.deepEqual(body, { sessionId: 'session-1', turnId: 'turn-1' });
  assert.equal(value.state, 'stopping');
  assert.equal(value.stopped, false);
});
test('only exact request hashes prove a pre-dispatch rejection', async () => {
  const canonical = '{"attachments":[],"catalog_revision":null,"execution_target":null,"message":"草稿","model":null,"persona":null,"reasoning_effort":null,"service_source":null,"sessionId":"session-1","turnId":"turn-1"}';
  const digest = createHash('sha256').update(canonical).digest('hex');
  for (const [hash, expected] of [[digest, true], ['a'.repeat(64), false]]) {
    const api = load(async () => ({ ok: false, status: 409, json: async () => ({ detail: '请选择模型', rejection: { ...valid, not_dispatched: true, request_hash: hash } }) }));
    const error = await new Promise(resolve => api.streamChat('草稿', undefined, 'session-1', () => {}, () => {}, resolve,
      undefined, undefined, undefined, 'turn-1'));
    assert.equal(!!error.rejection, expected);
  }
});
test('accepted task errors retain the durable terminal instead of stopping before done', async () => {
  const frames = `event: accepted\ndata: ${JSON.stringify(valid)}\n\nevent: error\ndata: "停止请求"\n\nevent: done\ndata: ${JSON.stringify({ ...valid, state: 'stopped', clean_end: false, stop_reason: 'user_stopped' })}\n\n`;
  const api = load(async () => new Response(frames));
  const error = await new Promise(resolve => api.streamChat('draft', undefined, 'session-1', () => {}, () => { throw Error('expected error terminal'); }, resolve,
    undefined, undefined, undefined, 'turn-1'));
  assert.equal(error.completion?.state, 'stopped');
  assert.equal(error.completion?.stop_reason, 'user_stopped');
});
test('request fingerprint is bound before the physical submission', async () => {
  const order = [];
  const api = load(async () => { order.push('post'); return new Response('event: done\ndata: {}\n\n'); });
  await new Promise((resolve, reject) => api.streamChat('draft', undefined, 'session-1', () => {}, resolve, reject,
    undefined, undefined, undefined, 'turn-1', false, undefined, [], undefined, undefined, undefined, undefined, undefined,
    binding => { assert.match(binding.request_hash, /^[a-f0-9]{64}$/); order.push('bound'); }));
  assert.deepEqual(order, ['bound', 'post']);
});
test('withdrawal fences the exact original turn without submitting a model request', async () => {
  const calls = [];
  const api = load(async (url, options) => { calls.push({url, options}); return { ok: true, json: async () => ({...valid, state:'rejected', accepted:false, not_dispatched:true}) }; });
  assert.equal(typeof api.withdrawChatTurn, 'function');
  const result = await api.withdrawChatTurn('session-1', 'turn-1', valid.request_hash);
  assert.equal(result.state, 'rejected');
  assert.equal(calls[0].url, '/api/chat/turns/session-1/turn-1/withdraw');
  assert.deepEqual(JSON.parse(calls[0].options.body), {request_hash: valid.request_hash});
  assert.equal(calls[0].options.method, 'POST');
  await assert.rejects(() => api.withdrawChatTurn('session-1', 'turn-1', 'bad'));
  assert.equal(calls.length, 1);
});
