// Private CosyVoice enrollment -> owned verification -> HTTP synthesis adapter.
const synthesisModels = ['cosyvoice-v3.5-plus', 'cosyvoice-v3.5-flash', 'cosyvoice-v3-plus', 'cosyvoice-v3-flash', 'cosyvoice-v2'];
export const meta = {
  apiVersion: 1, key: 'easel-dashscope-voice', name: 'Easel DashScope CosyVoice', version: '1.0.0',
  author: { name: 'Easel' },
  description: { en: 'Owned CosyVoice enrollment, verification and synchronous speech synthesis', zh: '带归属链的 CosyVoice 登记、审核查询与同步合成' },
  baseUrl: 'https://dashscope.aliyuncs.com', models: ['voice-enrollment', ...synthesisModels],
  fetchMode: 'per_task', upstreams: ['vendor'], auth: 'api_key',
  routes: [
    { method: 'POST', path: '/easel/dashscope/voice/enroll', type: 'submit', decode: 'enroll', render: 'result' },
    { method: 'POST', path: '/easel/dashscope/voice/verify', type: 'submit', decode: 'verify', render: 'result' },
    { method: 'POST', path: '/easel/dashscope/voice/synthesize', type: 'submit', decode: 'synthesize', render: 'result' },
    { method: 'GET', path: '/easel/dashscope/voice/tasks/:task_id', type: 'query', render: 'result' },
  ],
  usageSchema: {
    count: { type: 'number', unit: 'count', unitLabel: { en: 'operation', zh: '次' }, description: { en: 'Voice operation unit price', zh: '音色操作单价' } },
    operation: { enum: ['create_voice', 'query_voice'], description: { en: 'Voice operation', zh: '音色操作' } },
  },
  usageExamples: [{ label: 'Create a voice', facts: { count: 1, operation: 'create_voice' } }, { label: 'Verify a voice', facts: { count: 1, operation: 'query_voice' } }],
  usageProfiles: [{ models: synthesisModels, schema: { characters: { type: 'number', unit: 'count', unitLabel: { en: 'character', zh: '字符' }, description: { en: 'Speech synthesis unit price', zh: '语音合成单价' } } }, examples: [{ label: 'Reported characters', facts: { characters: 6 } }] }],
};

