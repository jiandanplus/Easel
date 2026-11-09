import { useState } from 'react';
import { ConsoleError, formatDate, Submission, validReason } from './core';
import type { ConsoleClient } from './core';
import { Modal } from './views';
import { buildOptions, connectionLabel, CHANNELS, isSameInterface, joinKey, platformOf, statusBadge } from './providerChannels';
import type { ChannelState, Connection, PlatformSpec } from './providerChannels';

export type ConnectionMode = { kind: 'create' } | { kind: 'server' } | { kind: 'edit' | 'delete'; connection: Connection };

const FAILURES: Record<string, string> = {
  console_provider_busy: '这一类还有正在进行的调用，等它们结束后再改地址、Key 或删除。',
  console_provider_key_required: '新建或修改 API 地址时，需要填写 Key。',
  console_provider_invalid: 'API 地址、Key 或选项不正确。地址必须以 https:// 开头，不能是内网地址；Key 不能含空格或中文。',
  console_provider_key_unconfigured: '服务器还没有配置密钥加密，暂时不能保存 Key。',
  console_provider_in_use: '这条接入下还有启用中的模型，请先在下面的模型列表里停用。',
  console_provider_name_taken: '这一类里已经有同名的接入，换个名称。',
  console_provider_not_found: '这条接入已经被删除，请刷新页面。',
};
const draftOf = (spec: PlatformSpec, item: Connection | null) => Object.fromEntries(spec.fields.map(field => {
  const value = item?.options?.[field.key];
  return [field.key, Array.isArray(value) ? value.join('\n') : typeof value === 'string' ? value : ''];
}));

