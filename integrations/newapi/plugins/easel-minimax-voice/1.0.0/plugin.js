// Easel private MiniMax voice adapter. Each submit produces one upstream POST.
// Public dependency IDs are resolved and ownership-checked by New API rc.45.
const speechModels = ["speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd", "speech-2.6-turbo", "speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo"];
const synthesisModels = speechModels.map(function (model) { return "easel-minimax-" + model; });
export const meta = {
  apiVersion: 1,
  key: "easel-minimax-voice",
  name: "Easel MiniMax Private Voice",
  version: "1.0.0",
  author: { name: "Easel" },
  description: { en: "Private, owner-bound MiniMax sample upload, voice registration, and short nonstreaming speech", zh: "Easel 私有样本上传、归属绑定音色登记和短文本合成" },
  baseUrl: "https://api.minimax.cn",
  allowedHosts: ["api.minimax.cn", "api.minimax.io"],
  models: ["easel-minimax-upload", "easel-minimax-clone"].concat(synthesisModels),
  fetchMode: "per_task",
  upstreams: ["vendor"],
  usageSchema: {
    upload_requests: { type: "number", unit: "count", description: { en: "Successful sample-upload request units; not supplier tokens", zh: "成功上传请求单位，不是供应商 token" } },
    registration_requests: { type: "number", unit: "count", description: { en: "Successful voice-registration request units", zh: "成功登记音色请求单位" } },
    characters: { type: "number", unit: "count", unitLabel: { en: "character", zh: "字符" }, description: { en: "Requested characters at reservation; supplier usage_characters at completion", zh: "预留按请求字符估算，完成按供应商 usage_characters" } },
    audio_milliseconds: { type: "number", unit: "count", unitLabel: { en: "millisecond", zh: "毫秒" }, description: { en: "Supplier-reported audio_length in milliseconds", zh: "供应商 audio_length 毫秒数" } },
  },
  usageProfiles: [
    { models: ["easel-minimax-upload"], schema: { upload_requests: { type: "number", unit: "count" } } },
    { models: ["easel-minimax-clone"], schema: { registration_requests: { type: "number", unit: "count" } } },
    { models: synthesisModels, schema: { characters: { type: "number", unit: "count", unitLabel: { en: "character", zh: "字符" } }, audio_milliseconds: { type: "number", unit: "count", unitLabel: { en: "millisecond", zh: "毫秒" } } } },
  ],
  routes: [
    { method: "POST", path: "/easel/minimax/v1/files/upload", type: "submit", models: ["easel-minimax-upload"], decode: "upload", render: "created" },
    { method: "POST", path: "/easel/minimax/v1/voice_clone", type: "submit", models: ["easel-minimax-clone"], decode: "clone", render: "created" },
    { method: "POST", path: "/easel/minimax/v1/t2a_v2", type: "submit", models: synthesisModels, decode: "synthesize", render: "created" },
    { method: "GET", path: "/easel/minimax/v1/tasks/:task_id", type: "query", render: "created" },
  ],
};

function object(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(label + " must be an object");
  return value;
}

function keysOnly(value, keys) {
  for (const key of Object.keys(value)) if (!keys.includes(key)) throw new Error("unsupported field: " + key);
}

function noQuery(ctx) {
  if (Object.keys(ctx.query || {}).length) throw new Error("client query parameters are not supported");
}

function jsonBody(ctx) {
  noQuery(ctx);
  if (!ctx.body || ctx.body.kind !== "json") throw new Error("JSON object required");
  return object(ctx.body.value, "request body");
}

function publicId(value) {
  if (typeof value !== "string" || !/^task_[A-Za-z0-9]{32}$/.test(value)) throw new Error("a public task ID is required");
  return value;
}

