import { useState } from 'react';
import { ConsoleError, Submission, validReason } from './core';
import type { ConsoleClient } from './core';
import { Modal } from './views';
import { chosenVoices, connectionLabel, matchVoices, platformOf, VOICE_KINDS, voiceRows } from './providerChannels';
import type { ChannelState, Connection } from './providerChannels';

const MAX_CHOSEN = 64;

export function VoicePullDialog({ client, channel, item, onClose, onFailure, onDone, onRefresh }: { client: ConsoleClient; channel: ChannelState; item: Connection; onClose: () => void; onFailure: (e: unknown) => string; onDone: (m: string) => void; onRefresh: () => void }) {
  const spec = platformOf(channel, item.platform);
  const [selected, setSelected] = useState<string[]>(() => chosenVoices(item)), [filter, setFilter] = useState(''), [search, setSearch] = useState(typeof item.options.voice_query === 'string' ? item.options.voice_query : '');
  const [reason, setReason] = useState('选择音色'), [error, setError] = useState(''), [busy, setBusy] = useState(false), [pulling, setPulling] = useState(false);
  const [save] = useState(() => new Submission()), [pull, setPull] = useState(() => new Submission());
  const listed = item.status?.voices ?? [], rows = voiceRows(listed, selected), shown = matchVoices(rows, filter);
  const body = (options: Record<string, unknown>, why: string) => ({ platform: item.platform, account: item.account, name: item.name, base_url: item.base_url, key: null, clear: false, options, reason: why });
  const failure = (cause: unknown) => setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败');
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(value => value !== id) : current.length >= MAX_CHOSEN ? current : [...current, id]);
  const repull = async (options: Record<string, unknown>) => {
    if (pulling) return;
    setPulling(true); setError('');
    try { await pull.send(client, `/api/provider-configs/${channel.channel}`, body(options, '拉取音色'), 'providers:manage'); setPull(new Submission()); onRefresh(); }
    catch (cause) { failure(cause); }
    finally { setPulling(false); }
  };
  const searchPublic = () => {
    const rest = Object.fromEntries(Object.entries(item.options).filter(([key]) => key !== 'voice_query'));
    void repull(search.trim() ? { ...rest, voice_query: search.trim() } : rest);
  };
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason);
      if (!selected.length) throw new Error('请至少勾选一个音色');
      setBusy(true);
      await save.send(client, `/api/provider-configs/${channel.channel}`, body({ ...item.options, voices: selected }, why), 'providers:manage');
      onDone(`已保存 ${selected.length} 个音色，几秒后生效。`);
    } catch (cause) { failure(cause); }
    finally { setBusy(false); }
  };
  const failed = item.status?.state === 'auth_failed' ? 'Key 无效或没有权限，拉取不到音色。' : item.status?.state === 'unreachable' ? '连不上这个地址，下面是上一次拉取的结果。' : '';
  return <Modal title={`拉取音色 · ${connectionLabel(channel, item)}`} onClose={onClose} busy={busy} wide><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <div className="pull-toolbar"><input value={filter} disabled={busy} onChange={e => setFilter(e.target.value)} placeholder={`在列表里筛选，共 ${rows.length} 个`} /><button type="button" className="button" disabled={busy || pulling} onClick={() => void repull(item.options)}>{pulling ? '拉取中…' : '重新拉取'}</button></div>
    {spec?.voice_search && <div className="pull-toolbar"><input value={search} maxLength={64} disabled={busy} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); searchPublic(); } }} placeholder="搜索公开声音库，例如 旁白" /><button type="button" className="button" disabled={busy || pulling} onClick={searchPublic}>搜索公开声音</button></div>}
    {failed && <p className="notice error" role="alert">{failed}</p>}
    {!item.status && <p className="muted">正在向平台查询，需要几秒钟…</p>}
    {item.status && rows.length === 0 && !failed && <div className="empty-state"><strong>没有拉取到音色</strong><p className="muted">{spec?.voice_search ? '可以稍后重新拉取，或搜索公开声音库。' : item.platform === 'dashscope' ? '阿里只能查到你复刻过的音色，系统音色请在“编辑”里手动填写。' : '可以稍后重新拉取，或在“编辑”里手动填写。'}</p></div>}
    {rows.length > 0 && <div className="pull-list">{shown.map(voice => <label key={voice.id} className="check voice-row"><input type="checkbox" checked={selected.includes(voice.id)} disabled={busy || (!selected.includes(voice.id) && selected.length >= MAX_CHOSEN)} onChange={() => toggle(voice.id)} /><span className="voice-name"><span>{voice.name}</span>{voice.name !== voice.id && <small className="muted">{voice.id}</small>}</span><small className="tag">{VOICE_KINDS[voice.kind] ?? voice.kind}</small></label>)}</div>}
    <p className="muted">已选 {selected.length} 个，最多 {MAX_CHOSEN} 个。用户只能使用勾选的音色。</p>
    <label className="reason-field">原因<input maxLength={255} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || !selected.length || save.conflict}>{busy ? '提交中…' : `保存选中的 ${selected.length} 个音色`}</button></div></form></Modal>;
}
