import type { MediaCapability, MediaGatewayPayload, MediaGatewaySettings } from '../lib/api';

export const MEDIA_CAPABILITIES: { value: MediaCapability; label: string }[] = [
  { value: 'image', label: '图片' }, { value: 'video', label: '视频' },
  { value: 'music', label: '音乐' }, { value: 'voice', label: '配音' },
];
export const VIDEO_PROFILES = ['standard', 'veo', 'kling', 'wan26-t2v', 'seedance1'];
export const MUSIC_PROVIDERS = [{ value: 'dashscope', label: 'DashScope' }, { value: 'suno-compatible', label: 'Suno' }];
export const VOICE_PROVIDERS = [
  { value: 'dashscope', label: 'DashScope' }, { value: 'minimax', label: 'MiniMax' },
  { value: 'fish-audio', label: 'Fish Audio' }, { value: 'openai-compatible', label: 'OpenAI 兼容' },
  { value: 'gemini', label: 'Gemini' },
];
export interface MediaGatewayDraft extends MediaGatewayPayload { key: string; clearKey: boolean }
export function gatewayDraft(value: MediaGatewaySettings): MediaGatewayDraft {
  return { baseUrl: value.baseUrl, key: '', clearKey: false, adopt: [], modes: { ...value.modes }, image: { ...value.image },
    video: { ...value.video }, music: { ...value.music }, voice: { ...value.voice }, geminiTtsApi: value.geminiTtsApi };
}
export function gatewayPayload(value: MediaGatewayDraft): MediaGatewayPayload {
  return { ...value, baseUrl: value.baseUrl.trim(), key: value.key.trim(),
    image: { model: value.image.model.trim() }, video: { model: value.video.model.trim(), profile: value.video.profile },
    music: { provider: value.music.provider, model: value.music.model.trim() },
    voice: { provider: value.voice.provider, model: value.voice.model.trim(), voiceId: value.voice.voiceId.trim() } };
}
export function gatewayLegacyPreserved(draft: MediaGatewayDraft, stored: MediaGatewaySettings, capability: MediaCapability): boolean {
  return stored.modes[capability] === 'new-api' && stored.managed?.[capability] === false
    && draft.modes[capability] === 'new-api' && !draft.adopt?.includes(capability)
    && JSON.stringify(draft[capability]) === JSON.stringify(stored[capability]);
}
export function gatewayValidation(draft: MediaGatewayDraft, stored: MediaGatewaySettings): string {
  const value = gatewayPayload(draft);
  const enabled = MEDIA_CAPABILITIES.filter(item => value.modes[item.value] === 'new-api' && !gatewayLegacyPreserved(draft, stored, item.value));
  if (value.clearKey && MEDIA_CAPABILITIES.some(item => value.modes[item.value] === 'new-api')) return '清除密钥前，请先将全部媒体能力切回自定义接口。';
  if (value.clearKey && value.key) return '请在填写新密钥和清除已存密钥之间选择一项。';
  if (value.baseUrl) {
    try {
      const url = new URL(value.baseUrl);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { return '请填写有效的网关根地址，不含账号、密码、查询参数或片段。'; }
  }
  if (stored.keyConfigured && value.baseUrl.replace(/\/+$/, '') !== stored.baseUrl.replace(/\/+$/, '') && !value.key && !value.clearKey) return '更换网关地址时，请重新填写对应的业务密钥。';
  if (!enabled.length) return '';
  if (!value.baseUrl) return '使用统一网关前，请填写网关根地址。';
  if (!value.key && !stored.keyConfigured) return '使用统一网关前，请填写业务密钥。';
  if (enabled.some(item => item.value === 'image') && !value.image.model) return '请填写统一网关的图片模型。';
  if (enabled.some(item => item.value === 'video') && !value.video.model) return '请填写统一网关的视频模型。';
  if (enabled.some(item => item.value === 'voice') && ['openai-compatible', 'gemini'].includes(value.voice.provider) && !value.voice.model) return '请填写所选配音供应商的模型。';
  return '';
}
