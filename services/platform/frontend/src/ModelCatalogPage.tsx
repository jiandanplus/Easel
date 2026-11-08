import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatDate, RequestGate, Submission, validReason } from './core';
import type { ConsoleClient, LoadState, RecordData } from './core';
import { Loading, Modal } from './views';

export type Template = { capability: string; label: string; feature: string; kind: string; meters: { key: string; label: string; per: number }[] };
export type CatalogRow = { kind: 'execution' | 'media'; id: string; name: string; capability: string; feature: string; provider_id: string; enabled: boolean; updated_at: string; managed: boolean; upstream_model: string | null; rate: RecordData; custom_price: boolean; trial_wallets: number; settled_calls: number };
type Catalog = { items: CatalogRow[]; providers: RecordData[]; templates: Template[] };
type Action = { kind: 'add' } | { kind: 'price' | 'trial' | 'state'; row: CatalogRow };

export function partPoints(part: { numerator?: unknown; denominator?: unknown } | undefined, per: number): string {
  if (!part) return '—';
  const value = Number(part.numerator) / Number(part.denominator) * per / 1000000;
  return Number.isFinite(value) ? String(Number(value.toFixed(6))) : '—';
}
export function pricesOf(row: CatalogRow, template?: Template): [string, string][] {
  if (row.capability === 'text') return [['每百万输入 token', String(Number(((Number((row.rate.input as RecordData).numerator) / Number((row.rate.input as RecordData).denominator))).toFixed(6)))], ['每百万输出 token', String(Number(((Number((row.rate.output as RecordData).numerator) / Number((row.rate.output as RecordData).denominator))).toFixed(6)))]];
  const meter = ((row.rate.meters as RecordData[]) ?? [])[0];
  const info = template?.meters[0];
  return [[info?.label ?? String(meter?.unit), partPoints(meter as RecordData, info?.per ?? 1)]];
}
function encode(id: string) { return encodeURIComponent(id); }

function Dialog({ client, action, data, onClose, onFailure, onDone }: { client: ConsoleClient; action: Action; data: Catalog; onClose: () => void; onFailure: (e: unknown) => string; onDone: (m: string) => void }) {
  const row = action.kind === 'add' ? null : action.row;
  const [capability, setCapability] = useState(row?.capability ?? 'text'), [name, setName] = useState(''), [id, setId] = useState(''), [upstream, setUpstream] = useState(''), [provider, setProvider] = useState('');
  const [prices, setPrices] = useState<Record<string, string>>({}), [user, setUser] = useState(''), [trialOff, setTrialOff] = useState(false), [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission());
  const template = data.templates.find(t => t.capability === capability);
  const providers = data.providers.filter(p => p.kind === template?.kind && (template?.kind === 'execution' || (Array.isArray(p.capabilities) && (p.capabilities as string[]).includes(capability))));
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason);
      const priced = () => { const out: Record<string, string> = {}; for (const meter of template?.meters ?? []) { const value = (prices[meter.key] ?? '').trim(); if (!/^\d+(\.\d{1,6})?$/.test(value)) throw new Error(`请填写${meter.label}的点数`); out[meter.key] = value; } return out; };
      let path = '/api/catalog/models', body: RecordData, done = '';
      if (action.kind === 'add') {
        if (!name.trim() || !/^[A-Za-z0-9_.:/-]{1,128}$/.test(id) || !upstream.trim() || !provider) throw new Error('请填写名称、模型编号、网关模型名并选择接口');
        body = { id, name: name.trim(), capability, provider_id: provider, upstream_model: upstream.trim(), prices: priced(), reason: why }; done = '模型已添加。先加入试用名单，真实调用成功后才能正式启用。';
      } else if (action.kind === 'price') {
        path = `/api/catalog/${action.row.kind}/${encode(action.row.id)}/price`; body = { prices: priced(), reason: why }; done = '新价格已生效，只影响之后的调用。';
      } else if (action.kind === 'trial') {
        if (!user.trim()) throw new Error('请填写用户名、邮箱或钱包编号');
        path = `/api/catalog/${action.row.kind}/${encode(action.row.id)}/trial`; body = { user: user.trim(), active: !trialOff, reason: why }; done = trialOff ? '已移出试用名单。' : '已加入试用名单，该用户现在能看到这个模型。';
      } else if (action.row.kind === 'media') {
        path = `/api/catalog/media/${encode(action.row.id)}/state`; body = { enabled: !action.row.enabled, reason: why }; done = action.row.enabled ? '模型已停用。' : '模型已启用。';
      } else {
        path = `/api/models/${encode(action.row.id)}/state`; body = { enabled: !action.row.enabled, reason: why, expected_updated_at: action.row.updated_at }; done = action.row.enabled ? '模型已停用。' : '模型已启用。';
      }
      setBusy(true);
      await submission.send(client, path, body, 'execution:publish');
      onDone(done);
    } catch (cause) {
      const code = cause instanceof ConsoleError ? cause.code : '';
      setError(code === 'console_model_needs_trial' ? '这个模型还没有成功的真实调用。请先加入试用名单，并用试用账号实际调用一次。' : cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败');
    } finally { setBusy(false); }
  };
  const title = action.kind === 'add' ? '添加模型' : action.kind === 'price' ? `设置单价 · ${action.row.name}` : action.kind === 'trial' ? `试用名单 · ${action.row.name}` : `${action.row.enabled ? '停用' : '启用'} · ${action.row.name}`;
  const showPrices = action.kind === 'add' || action.kind === 'price';
  return <Modal title={title} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {action.kind === 'add' && <>
      <label className="reason-field">类型<select value={capability} disabled={busy} onChange={e => { setCapability(e.target.value); setPrices({}); setProvider(''); }}>{data.templates.map(t => <option key={t.capability} value={t.capability}>{t.label}</option>)}</select></label>
      <label className="reason-field">接口<select value={provider} disabled={busy} onChange={e => setProvider(e.target.value)}><option value="">请选择</option>{providers.map(p => <option key={String(p.provider_id)} value={String(p.provider_id)}>{String(p.provider_id)}</option>)}</select>{providers.length === 0 && <span className="muted">服务器上还没有配置这类接口。</span>}</label>
      <label className="reason-field">显示名称<input value={name} maxLength={128} disabled={busy} onChange={e => setName(e.target.value)} placeholder="用户看到的名字" /></label>
      <label className="reason-field">模型编号<input value={id} maxLength={128} disabled={busy} onChange={e => setId(e.target.value)} placeholder="例如 youchuang-image-v1，添加后不能修改" /></label>
      <label className="reason-field">网关里的模型名<input value={upstream} maxLength={128} disabled={busy} onChange={e => setUpstream(e.target.value)} placeholder="和 New API 渠道里的模型名完全一致" /></label>
    </>}
    {showPrices && <div className="field-row">{template?.meters.map(meter => <label key={meter.key} className="reason-field">{meter.label}（点）<input inputMode="decimal" value={prices[meter.key] ?? ''} disabled={busy} onChange={e => setPrices({ ...prices, [meter.key]: e.target.value })} /></label>)}</div>}
    {action.kind === 'price' && <p className="muted">新价格对之后的调用生效，已经开始的调用仍按原价结算。</p>}
    {action.kind === 'trial' && <><p className="muted">未启用的模型只有名单里的用户能看到和使用，用于正式启用前的真实测试。</p>
      <label className="reason-field">用户名、邮箱或钱包编号<input value={user} maxLength={320} disabled={busy} onChange={e => setUser(e.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={trialOff} disabled={busy} onChange={e => setTrialOff(e.target.checked)} /> 从名单中移除</label></>}
    {action.kind === 'state' && <p className="muted">{action.row.enabled ? '停用后所有用户都不能再使用这个模型。' : '启用后，套餐里包含这个模型的用户都能使用。'}</p>}
    <label className="reason-field">原因<textarea required maxLength={255} rows={2} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || submission.conflict}>{busy ? '提交中…' : '确认'}</button></div></form></Modal>;
}