function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function supported(model) {
  if (synthesisModels.indexOf(model) === -1) throw new Error('Unsupported CosyVoice synthesis model.');
  return model;
}
function keys(value, allowed) {
  for (const key of Object.keys(value)) if (allowed.indexOf(key) === -1) throw new Error('Unsupported voice input field; use a public voice_task_id.');
}
function json(ctx) {
  if (!ctx.body || ctx.body.kind !== 'json' || !record(ctx.body.value) || !record(ctx.body.value.input)) throw new Error('A JSON model and input object are required.');
  const value = ctx.body.value;
  if (value.stream || value.background) throw new Error('Streaming and background voice requests are not supported.');
  return value;
}
function reference(input) {
  if (typeof input.voice_task_id !== 'string' || !input.voice_task_id.trim()) throw new Error('A public voice_task_id is required.');
  return input.voice_task_id;
}
function url(value) {
  if (typeof value !== 'string' || !/^https?:\/\/[^/?#\s@]+(?:[/?#]|$)/i.test(value)) throw new Error('A valid public audio URL is required.');
  return value;
}
function enrollment(value) {
  if (value.model !== 'voice-enrollment' || value.input.action !== 'create_voice') throw new Error('Only voice-enrollment create_voice is supported on this route.');
  keys(value.input, ['action', 'target_model', 'prefix', 'url']);
  supported(value.input.target_model);
  if (typeof value.input.prefix !== 'string' || !/^[A-Za-z0-9]{1,10}$/.test(value.input.prefix)) throw new Error('The voice prefix must contain 1 to 10 letters or digits.');
  url(value.input.url);
  return { model: 'voice-enrollment', input: { action: 'create_voice', target_model: value.input.target_model, prefix: value.input.prefix, url: value.input.url } };
}
function verification(value) {
  if (value.model !== 'voice-enrollment' || value.input.action !== 'query_voice') throw new Error('Only an owned query_voice is supported on this route.');
  keys(value.input, ['action', 'voice_task_id']);
  return { model: 'voice-enrollment', input: { action: 'query_voice', voice_task_id: reference(value.input) } };
}
function synthesis(value) {
  supported(value.model);
  keys(value.input, ['text', 'voice_task_id', 'format', 'sample_rate', 'volume', 'rate', 'pitch', 'instruction']);
  reference(value.input);
  if (typeof value.input.text !== 'string' || !value.input.text.trim()) throw new Error('Non-empty synthesis text is required.');
  const input = {};
  for (const key of Object.keys(value.input)) input[key] = value.input[key];
  if (input.format !== undefined && ['mp3', 'wav', 'pcm', 'opus'].indexOf(input.format) === -1) throw new Error('Unsupported audio format.');
  if (input.sample_rate !== undefined && [8000, 12000, 16000, 22050, 24000, 44100, 48000].indexOf(input.sample_rate) === -1) throw new Error('Unsupported sample rate.');
  if (input.format === 'opus' && input.sample_rate !== undefined && [8000, 12000, 16000, 24000, 48000].indexOf(input.sample_rate) === -1) throw new Error('Unsupported Opus sample rate.');
  for (const key of ['rate', 'pitch']) if (input[key] !== undefined && (typeof input[key] !== 'number' || !Number.isFinite(input[key]) || input[key] < 0.5 || input[key] > 2)) throw new Error('Voice rate and pitch must be between 0.5 and 2.');
  if (input.volume !== undefined && (!Number.isInteger(input.volume) || input.volume < 0 || input.volume > 100)) throw new Error('Voice volume must be an integer between 0 and 100.');
  if (input.instruction !== undefined && typeof input.instruction !== 'string') throw new Error('Voice instruction must be a string.');
  return { model: value.model, input };
}
function origin(ctx, stage) {
  const refs = ctx.originTasks;
  if (!Array.isArray(refs) || refs.length !== 1) throw new Error('Exactly one owned origin voice task is required.');
  const source = refs[0];
  if (!record(source.data) || source.status !== 'SUCCESS' || source.action !== stage || source.data.stage !== stage || typeof source.upstreamTaskId !== 'string' || !source.upstreamTaskId) throw new Error('The origin voice task has not completed the required verification stage.');
  if (ctx.requestBody && ctx.requestBody.input && source.taskId !== ctx.requestBody.input.voice_task_id) throw new Error('The origin voice reference does not match this request.');
  supported(source.data.target_model);
  return source;
}
function positive(value, field) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0 || !Number.isInteger(value)) throw new Error('Positive provider usage ' + field + ' is required.');
  return value;
}
function count(data) {
  if (!record(data.usage) || data.usage.count !== 1) throw new Error('Provider usage count must equal one.');
  return 1;
}
function characters(data) { return positive(record(data.usage) ? data.usage.characters : undefined, 'characters'); }
function audio(data) { return url(record(data.output) && record(data.output.audio) ? data.output.audio.url : undefined); }
function taskResult(task) {
  const data = record(task.data) ? task.data : {};
  const output = { task_id: task.task_id, task_status: task.status === 'SUCCESS' ? 'SUCCEEDED' : task.status === 'FAILURE' ? 'FAILED' : 'UNKNOWN' };
  const result = { output };
  if (task.status !== 'SUCCESS') return result;
  if (data.stage === 'create_voice' || data.stage === 'query_voice') {
    output.voice_task_id = task.task_id;
    output.target_model = data.target_model;
    output.voice_status = data.stage === 'create_voice' ? 'UNVERIFIED' : data.voice_status;
    result.usage = { count: count(data) };
  } else if (data.stage === 'synthesize') {
    output.audio = { url: audio(data) };
    output.finish_reason = 'stop';
    result.usage = { characters: characters(data) };
  } else throw new Error('Unknown persisted voice task stage.');
  return result;
}

export const native = {
  enroll(ctx) { const body = enrollment(json(ctx)); return { kind: 'submit', model: body.model, action: 'create_voice', requestBody: body }; },
  verify(ctx) { const body = verification(json(ctx)); return { kind: 'submit', model: body.model, action: 'query_voice', requestBody: body, originTaskIds: [body.input.voice_task_id] }; },
  synthesize(ctx) { const body = synthesis(json(ctx)); return { kind: 'submit', model: body.model, action: 'synthesize', requestBody: body, originTaskIds: [body.input.voice_task_id] }; },
  result(ctx, task) { return taskResult(task); },
};

