// Private Easel adapter for the documented Fun-Music non-streaming API.
// The New API host owns authentication, task ownership, HTTP and settlement.
export const meta = {
  apiVersion: 1,
  key: 'easel-dashscope-music',
  name: 'Easel DashScope Fun-Music',
  version: '1.0.0',
  author: { name: 'Easel' },
  description: { en: 'Fun-Music synchronous audio generation with host-managed task records', zh: 'Fun-Music 同步音乐生成及宿主任务记录' },
  baseUrl: 'https://dashscope.aliyuncs.com',
  models: ['fun-music-v1', 'fun-music-preview'],
  fetchMode: 'per_task',
  upstreams: ['vendor'],
  auth: 'api_key',
  routes: [
    { method: 'POST', path: '/easel/dashscope/api/v1/services/audio/music/generation', type: 'submit', decode: 'create', render: 'created' },
    { method: 'GET', path: '/easel/dashscope/api/v1/tasks/:task_id', type: 'query', render: 'status' },
  ],
  usageSchema: {
    duration_seconds: { type: 'number', unit: 'second', description: { en: 'Music generation unit price', zh: '音乐生成单价' } },
  },
  usageExamples: [{ label: 'Reported duration', facts: { duration_seconds: 123 } }],
};

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function supportedModel(model) {
  if (meta.models.indexOf(model) === -1) throw new Error('Unsupported Fun-Music model.');
  return model;
}

function normalize(value) {
  if (!record(value) || !record(value.input)) throw new Error('A JSON model and input object are required.');
  if (value.stream || value.background) throw new Error('Streaming and background requests are not supported by this plugin.');
  if (value.parameters !== undefined) throw new Error('Legacy parameters and duration are not supported by Fun-Music.');
  const model = supportedModel(value.model);
  const input = {};
  const allowed = ['prompt', 'lyrics', 'is_instrumental', 'gender', 'format', 'enable_aigc_watermark'];
  for (const key of Object.keys(value.input)) {
    if (allowed.indexOf(key) === -1) throw new Error('Unsupported Fun-Music input field.');
    input[key] = value.input[key];
  }
  if (input.prompt !== undefined && (typeof input.prompt !== 'string' || input.prompt.length < 1 || input.prompt.length > 2000)) throw new Error('The prompt must contain 1 to 2000 characters.');
  if (input.lyrics !== undefined && (typeof input.lyrics !== 'string' || input.lyrics.length < 5 || input.lyrics.length > 2000)) throw new Error('Lyrics must contain 5 to 2000 characters; provider language limits also apply.');
  if (!input.prompt && !input.lyrics) throw new Error('A prompt or lyrics are required.');
  if (model === 'fun-music-preview' && !input.prompt) throw new Error('The preview model requires a prompt.');
  for (const key of ['is_instrumental', 'enable_aigc_watermark']) {
    if (input[key] !== undefined && typeof input[key] !== 'boolean') throw new Error('Fun-Music switches must be boolean values.');
  }
  if (input.gender !== undefined && (model !== 'fun-music-v1' || (input.gender !== 'male' && input.gender !== 'female'))) throw new Error('The selected model does not support this gender.');
  if (input.format !== undefined && input.format !== 'mp3' && input.format !== 'wav') throw new Error('The audio format must be mp3 or wav.');
  return { model, input };
}

