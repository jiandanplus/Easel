import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

globalThis.utils = { unixNow: () => 1000 };
let plugin = {};
try {
  const source = await readFile(new URL('./plugin.js', import.meta.url), 'utf8');
  plugin = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
} catch (error) { if (error.code !== 'ENOENT') throw error; }
const id = `task_${'a'.repeat(32)}`;
const secret = 'F'.repeat(43);
const opaque = 'b'.repeat(64);
const request = { model: 'easel-fish-s2.1-pro', text: 'Hello', reference_id: 'admin_preset', format: 'mp3', speed: 1 };
const ctx = { publicTaskId: id, requestBody: request, model: request.model, upstreamModel: request.model, apiKey: secret, baseUrl: 'http://fish-bridge:8787' };
const metadata = { state: 'completed', operation_id: id, artifact_id: opaque, mime_type: 'audio/mpeg', format: 'mp3', size: 8, expires_at: 1600, request_count: 1, input_characters: 5 };
const decode = (value) => plugin.native.synthesize({ query: {}, body: { kind: 'json', value } });

test('private manifest has no standard model or public bridge route takeover', () => {
  assert.equal(plugin.meta?.key, 'easel-fish-tts');
  assert.ok(plugin.meta.models.every((model) => model.startsWith('easel-fish-')));
  assert.ok(plugin.meta.routes.every((route) => route.path.startsWith('/easel/fish/')));
});

test('decoder keeps a bounded reference payload and rejects arbitrary fields', () => {
  assert.equal(decode(request).model, request.model);
  assert.throws(() => decode({ ...request, url: 'https://evil.example' }), /unsupported field/);
  assert.throws(() => decode({ ...request, stream: true }), /stream/);
  assert.throws(() => decode({ ...request, model: 'unknown' }), /model/);
  assert.throws(() => decode({ ...request, text: 'x'.repeat(2001) }), /text/);
  assert.throws(() => decode({ ...request, references: [{ audio: 'UklGRg==', text: 'sample' }] }), /one reference source/);
  assert.throws(() => decode({ ...request, reference_id: undefined, references: [{ audio: 'notbase64', text: 'sample' }] }), /base64/);
});

test('inline samples remain base64 for the private bridge MessagePack conversion', () => {
  const value = { model: request.model, text: 'Hello', references: [{ audio: 'UklGRg==', text: 'sample' }] };
  const decoded = decode(value);
  assert.equal(decoded.requestBody.references[0].audio, 'UklGRg==');
  assert.equal(decoded.requestBody.reference_id, undefined);
});

test('submission has fixed private endpoint and stable public task idempotency key', () => {
  const result = plugin.buildSubmitRequest(ctx);
  assert.equal(result.url, 'http://fish-bridge:8787/v1/tts');
  assert.equal(result.method, 'POST');
  assert.equal(result.headers['Idempotency-Key'], id);
  assert.equal(result.headers.Authorization, `Bearer ${secret}`);
  assert.equal(result.body.model, 's2.1-pro');
  assert.equal(result.body.reference_id, 'admin_preset');
  for (const baseUrl of ['https://evil.example', 'http://0.0.0.0:8787', 'http://fish-bridge:8787@evil.example', 'http://fish-bridge:8787/path']) {
    assert.throws(() => plugin.buildSubmitRequest({ ...ctx, baseUrl }), /private bridge URL/);
  }
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, apiKey: 'weak' }), /internal secret/);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, publicTaskId: 'client-chosen' }), /public task/);
});

test('complete metadata becomes immediate success without audio bytes in task data', () => {
  const result = plugin.parseSubmitResponse(ctx, { statusCode: 200, body: metadata });
  assert.equal(result.taskId, id);
  assert.equal(result.immediate.status, 'SUCCESS');
  assert.equal(result.taskData.artifact_id, opaque);
  assert.equal(result.taskData.audio, undefined);
  assert.throws(() => plugin.parseSubmitResponse(ctx, { statusCode: 200, body: { ...metadata, operation_id: 'foreign' } }), /operation/);
  assert.throws(() => plugin.parseSubmitResponse(ctx, { statusCode: 200, body: { ...metadata, artifact_id: '../secret' } }), /artifact/);
});

test('unknown consumption remains pending and polling only reads existing receipt', () => {
  const result = plugin.parseSubmitResponse(ctx, { statusCode: 202, body: { state: 'pending', operation_id: id } });
  assert.equal(result.immediate, undefined);
  assert.equal(result.taskId, id);
  const query = plugin.buildQueryRequest({ ...ctx, taskId: id });
  assert.equal(query.method, 'GET');
  assert.equal(query.url, `http://fish-bridge:8787/v1/operations/${id}`);
  assert.equal(plugin.parseTaskResult({}, { state: 'pending', operation_id: id }).status, 'IN_PROGRESS');
  assert.equal(plugin.parseTaskResult({}, { state: 'unknown' }).status, 'UNKNOWN');
});

test('only unexpired successful tasks advertise the audio artifact', () => {
  assert.deepEqual(plugin.listArtifacts({ status: 'SUCCESS', data: metadata }), [{ key: 'audio', type: 'audio', mimeType: 'audio/mpeg' }]);
  assert.deepEqual(plugin.listArtifacts({ status: 'IN_PROGRESS', data: metadata }), []);
  assert.deepEqual(plugin.listArtifacts({ status: 'SUCCESS', data: { ...metadata, expires_at: 999 } }), []);
  assert.deepEqual(plugin.listArtifacts({ status: 'SUCCESS', data: { ...metadata, state: 'expired' } }), []);
});

test('artifact proxy descriptor authenticates the same secret and forwards only valid Range', () => {
  const value = { ...ctx, data: metadata, artifactKey: 'audio', clientRequest: { method: 'GET', headers: { Range: 'bytes=0-2', Authorization: 'evil' } } };
  const result = plugin.buildContentRequest(value);
  assert.equal(result.url, `http://fish-bridge:8787/v1/artifacts/${opaque}`);
  assert.equal(result.headers.Authorization, `Bearer ${secret}`);
  assert.equal(result.headers.Range, 'bytes=0-2');
  assert.equal(result.credentialless, undefined);
  assert.throws(() => plugin.buildContentRequest({ ...value, artifactKey: '../../other' }), /artifact_not_found/);
  assert.throws(() => plugin.buildContentRequest({ ...value, clientRequest: { method: 'POST' } }), /GET or HEAD/);
});

test('public JSON has only New API owner-protected content URL', () => {
  const result = plugin.native.created({}, { task_id: id, status: 'SUCCESS', data: metadata });
  assert.equal(result.content_url, `/v1/tasks/${id}/artifacts/audio/content`);
  assert.ok(!JSON.stringify(result).includes(opaque));
  assert.ok(!JSON.stringify(result).includes('fish-bridge'));
});

test('usage reports observed request and input characters without provider token claims', () => {
  assert.deepEqual(plugin.extractUsage(ctx), { synthesis_requests: 1, input_characters: 5 });
  assert.deepEqual(plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, metadata), { synthesis_requests: 1, input_characters: 5 });
  assert.deepEqual(plugin.extractUsageOnComplete({}, { status: 'IN_PROGRESS' }, { state: 'pending' }), {});
  assert.throws(() => plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, { ...metadata, request_count: 2 }), /request_count/);
});
