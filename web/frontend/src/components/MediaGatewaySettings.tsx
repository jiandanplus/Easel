import { useEffect, useState } from 'react';
import { fetchMediaGateway, saveMediaGateway } from '../lib/api';
import type { MediaCapability, MediaGatewayMode, MediaGatewaySettings as Settings } from '../lib/api';
import { gatewayDraft, gatewayPayload, gatewayValidation, gatewayLegacyPreserved, MEDIA_CAPABILITIES, MUSIC_PROVIDERS, VIDEO_PROFILES, VOICE_PROVIDERS } from './mediaGatewayState';
import type { MediaGatewayDraft } from './mediaGatewayState';
import './MediaGatewaySettings.css';

export default function MediaGatewaySettings({ onChange }: { onChange?: (settings: Settings) => void }) {
  const [stored, setStored] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<MediaGatewayDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError('');
    void fetchMediaGateway().then(value => {
      if (!alive) return;
      setStored(value); setDraft(gatewayDraft(value)); onChange?.(value);
    }).catch(failure => { if (alive) setError(failure instanceof Error ? `读取失败：${failure.message}` : '读取媒体网关失败'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  // onChange only reports a completed read; a parent render must not reset edits.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision]);
  const edit = (update: (value: MediaGatewayDraft) => MediaGatewayDraft) => {
    setDraft(value => value ? update(value) : value); setError(''); setNote('');
  };
  const save = async () => {
    if (!draft || !stored || loading || busy) return;
    const invalid = gatewayValidation(draft, stored);
    if (invalid) { setError(invalid); return; }
    const payload = gatewayPayload(draft);
    setBusy(true); setError(''); setNote('');
    setDraft(value => value ? { ...value, key: '' } : value);
    try {
      const value = await saveMediaGateway(payload);
      setStored(value); setDraft(gatewayDraft(value)); onChange?.(value);
      setNote('已保存 · 模型待验证');
    } catch (failure) {
      setError(failure instanceof Error ? `保存失败：${failure.message}` : '保存失败，请重试');
    } finally { setBusy(false); }
  };
  const dirty = !!draft && !!stored && JSON.stringify(gatewayPayload(draft)) !== JSON.stringify(gatewayPayload(gatewayDraft(stored)));
  const allCustom = !!draft && MEDIA_CAPABILITIES.every(item => draft.modes[item.value] === 'custom');
  const setMode = (capability: MediaCapability, value: string) => edit(current => ({ ...current,
    ...(value === 'legacy' && stored ? { [capability]: { ...stored[capability] } } : {}),
    adopt: value === 'new-api' && stored?.modes[capability] === 'new-api' && stored.managed[capability] === false
      ? [...new Set([...(current.adopt || []), capability])] : (current.adopt || []).filter(item => item !== capability),
    clearKey: value !== 'custom' ? false : current.clearKey,
    modes: { ...current.modes, [capability]: value === 'legacy' ? 'new-api' : value as MediaGatewayMode },
  }));
  return <section className="media-gateway" aria-labelledby="media-gateway-title" aria-busy={loading || busy}>
    <div className="media-gateway-heading"><div><h3 id="media-gateway-title">统一媒体网关</h3></div><span className="media-gateway-state">{loading ? '读取中…' : dirty ? '有未保存的修改' : stored && Object.values(stored.managed || {}).some(Boolean) ? '统一网关 · 待验证' : '沿用已有接口'}</span></div>
    {error && <p className="media-gateway-error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取媒体网关配置…</p>}
    {!loading && !draft && <button className="btn btn-sm" type="button" onClick={() => setRevision(value => value + 1)}>重新读取媒体配置</button>}
    {draft && stored && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <fieldset disabled={loading || busy} className="media-gateway-fields"><legend className="media-gateway-sr">媒体网关配置</legend>
        <div className="media-gateway-connection">
          <label htmlFor="media-gateway-url">网关根地址<input id="media-gateway-url" className="mock" type="url" value={draft.baseUrl} placeholder="https://gateway.example/proxy" autoComplete="off" onChange={event => edit(value => ({ ...value, baseUrl: event.target.value }))} /></label>
          <label htmlFor="media-gateway-key">业务密钥<input id="media-gateway-key" className="mock" type="password" value={draft.key} autoComplete="off" disabled={draft.clearKey} placeholder={stored.keyConfigured ? '已保存密钥，留空保留' : '填写网关业务密钥'} onChange={event => edit(value => ({ ...value, key: event.target.value }))} /></label>
        </div>
        <div className="media-gateway-capabilities">{MEDIA_CAPABILITIES.map(({ value: capability, label }) => <fieldset className="media-gateway-capability" key={capability}>
          <legend>{label}</legend>
          <label htmlFor={`media-${capability}-mode`}>接口来源<select id={`media-${capability}-mode`} className="mock" value={gatewayLegacyPreserved(draft, stored, capability) ? 'legacy' : draft.modes[capability]} onChange={event => setMode(capability, event.target.value)}><option value="custom">自定义接口</option>{stored.modes[capability] === 'new-api' && stored.managed?.[capability] === false && <option value="legacy">保留旧版 New API</option>}<option value="new-api">统一网关</option></select></label>
          {draft.modes[capability] === 'custom' || gatewayLegacyPreserved(draft, stored, capability) ? null : <>
            {capability === 'music' && <label htmlFor="media-music-provider">音乐供应商<select id="media-music-provider" className="mock" value={draft.music.provider} onChange={event => edit(value => ({ ...value, music: { ...value.music, provider: event.target.value } }))}>{MUSIC_PROVIDERS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
            {capability === 'voice' && <label htmlFor="media-voice-provider">配音供应商<select id="media-voice-provider" className="mock" value={draft.voice.provider} onChange={event => edit(value => ({ ...value, voice: { ...value.voice, provider: event.target.value } }))}>{VOICE_PROVIDERS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
            <label htmlFor={`media-${capability}-model`}>{label}模型<input id={`media-${capability}-model`} className="mock" value={draft[capability].model} placeholder={['image', 'video'].includes(capability) || (capability === 'voice' && ['gemini', 'openai-compatible'].includes(draft.voice.provider)) ? '填写网关中的模型名称' : '留空使用所选供应商的默认模型'} onChange={event => edit(value => ({ ...value, [capability]: { ...value[capability], model: event.target.value } }))} /></label>
            {capability === 'video' && <label htmlFor="media-video-profile">视频参数映射<select id="media-video-profile" className="mock" value={draft.video.profile} onChange={event => edit(value => ({ ...value, video: { ...value.video, profile: event.target.value } }))}>{VIDEO_PROFILES.map(value => <option key={value} value={value}>{value}</option>)}</select></label>}
            {capability === 'voice' && <><label htmlFor="media-voice-id">音色 ID<input id="media-voice-id" className="mock" value={draft.voice.voiceId} placeholder={['dashscope', 'minimax'].includes(draft.voice.provider) ? '填写登记后返回的公共音色 ID' : draft.voice.provider === 'fish-audio' ? '管理员允许的音色 ID，或在任务中提供参考音频' : '留空使用默认音色'} onChange={event => edit(value => ({ ...value, voice: { ...value.voice, voiceId: event.target.value } }))} /></label></>}
            <p className="media-gateway-hint">{stored.modes[capability] === 'new-api' && stored.configured[capability] ? '配置完整 · 模型待验证' : '配置未确认 · 模型待验证'}</p>
          </>}
        </fieldset>)}</div>
        <details className="media-gateway-advanced"><summary>自定义 Gemini 配音协议</summary><label htmlFor="media-gemini-protocol">直连接口协议<select id="media-gemini-protocol" className="mock" value={draft.geminiTtsApi} onChange={event => edit(value => ({ ...value, geminiTtsApi: event.target.value }))}><option value="generate-content">Generate Content</option><option value="interactions">Interactions</option></select></label></details>
        <label className="media-gateway-clear"><input type="checkbox" checked={draft.clearKey} disabled={!allCustom || !stored.keyConfigured} onChange={event => edit(value => ({ ...value, clearKey: event.target.checked, key: '' }))} />清除已存网关密钥<span>全部切换自定义接口后可清除</span></label>
        <div className="media-gateway-actions"><button className="btn btn-sm btn-primary" type="submit" disabled={!dirty || loading || busy}>{busy ? '保存中…' : '保存媒体网关'}</button></div>
      </fieldset>
      {note && <p className="media-gateway-success" role="status">{note}</p>}
    </form>}
  </section>;
}