function audioUrl(data) {
  const url = record(data) && record(data.output) && record(data.output.audio) ? data.output.audio.url : undefined;
  if (typeof url !== 'string' || !/^https?:\/\/[^/?#\s@]+(?:[/?#]|$)/i.test(url)) throw new Error('The provider did not return a valid audio URL.');
  return url;
}

function duration(data) {
  const seconds = record(data) && record(data.usage) ? data.usage.duration : undefined;
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) throw new Error('A positive provider usage duration is required.');
  return seconds;
}

function completion(data) {
  if (!record(data) || data.code) throw new Error('The music provider rejected the request.');
  if (!record(data.output) || data.output.finish_reason !== 'stop') throw new Error('The provider did not return a final music result.');
  const url = audioUrl(data);
  const seconds = duration(data);
  return { status: 'SUCCESS', progress: '100%', url, duration: seconds };
}

function nativeResult(task) {
  const data = record(task.data) ? task.data : {};
  const states = { SUCCESS: 'SUCCEEDED', FAILURE: 'FAILED', NOT_START: 'PENDING', SUBMITTED: 'PENDING', QUEUED: 'PENDING', IN_PROGRESS: 'RUNNING' };
  const output = { task_id: task.task_id, task_status: states[task.status] || 'UNKNOWN' };
  const response = { output };
  if (task.status === 'SUCCESS') {
    completion(data);
    output.audio = { url: audioUrl(data) };
    if (data.output.audio.expires_at !== undefined) output.audio.expires_at = data.output.audio.expires_at;
    output.finish_reason = 'stop';
    response.usage = { duration: duration(data) };
  }
  return response;
}

export const native = {
  create(ctx) {
    if (!ctx.body || ctx.body.kind !== 'json') throw new Error('A JSON request body is required.');
    const body = normalize(ctx.body.value);
    return { kind: 'submit', model: body.model, action: 'generate', requestBody: body };
  },
  created(ctx, task) { return nativeResult(task); },
  status(ctx, task) { return nativeResult(task); },
};

export function buildSubmitRequest(ctx) {
  const body = normalize(ctx.requestBody);
  body.model = supportedModel(ctx.upstreamModel || body.model);
  const base = ctx.baseUrl.replace(/\/+$/, '').replace(/\/api\/v1$/, '');
  return {
    url: base + '/api/v1/services/audio/music/generation',
    method: 'POST',
    headers: { Authorization: ctx.authHeader, 'Content-Type': 'application/json' },
    body,
  };
}

export function parseSubmitResponse(ctx, response) {
  if (response.statusCode < 200 || response.statusCode >= 300) throw new Error('The music provider rejected the request.');
  const data = response.body;
  const result = completion(data);
  const id = data.output.audio.id || data.request_id;
  if (typeof id !== 'string' || !id) throw new Error('The provider did not return an audio or request identifier.');
  const audio = { url: result.url };
  if (data.output.audio.expires_at !== undefined) audio.expires_at = data.output.audio.expires_at;
  const taskData = { output: { audio, finish_reason: 'stop' }, usage: { duration: result.duration } };
  return { taskId: id, taskData, immediate: { status: 'SUCCESS', progress: '100%', url: result.url } };
}

export function buildQueryRequest() {
  throw new Error('Fun-Music has no upstream polling endpoint; successful submissions complete immediately.');
}

export function parseTaskResult(ctx, body, response) {
  if (response && response.status >= 500) return { status: 'UNKNOWN', reason: 'The music provider is unavailable.' };
  if (record(body) && body.code) return { status: 'FAILURE', reason: 'The music provider rejected the task.' };
  try {
    const result = completion(body);
    return { status: result.status, progress: result.progress, url: result.url };
  } catch (_) {
    return { status: 'UNKNOWN', reason: 'The provider did not return a verifiable final music result.' };
  }
}

// There is no documented request-side duration limit. Unknown is not zero usage.
// The operator must configure and verify host admission/reservation separately.
export function extractUsage() { return null; }

export function extractUsageOnComplete(ctx, result, data) {
  if (result.status !== 'SUCCESS') return null;
  return { duration_seconds: duration(data === undefined ? ctx.data : data) };
}

export function listArtifacts(task) {
  if (task.status !== 'SUCCESS') return [];
  audioUrl(task.data);
  const isWav = /\.wav(?:[?#]|$)/i.test(task.data.output.audio.url);
  return [{ key: 'audio', type: 'audio', mimeType: isWav ? 'audio/wav' : 'audio/mpeg' }];
}

export function buildContentRequest(ctx) {
  if (ctx.artifactKey !== 'audio') throw new Error('Unknown music artifact.');
  const method = ctx.clientRequest && ctx.clientRequest.method === 'HEAD' ? 'HEAD' : 'GET';
  return { url: audioUrl(ctx.data), method, credentialless: true };
}
