import { useMemo, useState } from 'react';
import { ConsoleError, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';
import { Modal } from './views';
import { channelOf, connectionLabel, platformOf } from './providerChannels';
import type { ChannelState, Connection, PlatformSpec, Probe } from './providerChannels';
import type { CatalogRow, Template } from './ModelCatalogPage';

type Data = { items: CatalogRow[]; providers: RecordData[]; templates: Template[] };

export type ImportSource = { spec: PlatformSpec; title: string; base: string | null; status: Probe | null; connection: Connection | null };
export function sourceOf(channel: ChannelState, id: string | null): ImportSource | null {
  if (id === null) { const spec = platformOf(channel, 'gateway'); return channel.server && spec ? { spec, title: '服务器配置', base: null, status: channel.server.status, connection: null } : null; }
  const connection = channel.connections.find(c => c.provider_id === id), spec = connection && platformOf(channel, connection.platform);
  return connection && spec ? { spec, title: connectionLabel(channel, connection), base: id, status: connection.status, connection } : null;
}
export function providerFor(providers: RecordData[], base: string | null, template: Template | undefined): string {
  if (!template) return '';
  const fits = (p: RecordData) => p.kind === template.kind && (template.kind === 'execution' || (Array.isArray(p.capabilities) && (p.capabilities as string[]).includes(template.capability)));
  const usable = providers.filter(fits).map(p => String(p.provider_id));
  if (base === null) return usable.find(id => !id.startsWith('console-')) ?? '';
  return usable.find(id => id === base || id === `${base}-clone`) ?? '';
}
export function matchNames(names: string[], query: string): string[] {
  const needle = query.trim().toLowerCase();
  return needle ? names.filter(name => name.toLowerCase().includes(needle)) : names;
}
export function addedNames(items: CatalogRow[], providerId: string): Set<string> {
  return new Set(items.filter(row => row.provider_id === providerId && row.upstream_model).map(row => String(row.upstream_model)));
}
const validPrice = (value: string) => /^\d+(\.\d{1,6})?$/.test(value.trim()) && Number(value) > 0;

export function ModelImportDialog({ client, channel, source, data, onClose, onFailure, onDone, onRefresh }: { client: ConsoleClient; channel: ChannelState; source: ImportSource; data: Data; onClose: () => void; onFailure: (e: unknown) => string; onDone: (m: string) => void; onRefresh: () => void }) {
  const templates = data.templates.filter(t => channelOf(t.capability) === channel.channel);
  const [capability, setCapability] = useState(templates[0]?.capability ?? ''), [filter, setFilter] = useState(''), [selected, setSelected] = useState<string[]>([]);
  const [prices, setPrices] = useState<Record<string, string>>({}), [enabled, setEnabled] = useState(true), [reason, setReason] = useState('拉取并添加模型');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pulling, setPulling] = useState(false);
  const [submission] = useState(() => new Submission()), [pull, setPull] = useState(() => new Submission());
  const template = templates.find(t => t.capability === capability);
  const provider = providerFor(data.providers, source.base, template), { spec, status, connection } = source;
  const added = useMemo(() => addedNames(data.items, provider), [data.items, provider]);
  const names = status?.models ?? [], shown = matchNames(names, filter), choosable = shown.filter(name => !added.has(name));
  const toggle = (name: string) => setSelected(current => current.includes(name) ? current.filter(item => item !== name) : [...current, name]);
  const allShown = choosable.length > 0 && choosable.every(name => selected.includes(name));
  const toggleAll = () => setSelected(current => allShown ? current.filter(name => !choosable.includes(name)) : [...new Set([...current, ...choosable])]);
  const refresh = async () => {
    if (pulling || !connection) { onRefresh(); return; }
    setPulling(true); setError('');
    try {
      await pull.send(client, `/api/provider-configs/${channel.channel}`, { platform: connection.platform, account: connection.account, name: connection.name, base_url: connection.base_url, key: null, clear: false, options: connection.options, reason: '重新拉取模型' }, 'providers:manage');
      setPull(new Submission()); onRefresh();
    } catch (cause) { setError(cause instanceof ConsoleError ? onFailure(cause) : '拉取失败'); }
    finally { setPulling(false); }
  };
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason);
      if (!selected.length) throw new Error('请至少勾选一个模型');
      if (selected.length > 100) throw new Error('一次最多添加 100 个模型');
      if (!provider) throw new Error('这一类还没有可用的接口，请先保存地址和 Key，等几秒后再试');
      const body: Record<string, string> = {};
      for (const meter of template?.meters ?? []) { const value = prices[meter.key] ?? '1'; if (!validPrice(value)) throw new Error(`请填写${meter.label}的点数`); body[meter.key] = value.trim(); }
      setBusy(true);
      const result = await submission.send(client, '/api/catalog/import', { capability, provider_id: provider, upstream_models: selected, prices: body, enabled, reason: why }, 'execution:publish');
      const count = (result.added as string[]).length, skipped = (result.skipped as string[]).length;
      onDone(`已添加 ${count} 个模型${enabled ? '并启用' : ''}${skipped ? `，跳过 ${skipped} 个已存在的` : ''}。单价可以之后逐个修改。`);
    } catch (cause) { setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'); }
    finally { setBusy(false); }
  };
  return <Modal title={`拉取模型 · ${source.title}`} onClose={onClose} busy={busy} wide><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {!spec.lists_models ? <p className="notice">这个平台不提供模型列表，请用“添加模型”手动填写。</p> : <>
      <div className="pull-toolbar"><input value={filter} disabled={busy} onChange={e => setFilter(e.target.value)} placeholder={`搜索模型，共 ${names.length} 个`} /><button type="button" className="button" disabled={busy || pulling} onClick={() => void refresh()}>{pulling ? '拉取中…' : '重新拉取'}</button></div>
      {names.length === 0 ? <div className="empty-state"><strong>{status ? '接口没有返回模型' : '还没有拉取到模型'}</strong><p className="muted">检测需要几秒钟，请稍后点“重新拉取”。</p></div> : <>
        <label className="check pull-all"><input type="checkbox" checked={allShown} disabled={busy || !choosable.length} onChange={toggleAll} /> 全选当前列表（已选 {selected.length} 个）</label>
        <div className="pull-list">{shown.map(name => <label key={name} className={`check pull-row ${added.has(name) ? 'muted' : ''}`}><input type="checkbox" checked={added.has(name) || selected.includes(name)} disabled={busy || added.has(name)} onChange={() => toggle(name)} /> <span>{name}</span>{added.has(name) && <small>已添加</small>}</label>)}</div>
      </>}
      {templates.length > 1 && <label className="reason-field">类型<select value={capability} disabled={busy} onChange={e => { setCapability(e.target.value); setPrices({}); }}>{templates.map(t => <option key={t.capability} value={t.capability}>{t.label}</option>)}</select></label>}
      <div className="field-row">{template?.meters.map(meter => <label key={meter.key} className="reason-field">{meter.label}（点）<input inputMode="decimal" value={prices[meter.key] ?? '1'} disabled={busy} onChange={e => setPrices({ ...prices, [meter.key]: e.target.value })} /></label>)}</div>
      <p className="muted">勾选的模型先用同一个单价，之后可以在列表里逐个修改。</p>
      <label className="check"><input type="checkbox" checked={enabled} disabled={busy} onChange={e => setEnabled(e.target.checked)} /> 添加后立即启用</label>
      <label className="reason-field">原因<input maxLength={255} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    </>}
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>{spec.lists_models && <button type="submit" className="button primary" disabled={busy || !selected.length || submission.conflict}>{busy ? '提交中…' : `添加选中的 ${selected.length} 个模型`}</button>}</div></form></Modal>;
}