function validateFile(files) {
  if (!Array.isArray(files) || files.length !== 1 || files[0].field !== "file") throw new Error("exactly one file in field file is required");
  const file = files[0];
  if (file.ref !== "request_file:file") throw new Error("host file reference is invalid");
  if (!Number.isFinite(file.size) || file.size <= 0 || file.size > 20 * 1024 * 1024) throw new Error("sample must be between 1 byte and 20 MiB");
  if (typeof file.filename !== "string" || /[\\/\r\n]/.test(file.filename) || !/\.(mp3|m4a|wav)$/i.test(file.filename)) throw new Error("sample must be mp3, m4a or wav");
  return file;
}

function cloneRequest(req) {
  keysOnly(req, ["file_task_id", "need_noise_reduction", "need_volume_normalization"]);
  const result = { file_task_id: publicId(req.file_task_id) };
  for (const key of ["need_noise_reduction", "need_volume_normalization"]) {
    if (req[key] !== undefined) {
      if (typeof req[key] !== "boolean") throw new Error(key + " must be boolean");
      result[key] = req[key];
    }
  }
  return result;
}

function synthesisRequest(req) {
  keysOnly(req, ["model", "voice_task_id", "text", "speed", "emotion", "stream"]);
  if (!synthesisModels.includes(req.model)) throw new Error("unsupported private speech model");
  if (typeof req.text !== "string" || !req.text.trim() || req.text.length > 500) throw new Error("text must contain 1 to 500 characters");
  if (req.stream !== undefined && req.stream !== false) throw new Error("stream is not supported");
  const result = { model: req.model, voice_task_id: publicId(req.voice_task_id), text: req.text };
  if (req.speed !== undefined) {
    if (typeof req.speed !== "number" || !Number.isFinite(req.speed) || req.speed < 0.5 || req.speed > 2) throw new Error("speed must be between 0.5 and 2");
    result.speed = req.speed;
  }
  if (req.emotion !== undefined) {
    if (!["happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral"].includes(req.emotion)) throw new Error("unsupported emotion");
    result.emotion = req.emotion;
  }
  return result;
}

function configuredEndpoint(ctx, path) {
  const match = /^https:\/\/(api\.minimax\.(cn|io))\/?(?:\?GroupId=([0-9]{1,32}))?$/.exec(String(ctx.baseUrl || ""));
  if (!match) throw new Error("configure an official MiniMax base URL (cn or io)");
  if (typeof ctx.apiKey !== "string" || !ctx.apiKey.trim() || /[\r\n]/.test(ctx.apiKey)) throw new Error("a valid channel API key is required");
  return "https://" + match[1] + path + (match[3] ? "?GroupId=" + match[3] : "");
}

function resolvedOrigin(ctx, expectedId, expectedAction) {
  const origins = ctx.originTasks;
  if (!Array.isArray(origins) || origins.length !== 1 || origins[0].taskId !== expectedId) throw new Error("one matching host-resolved origin is required");
  const origin = origins[0];
  if (origin.status !== "SUCCESS" || origin.action !== expectedAction || !origin.data || origin.data.operation !== expectedAction || origin.data.status !== "SUCCESS") throw new Error("origin must be a successful " + expectedAction + " task");
  return origin;
}

function safeFileId(value) {
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error("unsafe file_id: host must preserve the supplier int64");
    return String(value);
  }
  if (typeof value !== "string" || !/^[1-9][0-9]{0,18}$/.test(value) || (value.length === 19 && value > "9223372036854775807")) throw new Error("unsafe file_id: host must preserve the supplier int64");
  return value;
}

function generatedVoice(ctx) {
  return "easel_" + publicId(ctx.publicTaskId);
}

function actualCharacters(value) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 100000) throw new Error("valid supplier usage_characters is required");
  return value;
}

