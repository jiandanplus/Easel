// Private binary extension: New API owns user auth/ledger; the bridge stores
// bounded Fish audio and durable no-replay receipts on a private network.
const models = ["s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"];
const aliases = models.map(function (model) { return "easel-fish-" + model; });
const mimeTypes = { mp3: "audio/mpeg", wav: "audio/wav", pcm: "application/octet-stream", opus: "audio/opus" };
export const meta = {
  apiVersion: 1,
  key: "easel-fish-tts",
  name: "Easel Fish Private TTS",
  version: "1.0.0",
  author: { name: "Easel" },
  description: { en: "Fish binary TTS through a private, authenticated, idempotent bridge", zh: "通过私有鉴权幂等桥接收 Fish 二进制语音" },
  baseUrl: "http://fish-bridge:8787",
  allowedHosts: ["fish-bridge", "127.0.0.1", "localhost"],
  models: aliases,
  upstreams: ["vendor"],
  fetchMode: "per_task",
  usageSchema: {
    synthesis_requests: { type: "number", unit: "count", description: { en: "Observed completed synthesis requests, not supplier token usage", zh: "观察到的成功合成请求数，不是供应商 token 用量" } },
    input_characters: { type: "number", unit: "count", unitLabel: { en: "input character", zh: "输入字符" }, description: { en: "Input characters observed by the bridge; not an exact provider invoice", zh: "桥观察到的输入字符数，不代表供应商精确账单" } },
  },
  routes: [
    { method: "POST", path: "/easel/fish/v1/tts", type: "submit", models: aliases, decode: "synthesize", render: "created" },
    { method: "GET", path: "/easel/fish/v1/tasks/:task_id", type: "query", render: "created" },
  ],
};

function object(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(label + " must be an object");
  return value;
}

function keysOnly(value, keys) {
  for (const key of Object.keys(value)) if (!keys.includes(key)) throw new Error("unsupported field: " + key);
}

function publicId(value) {
  if (typeof value !== "string" || !/^task_[A-Za-z0-9]{32}$/.test(value)) throw new Error("host public task ID required");
  return value;
}

function base(ctx) {
  if (!/^http:\/\/(fish-bridge|127\.0\.0\.1|localhost):8787\/?$/.test(String(ctx.baseUrl || ""))) throw new Error("configure the private bridge URL");
  if (typeof ctx.apiKey !== "string" || !/^[A-Za-z0-9_-]{43,128}$/.test(ctx.apiKey)) throw new Error("configure a strong internal secret as channel API key");
  return String(ctx.baseUrl).replace(/\/$/, "");
}

function normalizeRequest(value) {
  const req = object(value, "request");
  keysOnly(req, ["model", "text", "format", "reference_id", "references", "speed", "stream"]);
  if (!aliases.includes(req.model)) throw new Error("unsupported private model");
  if (typeof req.text !== "string" || !req.text.trim() || req.text.length > 2000) throw new Error("text must contain 1 to 2000 characters");
  if (req.stream !== undefined && req.stream !== false) throw new Error("stream is not supported by this private task adapter");
  const format = req.format === undefined ? "mp3" : req.format;
  if (!["mp3", "wav", "pcm", "opus"].includes(format)) throw new Error("unsupported audio format");
  const speed = req.speed === undefined ? 1 : req.speed;
  if (typeof speed !== "number" || !Number.isFinite(speed) || speed < 0.5 || speed > 2) throw new Error("speed must be between 0.5 and 2");
  if ((req.reference_id !== undefined) === (req.references !== undefined)) throw new Error("choose one reference source");
  const result = { model: req.model, text: req.text, format: format, speed: speed };
  if (req.reference_id !== undefined) {
    if (typeof req.reference_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(req.reference_id)) throw new Error("invalid reference_id");
    result.reference_id = req.reference_id;
  } else {
    if (!Array.isArray(req.references) || req.references.length < 1 || req.references.length > 2) throw new Error("one or two reference samples required");
    result.references = [];
    for (const item of req.references) {
      object(item, "reference sample");
      keysOnly(item, ["audio", "text"]);
      if (typeof item.audio !== "string" || !item.audio || item.audio.length > 1398104 || item.audio.length % 4 !== 0 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(item.audio)) throw new Error("bounded base64 sample required");
      const padding = item.audio.endsWith("==") ? 2 : item.audio.endsWith("=") ? 1 : 0;
      if (item.audio.length / 4 * 3 - padding > 1048576) throw new Error("sample exceeds 1 MiB");
      if (typeof item.text !== "string" || !item.text.trim() || item.text.length > 2000) throw new Error("sample transcript is required");
      result.references.push({ audio: item.audio, text: item.text });
    }
  }
  return result;
}

function completeMetadata(body, expectedOperation) {
  object(body, "bridge result");
  if (expectedOperation && body.operation_id !== expectedOperation) throw new Error("bridge operation mismatch");
  publicId(body.operation_id);
  if (!["completed", "expired"].includes(body.state)) throw new Error("bridge result is not complete");
  if (typeof body.artifact_id !== "string" || !/^[0-9a-f]{64}$/.test(body.artifact_id)) throw new Error("invalid bridge artifact");
  if (!Object.prototype.hasOwnProperty.call(mimeTypes, body.format) || body.mime_type !== mimeTypes[body.format]) throw new Error("invalid bridge audio format");
  if (!Number.isSafeInteger(body.size) || body.size < 1 || body.size > 8388608) throw new Error("invalid bridge artifact size");
  if (!Number.isSafeInteger(body.expires_at) || body.expires_at < 1) throw new Error("invalid bridge artifact expiry");
  if (body.request_count !== 1) throw new Error("bridge request_count must be one");
  if (!Number.isSafeInteger(body.input_characters) || body.input_characters < 1 || body.input_characters > 2000) throw new Error("invalid bridge input_characters");
  return { state: body.state, operation_id: body.operation_id, artifact_id: body.artifact_id, mime_type: body.mime_type, format: body.format, size: body.size, expires_at: body.expires_at, request_count: 1, input_characters: body.input_characters };
}

