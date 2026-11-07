import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

let plugin;
try {
  const source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8');
  plugin = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  plugin = {};
}
const enroll = { model: 'voice-enrollment', input: { action: 'create_voice', target_model: 'cosyvoice-v2', prefix: 'myvoice', url: 'https://sample.example.test/sample.wav' } };
const verify = { model: 'voice-enrollment', input: { action: 'query_voice', voice_task_id: 'task_enroll' } };
const synth = { model: 'cosyvoice-v2', input: { voice_task_id: 'task_verify', text: '你好，世界。', format: 'wav', rate: 1 } };
const decode = value => ({ body: { kind: 'json', value } });
const common = { baseUrl: 'https://workspace.cn-beijing.maas.aliyuncs.com', authHeader: 'Bearer fixture-key' };
const enrolled = { taskId: 'task_enroll', upstreamTaskId: 'cosyvoice-private-voice-id', action: 'create_voice', status: 'SUCCESS', data: { stage: 'create_voice', target_model: 'cosyvoice-v2', usage: { count: 1 } } };
const verified = { taskId: 'task_verify', upstreamTaskId: 'cosyvoice-private-voice-id', action: 'query_voice', status: 'SUCCESS', data: { stage: 'query_voice', target_model: 'cosyvoice-v2', voice_status: 'OK', usage: { count: 1 } } };
const ttsResponse = { request_id: 'request_tts', output: { finish_reason: 'stop', audio: { id: 'audio_tts', url: 'https://media.example.test/speech.wav', expires_at: 2000000000 } }, usage: { characters: 6 } };

test('declares private read/create routes without destructive voice actions', () => {
  assert.equal(plugin.meta?.key, 'easel-dashscope-voice');
  assert.ok(plugin.meta.routes.every(route => route.path.startsWith('/easel/dashscope/voice/')));
  assert.equal(plugin.meta.routes.filter(route => route.method === 'DELETE').length, 0);
});

test('decodes voice creation and rejects destructive or arbitrary provider actions', () => {
  assert.equal(typeof plugin.native?.enroll, 'function');
  assert.deepEqual(plugin.native.enroll(decode(enroll)), { kind: 'submit', model: 'voice-enrollment', action: 'create_voice', requestBody: enroll });
  assert.throws(() => plugin.native.enroll(decode({ ...enroll, input: { ...enroll.input, action: 'delete_voice' } })), /create_voice/i);
  assert.throws(() => plugin.native.enroll(decode({ ...enroll, input: { ...enroll.input, prefix: 'invalid-prefix' } })), /prefix/i);
});

test('decodes reference lookups and synthesis exclusively through public origin task IDs', () => {
  assert.equal(typeof plugin.native?.verify, 'function');
  assert.deepEqual(plugin.native.verify(decode(verify)).originTaskIds, ['task_enroll']);
  assert.deepEqual(plugin.native.synthesize(decode(synth)).originTaskIds, ['task_verify']);
  assert.throws(() => plugin.native.synthesize(decode({ ...synth, input: { text: 'test', voice: 'private-id' } })), /voice_task_id|unsupported/i);
  assert.throws(() => plugin.native.verify(decode({ ...verify, input: { action: 'query_voice', voice_id: 'private-id' } })), /voice_task_id|unsupported/i);
});

test('builds the actual enrollment and verification HTTP requests', () => {
  assert.equal(typeof plugin.buildSubmitRequest, 'function');
  const created = plugin.buildSubmitRequest({ ...common, action: 'create_voice', upstreamModel: 'voice-enrollment', requestBody: enroll });
  assert.equal(created.url, common.baseUrl + '/api/v1/services/audio/tts/customization');
  assert.deepEqual(created.body, enroll);
  const checked = plugin.buildSubmitRequest({ ...common, action: 'query_voice', requestBody: verify, originTasks: [enrolled] });
  assert.deepEqual(checked.body, { model: 'voice-enrollment', input: { action: 'query_voice', voice_id: enrolled.upstreamTaskId } });
});

