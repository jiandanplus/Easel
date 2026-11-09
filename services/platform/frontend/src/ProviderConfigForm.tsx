import { useState } from 'react';
import { ConsoleError, formatDate, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';

export type ChannelId = 'chat' | 'transcribe' | 'speech' | 'image' | 'video' | 'music';
export const CHANNELS: { id: ChannelId; label: string; capabilities: string[] }[] = [
  { id: 'chat', label: '对话与脚本', capabilities: ['text'] },
  { id: 'transcribe', label: '语音转写', capabilities: ['audio.transcribe'] },
  { id: 'speech', label: '配音', capabilities: ['audio.speech', 'voice.synthesize', 'voice.enroll'] },
  { id: 'image', label: '生图', capabilities: ['image.generate', 'image.edit', 'image.variation'] },
  { id: 'video', label: '视频', capabilities: ['video.generate'] },
  { id: 'music', label: '音乐', capabilities: ['music.generate'] },
];
export const channelOf = (capability: string): ChannelId | undefined => CHANNELS.find(c => c.capabilities.includes(capability))?.id;

export type Field = { key: string; label: string; required: boolean; list: boolean; placeholder: string };
export type PlatformState = {
  id: string; label: string; default_url: string; hint: string; key_label: string; second_key: string | null; fields: Field[]; lists_models: boolean;
  config: { id: string; base_url: string; key_hint: string | null; options: RecordData; updated_at: string } | null;
  status: { state: string; models: string[]; checked_at: string } | null;
};
export type ChannelState = { channel: ChannelId; server_base_url: string | null; platforms: PlatformState[] };

const STATES: Record<string, [string, string]> = {
  ok: ['positive', '连接正常'],
  auth_failed: ['warning', 'Key 无效或没有权限'],
  unreachable: ['warning', '连不上这个地址'],
  no_model_list: ['neutral', '已保存，这个平台不提供模型列表，调用时才能确认'],
  invalid: ['warning', '设置没有通过校验，未生效'],
};
const GATEWAY = 'gateway';
export function statusBadge(item: PlatformState, serverBaseUrl: string | null): [string, string] {
  const usesServer = item.id === GATEWAY && Boolean(serverBaseUrl);
  if (!item.config) return usesServer ? ['neutral', '使用服务器配置'] : ['neutral', '未配置'];
  if (!item.config.key_hint) return ['neutral', usesServer ? 'Key 已清除，使用服务器配置' : 'Key 已清除'];
  if (!item.status) return ['neutral', '检测中…'];
  const found = STATES[item.status.state] ?? ['neutral', item.status.state];
  return item.status.state === 'ok' ? [found[0], `${found[1]} · 可用模型 ${item.status.models.length} 个`] : found;
}
export const isConfigured = (item: PlatformState) => Boolean(item.config?.key_hint);
export const initialPlatform = (channel: ChannelState) => (channel.platforms.find(isConfigured) ?? channel.platforms[0]).id;
export function providerLabel(channel: ChannelState | undefined, providerId: string): string {
  if (!providerId.startsWith('console-')) return `服务器配置 · ${providerId}`;
  const raw = channel ? providerId.slice(`console-${channel.channel}`.length + 1) : '';
  const clone = raw.endsWith('-clone'), suffix = clone ? raw.slice(0, -6) : raw;
  return `后台配置 · ${channel?.platforms.find(p => p.id === (suffix || GATEWAY))?.label ?? providerId}${clone ? ' · 声音复刻' : ''}`;
}

export function parseList(text: string): string[] {
  return [...new Set(text.split(/[\s,，、]+/).map(v => v.trim()).filter(Boolean))];
}
export function buildOptions(fields: Field[], draft: Record<string, string>): RecordData {
  const options: RecordData = {};
  for (const field of fields) {
    const value = field.list ? parseList(draft[field.key] ?? '') : (draft[field.key] ?? '').trim();
    if (field.required && !value.length) throw new Error(`请填写${field.label}`);
    options[field.key] = value;
  }
  return options;
}
export function joinKey(first: string, second: string, needsSecond: boolean): string | null {
  const a = first.trim(), b = second.trim();
  if (!needsSecond) return a || null;
  if (!a && !b) return null;
  if (!a || !b) throw new Error('两个 Key 都要填写');
  return `${a}|${b}`;
}

const FAILURES: Record<string, string> = {
  console_provider_busy: '这一类还有正在进行的调用，等它们结束后再改地址或 Key。',
  console_provider_key_required: '第一次保存或修改 API 地址时，需要填写 Key。',
  console_provider_invalid: 'API 地址、Key 或选项不正确。地址必须以 https:// 开头，不能是内网地址；Key 不能含空格或中文。',
  console_provider_key_unconfigured: '服务器还没有配置密钥加密，暂时不能保存 Key。',
};
const initialDraft = (item: PlatformState) => Object.fromEntries(item.fields.map(field => {
  const value = item.config?.options?.[field.key];
  return [field.key, Array.isArray(value) ? value.join('\n') : typeof value === 'string' ? value : ''];
}));

export function ProviderConfigForm({ client, channel, item, sealingReady, onPlatform, onFailure, onDone }: { client: ConsoleClient; channel: ChannelState; item: PlatformState; sealingReady: boolean; onPlatform: (id: string) => void; onFailure: (e: unknown) => string; onDone: (m: string) => void }) {
  const config = item.config, saved = isConfigured(item), page = CHANNELS.find(c => c.id === channel.channel)!;
  const [url, setUrl] = useState(config?.base_url ?? item.default_url), [key, setKey] = useState(''), [key2, setKey2] = useState(''), [reason, setReason] = useState('');
  const [draft, setDraft] = useState<Record<string, string>>(() => initialDraft(item));
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [submission, setSubmission] = useState(() => new Submission());
  const [badge, text] = statusBadge(item, channel.server_base_url);
  const send = async (intent: 'save' | 'clear' | 'recheck') => {
    if (busy) return; setError('');
    try {
      const why = intent === 'recheck' ? '重新检测' : validReason(reason);
      if (intent !== 'clear' && !url.trim()) throw new Error('请填写 API 地址');
      const typed = intent === 'save' ? joinKey(key, key2, Boolean(item.second_key)) : null;
      if (intent === 'save' && !saved && !typed) throw new Error(`请填写 ${item.key_label}`);
      const body = { platform: item.id, base_url: url.trim(), key: typed, clear: intent === 'clear', options: buildOptions(item.fields, draft), reason: why };
      setBusy(true);
      await submission.send(client, `/api/provider-configs/${channel.channel}`, body, 'providers:manage');
      setKey(''); setKey2(''); setReason(''); setSubmission(new Submission());
      onDone(intent === 'clear' ? 'Key 已清除。' : intent === 'recheck' ? '已重新检测，几秒后更新结果。' : '已保存，几秒后生效并显示检测结果，不用重启。');
    } catch (cause) {
      const code = cause instanceof ConsoleError ? cause.code : '';
      setError(FAILURES[code] ?? (cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'));
    } finally { setBusy(false); }
  };
  return <section className="panel provider-form"><form onSubmit={e => { e.preventDefault(); void send('save'); }}>
    <div className="provider-head"><h2>{page.label}接口</h2><span className={`badge ${badge}`}>{text}</span></div>
    <label className="provider-row"><span>平台</span><select value={item.id} disabled={busy} onChange={e => onPlatform(e.target.value)}>{channel.platforms.map(p => <option key={p.id} value={p.id}>{p.label}{isConfigured(p) ? '（已配置）' : ''}</option>)}</select></label>
    {item.id === GATEWAY && !saved && channel.server_base_url && <p className="muted">现在用的是服务器配置的接口：<code>{channel.server_base_url}</code>。在这里保存后，本页服务器配置的模型都改走这里的地址和 Key；清除 Key 后恢复服务器配置。</p>}
    {!sealingReady && <p className="notice error">服务器还没有配置密钥加密，暂时不能保存 Key。</p>}
    <label className="provider-row"><span>API 地址</span><input value={url} disabled={busy} maxLength={512} onChange={e => setUrl(e.target.value)} placeholder="https://provider.example/v1" /></label>
    <label className="provider-row"><span>{item.key_label}</span><input type="password" autoComplete="off" value={key} disabled={busy} maxLength={2000} onChange={e => setKey(e.target.value)} placeholder={saved ? `已保存 ····${config?.key_hint}，留空则不修改` : '请输入'} /></label>
    {item.second_key && <label className="provider-row"><span>{item.second_key}</span><input type="password" autoComplete="off" value={key2} disabled={busy} maxLength={2000} onChange={e => setKey2(e.target.value)} placeholder={saved ? '留空则不修改' : '请输入'} /></label>}
    {item.fields.map(field => <label key={field.key} className="provider-row"><span>{field.label}</span>{field.list
      ? <textarea rows={2} value={draft[field.key] ?? ''} disabled={busy} onChange={e => setDraft({ ...draft, [field.key]: e.target.value })} placeholder={field.placeholder} />
      : <input value={draft[field.key] ?? ''} disabled={busy} onChange={e => setDraft({ ...draft, [field.key]: e.target.value })} placeholder={field.placeholder} />}</label>)}
    <label className="provider-row"><span>修改原因</span><input value={reason} disabled={busy} maxLength={255} onChange={e => setReason(e.target.value)} placeholder="保存或清除时必填" /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="provider-actions">
      <button type="submit" className="button primary" disabled={busy || !sealingReady && Boolean(key) || submission.conflict}>{busy ? '提交中…' : '保存'}</button>
      <button type="button" className="button" disabled={busy || !saved} onClick={() => void send('clear')}>清除 Key</button>
      <button type="button" className="button" disabled={busy || !saved || url.trim() !== config?.base_url} onClick={() => void send('recheck')}>重新检测</button>
      <span className="muted provider-hint">{item.hint}{item.status && ` 上次检测：${formatDate(item.status.checked_at)}`}</span>
    </div>
  </form></section>;
}
