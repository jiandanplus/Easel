import { useState } from 'react';
import { ConsoleError, formatDate, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';

export type ChannelId = 'chat' | 'transcribe' | 'speech' | 'image' | 'video' | 'music';
export const CHANNELS: { id: ChannelId; label: string; capabilities: string[]; hint: string }[] = [
  { id: 'chat', label: '对话与脚本', capabilities: ['text'], hint: '地址要支持 OpenAI Responses 接口（/responses），New API 网关支持。' },
  { id: 'transcribe', label: '语音转写', capabilities: ['audio.transcribe'], hint: '调用 /audio/transcriptions。' },
  { id: 'speech', label: '配音', capabilities: ['audio.speech', 'voice.synthesize', 'voice.enroll'], hint: '调用 /audio/speech。' },
  { id: 'image', label: '生图', capabilities: ['image.generate', 'image.edit', 'image.variation'], hint: '调用 /images/generations。' },
  { id: 'video', label: '视频', capabilities: ['video.generate'], hint: '调用 /videos，使用 New API 网关的视频格式。' },
  { id: 'music', label: '音乐', capabilities: ['music.generate'], hint: '调用 /suno/submit/music，使用 New API 网关的 Suno 格式。' },
];
export const channelOf = (capability: string): ChannelId | undefined => CHANNELS.find(c => c.capabilities.includes(capability))?.id;

export type ChannelState = {
  channel: ChannelId; server_base_url: string | null;
  config: { id: string; base_url: string; key_hint: string | null; options: RecordData; updated_at: string } | null;
  status: { state: string; models: string[]; checked_at: string } | null;
};

const STATES: Record<string, [string, string]> = {
  ok: ['positive', '连接正常'],
  auth_failed: ['warning', 'Key 无效或没有权限'],
  unreachable: ['warning', '连不上这个地址'],
  no_model_list: ['neutral', '接口不提供模型列表，调用时才能确认'],
  invalid: ['warning', '设置没有通过校验，未生效'],
};
export function statusBadge(item: ChannelState): [string, string] {
  if (!item.config) return item.server_base_url ? ['neutral', '使用服务器配置'] : ['neutral', '未配置'];
  if (!item.config.key_hint) return ['neutral', item.server_base_url ? 'Key 已清除，使用服务器配置' : 'Key 已清除'];
  if (!item.status) return ['neutral', '检测中…'];
  const found = STATES[item.status.state] ?? ['neutral', item.status.state];
  return item.status.state === 'ok' ? [found[0], `${found[1]} · 可用模型 ${item.status.models.length} 个`] : found;
}

export function parseList(text: string): string[] {
  return [...new Set(text.split(/[\s,，、]+/).map(v => v.trim()).filter(Boolean))];
}
type Draft = { mode: string; voices: string; origins: string; prefixes: string };
export function buildOptions(channel: ChannelId, draft: Draft): RecordData {
  if (channel === 'speech') {
    const voices = parseList(draft.voices);
    if (!voices.length) throw new Error('请至少填写一个可选音色');
    return { voices };
  }
  if (channel === 'image') return { mode: draft.mode, download_origins: parseList(draft.origins) };
  if (channel === 'music') {
    const origins = parseList(draft.origins), prefixes = parseList(draft.prefixes);
    if (!origins.length || !prefixes.length) throw new Error('请填写音频下载域名和路径前缀');
    return { download_origins: origins, download_path_prefixes: prefixes };
  }
  return {};
}

const FAILURES: Record<string, string> = {
  console_provider_busy: '这个接口还有正在进行的调用，等它们结束后再改地址或 Key。',
  console_provider_key_required: '第一次保存或修改 API 地址时，需要填写 API Key。',
  console_provider_invalid: 'API 地址、Key 或选项不正确。地址必须以 https:// 开头，不能是内网地址；Key 不能含空格或中文。',
  console_provider_key_unconfigured: '服务器还没有配置密钥加密，暂时不能保存 Key。',
};
const words = (value: unknown) => Array.isArray(value) ? value.join('\n') : '';

export function ProviderConfigForm({ client, item, sealingReady, onFailure, onDone }: { client: ConsoleClient; item: ChannelState; sealingReady: boolean; onFailure: (e: unknown) => string; onDone: (m: string) => void }) {
  const spec = CHANNELS.find(c => c.id === item.channel)!, config = item.config, options = config?.options ?? {};
  const [url, setUrl] = useState(config?.base_url ?? ''), [key, setKey] = useState(''), [reason, setReason] = useState('');
  const [mode, setMode] = useState(String(options.mode ?? 'gateway')), [voices, setVoices] = useState(words(options.voices));
  const [origins, setOrigins] = useState(words(options.download_origins)), [prefixes, setPrefixes] = useState(words(options.download_path_prefixes));
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [submission, setSubmission] = useState(() => new Submission());
  const [badge, text] = statusBadge(item), saved = Boolean(config?.key_hint);
  const send = async (intent: 'save' | 'clear' | 'recheck') => {
    if (busy) return; setError('');
    try {
      const why = intent === 'recheck' ? '重新检测' : validReason(reason);
      if (intent !== 'clear' && !url.trim()) throw new Error('请填写 API 地址');
      if (intent === 'save' && !saved && !key.trim()) throw new Error('请填写 API Key');
      const body = { base_url: url.trim(), key: intent === 'save' && key.trim() ? key.trim() : null, clear: intent === 'clear',
        options: buildOptions(item.channel, { mode, voices, origins, prefixes }), reason: why };
      setBusy(true);
      await submission.send(client, `/api/provider-configs/${item.channel}`, body, 'providers:manage');
      setKey(''); setReason(''); setSubmission(new Submission());
      onDone(intent === 'clear' ? 'Key 已清除。' : intent === 'recheck' ? '已重新检测，几秒后更新结果。' : '已保存，几秒后生效并显示检测结果，不用重启。');
    } catch (cause) {
      const code = cause instanceof ConsoleError ? cause.code : '';
      setError(FAILURES[code] ?? (cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'));
    } finally { setBusy(false); }
  };
  return <section className="panel provider-form"><form onSubmit={e => { e.preventDefault(); void send('save'); }}>
    <div className="provider-head"><h2>{spec.label}接口</h2><span className={`badge ${badge}`}>{text}</span></div>
    {!config?.key_hint && item.server_base_url && <p className="muted">现在用的是服务器配置的接口：<code>{item.server_base_url}</code>。在这里保存后，本页所有模型都改走这里的地址和 Key；清除 Key 后恢复服务器配置。</p>}
    {!sealingReady && <p className="notice error">服务器还没有配置密钥加密，暂时不能保存 Key。</p>}
    <label className="provider-row"><span>API 地址</span><input value={url} disabled={busy} maxLength={512} onChange={e => setUrl(e.target.value)} placeholder="https://provider.example/v1" /></label>
    <label className="provider-row"><span>API Key</span><input type="password" autoComplete="off" value={key} disabled={busy} maxLength={4096} onChange={e => setKey(e.target.value)} placeholder={saved ? `已保存 ····${config?.key_hint}，留空则不修改` : '请输入 Key'} /></label>
    {item.channel === 'speech' && <label className="provider-row"><span>可选音色</span><textarea rows={2} value={voices} disabled={busy} onChange={e => setVoices(e.target.value)} placeholder="一行一个或用逗号分隔，例如 alloy" /></label>}
    {item.channel === 'image' && <label className="provider-row"><span>接口类型</span><select value={mode} disabled={busy} onChange={e => setMode(e.target.value)}><option value="gateway">New API 网关（只生图）</option><option value="openai">OpenAI 官方格式（生图和改图）</option></select></label>}
    {(item.channel === 'image' || item.channel === 'music') && <label className="provider-row"><span>{item.channel === 'image' ? '图片下载域名' : '音频下载域名'}</span><textarea rows={2} value={origins} disabled={busy} onChange={e => setOrigins(e.target.value)} placeholder={item.channel === 'image' ? '接口返回图片链接时填写，例如 https://cdn.example.com' : '例如 https://cdn.example.com'} /></label>}
    {item.channel === 'music' && <label className="provider-row"><span>下载路径前缀</span><textarea rows={2} value={prefixes} disabled={busy} onChange={e => setPrefixes(e.target.value)} placeholder="例如 /audio/" /></label>}
    <label className="provider-row"><span>修改原因</span><input value={reason} disabled={busy} maxLength={255} onChange={e => setReason(e.target.value)} placeholder="保存或清除时必填" /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="provider-actions">
      <button type="submit" className="button primary" disabled={busy || !sealingReady && Boolean(key) || submission.conflict}>{busy ? '提交中…' : '保存'}</button>
      <button type="button" className="button" disabled={busy || !saved} onClick={() => void send('clear')}>清除 Key</button>
      <button type="button" className="button" disabled={busy || !saved || url.trim() !== config?.base_url} onClick={() => void send('recheck')}>重新检测</button>
      <span className="muted provider-hint">{spec.hint}{item.status && ` 上次检测：${formatDate(item.status.checked_at)}`}</span>
    </div>
  </form></section>;
}
