import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

let plugin = {};
try {
  const source = await readFile(new URL('./plugin.js', import.meta.url), 'utf8');
  plugin = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
const id = (letter) => `task_${letter.repeat(32)}`;
const file = { ref: 'request_file:file', field: 'file', filename: 'sample.wav', mimeType: 'audio/wav', size: 1024 };
const uploadRequest = (fields = { purpose: ['voice_clone'] }, files = [file]) => ({ query: {}, body: { kind: 'multipart', fields, files } });
const jsonRequest = (value) => ({ query: {}, body: { kind: 'json', value } });
const context = (action, requestBody = {}, extra = {}) => ({ action, requestBody, publicTaskId: id('c'), baseUrl: 'https://api.minimax.cn', apiKey: 'fixture-key', files: [], model: `easel-minimax-${action}`, upstreamModel: `easel-minimax-${action}`, ...extra });
const origin = (action, upstreamTaskId, taskId = id('a')) => ({ action, upstreamTaskId, taskId, status: 'SUCCESS', data: { operation: action, status: 'SUCCESS' } });
const success = (body) => ({ statusCode: 200, body: { ...body, base_resp: { status_code: 0, status_msg: 'success' } } });

test('private plugin declares only namespaced routes and models', () => {
  assert.equal(plugin.meta?.key, 'easel-minimax-voice');
  assert.equal(plugin.meta.version, '1.0.0');
  assert.ok(plugin.meta.models.every((model) => model.startsWith('easel-minimax-')));
  assert.ok(plugin.meta.routes.every((route) => route.path.startsWith('/easel/minimax/')));
  assert.equal(plugin.meta.protocols, undefined);
});

test('upload decoder selects one local sample without URLs or raw bytes', () => {
  const result = plugin.native.upload(uploadRequest());
  assert.deepEqual(result, { kind: 'submit', action: 'upload', model: 'easel-minimax-upload', requestBody: { purpose: 'voice_clone' } });
  assert.throws(() => plugin.native.upload(uploadRequest({ purpose: ['voice_clone'], url: ['https://example.com/sample.wav'] })), /unsupported field/);
  assert.throws(() => plugin.native.upload(uploadRequest({}, [file, file])), /one file/);
  assert.throws(() => plugin.native.upload(uploadRequest({}, [{ ...file, size: 21 * 1024 * 1024 }])), /20 MiB/);
  assert.throws(() => plugin.native.upload(uploadRequest({}, [{ ...file, filename: 'sample.exe' }])), /mp3, m4a or wav/);
});

test('upload descriptor forwards host-owned multipart once to official host', () => {
  const result = plugin.buildSubmitRequest(context('upload', { purpose: 'voice_clone' }, { files: [file] }));
  assert.equal(result.url, 'https://api.minimax.cn/v1/files/upload');
  assert.equal(result.method, 'POST');
  assert.equal(result.bodyType, 'multipart');
  assert.deepEqual(result.parts, [{ name: 'purpose', value: 'voice_clone' }, { name: 'file', fileRef: file.ref, filename: 'sample.wav' }]);
  assert.equal(result.headers.Authorization, 'Bearer fixture-key');
  assert.equal(result.headers['Content-Type'], undefined);
});

test('base URL is administrator-only and restricted to the current official regions', () => {
  const make = (baseUrl) => context('upload', { purpose: 'voice_clone' }, { files: [file], baseUrl });
  assert.equal(plugin.buildSubmitRequest(make('https://api.minimax.io/')).url, 'https://api.minimax.io/v1/files/upload');
  assert.equal(plugin.buildSubmitRequest(make('https://api.minimax.cn?GroupId=123456')).url, 'https://api.minimax.cn/v1/files/upload?GroupId=123456');
  for (const baseUrl of ['http://api.minimax.cn', 'https://evil.example', 'https://api.minimax.chat', 'https://api.minimax.cn@evil.example', 'https://api.minimax.cn/v1', 'https://api.minimax.cn?url=https://evil.example']) {
    assert.throws(() => plugin.buildSubmitRequest(make(baseUrl)), /official MiniMax base URL/);
  }
  assert.throws(() => plugin.buildSubmitRequest({ ...make('https://api.minimax.cn'), apiKey: 'bad\nkey' }), /API key/);
});

test('upload completion stores supplier ID privately and creates immediate task', () => {
  const result = plugin.parseSubmitResponse(context('upload'), success({ file: { file_id: '123456789', purpose: 'voice_clone' } }));
  assert.equal(result.taskId, '123456789');
  assert.deepEqual(result.immediate, { status: 'SUCCESS', progress: '100%' });
  assert.deepEqual(result.taskData, { operation: 'upload', status: 'SUCCESS', purpose: 'voice_clone' });
  assert.throws(() => plugin.parseSubmitResponse(context('upload'), success({ file: { file_id: 9007199254740992 } })), /unsafe file_id/);
});

test('clone decoder declares the public upload dependency and rejects supplier IDs', () => {
  const result = plugin.native.clone(jsonRequest({ file_task_id: id('a'), need_noise_reduction: true }));
  assert.deepEqual(result.originTaskIds, [id('a')]);
  assert.equal(result.action, 'clone');
  assert.throws(() => plugin.native.clone(jsonRequest({ file_id: 123, voice_id: 'foreignVoice' })), /unsupported field/);
  assert.throws(() => plugin.native.clone(jsonRequest({ file_task_id: '123' })), /public task/);
  assert.throws(() => plugin.native.clone(jsonRequest({ file_task_id: id('a'), text: 'extra billable preview' })), /unsupported field/);
});

test('clone uses only a successful host-resolved upload and generates its voice ID', () => {
  const ctx = context('clone', { file_task_id: id('a'), need_noise_reduction: true }, { originTasks: [origin('upload', '123456789')] });
  const result = plugin.buildSubmitRequest(ctx);
  assert.equal(result.url, 'https://api.minimax.cn/v1/voice_clone');
  assert.equal(result.body.file_id, 123456789);
  assert.equal(result.body.voice_id, `easel_${id('c')}`);
  assert.equal(result.body.need_noise_reduction, true);
  assert.equal(result.body.text, undefined);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, originTasks: [] }), /host-resolved/);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, originTasks: [origin('clone', '123')] }), /successful upload/);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, originTasks: [{ ...origin('upload', '123'), status: 'FAILURE' }] }), /successful upload/);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, originTasks: [origin('upload', '123', id('b'))] }), /host-resolved/);
});