export const native = {
  upload: function (ctx) {
    noQuery(ctx);
    if (!ctx.body || ctx.body.kind !== "multipart") throw new Error("multipart sample required");
    const fields = object(ctx.body.fields || {}, "multipart fields");
    keysOnly(fields, ["purpose"]);
    const values = fields.purpose === undefined ? ["voice_clone"] : fields.purpose;
    if (!Array.isArray(values) || values.length !== 1) throw new Error("purpose must be provided once");
    if (values[0] !== "voice_clone") throw new Error("only voice_clone uploads are supported");
    validateFile(ctx.body.files);
    return { kind: "submit", action: "upload", model: "easel-minimax-upload", requestBody: { purpose: "voice_clone" } };
  },
  clone: function (ctx) {
    const req = cloneRequest(jsonBody(ctx));
    return { kind: "submit", action: "clone", model: "easel-minimax-clone", originTaskIds: [req.file_task_id], requestBody: req };
  },
  synthesize: function (ctx) {
    const req = synthesisRequest(jsonBody(ctx));
    return { kind: "submit", action: "synthesize", model: req.model, originTaskIds: [req.voice_task_id], requestBody: req };
  },
  created: function (ctx, task) {
    const data = task.data || {};
    const response = { task_id: task.task_id, status: task.status };
    if (task.status !== "SUCCESS") return response;
    if (data.operation === "upload") response.file_task_id = task.task_id;
    else if (data.operation === "clone") response.voice_task_id = task.task_id;
    else if (data.operation === "synthesize") {
      response.data = { audio: data.audio, status: 2 };
      response.extra_info = { usage_characters: data.usage_characters };
      if (data.audio_length !== undefined) response.extra_info.audio_length = data.audio_length;
      response.extra_info.audio_format = "mp3";
    }
    return response;
  },
  error: function (ctx, error) {
    return { error: { code: error.code || "minimax_voice_error", message: "MiniMax private voice operation failed" } };
  },
};

export function buildSubmitRequest(ctx) {
  const req = object(ctx.requestBody || {}, "request body");
  const headers = { Authorization: "Bearer " + ctx.apiKey };
  if (ctx.action === "upload") {
    keysOnly(req, ["purpose"]);
    if (req.purpose !== "voice_clone") throw new Error("only voice_clone uploads are supported");
    const file = validateFile(ctx.files);
    return { url: configuredEndpoint(ctx, "/v1/files/upload"), method: "POST", headers: headers, bodyType: "multipart", parts: [{ name: "purpose", value: "voice_clone" }, { name: "file", fileRef: file.ref, filename: file.filename }] };
  }
  headers["Content-Type"] = "application/json";
  if (ctx.action === "clone") {
    const value = cloneRequest(req);
    const origin = resolvedOrigin(ctx, value.file_task_id, "upload");
    const fileId = safeFileId(origin.upstreamTaskId);
    const fields = { voice_id: generatedVoice(ctx) };
    for (const key of ["need_noise_reduction", "need_volume_normalization"]) if (value[key] !== undefined) fields[key] = value[key];
    // A quoted int64 from the supplier can stay exact as a JSON numeric
    // literal. The host writes string bodies unchanged; fileId is digits only.
    const body = Number.isSafeInteger(Number(fileId)) ? Object.assign({ file_id: Number(fileId) }, fields) : '{"file_id":' + fileId + ',' + JSON.stringify(fields).slice(1);
    return { url: configuredEndpoint(ctx, "/v1/voice_clone"), method: "POST", headers: headers, body: body };
  }
  if (ctx.action === "synthesize") {
    const value = synthesisRequest(req);
    const origin = resolvedOrigin(ctx, value.voice_task_id, "clone");
    const voice = String(origin.upstreamTaskId || "");
    if (!/^easel_[A-Za-z0-9_-]{8,128}$/.test(voice)) throw new Error("invalid private origin voice ID");
    const selected = String(ctx.upstreamModel || value.model);
    const model = synthesisModels.includes(selected) ? selected.slice("easel-minimax-".length) : selected;
    if (!speechModels.includes(model)) throw new Error("unsupported channel model mapping");
    const setting = { voice_id: voice, speed: value.speed === undefined ? 1 : value.speed };
    if (value.emotion !== undefined) setting.emotion = value.emotion;
    return {
      url: configuredEndpoint(ctx, "/v1/t2a_v2"), method: "POST", headers: headers,
      body: { model: model, text: value.text, stream: false, output_format: "hex", voice_setting: setting, audio_setting: { format: "mp3", sample_rate: 16000, bitrate: 32000, channel: 1 } },
    };
  }
  throw new Error("unsupported voice operation");
}