export function ModelCatalogPage({ client, permissions, onFailure }: { client: ConsoleClient; permissions: string[]; onFailure: (e: unknown) => string }) {
  const [state, setState] = useState<LoadState<Catalog>>({ kind: 'loading' }), [revision, setRevision] = useState(0), [action, setAction] = useState<Action | null>(null), [notice, setNotice] = useState('');
  const gate = useRef(new RequestGate()), canWrite = permissions.includes('execution:publish');
  useEffect(() => {
    const request = gate.current.begin(); setState({ kind: 'loading' });
    void client.read<Catalog>('/api/catalog', request.signal).then(v => { if (request.current()) setState({ kind: 'ready', data: v }); }).catch(e => { if (request.current()) setState({ kind: 'error', message: onFailure(e) }); });
    return () => gate.current.cancel();
  }, [client, revision, onFailure]);
  const heading = <div className="page-heading"><div><h1>模型</h1><p>统一管理文本、生图、视频、音乐和语音模型。供应商密钥在 New API 管理页填写，这里决定开放哪些模型和每次消耗多少点。</p></div>{canWrite && <button className="button primary" onClick={() => setAction({ kind: 'add' })}>添加模型</button>}</div>;
  if (state.kind !== 'ready') return <>{heading}<Loading state={state} onRetry={() => setRevision(x => x + 1)} /></>;
  const { items, templates } = state.data;
  return <>{heading}{notice && <div className="notice" role="status">{notice}</div>}
    <section className="panel">{items.length === 0 ? <div className="empty-state"><strong>还没有模型</strong></div> : <div className="table-scroll"><table><thead><tr><th>模型</th><th>类型</th><th>接口</th><th>单价（点）</th><th>状态</th><th>真实调用</th><th>操作</th></tr></thead><tbody>
      {items.map(row => { const template = templates.find(t => t.capability === row.capability); return <tr key={row.kind + row.id}>
        <td><div className="person"><strong>{row.name}</strong><small>{row.id}</small></div></td><td>{template?.label ?? row.capability}</td>
        <td><div className="person"><span>{row.provider_id}</span><small>{row.upstream_model ?? '服务器配置'}</small></div></td>
        <td>{pricesOf(row, template).map(([label, value]) => <div key={label} className="price-line"><span className="muted">{label}</span> {value}</div>)}{row.custom_price && <small className="muted">已单独定价</small>}</td>
        <td>{row.enabled ? <span className="badge positive">已启用</span> : row.trial_wallets > 0 ? <span className="badge warning">试用中 · {row.trial_wallets}</span> : <span className="badge neutral">未启用</span>}</td><td>{row.settled_calls}</td>
        <td>{canWrite ? <div className="row-actions"><button className="text-button" onClick={() => setAction({ kind: 'price', row })}>改价</button><button className="text-button" onClick={() => setAction({ kind: 'trial', row })}>试用</button><button className="text-button" onClick={() => setAction({ kind: 'state', row })}>{row.enabled ? '停用' : '启用'}</button></div> : '—'}</td></tr>; })}
    </tbody></table></div>}</section>
    <p className="caption">更新于 {formatDate(new Date().toISOString())}。</p>
    {action && <Dialog client={client} action={action} data={state.data} onClose={() => setAction(null)} onFailure={onFailure} onDone={m => { setAction(null); setNotice(m); setRevision(x => x + 1); }} />}
  </>;
}