test('quoted int64 file IDs retain every digit when emitted as a JSON numeric literal', () => {
  const number = '9007199254740993';
  const uploaded = plugin.parseSubmitResponse(context('upload'), success({ file: { file_id: number } }));
  assert.equal(uploaded.taskId, number);
  const result = plugin.buildSubmitRequest(context('clone', { file_task_id: id('a') }, { originTasks: [origin('upload', number)] }));
  assert.equal(typeof result.body, 'string');
  assert.ok(result.body.includes('"file_id":9007199254740993'));
  assert.ok(!result.body.includes('9007199254740992'));
  assert.throws(() => plugin.parseSubmitResponse(context('upload'), success({ file: { file_id: '9223372036854775808' } })), /unsafe file_id/);
});

test('clone completion stores no public upstream voice identifier', () => {
  const result = plugin.parseSubmitResponse(context('clone'), success({ input_sensitive: false }));
  assert.equal(result.taskId, `easel_${id('c')}`);
  assert.deepEqual(result.taskData, { operation: 'clone', status: 'SUCCESS' });
  const rendered = plugin.native.created({}, { task_id: id('c'), status: 'SUCCESS', data: result.taskData });
  assert.equal(rendered.voice_task_id, id('c'));
  assert.ok(!JSON.stringify(rendered).includes('easel_task_'));
});

test('synthesis decoder requires public clone ID and namespaced allowlisted model', () => {
  const result = plugin.native.synthesize(jsonRequest({ model: 'easel-minimax-speech-2.8-hd', voice_task_id: id('b'), text: '你好', emotion: 'happy', speed: 1 }));
  assert.deepEqual(result.originTaskIds, [id('b')]);
  assert.equal(result.action, 'synthesize');
  for (const request of [
    { model: 'speech-2.8-hd', voice_task_id: id('b'), text: 'test' },
    { model: '../../v1/voice_clone', voice_task_id: id('b'), text: 'test' },
    { model: 'easel-minimax-speech-2.8-hd', voice_id: 'foreignVoice', text: 'test' },
    { model: 'easel-minimax-speech-2.8-hd', voice_task_id: id('b'), text: 'test', stream: true },
    { model: 'easel-minimax-speech-2.8-hd', voice_task_id: id('b'), text: 'x'.repeat(501) },
  ]) assert.throws(() => plugin.native.synthesize(jsonRequest(request)));
});

test('synthesis descriptor uses clone ownership and fixed nonstreaming hex request', () => {
  const ctx = context('synthesize', { model: 'easel-minimax-speech-2.8-hd', voice_task_id: id('b'), text: '你好', speed: 1, emotion: 'happy' }, { model: 'easel-minimax-speech-2.8-hd', upstreamModel: 'speech-2.8-hd', originTasks: [origin('clone', 'easel_privateVoice01', id('b'))] });
  const result = plugin.buildSubmitRequest(ctx);
  assert.equal(result.url, 'https://api.minimax.cn/v1/t2a_v2');
  assert.deepEqual(result.body.voice_setting, { voice_id: 'easel_privateVoice01', speed: 1, emotion: 'happy' });
  assert.equal(result.body.model, 'speech-2.8-hd');
  assert.equal(result.body.stream, false);
  assert.equal(result.body.output_format, 'hex');
  assert.deepEqual(result.body.audio_setting, { format: 'mp3', sample_rate: 16000, bitrate: 32000, channel: 1 });
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, upstreamModel: 'foreign-model' }), /model mapping/);
  assert.throws(() => plugin.buildSubmitRequest({ ...ctx, originTasks: [origin('upload', '123', id('b'))] }), /successful clone/);
});