export function parseSubmitResponse(ctx, response) {
  if (!Number.isInteger(response.statusCode) || response.statusCode < 200 || response.statusCode >= 300) throw new Error("upstream HTTP " + String(response.statusCode));
  const body = object(response.body, "MiniMax response");
  if (!body.base_resp || !Number.isInteger(body.base_resp.status_code)) throw new Error("missing MiniMax status");
  if (body.base_resp.status_code !== 0) throw new Error("MiniMax error " + body.base_resp.status_code);
  if (body.input_sensitive === true || Number(body.input_sensitive_type || 0) > 0) throw new Error("MiniMax rejected sensitive input");
  const data = { operation: ctx.action, status: "SUCCESS" };
  let upstreamId;
  if (ctx.action === "upload") {
    const file = object(body.file, "uploaded file");
    upstreamId = safeFileId(file.file_id);
    if (file.purpose !== undefined && file.purpose !== "voice_clone") throw new Error("unexpected upload purpose");
    data.purpose = "voice_clone";
  } else if (ctx.action === "clone") {
    upstreamId = generatedVoice(ctx);
  } else if (ctx.action === "synthesize") {
    const audio = object(body.data, "audio data");
    if (audio.status !== 2) throw new Error("incomplete nonstreaming audio");
    if (typeof audio.audio !== "string" || !audio.audio || audio.audio.length > 900000 || audio.audio.length % 2 !== 0 || !/^[0-9a-f]+$/i.test(audio.audio)) throw new Error("valid bounded hex audio is required");
    const extra = object(body.extra_info, "extra_info");
    data.audio = audio.audio;
    data.usage_characters = actualCharacters(extra.usage_characters);
    if (extra.audio_length !== undefined) {
      if (typeof extra.audio_length !== "number" || !Number.isFinite(extra.audio_length) || extra.audio_length < 0) throw new Error("invalid supplier audio_length");
      data.audio_length = extra.audio_length;
    }
    upstreamId = publicId(ctx.publicTaskId);
  } else throw new Error("unsupported voice operation");
  return { taskId: upstreamId, taskData: data, immediate: { status: "SUCCESS", progress: "100%" } };
}

export function extractUsage(ctx) {
  if (ctx.action === "upload") return { upload_requests: 1 };
  if (ctx.action === "clone") return { registration_requests: 1 };
  if (ctx.action === "synthesize") return { characters: String((ctx.requestBody || {}).text || "").length };
  throw new Error("unsupported voice operation");
}

export function extractUsageOnComplete(ctx, result, body) {
  if (result.status !== "SUCCESS") return {};
  if (body.operation === "upload") return { upload_requests: 1 };
  if (body.operation === "clone") return { registration_requests: 1 };
  if (body.operation !== "synthesize") throw new Error("unrecognized immediate operation");
  const facts = { characters: actualCharacters(body.usage_characters) };
  if (body.audio_length !== undefined) {
    if (typeof body.audio_length !== "number" || !Number.isFinite(body.audio_length) || body.audio_length < 0) throw new Error("invalid supplier audio_length");
    facts.audio_milliseconds = body.audio_length;
  }
  return facts;
}

export function buildQueryRequest() {
  throw new Error("immediate tasks have no upstream polling operation");
}

export function parseTaskResult(ctx, body) {
  if (body && ["upload", "clone", "synthesize"].includes(body.operation) && body.status === "SUCCESS") return { status: "SUCCESS", progress: "100%" };
  return { status: "UNKNOWN", reason: "unrecognized immediate task result" };
}

export function listArtifacts() { return []; }
export function buildContentRequest() { throw new Error("artifact_not_found"); }