export const native = {
  synthesize: function (ctx) {
    if (Object.keys(ctx.query || {}).length) throw new Error("client query parameters are not supported");
    if (!ctx.body || ctx.body.kind !== "json") throw new Error("JSON object required");
    const req = normalizeRequest(ctx.body.value);
    return { kind: "submit", model: req.model, action: "synthesize", requestBody: req };
  },
  created: function (ctx, task) {
    const response = { task_id: task.task_id, status: task.status };
    const data = task.data || {};
    if (task.status === "SUCCESS" && data.state === "completed" && data.expires_at > utils.unixNow()) {
      response.content_url = "/v1/tasks/" + publicId(task.task_id) + "/artifacts/audio/content";
      response.mime_type = data.mime_type;
      response.size = data.size;
      response.expires_at = data.expires_at;
    } else if (task.status === "SUCCESS") response.artifact_expired = true;
    return response;
  },
  error: function (ctx, error) { return { error: { code: error.code || "fish_task_error", message: "Fish private TTS operation failed" } }; },
};

export function buildSubmitRequest(ctx) {
  const origin = base(ctx);
  const req = normalizeRequest(ctx.requestBody);
  const mapped = String(ctx.upstreamModel || req.model);
  const model = aliases.includes(mapped) ? mapped.slice("easel-fish-".length) : mapped;
  if (!models.includes(model)) throw new Error("unsupported channel model mapping");
  const body = { model: model, text: req.text, format: req.format, speed: req.speed };
  if (req.reference_id !== undefined) body.reference_id = req.reference_id;
  else body.references = req.references;
  return { url: origin + "/v1/tts", method: "POST", headers: { Authorization: "Bearer " + ctx.apiKey, "Content-Type": "application/json", "Idempotency-Key": publicId(ctx.publicTaskId) }, body: body };
}

export function parseSubmitResponse(ctx, response) {
  if (![200, 202].includes(response.statusCode)) throw new Error("private bridge HTTP " + String(response.statusCode));
  const taskId = publicId(ctx.publicTaskId);
  const body = object(response.body, "bridge response");
  if (body.operation_id !== taskId) throw new Error("bridge operation mismatch");
  if (body.state === "pending") return { taskId: taskId, taskData: { state: "pending", operation_id: taskId } };
  const data = completeMetadata(body, taskId);
  return { taskId: taskId, taskData: data, immediate: { status: "SUCCESS", progress: "100%" } };
}

export function buildQueryRequest(ctx) {
  return { url: base(ctx) + "/v1/operations/" + publicId(ctx.taskId), method: "GET", headers: { Authorization: "Bearer " + ctx.apiKey } };
}

export function parseTaskResult(ctx, body) {
  if (body && body.state === "pending") return { status: "IN_PROGRESS", progress: "0%", reason: "upstream outcome unconfirmed; no automatic resubmission" };
  if (body && ["completed", "expired"].includes(body.state)) {
    completeMetadata(body, ctx.taskId || ctx.upstreamTaskId);
    return { status: "SUCCESS", progress: "100%" };
  }
  return { status: "UNKNOWN", reason: "unrecognized bridge receipt" };
}

export function listArtifacts(task) {
  const data = task.data || {};
  if (task.status !== "SUCCESS" || data.state !== "completed" || data.expires_at <= utils.unixNow()) return [];
  const value = completeMetadata(data);
  return [{ key: "audio", type: "audio", mimeType: value.mime_type }];
}

export function buildContentRequest(ctx) {
  if (ctx.artifactKey !== "audio") throw new Error("artifact_not_found");
  const data = completeMetadata(ctx.data);
  if (data.state !== "completed" || data.expires_at <= utils.unixNow()) throw new Error("artifact_not_found");
  const client = ctx.clientRequest || {};
  if (!["GET", "HEAD"].includes(client.method)) throw new Error("only GET or HEAD artifact access is supported");
  const headers = { Authorization: "Bearer " + ctx.apiKey };
  const range = (client.headers || {}).Range || (client.headers || {}).range;
  if (range !== undefined) {
    if (typeof range !== "string" || range.length > 128 || !/^bytes=[0-9]*-[0-9]*$/.test(range)) throw new Error("unsupported byte range");
    headers.Range = range;
  }
  return { url: base(ctx) + "/v1/artifacts/" + data.artifact_id, method: client.method, headers: headers };
}

export function extractUsage(ctx) {
  return { synthesis_requests: 1, input_characters: String((ctx.requestBody || {}).text || "").length };
}

export function extractUsageOnComplete(ctx, result, body) {
  if (result.status !== "SUCCESS") return {};
  const value = completeMetadata(body);
  return { synthesis_requests: 1, input_characters: value.input_characters };
}
