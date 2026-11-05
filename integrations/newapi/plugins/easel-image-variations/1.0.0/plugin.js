// Native variations transport for explicitly configured compatible providers.
// This adapter does not add a variations capability to an upstream model.
export const meta = {
  apiVersion: 1, key: 'easel-image-variations', name: 'Easel Image Variations',
  version: '1.0.0', author: { name: 'Easel' },
  models: ['easel-image-variations'], fetchMode: 'per_task',
  auth: 'api_key', upstreams: ['vendor'],
  routes: [
    { method: 'POST', path: '/easel/openai/v1/images/variations', type: 'submit', decode: 'create', render: 'result' },
    { method: 'GET', path: '/easel/openai/v1/images/tasks/:task_id', type: 'query', render: 'result' },
  ],
  usageSchema: { images: { type: 'number', unit: 'count', description: { en: 'Returned images', zh: '实际返回图片数' } } },
};

function options(body) {
  const n = body.n === undefined ? 1 : Number(body.n);
  const size = body.size || '1024x1024';
  const format = body.response_format || 'url';
  if (!Number.isInteger(n) || n < 1 || n > 10) throw new Error('n must be an integer from 1 to 10');
  if (!['256x256', '512x512', '1024x1024'].includes(size)) throw new Error('unsupported image size');
  if (!['url', 'b64_json'].includes(format)) throw new Error('unsupported response_format');
  return { n: n, size: size, response_format: format };
}

function image(files) {
  if (!Array.isArray(files) || files.length !== 1) throw new Error('one PNG image is required');
  const f = files[0];
  if (f.field !== 'image' || f.mimeType !== 'image/png' || f.size <= 0 || f.size > 4194304 || !f.ref) {
    throw new Error('image must be a PNG of at most 4 MiB');
  }
  return f;
}

function result(body) {
  if (!body || !Array.isArray(body.data) || !body.data.length || body.data.length > 10 || body.error) {
    throw new Error('upstream returned no usable images');
  }
  const images = body.data.map(function (item) {
    if (!item || typeof item !== 'object') throw new Error('upstream returned no usable images');
    const out = {};
    if (typeof item.url === 'string' && /^https:\/\/[^\s@]+$/i.test(item.url)) out.url = item.url;
    if (typeof item.b64_json === 'string' && item.b64_json.length > 0 && /^[A-Za-z0-9+/]+={0,2}$/.test(item.b64_json)) out.b64_json = item.b64_json;
    if (!out.url && !out.b64_json) throw new Error('upstream returned no usable images');
    if (typeof item.revised_prompt === 'string') out.revised_prompt = item.revised_prompt;
    return out;
  });
  const out = { data: images };
  if (Number.isFinite(body.created)) out.created = body.created;
  return out;
}

export const native = {
  create: function (ctx) {
    const body = ctx.body;
    if (!body || body.kind !== 'multipart') throw new Error('multipart input is required');
    image(body.files);
    const fields = body.fields || {};
    const input = {};
    Object.keys(fields).forEach(function (key) {
      if (!['model', 'n', 'size', 'response_format', 'user'].includes(key)) throw new Error('unsupported input field');
      if (!Array.isArray(fields[key]) || fields[key].length !== 1) throw new Error('duplicate or invalid input field');
      input[key] = fields[key][0];
    });
    if (input.model !== 'easel-image-variations') throw new Error('explicit variations model is required');
    return { kind: 'submit', model: input.model, action: 'variations', requestBody: options(input) };
  },
  result: function (ctx, task) {
    if (!task || task.status !== 'SUCCESS') throw new Error('variation task is not successful');
    return result(task.data);
  },
};

export function buildSubmitRequest(ctx) {
  if (!/^https:\/\/[^\s?#@]+$/i.test(ctx.baseUrl || '')) throw new Error('invalid channel base URL');
  if (!ctx.upstreamModel) throw new Error('channel upstream model mapping is required');
  const input = options(ctx.requestBody || {});
  return {
    url: ctx.baseUrl.replace(/\/$/, '').replace(/\/v1$/, '') + '/v1/images/variations',
    method: 'POST', headers: { Authorization: ctx.authHeader }, bodyType: 'multipart',
    parts: [
      { name: 'image', fileRef: image(ctx.files).ref },
      { name: 'model', value: ctx.upstreamModel },
      { name: 'n', value: String(input.n) },
      { name: 'size', value: input.size },
      { name: 'response_format', value: input.response_format },
    ],
  };
}

export function parseSubmitResponse(ctx, response) {
  if (response.statusCode < 200 || response.statusCode >= 300) throw new Error('variations upstream request failed');
  const data = result(response.body);
  return { taskId: ctx.publicTaskId, taskData: data, immediate: { status: 'SUCCESS', progress: '100%' } };
}
export function buildQueryRequest() { throw new Error('variations are immediate tasks; no upstream polling'); }
export function parseTaskResult(ctx, body) {
  try { result(body); return { status: 'SUCCESS', progress: '100%' }; }
  catch { return { status: 'UNKNOWN' }; }
}
// Request count is a reservation estimate. The submit hook replaces it with
// the number of images actually returned, rather than charging requested n.
export function extractUsage(ctx) { return { images: options(ctx.requestBody || {}).n }; }
export function extractUsageOnSubmit(ctx, data) { return { images: result(data).data.length }; }
export function extractUsageOnComplete(ctx, normalized, data) {
  if (normalized.status !== 'SUCCESS') return null;
  return { images: result(data).data.length };
}