export function buildSubmitRequest(ctx) {
  const base = ctx.baseUrl.replace(/\/+$/, '').replace(/\/api\/v1$/, '');
  let body;
  let path = '/api/v1/services/audio/tts/customization';
  if (ctx.action === 'create_voice') body = enrollment(ctx.requestBody);
  else if (ctx.action === 'query_voice') {
    verification(ctx.requestBody);
    const source = origin(ctx, 'create_voice');
    body = { model: 'voice-enrollment', input: { action: 'query_voice', voice_id: source.upstreamTaskId } };
  } else if (ctx.action === 'synthesize') {
    body = synthesis(ctx.requestBody);
    const source = origin(ctx, 'query_voice');
    if (source.data.voice_status !== 'OK') throw new Error('The voice is not ready: verification must report OK.');
    if (body.model !== source.data.target_model || (ctx.upstreamModel && ctx.upstreamModel !== source.data.target_model)) throw new Error('The synthesis model must match the enrolled target model.');
    delete body.input.voice_task_id;
    body.input.voice = source.upstreamTaskId;
    path = '/api/v1/services/audio/tts/SpeechSynthesizer';
  } else throw new Error('Unsupported voice task action.');
  return { url: base + path, method: 'POST', headers: { Authorization: ctx.authHeader, 'Content-Type': 'application/json' }, body };
}

export function parseSubmitResponse(ctx, response) {
  const body = response.body;
  if (response.statusCode < 200 || response.statusCode >= 300 || !record(body) || body.code || !record(body.output)) throw new Error('The voice provider rejected the request.');
  let id;
  let data;
  if (ctx.action === 'create_voice') {
    const request = enrollment(ctx.requestBody);
    count(body);
    id = body.output.voice_id;
    if (typeof id !== 'string' || !id) throw new Error('The voice provider did not return a voice identifier.');
    data = { stage: 'create_voice', target_model: request.input.target_model, usage: { count: 1 } };
  } else if (ctx.action === 'query_voice') {
    const source = origin(ctx, 'create_voice');
    count(body);
    if (['DEPLOYING', 'OK', 'UNDEPLOYED'].indexOf(body.output.status) === -1) throw new Error('The provider returned an unknown voice review status.');
    if (body.output.target_model !== source.data.target_model) throw new Error('The provider voice target model does not match the origin.');
    id = source.upstreamTaskId;
    data = { stage: 'query_voice', target_model: source.data.target_model, voice_status: body.output.status, usage: { count: 1 } };
  } else if (ctx.action === 'synthesize') {
    if (body.output.finish_reason !== 'stop') throw new Error('The provider did not return final speech.');
    const link = audio(body);
    const used = characters(body);
    id = record(body.output.audio) && body.output.audio.id ? body.output.audio.id : body.request_id;
    if (typeof id !== 'string' || !id) throw new Error('The provider did not return an audio identifier.');
    data = { stage: 'synthesize', output: { audio: { url: link }, finish_reason: 'stop' }, usage: { characters: used } };
    if (body.output.audio.expires_at !== undefined) data.output.audio.expires_at = body.output.audio.expires_at;
  } else throw new Error('Unsupported voice task action.');
  return { taskId: id, taskData: data, immediate: { status: 'SUCCESS', progress: '100%' } };
}

export function buildQueryRequest() { throw new Error('These voice operations have no upstream polling; use the owned verify route.'); }
export function parseTaskResult() { return { status: 'UNKNOWN', reason: 'No automatic upstream polling is defined for these voice operations.' }; }
export function extractUsage() { return null; }
export function extractUsageOnComplete(ctx, result, data) {
  if (result.status !== 'SUCCESS') return null;
  const source = data === undefined ? ctx.data : data;
  const action = ctx.action || source.stage;
  if (action === 'synthesize') return { characters: characters(source) };
  if (action !== 'create_voice' && action !== 'query_voice') throw new Error('Unknown voice usage operation.');
  return { count: count(source), operation: action };
}
export function listArtifacts(task) {
  if (task.status !== 'SUCCESS' || !record(task.data) || task.data.stage !== 'synthesize') return [];
  const link = audio(task.data);
  const mimeType = /\.wav(?:[?#]|$)/i.test(link) ? 'audio/wav' : /\.opus(?:[?#]|$)/i.test(link) ? 'audio/ogg' : /\.pcm(?:[?#]|$)/i.test(link) ? 'application/octet-stream' : 'audio/mpeg';
  return [{ key: 'audio', type: 'audio', mimeType }];
}
export function buildContentRequest(ctx) {
  if (ctx.artifactKey !== 'audio' || !record(ctx.data) || ctx.data.stage !== 'synthesize') throw new Error('Unknown voice audio artifact.');
  return { url: audio(ctx.data), method: ctx.clientRequest && ctx.clientRequest.method === 'HEAD' ? 'HEAD' : 'GET', credentialless: true };
}