export function ConnectionDialog({ client, channel, mode, sealingReady, onClose, onFailure, onDone }: { client: ConsoleClient; channel: ChannelState; mode: ConnectionMode; sealingReady: boolean; onClose: () => void; onFailure: (e: unknown) => string; onDone: (m: string) => void }) {
  const item = mode.kind === 'edit' || mode.kind === 'delete' ? mode.connection : null;
  const page = CHANNELS.find(c => c.id === channel.channel)!;
  const [platform, setPlatform] = useState(item?.platform ?? (mode.kind === 'server' ? 'gateway' : channel.platforms[0].id));
  const spec = platformOf(channel, platform)!;
  const [name, setName] = useState(item?.name ?? ''), [url, setUrl] = useState(item?.base_url ?? (mode.kind === 'server' ? channel.server_base_url ?? '' : spec.default_url));
  const [key, setKey] = useState(''), [key2, setKey2] = useState(''), [draft, setDraft] = useState<Record<string, string>>(() => draftOf(spec, item));
  const [reason, setReason] = useState({ create: '新建接入', server: '改为后台配置', edit: '修改接入', delete: '删除接入' }[mode.kind]);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [submission] = useState(() => new Submission());
  const account = mode.kind === 'create' ? null : mode.kind === 'server' ? '' : item!.account;
  const removing = mode.kind === 'delete', original = account === '';
  const choose = (id: string) => { const next = platformOf(channel, id)!; setPlatform(id); setUrl(next.default_url); setDraft(draftOf(next, null)); setKey(''); setKey2(''); };
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason);
      let body;
      if (removing) body = { platform, account, name: '', base_url: item!.base_url, key: null, clear: true, options: item!.options, reason: why };
      else {
        if (!original && !name.trim()) throw new Error('请给这条接入起个名称');
        if (!url.trim()) throw new Error('请填写 API 地址');
        const typed = joinKey(key, key2, Boolean(spec.second_key));
        if (!item && !typed) throw new Error(`请填写 ${spec.key_label}`);
        body = { platform, account, name: name.trim(), base_url: url.trim(), key: typed, clear: false, options: buildOptions(spec.fields, draft), reason: why };
      }
      setBusy(true);
      await submission.send(client, `/api/provider-configs/${channel.channel}`, body, 'providers:manage');
      onDone(removing ? '接入已删除。' : '已保存，几秒后生效并显示检测结果，不用重启。');
    } catch (cause) {
      const code = cause instanceof ConsoleError ? cause.code : '';
      setError(FAILURES[code] ?? (cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'));
    } finally { setBusy(false); }
  };
  const title = removing ? `删除接入 · ${connectionLabel(channel, item!)}` : mode.kind === 'create' ? `新建${page.label}接入` : mode.kind === 'server' ? '改为后台配置' : `编辑接入 · ${connectionLabel(channel, item!)}`;
  return <Modal title={title} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {removing ? <p className="muted">{original && platform === 'gateway' && channel.server_base_url ? '删除后这一页恢复使用服务器配置。' : '删除后这条接入的 Key 会被清除，不能恢复。'}它下面的模型必须先停用。</p> : <>
      {mode.kind === 'create'
        ? <label className="reason-field">平台<select value={platform} disabled={busy} onChange={e => choose(e.target.value)}>{channel.platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        : <p className="muted">平台：{spec.label}</p>}
      {mode.kind === 'server' && <p className="muted">保存后，这一页服务器配置的模型都改走这里的地址和 Key；删除后恢复服务器配置。</p>}
      <label className="reason-field">名称<input value={name} disabled={busy} maxLength={64} onChange={e => setName(e.target.value)} placeholder="例如 MiniMax 主账号" /></label>
      {!sealingReady && <p className="notice error">服务器还没有配置密钥加密，暂时不能保存 Key。</p>}
      <label className="reason-field">API 地址<input value={url} disabled={busy} maxLength={512} onChange={e => setUrl(e.target.value)} placeholder="https://provider.example/v1" /></label>
      <label className="reason-field">{spec.key_label}<input type="password" autoComplete="off" value={key} disabled={busy} maxLength={2000} onChange={e => setKey(e.target.value)} placeholder={item ? `已保存 ····${item.key_hint}，留空则不修改` : '请输入'} /></label>
      {spec.second_key && <label className="reason-field">{spec.second_key}<input type="password" autoComplete="off" value={key2} disabled={busy} maxLength={2000} onChange={e => setKey2(e.target.value)} placeholder={item ? '留空则不修改' : '请输入'} /></label>}
      {spec.fields.map(field => <label key={field.key} className="reason-field">{field.label}{field.list
        ? <textarea rows={2} value={draft[field.key] ?? ''} disabled={busy} onChange={e => setDraft({ ...draft, [field.key]: e.target.value })} placeholder={field.placeholder} />
        : <input value={draft[field.key] ?? ''} disabled={busy} onChange={e => setDraft({ ...draft, [field.key]: e.target.value })} placeholder={field.placeholder} />}</label>)}
      <p className="muted">{spec.hint}</p>
    </>}
    <label className="reason-field">原因<input maxLength={255} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className={`button ${removing ? 'danger' : 'primary'}`} disabled={busy || submission.conflict}>{busy ? '提交中…' : removing ? '确认删除' : '保存'}</button></div></form></Modal>;
}

export function ConnectionList({ channel, items, canAddModels, onOpen, onPull, onAdd }: { channel: ChannelState; items: { provider_id: string }[]; canAddModels: boolean; onOpen: (mode: ConnectionMode) => void; onPull: (source: string | null) => void; onAdd: (base: string) => void }) {
  const page = CHANNELS.find(c => c.id === channel.channel)!, count = channel.connections.length + (channel.server ? 1 : 0);
  return <section className="panel connections"><div className="models-head"><h2 className="panel-title">{page.label}接入{count > 0 && <span className="muted"> · {count} 条</span>}</h2><button className="button primary" onClick={() => onOpen({ kind: 'create' })}>新建接入</button></div>
    {count === 0 && <div className="empty-state"><strong>还没有接入</strong><p className="muted">点“新建接入”，选平台并填写地址和 Key。同一个平台可以接入多个账号。</p></div>}
    <div className="connection-list">
      {channel.server && (() => { const [badge, text] = statusBadge(channel.server.status); return <article className="connection-row">
        <div className="connection-main"><div className="connection-title"><strong>服务器配置</strong><span className={`badge ${badge}`}>{text}</span></div><small className="muted">{channel.server.base_url}</small></div>
        <div className="row-actions">{canAddModels && <button className="text-button" onClick={() => onPull(null)}>拉取模型</button>}<button className="text-button" onClick={() => onOpen({ kind: 'server' })}>改为后台配置</button></div>
      </article>; })()}
      {channel.connections.map(item => { const spec = platformOf(channel, item.platform), [badge, text] = statusBadge(item.status), added = items.filter(row => isSameInterface(row.provider_id, item.provider_id)).length; return <article className="connection-row" key={item.provider_id}>
        <div className="connection-main"><div className="connection-title"><strong>{connectionLabel(channel, item)}</strong><span className={`badge ${badge}`}>{text}</span></div>
          <small className="muted">{spec?.label} · {item.base_url} · Key ····{item.key_hint} · 已添加模型 {added} 个 · 更新于 {formatDate(item.updated_at)}</small></div>
        <div className="row-actions">{canAddModels && (spec?.lists_models ? <button className="text-button" onClick={() => onPull(item.provider_id)}>拉取模型</button> : <button className="text-button" onClick={() => onAdd(item.provider_id)}>添加模型</button>)}<button className="text-button" onClick={() => onOpen({ kind: 'edit', connection: item })}>编辑</button><button className="text-button" onClick={() => onOpen({ kind: 'delete', connection: item })}>删除</button></div>
      </article>; })}
    </div></section>;
}
