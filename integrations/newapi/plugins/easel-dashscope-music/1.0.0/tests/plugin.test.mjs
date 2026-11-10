import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const pluginPath = new URL('../plugin.js', import.meta.url);
let plugin;
try {
  const source = await readFile(pluginPath, 'utf8');
  plugin = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  plugin = {};
}
globalThis.utils = { uuid: () => 'fixture-local-id' };
const success = {
  request_id: 'vendor-request-id',
  output: {
    audio: { id: 'vendor-audio-id', url: 'https://media.example.test/song.mp3?signature=fixture', expires_at: 2000000000 },
    extra_info: { channels: 2, sample_rate: 48000 },
    finish_reason: 'stop',
  },
  usage: { duration: 123 },
};
const body = { model: 'fun-music-v1', input: { prompt: 'An upbeat piano song', is_instrumental: true } };
const driver = { baseUrl: 'https://workspace.cn-beijing.maas.aliyuncs.com', authHeader: 'Bearer fixture-channel-key', upstreamModel: 'fun-music-v1', publicTaskId: 'task_public', requestBody: body };

test('declares a vendor-only plugin with private routes and actual Fun-Music models', () => {
  assert.equal(plugin.meta?.key, 'easel-dashscope-music');
  assert.deepEqual(plugin.meta.models, ['fun-music-v1', 'fun-music-preview']);
  assert.deepEqual(plugin.meta.upstreams, ['vendor']);
  assert.ok(plugin.meta.routes.every(route => route.path.startsWith('/easel/dashscope/')));
});

test('builds the documented synchronous music endpoint without legacy async headers', () => {
  assert.equal(typeof plugin.buildSubmitRequest, 'function');
  const request = plugin.buildSubmitRequest(driver);
  assert.equal(request.url, 'https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/music/generation');
  assert.equal(request.method, 'POST');
  assert.equal(request.headers.Authorization, 'Bearer fixture-channel-key');
  assert.equal(request.headers['X-DashScope-Async'], undefined);
  assert.equal(request.headers['X-DashScope-SSE'], undefined);
  assert.deepEqual(request.body, body);
  assert.equal(plugin.buildSubmitRequest({ ...driver, baseUrl: driver.baseUrl + '/api/v1/' }).url, request.url);
});

test('validates real provider inputs without accepting unsupported duration or stream requests', () => {
  assert.equal(typeof plugin.native?.create, 'function');
  const ctx = value => ({ body: { kind: 'json', value } });
  assert.deepEqual(plugin.native.create(ctx(body)), { kind: 'submit', model: 'fun-music-v1', action: 'generate', requestBody: body });
  assert.throws(() => plugin.native.create(ctx({ ...body, parameters: { duration: 90 } })), /duration/i);
  assert.throws(() => plugin.native.create(ctx({ ...body, stream: true })), /stream/i);
  assert.throws(() => plugin.native.create(ctx({ model: 'audio-generation', input: { prompt: 'test' } })), /model/i);
  assert.throws(() => plugin.native.create(ctx({ model: 'fun-music-preview', input: { lyrics: 'some lyrics' } })), /prompt/i);
  assert.throws(() => plugin.native.create(ctx({ model: 'fun-music-v1', input: { prompt: 'test', is_instrumental: 'true' } })), /boolean/i);
});

test('only marks a response complete with a final audio URL and positive provider duration', () => {
  assert.equal(typeof plugin.parseSubmitResponse, 'function');
  const result = plugin.parseSubmitResponse(driver, { statusCode: 200, body: success });
  assert.equal(result.immediate.status, 'SUCCESS');
  assert.equal(result.taskId, 'vendor-audio-id');
  assert.equal(result.taskData.usage.duration, 123);
  assert.equal(result.taskData.output.audio.id, undefined);
  assert.equal(result.taskData.request_id, undefined);
  for (const duration of [undefined, 0, -1, '123', Infinity]) {
    const malformed = { ...success, usage: { duration } };
    assert.throws(() => plugin.parseSubmitResponse(driver, { statusCode: 200, body: malformed }), /usage|duration/i);
  }
  assert.throws(() => plugin.parseSubmitResponse(driver, { statusCode: 200, body: { ...success, output: { ...success.output, audio: {} } } }), /audio/i);
  assert.throws(() => plugin.parseSubmitResponse(driver, { statusCode: 200, body: { code: 'InvalidApiKey', message: 'do not echo this raw response' } }), /provider rejected/i);
});

test('keeps unknown and failed observations distinct and never invents a vendor poll endpoint', () => {
  assert.equal(typeof plugin.parseTaskResult, 'function');
  assert.equal(plugin.parseTaskResult({}, success, { status: 200 }).status, 'SUCCESS');
  assert.equal(plugin.parseTaskResult({}, { code: 'GenerationFailed' }, { status: 200 }).status, 'FAILURE');
  assert.equal(plugin.parseTaskResult({}, { output: { finish_reason: 'null' } }, { status: 200 }).status, 'UNKNOWN');
  assert.equal(plugin.parseTaskResult({}, {}, { status: 503 }).status, 'UNKNOWN');
  assert.throws(() => plugin.buildQueryRequest({ taskId: 'private' }), /no upstream polling/i);
});

test('reports duration from vendor usage, never from requested duration or a zero fallback', () => {
  assert.equal(typeof plugin.extractUsage, 'function');
  assert.equal(plugin.extractUsage(driver), null);
  assert.deepEqual(plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, success), { duration_seconds: 123 });
  assert.equal(plugin.extractUsageOnComplete({}, { status: 'FAILURE' }, {}), null);
  assert.throws(() => plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, {}), /duration/i);
});

test('native create/query expose public task IDs while retaining provider usage', () => {
  assert.equal(typeof plugin.native?.created, 'function');
  const task = { task_id: 'task_public', status: 'SUCCESS', data: success };
  const response = plugin.native.created({}, task);
  assert.equal(response.output.task_id, 'task_public');
  assert.equal(response.output.task_status, 'SUCCEEDED');
  assert.equal(response.output.audio.id, undefined);
  assert.equal(response.usage.duration, 123);
  assert.deepEqual(plugin.native.status({}, task), response);
  assert.equal(plugin.native.status({}, { task_id: 'task_public', status: 'UNKNOWN', data: {} }).output.task_status, 'UNKNOWN');
});

test('projects an audio artifact and serves content without sending channel credentials to its CDN', () => {
  assert.equal(typeof plugin.listArtifacts, 'function');
  const task = { status: 'SUCCESS', data: success };
  assert.deepEqual(plugin.listArtifacts(task), [{ key: 'audio', type: 'audio', mimeType: 'audio/mpeg' }]);
  assert.deepEqual(plugin.listArtifacts({ status: 'FAILURE', data: success }), []);
  const request = plugin.buildContentRequest({ artifactKey: 'audio', data: success, authHeader: 'Bearer secret-must-not-leak', clientRequest: { method: 'HEAD', headers: { Range: 'bytes=0-99' } } });
  assert.deepEqual(request, { url: success.output.audio.url, method: 'HEAD', credentialless: true });
  assert.throws(() => plugin.buildContentRequest({ artifactKey: 'video', data: success }), /artifact/i);
  assert.throws(() => plugin.buildContentRequest({ artifactKey: 'audio', data: { ...success, output: { audio: { url: 'file:///secret' } } } }), /audio/i);
});
