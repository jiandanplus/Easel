import { useEffect, useRef, useState } from 'react';
import { ConsoleError, decimalUnits, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';
import type { CatalogRow, Template } from './ModelCatalogPage';
import { Modal } from './views';

export function multiplierFraction(value: string): { numerator: string; denominator: string } {
  const text = value.trim();
  if (!/^\d{1,4}(\.\d{1,4})?$/.test(text) || Number(text) <= 0) throw new Error('倍率要大于 0，最多 4 位小数，例如 0.8');
  const [whole, fraction = ''] = text.split('.');
  return { numerator: String(BigInt(whole + fraction)), denominator: String(10n ** BigInt(fraction.length)) };
}
export function buildPlan(values: { name: string; credits: string; days: string; concurrency: string; multiplier: string; selected: string[]; overrides: Record<string, string> }, items: CatalogRow[], id: string): RecordData {
  const days = Number(values.days), concurrency = Number(values.concurrency);
  if (!values.name.trim() || values.name.length > 60) throw new Error('请填写 1–60 个字的套餐名称');
  if (!Number.isInteger(days) || days < 1 || days > 3650) throw new Error('有效天数要在 1 到 3650 之间');
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 100) throw new Error('并发数要在 1 到 100 之间');
  if (!values.selected.length) throw new Error('至少选择一个模型');
  const chosen = items.filter(item => values.selected.includes(item.id));
  const models: Record<string, unknown> = {};
  for (const model of values.selected) if ((values.overrides[model] ?? '').trim()) models[model] = multiplierFraction(values.overrides[model]);
  const pricing: RecordData = { default: multiplierFraction(values.multiplier) };
  if (Object.keys(models).length) pricing.models = models;
  return { id, name: values.name.trim(), version: '1', included_products: ['easel'], features: [...new Set(chosen.map(item => item.feature))].sort(), credits: decimalUnits(values.credits.trim(), 6), days, concurrency, test_only: false, models: values.selected, pricing };
}

export function PlanEditor({ client, onClose, onFailure, onDone }: { client: ConsoleClient; onClose: () => void; onFailure: (e: unknown) => string; onDone: () => void }) {
  const [items, setItems] = useState<CatalogRow[] | null>(null), [templates, setTemplates] = useState<Template[]>([]);
  const [name, setName] = useState(''), [credits, setCredits] = useState(''), [days, setDays] = useState('30'), [concurrency, setConcurrency] = useState('2'), [multiplier, setMultiplier] = useState('1');
  const [selected, setSelected] = useState<string[]>([]), [overrides, setOverrides] = useState<Record<string, string>>({}), [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission()), planId = useRef(`plan-${Date.now().toString(36)}`);
  useEffect(() => { const c = new AbortController(); void client.read<{ items: CatalogRow[]; templates: Template[] }>('/api/catalog', c.signal).then(v => { if (!c.signal.aborted) { setItems(v.items); setTemplates(v.templates); } }).catch(() => { if (!c.signal.aborted) setItems([]); }); return () => c.abort(); }, [client]);
  const toggle = (id: string) => setSelected(list => list.includes(id) ? list.filter(x => x !== id) : [...list, id]);
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason), definition = buildPlan({ name, credits, days, concurrency, multiplier, selected, overrides }, items ?? [], planId.current);
      setBusy(true); await submission.send(client, '/api/plans', { definition, reason: why }, 'plans:publish'); onDone();
    } catch (cause) { setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '发布失败'); } finally { setBusy(false); }
  };
  return <Modal title="发布套餐" onClose={onClose} busy={busy} wide><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <label className="reason-field">套餐名称<input value={name} maxLength={60} disabled={busy} onChange={e => setName(e.target.value)} placeholder="例如：专业版" /></label>
    <div className="field-row"><label className="reason-field">总点数<input inputMode="decimal" value={credits} disabled={busy} onChange={e => setCredits(e.target.value)} /></label><label className="reason-field">有效天数<input inputMode="numeric" value={days} disabled={busy} onChange={e => setDays(e.target.value)} /></label></div>
    <div className="field-row"><label className="reason-field">同时运行的任务数<input inputMode="numeric" value={concurrency} disabled={busy} onChange={e => setConcurrency(e.target.value)} /></label><label className="reason-field">默认消耗倍率<input inputMode="decimal" value={multiplier} disabled={busy} onChange={e => setMultiplier(e.target.value)} /></label></div>
    <p className="muted">倍率 1 表示按模型单价扣点，0.8 表示打八折。可以给单个模型设置不同的倍率，留空就用默认倍率。这个倍率只对这个套餐发放的点数生效。</p>
    <fieldset className="model-picker"><legend>可使用的模型</legend>{items === null ? <p className="muted">正在读取模型…</p> : items.length === 0 ? <p className="muted">还没有模型，请先在“模型”页添加。</p> : items.map(item => <div key={item.id} className="model-pick"><label className="check"><input type="checkbox" checked={selected.includes(item.id)} disabled={busy} onChange={() => toggle(item.id)} /> {item.name} <small className="muted">{templates.find(t => t.capability === item.capability)?.label ?? item.capability}{item.enabled ? '' : ' · 未启用'}</small></label>{selected.includes(item.id) && <input aria-label={`${item.name} 的倍率`} className="rate-input" inputMode="decimal" placeholder="默认" value={overrides[item.id] ?? ''} disabled={busy} onChange={e => setOverrides({ ...overrides, [item.id]: e.target.value })} />}</div>)}</fieldset>
    <label className="reason-field">原因<textarea required maxLength={255} rows={2} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || submission.conflict}>{busy ? '发布中…' : '发布套餐'}</button></div></form></Modal>;
}