test('requires a successful owned verification and matching target model before synthesis', () => {
  assert.equal(typeof plugin.buildSubmitRequest, 'function');
  const context = { ...common, action: 'synthesize', upstreamModel: 'cosyvoice-v2', requestBody: synth, originTasks: [verified] };
  const request = plugin.buildSubmitRequest(context);
  assert.equal(request.url, common.baseUrl + '/api/v1/services/audio/tts/SpeechSynthesizer');
  assert.deepEqual(request.body.input, { text: '你好，世界。', format: 'wav', rate: 1, voice: verified.upstreamTaskId });
  assert.throws(() => plugin.buildSubmitRequest({ ...context, originTasks: [] }), /origin/i);
  assert.throws(() => plugin.buildSubmitRequest({ ...context, originTasks: [enrolled] }), /verif|origin/i);
  assert.throws(() => plugin.buildSubmitRequest({ ...context, originTasks: [{ ...verified, data: { ...verified.data, voice_status: 'DEPLOYING' } }] }), /ready|OK/i);
  assert.throws(() => plugin.buildSubmitRequest({ ...context, upstreamModel: 'cosyvoice-v3-plus' }), /model/i);
});

test('stores private voice IDs only in the host private task identifier', () => {
  assert.equal(typeof plugin.parseSubmitResponse, 'function');
  const created = plugin.parseSubmitResponse({ action: 'create_voice', requestBody: enroll }, { statusCode: 200, body: { output: { voice_id: enrolled.upstreamTaskId }, usage: { count: 1 }, request_id: 'private-request' } });
  assert.equal(created.taskId, enrolled.upstreamTaskId);
  assert.equal(created.immediate.status, 'SUCCESS');
  assert.equal(JSON.stringify(created.taskData).includes(enrolled.upstreamTaskId), false);
  const checked = plugin.parseSubmitResponse({ action: 'query_voice', requestBody: verify, originTasks: [enrolled] }, { statusCode: 200, body: { output: { status: 'DEPLOYING', target_model: 'cosyvoice-v2' }, usage: { count: 1 } } });
  assert.equal(checked.taskData.voice_status, 'DEPLOYING');
  assert.equal(checked.taskData.target_model, 'cosyvoice-v2');
});

test('native responses expose only opaque voice task references', () => {
  assert.equal(typeof plugin.native?.result, 'function');
  const result = plugin.native.result({}, { task_id: 'task_verify', status: 'SUCCESS', data: verified.data });
  assert.equal(result.output.voice_task_id, 'task_verify');
  assert.equal(result.output.voice_status, 'OK');
  assert.equal(result.output.voice_id, undefined);
  assert.equal(JSON.stringify(result).includes(enrolled.upstreamTaskId), false);
});

test('uses provider count and characters without inventing successful usage', () => {
  assert.equal(typeof plugin.extractUsageOnComplete, 'function');
  assert.equal(plugin.extractUsage({}), null);
  assert.deepEqual(plugin.extractUsageOnComplete({ action: 'create_voice' }, { status: 'SUCCESS' }, enrolled.data), { count: 1, operation: 'create_voice' });
  assert.deepEqual(plugin.extractUsageOnComplete({ action: 'synthesize' }, { status: 'SUCCESS' }, { usage: { characters: 6 } }), { characters: 6 });
  assert.throws(() => plugin.parseSubmitResponse({ action: 'synthesize' }, { statusCode: 200, body: { ...ttsResponse, usage: {} } }), /characters|usage/i);
  assert.throws(() => plugin.parseSubmitResponse({ action: 'create_voice', requestBody: enroll }, { statusCode: 200, body: { output: { voice_id: 'private' }, usage: {} } }), /count|usage/i);
});

test('projects only synthesis audio and never forwards channel credentials to the CDN', () => {
  assert.equal(typeof plugin.listArtifacts, 'function');
  const response = plugin.parseSubmitResponse({ action: 'synthesize' }, { statusCode: 200, body: ttsResponse });
  const task = { status: 'SUCCESS', data: response.taskData };
  assert.deepEqual(plugin.listArtifacts(task), [{ key: 'audio', type: 'audio', mimeType: 'audio/wav' }]);
  assert.deepEqual(plugin.listArtifacts({ status: 'SUCCESS', data: enrolled.data }), []);
  assert.deepEqual(plugin.buildContentRequest({ artifactKey: 'audio', data: response.taskData, authHeader: 'Bearer never-forward', clientRequest: { method: 'GET' } }), { url: 'https://media.example.test/speech.wav', method: 'GET', credentialless: true });
  assert.throws(() => plugin.buildQueryRequest({}), /no upstream polling/i);
  assert.equal(plugin.parseTaskResult({}, {}, { status: 503 }).status, 'UNKNOWN');
});
