import type { RecordData } from './core';

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
export type PlatformSpec = { id: string; label: string; default_url: string; hint: string; key_label: string; second_key: string | null; fields: Field[]; lists_models: boolean };
export type Probe = { state: string; models: string[]; checked_at: string };
export type Connection = { id: string; platform: string; account: string; name: string; provider_id: string; base_url: string; key_hint: string; options: RecordData; updated_at: string; status: Probe | null };
export type ChannelState = { channel: ChannelId; server_base_url: string | null; platforms: PlatformSpec[]; connections: Connection[]; server: { base_url: string; status: Probe | null } | null };

const STATES: Record<string, [string, string]> = {
  ok: ['positive', '连接正常'],
  auth_failed: ['warning', 'Key 无效或没有权限'],
  unreachable: ['warning', '连不上这个地址'],
  no_model_list: ['neutral', '已保存，这个平台不提供模型列表，调用时才能确认'],
  invalid: ['warning', '设置没有通过校验，未生效'],
};
export function statusBadge(status: Probe | null): [string, string] {
  if (!status) return ['neutral', '检测中…'];
  const found = STATES[status.state] ?? ['neutral', status.state];
  return status.state === 'ok' ? [found[0], `${found[1]} · 可用模型 ${status.models.length} 个`] : found;
}
export const platformOf = (channel: ChannelState, id: string) => channel.platforms.find(p => p.id === id);
export const connectionLabel = (channel: ChannelState, item: Connection) => item.name || platformOf(channel, item.platform)?.label || item.platform;
export function providerLabel(channel: ChannelState | undefined, providerId: string): string {
  if (!providerId.startsWith('console-')) return `服务器配置 · ${providerId}`;
  const clone = providerId.endsWith('-clone'), own = clone ? providerId.slice(0, -6) : providerId;
  const item = channel?.connections.find(c => c.provider_id === own);
  return item ? `${connectionLabel(channel!, item)}${clone ? ' · 声音复刻' : ''}` : '已删除的接入';
}
export const isSameInterface = (providerId: string, base: string) => providerId === base || providerId === `${base}-clone`;
export function knownModels(channel: ChannelState | undefined): string[] {
  if (!channel) return [];
  return [...new Set([...channel.connections.flatMap(c => c.status?.models ?? []), ...(channel.server?.status?.models ?? [])])];
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
