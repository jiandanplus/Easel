/** Easel-owned Pi core worker. No discovery, native tools, auth files or CLI. */
import { Agent } from '@earendil-works/pi-agent-core';
import { Type, createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { streamSimple as streamResponses } from '@earendil-works/pi-ai/api/openai-responses';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';

const MAX_LINE = 3 * 1024 * 1024;
const MAX_TOTAL = 8 * 1024 * 1024;
const MAX_REQUESTS = 8;
const MAX_TOOLS = 16;
const REASONING_EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

function failureStream(model, code) {
  const stream = createAssistantMessageEventStream();
  const error = { role: 'assistant', content: [], api: model.api, provider: model.provider,
    model: model.id, timestamp: Date.now(), stopReason: 'error', errorMessage: code,
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
  // This is a local validation failure, never commercial usage authority.
  stream.push({ type: 'error', reason: 'error', error });
  stream.end();
  return stream;
}

function validate(input) {
  if (!input || input.type !== 'run' || typeof input.id !== 'string' ||
      typeof input.session_id !== 'string' || !/^pi:[a-f0-9-]{36}$/.test(input.session_id) ||
      typeof input.prompt !== 'string' || !input.prompt || Buffer.byteLength(input.prompt) > 256 * 1024 ||
      typeof input.system_prompt !== 'string' || Buffer.byteLength(input.system_prompt) > 16384 ||
      typeof input.api_key !== 'string' || !input.api_key || input.api_key.length > 16384 ||
      !Array.isArray(input.messages) || input.messages.length > 256 ||
      input.messages.some(m => !m || !['user', 'assistant', 'toolResult'].includes(m.role)) ||
      Buffer.byteLength(JSON.stringify(input.messages)) > 2 * 1024 * 1024 ||
      typeof input.tools_enabled !== 'boolean') throw new Error('invalid_input');
  const media = input.media_models ?? [];
  if (!Array.isArray(media) || media.length > 100 || media.some(row =>
    !row || Object.keys(row).some(key => !['id','name','version','capability','rate','limits','parameters','test_only','readiness'].includes(key)) ||
    typeof row.id !== 'string' || !/^[\x21-\x7e]{1,240}$/.test(row.id) ||
    typeof row.name !== 'string' || !row.name || row.name.length > 240 || /[\x00-\x1f]/.test(row.name) ||
    !['image.generate','image.edit','image.variation','video.generate','music.generate','audio.transcribe','audio.speech','voice.enroll','voice.synthesize'].includes(row.capability) ||
    !row.rate || !Array.isArray(row.rate.meters) || row.rate.meters.length < 1 || row.rate.meters.length > 8 ||
    row.rate.meters.some(m => !m || Object.keys(m).some(k => !['unit','basis','numerator','denominator'].includes(k)) ||
      !['image','audio_clip','audio_ms','video_ms','unicode_scalar','registration','input_token','output_token'].includes(m.unit) ||
      !['verified_input_v1','verified_delivery_v1','provider_usage_v1'].includes(m.basis) ||
      !['numerator','denominator'].every(k => typeof m[k] === 'string' && /^[0-9]{1,19}$/.test(m[k]) && BigInt(m[k]) > 0n)) ||
    !row.limits || !Number.isInteger(row.limits.max_assets) || row.limits.max_assets < 0 || row.limits.max_assets > 32 ||
    !row.parameters || row.parameters.type !== 'object' || row.parameters.additionalProperties !== false ||
    !row.parameters.properties || Object.keys(row.parameters.properties).length > 32 ||
    JSON.stringify(row).match(/"(?:api_key|access_token|refresh_token|task_token|provider_voice_id|supplier_url|wallet|scope)"/)) ||
    new Set(media.map(row => row.id)).size !== media.length) throw new Error('invalid_media_catalog');
  const m = input.model;
  if (!m || typeof m.id !== 'string' || !m.id || m.id.length > 240 ||
      !Number.isInteger(m.maxTokens) || m.maxTokens < 16 || m.maxTokens > 4096 ||
      !Number.isInteger(m.contextWindow) || m.contextWindow < m.maxTokens + 4096 || m.contextWindow > 131072)
    throw new Error('invalid_model');
  const url = new URL(m.baseUrl);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error('invalid_endpoint');
  const efforts = m.reasoningEfforts ?? [];
  const effort = input.reasoning_effort ?? '';
  if (!Array.isArray(efforts) || efforts.length > REASONING_EFFORTS.length ||
      efforts.some(level => !REASONING_EFFORTS.includes(level)) ||
      new Set(efforts).size !== efforts.length || typeof effort !== 'string' ||
      (effort && !efforts.includes(effort))) throw new Error('invalid_reasoning');
}

/** Exported for offline SDK acceptance with an injected noncommercial stream. */
export async function runAgent(input, io, providerStream = streamResponses) {
  validate(input);
  const apiKey = input.api_key;
  input.api_key = '';
  let requests = 0, toolCount = 0, toolAttempts = 0, overflow = false, bytes = 0;
  const effort = input.reasoning_effort || '';
  // An omitted choice means supplier default. Pi would otherwise send effort:none
  // for a reasoning model in its "off" state, which is not universally supported.
  const thinkingLevelMap = Object.fromEntries(['off', ...REASONING_EFFORTS]
    .map(level => [level, input.model.reasoningEfforts?.includes(level) ? level : null]));
  const model = { ...input.model, name: input.model.id, api: 'openai-responses', provider: 'easel',
    reasoning: !!effort, thinkingLevelMap, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: { supportsDeveloperRole: false, supportsStrictMode: false } };
  const send = async value => {
    bytes += Buffer.byteLength(JSON.stringify(value));
    if (bytes > MAX_TOTAL) { overflow = true; agent.abort(); throw new Error('output_limit'); }
    await io.send(value);
  };
  const schemas = {
    list_files: Type.Object({ path: Type.Optional(Type.String({ maxLength: 4096 })) }),
    read_file: Type.Object({ path: Type.String({ maxLength: 4096 }) }),
    write_file: Type.Object({ path: Type.String({ maxLength: 4096 }), content: Type.String({ maxLength: 262144 }) }),
    run_command: Type.Object({ command: Type.String({ maxLength: 8192 }),
      timeout_seconds: Type.Optional(Type.Integer({ minimum: 1, maximum: 120 })) }),
  };
  const descriptions = {
    list_files: 'List immediate visible entries inside the workspace.',
    read_file: 'Read a permitted UTF-8 workspace file up to 256 KiB.',
    write_file: 'Write a permitted workspace file when workspace writes are enabled.',
    run_command: 'Request one explicitly approved command under the supported OS sandbox. May be denied.',
  };
  const media = input.media_models ?? [];
  if (media.length) {
    const jobId = Type.String({pattern:'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'});
    const variants = media.map(row => {
      const noPrompt = ['audio.transcribe','voice.enroll','image.variation'].includes(row.capability);
      const fields = {model:Type.Literal(row.id),
        capability:row.capability === 'image.generate' ? Type.Optional(Type.Literal(row.capability)) : Type.Literal(row.capability),
        prompt:noPrompt ? Type.Optional(Type.String({maxLength:65536})) : Type.String({minLength:1,maxLength:65536}),
        parameters:row.capability.startsWith('image.') || !(row.parameters.required?.length) ? Type.Optional(row.parameters) : row.parameters};
      if (row.limits.max_assets > 0) {
        const required = ['image.edit','image.variation','audio.transcribe','voice.enroll'].includes(row.capability);
        const assets = Type.Array(Type.String({minLength:1,maxLength:4096}), {minItems:required ? 1 : 0,
          maxItems:Math.min(row.limits.max_assets,row.limits.input_media?.max_files ?? row.limits.max_assets),uniqueItems:true});
        fields.assets = required ? assets : Type.Optional(assets);
      }
      if (row.capability === 'voice.synthesize') fields.voice_id = jobId;
      else if (row.capability === 'audio.speech') fields.voice_id = Type.Optional(jobId);
      return Type.Object(fields,{additionalProperties:false});
    });
    Object.assign(schemas, {
      create_media: Type.Union(variants,{type:'object'}),
      get_media_job: Type.Object({job_id:Type.Optional(jobId),operation_key:Type.Optional(jobId)},{additionalProperties:false}),
      cancel_media_job: Type.Object({job_id:jobId},{additionalProperties:false}),
      download_media_artifact: Type.Object({job_id:jobId,artifact_id:jobId,path:Type.String({minLength:1,maxLength:4096})},{additionalProperties:false}),
    });
    Object.assign(descriptions, {
      create_media:'Create the requested subscription media using the exact published model and capability. Image generation/edit/variation share one image per turn; each other capability has one operation per turn. Never switch model or capability to retry a failed/unknown operation. Assets must be permitted workspace file paths; voice_id must be an owned ready central voice from a completed enrollment. Available models: ' +
        media.map(row => `${row.id} (${row.capability}): ` + row.rate.meters.map(m => `${m.numerator}/${m.denominator} microcredits per ${m.unit} (${m.basis})`).join(' + ') +
          '; limits=' + JSON.stringify(row.limits)).join('; ') +
        '. Sum exact metered quantities and round up once; 1000000 microcredits = 1 point. These are rate components, not a guaranteed total. Queued/running means wait and query the same job. Save artifacts with download_media_artifact.',
      get_media_job:'Read the original owned media operation or job. This only queries and never creates another operation.',
      cancel_media_job:'Request cancellation of an owned media job. A request sent to the provider may remain pending reconciliation; this does not promise a refund.',
      download_media_artifact:'Save an original verified owned artifact to a new outputs path with its matching .png, .jpg, .webp, .mp4, .mp3, .wav, .ogg, .m4a or .json extension, at most 64 MiB. Existing files are never overwritten. Download failure must not trigger regeneration.',
    });
  }
  const tools = input.tools_enabled ? Object.entries(schemas).map(([name, parameters]) => ({
    name, label: name, description: descriptions[name], parameters, replay: 'never',
    execute: async (toolCallId, arguments_, signal) => {
      if (++toolCount > MAX_TOOLS) { overflow = true; agent.abort(); throw new Error('tool_limit'); }
      const result = await io.tool({ type: 'tool_request', id: toolCallId, name, arguments: arguments_ }, signal);
      return { content: result.content, details: result.details,
        isError: result.isError === true || result.details?.ok !== true };
    },
  })) : [];
  const agent = new Agent({
    initialState: { systemPrompt: input.system_prompt, model, thinkingLevel: effort || 'off', tools, messages: input.messages },
    toolExecution: 'sequential', transport: 'sse', identity: { attribution: null },
    streamFn: (selected, context, options) => {
      if (overflow || options?.signal?.aborted) return failureStream(selected, 'cancelled');
      if (++requests > MAX_REQUESTS) { overflow = true; return failureStream(selected, 'request_limit'); }
      // Version 1.4 embeds the system prompt/tool declarations in context.messages.
      // Forward the core AbortSignal so cancellation reaches the actual provider.
      return providerStream(selected, context, { ...options, apiKey,
        // Header waiting outlives the central (300s) and native (315s) budgets.
        maxTokens: input.model.maxTokens, maxRetries: 0, timeoutMs: 330000,
        cacheRetention: 'none', transport: 'sse' });
    },
  });
  io.onAbort(() => agent.abort());
  agent.subscribe(async event => {
    if (event.type === 'message_update') {
      const update = event.assistantMessageEvent;
      if (update?.type === 'text_delta') await send({ type: 'token', text: update.delta });
      else if (update?.type === 'thinking_delta') await send({ type: 'thinking', text: update.delta });
    } else if (event.type === 'tool_execution_start') {
      if (++toolAttempts > MAX_TOOLS) { overflow = true; agent.abort(); return; }
      await send({ type: 'activity', text: `工具：${event.toolName}` });
    }
  });
  await agent.prompt(input.prompt);
  await agent.waitForIdle();
  const final = [...agent.state.messages].reverse().find(m => m.role === 'assistant');
  const success = !overflow && !agent.state.errorMessage && final && ['stop', 'length'].includes(final.stopReason);
  if (!success) { await io.send({ type: 'error', code: overflow ? 'turn_limit' : 'provider_failed' }); return; }
  const messages = agent.state.messages.filter(m => ['user', 'assistant', 'toolResult'].includes(m.role));
  if (messages.length > 256 || Buffer.byteLength(JSON.stringify(messages)) > 2 * 1024 * 1024)
    throw new Error('history_limit');
  await send({ type: 'done', session_id: input.session_id, messages, success: true });
}

async function main() {
  let buffer = Buffer.alloc(0), running = null, started = false, abort = () => {}, stopped = false;
  let sent = 0;
  const pending = new Map();
  async function send(value) {
    if (stopped) return;
    const line = JSON.stringify(value) + '\n';
    sent += Buffer.byteLength(line);
    if (Buffer.byteLength(line) > MAX_LINE || sent > MAX_TOTAL) throw new Error('output_limit');
    if (!process.stdout.write(line)) await once(process.stdout, 'drain');
  }
  function cancel() {
    abort();
    for (const { reject } of pending.values()) reject(new Error('cancelled'));
    pending.clear();
  }
  const io = {
    send, onAbort(callback) { abort = callback; },
    tool(value, signal) {
      return new Promise((resolve, reject) => {
        if (signal.aborted || pending.has(value.id)) { reject(new Error('cancelled')); return; }
        const cancelTool = () => { pending.delete(value.id); reject(new Error('cancelled')); };
        signal.addEventListener('abort', cancelTool, { once: true });
        pending.set(value.id, { resolve: result => {
          signal.removeEventListener('abort', cancelTool); resolve(result);
        }, reject });
        send(value).catch(reject);
      });
    },
  };
  // Input buffering is explicitly LF-only, byte bounded before JSON allocation.
  process.stdin.on('data', chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    while (true) {
      const end = buffer.indexOf(10);
      if (end < 0) break;
      const raw = buffer.subarray(0, end); buffer = buffer.subarray(end + 1);
      if (raw.length > MAX_LINE) { cancel(); process.stdin.destroy(); return; }
      try {
        const value = JSON.parse(raw.toString('utf8'));
        if (!started) {
          started = true;
          running = runAgent(value, io).catch(() => send({ type: 'error', code: 'worker_failed' })).finally(() => {
            stopped = true; process.stdin.destroy();
          });
        } else if (value.type === 'tool_result' && pending.has(value.id) && value.result &&
                   Array.isArray(value.result.content) && value.result.details && typeof value.result.details.ok === 'boolean') {
          pending.get(value.id).resolve(value.result); pending.delete(value.id);
        } else { cancel(); void send({ type: 'error', code: 'invalid_protocol' }); }
      } catch { cancel(); void send({ type: 'error', code: 'invalid_protocol' }); }
    }
    if (buffer.length > MAX_LINE) { cancel(); process.stdin.destroy(); }
  });
  process.stdin.on('end', () => { if (!stopped) cancel(); });
  process.stdin.on('error', cancel);
  process.stdout.on('error', cancel);
  process.on('SIGTERM', cancel);
  process.on('SIGINT', cancel);
  // Parent also enforces its deadline and kills the owned process group.
  const timer = setTimeout(() => { cancel(); process.exitCode = 1; process.stdin.destroy(); }, 1800000);
  timer.unref();
  await once(process.stdin, 'close').catch(() => {});
  if (running) await running;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch(() => { process.stdout.write('{"type":"error","code":"worker_failed"}\n'); process.exitCode = 1; });
}