test('synthesis completion requires full audio, valid hex and actual usage', () => {
  const ctx = context('synthesize');
  const result = plugin.parseSubmitResponse(ctx, success({ data: { audio: '494433', status: 2 }, extra_info: { usage_characters: 2, audio_length: 1200, audio_format: 'mp3' } }));
  assert.equal(result.taskId, id('c'));
  assert.equal(result.taskData.audio, '494433');
  assert.equal(result.taskData.usage_characters, 2);
  for (const body of [
    { data: { audio: 'abc', status: 2 }, extra_info: { usage_characters: 2 } },
    { data: { audio: 'nothex', status: 2 }, extra_info: { usage_characters: 2 } },
    { data: { audio: '494433', status: 1 }, extra_info: { usage_characters: 2 } },
    { data: { audio: '494433', status: 2 }, extra_info: {} },
  ]) assert.throws(() => plugin.parseSubmitResponse(ctx, success(body)));
});

test('HTTP 200 vendor failures and missing status are never successes', () => {
  for (const action of ['upload', 'clone', 'synthesize']) {
    assert.throws(() => plugin.parseSubmitResponse(context(action), { statusCode: 200, body: { base_resp: { status_code: 1008, status_msg: 'balance low' } } }), /MiniMax error 1008/);
    assert.throws(() => plugin.parseSubmitResponse(context(action), { statusCode: 200, body: {} }), /missing MiniMax status/);
    assert.throws(() => plugin.parseSubmitResponse(context(action), { statusCode: 401, body: {} }), /upstream HTTP 401/);
  }
});

test('public presenters return host IDs and hex without leaking private identifiers', () => {
  const upload = plugin.native.created({}, { task_id: id('a'), status: 'SUCCESS', data: { operation: 'upload', status: 'SUCCESS', purpose: 'voice_clone', file_id: 'private-upstream' } });
  assert.equal(upload.file_task_id, id('a'));
  assert.ok(!JSON.stringify(upload).includes('private-upstream'));
  const audio = plugin.native.created({}, { task_id: id('c'), status: 'SUCCESS', data: { operation: 'synthesize', status: 'SUCCESS', audio: '494433', usage_characters: 2, audio_length: 1200 } });
  assert.deepEqual(audio.data, { audio: '494433', status: 2 });
  assert.equal(audio.extra_info.usage_characters, 2);
});

test('usage separates administrative request units from actual synthesis characters', () => {
  assert.deepEqual(plugin.extractUsage(context('upload')), { upload_requests: 1 });
  assert.deepEqual(plugin.extractUsage(context('clone')), { registration_requests: 1 });
  assert.deepEqual(plugin.extractUsage(context('synthesize', { text: 'abcd' })), { characters: 4 });
  assert.deepEqual(plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, { operation: 'synthesize', usage_characters: 2, audio_length: 1200 }), { characters: 2, audio_milliseconds: 1200 });
  assert.deepEqual(plugin.extractUsageOnComplete({}, { status: 'FAILURE' }, { operation: 'clone' }), {});
  assert.throws(() => plugin.extractUsageOnComplete({}, { status: 'SUCCESS' }, { operation: 'synthesize' }), /usage_characters/);
});

test('query decoder relies on host ownership and no task can trigger polling POST', () => {
  const route = plugin.meta.routes.find((item) => item.method === 'GET');
  assert.equal(route.type, 'query');
  assert.equal(route.decode, undefined);
  assert.ok(route.path.endsWith('/:task_id'));
  assert.throws(() => plugin.buildQueryRequest({}), /immediate tasks/);
  assert.deepEqual(plugin.parseTaskResult({}, { operation: 'upload', status: 'SUCCESS' }), { status: 'SUCCESS', progress: '100%' });
  assert.deepEqual(plugin.parseTaskResult({}, { status: 'unexpected' }), { status: 'UNKNOWN', reason: 'unrecognized immediate task result' });
  assert.deepEqual(plugin.listArtifacts({}), []);
  assert.throws(() => plugin.buildContentRequest({}), /artifact_not_found/);
});

test('client queries, duplicate form fields and overrides cannot smuggle endpoints', () => {
  assert.throws(() => plugin.native.upload({ ...uploadRequest(), query: { GroupId: ['999'] } }), /query parameters/);
  assert.throws(() => plugin.native.upload(uploadRequest({ purpose: ['voice_clone', 'voice_clone'] })), /once/);
  assert.throws(() => plugin.native.clone(jsonRequest({ file_task_id: id('a'), baseUrl: 'https://evil.example' })), /unsupported field/);
  assert.throws(() => plugin.native.synthesize(jsonRequest({ model: 'easel-minimax-speech-2.8-hd', voice_task_id: id('b'), text: 'hi', speed: 0 })), /speed/);
});
