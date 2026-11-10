import { useEffect, useRef, useState } from 'react';
import { ConsoleError, decimalUnits, formatCredits, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';
import type { CatalogRow, Template } from './ModelCatalogPage';
import { Modal } from './views';

export type PlanMode = { kind: 'create' } | { kind: 'edit' | 'copy'; base: RecordData };

export function multiplierFraction(value: string): { numerator: string; denominator: string } {
  const text = value.trim();
  if (!/^\d{1,4}(\.\d{1,4})?$/.test(text) || Number(text) <= 0) throw new Error('倍率要大于 0，最多 4 位小数，例如 0.8');
  const [whole, fraction = ''] = text.split('.');
  return { numerator: String(BigInt(whole + fraction)), denominator: String(10n ** BigInt(fraction.length)) };
}
/** The reverse of multiplierFraction, for filling the form from a published plan. */
export function fractionText(value: unknown): string {
  if (!value || typeof value !== 'object') return '';
  const { numerator, denominator } = value as Record<string, unknown>;
  if (typeof numerator !== 'string' || typeof denominator !== 'string' || !/^\d+$/.test(numerator) || !/^[1-9]\d*$/.test(denominator)) return '';
  const n = Number(numerator) / Number(denominator);
  return Number.isFinite(n) ? String(Math.round(n * 10000) / 10000) : '';
}
/** An edit is a new version of the same plan name; the old version keeps serving its members. */
export function nextVersion(version: unknown): string {
  const text = typeof version === 'string' ? version : '';
  return /^\d{1,6}$/.test(text) ? String(Number(text) + 1) : `${text || '1'}-${Date.now().toString(36)}`.slice(0, 64);
}
export function buildPlan(values: { name: string; credits: string; days: string; concurrency: string; multiplier: string; selected: string[]; overrides: Record<string, string> }, items: CatalogRow[], id: string, version = '1'): RecordData {
  const days = Number(values.days), concurrency = Number(values.concurrency);
  if (!values.name.trim() || values.name.length > 60) throw new Error('请填写 1–60 个字的套餐名称');
  if (!Number.isInteger(days) || days < 1 || days > 3650) throw new Error('有效天数要在 1 到 3650 之间');
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 100) throw new Error('并发数要在 1 到 100 之间');
  if (!values.selected.length) throw new Error('至少选择一个模型');
  const chosen = items.filter(item => values.selected.includes(item.id));
  if (chosen.length !== values.selected.length) throw new Error('有勾选的模型已经不存在，请取消勾选后再保存');
  const models: Record<string, unknown> = {};
  for (const model of values.selected) if ((values.overrides[model] ?? '').trim()) models[model] = multiplierFraction(values.overrides[model]);
  const pricing: RecordData = { default: multiplierFraction(values.multiplier) };
  if (Object.keys(models).length) pricing.models = models;
  return { id, name: values.name.trim(), version, included_products: ['easel'], features: [...new Set(chosen.map(item => item.feature))].sort(), credits: decimalUnits(values.credits.trim(), 6), days, concurrency, test_only: false, models: values.selected, pricing };
}
/** Form values taken from a published plan. A plan without a model list allows every model of its features. */
export function planDraft(base: RecordData, items: CatalogRow[]) {
  const features = Array.isArray(base.features) ? base.features as string[] : [];
  const selected = Array.isArray(base.models) ? (base.models as string[]).filter(id => items.some(item => item.id === id)) : items.filter(item => features.includes(item.feature)).map(item => item.id);
  const pricing = (base.pricing && typeof base.pricing === 'object' ? base.pricing : {}) as RecordData;
  const overrides: Record<string, string> = {};
  for (const [model, value] of Object.entries((pricing.models && typeof pricing.models === 'object' ? pricing.models : {}) as RecordData)) if (selected.includes(model)) overrides[model] = fractionText(value);
  return { name: typeof base.name === 'string' ? base.name : '', credits: formatCredits(base.credits), days: String(base.days ?? '30'), concurrency: String(base.concurrency ?? '2'), multiplier: fractionText(pricing.default) || '1', selected, overrides };
}
const FAILURES: Record<string, string> = {
  plan_version_immutable: '已经有同名同版本的套餐，请换一个名称。',
  console_plan_retired: '旧套餐已经停用，请刷新页面。',
  console_plan_has_offers: '旧套餐还有在售价格，请先到“售卖价格”撤回，再编辑。',
  console_plan_model_unknown: '勾选的模型有的已不存在，请刷新后重新勾选。',
};

export function PlanEditor({ client, mode = { kind: 'create' }, onClose, onFailure, onDone }: { client: ConsoleClient; mode?: PlanMode; onClose: () => void; onFailure: (e: unknown) => string; onDone: () => void }) {
  const base = mode.kind === 'create' ? null : mode.base;
  const [items, setItems] = useState<CatalogRow[] | null>(null), [templates, setTemplates] = useState<Template[]>([]);
  const [name, setName] = useState(''), [credits, setCredits] = useState(''), [days, setDays] = useState('30'), [concurrency, setConcurrency] = useState('2'), [multiplier, setMultiplier] = useState('1');
  const [selected, setSelected] = useState<string[]>([]), [overrides, setOverrides] = useState<Record<string, string>>({});
  const [reason, setReason] = useState(mode.kind === 'edit' ? '修改套餐' : mode.kind === 'copy' ? '复制套餐' : ''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission()), planId = useRef(`plan-${Date.now().toString(36)}`);
  useEffect(() => { const c = new AbortController(); void client.read<{ items: CatalogRow[]; templates: Template[] }>('/api/catalog', c.signal).then(v => {
    if (c.signal.aborted) return;
    setItems(v.items); setTemplates(v.templates);
    if (base) { const d = planDraft(base, v.items); setName(mode.kind === 'copy' ? `${d.name} 副本`.slice(0, 60) : d.name); setCredits(d.credits); setDays(d.days); setConcurrency(d.concurrency); setMultiplier(d.multiplier); setSelected(d.selected); setOverrides(d.overrides); }
  }).catch(() => { if (!c.signal.aborted) setItems([]); }); return () => c.abort(); }, [client, base, mode.kind]);
  const toggle = (id: string) => setSelected(list => list.includes(id) ? list.filter(x => x !== id) : [...list, id]);
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason), version = mode.kind === 'edit' ? nextVersion(base!.version) : '1';
      const definition = buildPlan({ name, credits, days, concurrency, multiplier, selected, overrides }, items ?? [], planId.current, version);
      setBusy(true);
      await submission.send(client, mode.kind === 'edit' ? `/api/plans/${encodeURIComponent(String(base!.id))}/revise` : '/api/plans', { definition, reason: why }, 'plans:publish');
      onDone();
    } catch (cause) { setError(cause instanceof ConsoleError ? FAILURES[cause.code] ?? onFailure(cause) : cause instanceof Error ? cause.message : '发布失败'); } finally { setBusy(false); }
  };
  const title = mode.kind === 'edit' ? `编辑套餐 · ${String(base!.name)}` : mode.kind === 'copy' ? '复制套餐' : '发布套餐';
  return <Modal title={title} onClose={onClose} busy={busy} wide><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {mode.kind === 'edit' && <p className="notice">保存后会发布这个套餐的新版本（第 {nextVersion(base!.version)} 版），旧版本自动停用：以后发放和兑换码都用新版本，已经发给用户的旧版本照常使用到期。</p>}
    <label className="reason-field">套餐名称<input value={name} maxLength={60} disabled={busy} onChange={e => setName(e.target.value)} placeholder="例如：专业版" /></label>
    <div className="field-row"><label className="reason-field">总点数<input inputMode="decimal" value={credits} disabled={busy} onChange={e => setCredits(e.target.value)} /></label><label className="reason-field">有效天数<input inputMode="numeric" value={days} disabled={busy} onChange={e => setDays(e.target.value)} /></label></div>
    <div className="field-row"><label className="reason-field">同时运行的任务数<input inputMode="numeric" value={concurrency} disabled={busy} onChange={e => setConcurrency(e.target.value)} /></label><label className="reason-field">默认消耗倍率<input inputMode="decimal" value={multiplier} disabled={busy} onChange={e => setMultiplier(e.target.value)} /></label></div>
    <p className="muted">倍率 1 表示按模型单价扣点，0.8 表示打八折。可以给单个模型设置不同的倍率，留空就用默认倍率。这个倍率只对这个套餐发放的点数生效。</p>
    <fieldset className="model-picker"><legend>可使用的模型{items && items.length > 0 && <span className="muted"> · 已选 {selected.length} 个 <button type="button" className="text-button" disabled={busy} onClick={() => setSelected(selected.length === items.length ? [] : items.map(item => item.id))}>{selected.length === items.length ? '全不选' : '全选'}</button></span>}</legend>{items === null ? <p className="muted">正在读取模型…</p> : items.length === 0 ? <p className="muted">还没有模型，请先在“模型”页添加。</p> : items.map(item => <div key={item.id} className="model-pick"><label className="check"><input type="checkbox" checked={selected.includes(item.id)} disabled={busy} onChange={() => toggle(item.id)} /> {item.name} <small className="muted">{templates.find(t => t.capability === item.capability)?.label ?? item.capability}{item.enabled ? '' : ' · 未启用'}</small></label>{selected.includes(item.id) && <input aria-label={`${item.name} 的倍率`} className="rate-input" inputMode="decimal" placeholder="默认" value={overrides[item.id] ?? ''} disabled={busy} onChange={e => setOverrides({ ...overrides, [item.id]: e.target.value })} />}</div>)}</fieldset>
    <label className="reason-field">原因<textarea required maxLength={255} rows={2} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || submission.conflict || items === null}>{busy ? '保存中…' : mode.kind === 'edit' ? '发布新版本' : '发布套餐'}</button></div></form></Modal>;
}

const STATE_FAILURES: Record<string, string> = {
  console_plan_has_offers: '这个套餐还有在售价格，请先到“售卖价格”撤回，再停用。',
  console_plan_retired: '这个套餐已经停用了，请刷新页面。',
  console_internal_plan_protected: '系统套餐由服务自动管理，不能停用。',
  console_plan_not_found: '找不到这个套餐，请刷新页面。',
};

export function PlanStateDialog({ client, plan, onClose, onFailure, onDone }: { client: ConsoleClient; plan: RecordData; onClose: () => void; onFailure: (e: unknown) => string; onDone: (message: string) => void }) {
  const retire = plan.retired !== true;
  const [reason, setReason] = useState(retire ? '停用套餐' : '恢复套餐'), [error, setError] = useState(''), [busy, setBusy] = useState(false), [submission] = useState(() => new Submission());
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason); setBusy(true);
      await submission.send(client, `/api/plans/${encodeURIComponent(String(plan.id))}/state`, { retired: retire, reason: why }, 'plans:publish');
      onDone(retire ? '套餐已停用。已发给用户的照常使用，以后不能再发放或生成兑换码。' : '套餐已恢复，可以重新发放。');
    } catch (cause) { setError(cause instanceof ConsoleError ? STATE_FAILURES[cause.code] ?? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'); } finally { setBusy(false); }
  };
  return <Modal title={`${retire ? '停用' : '恢复'}套餐 · ${String(plan.name)}`} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <p className="muted">{retire ? '停用后，这个套餐不能再发给用户，也不能再生成兑换码，桌面端的套餐列表里也不再显示。已经发给用户的照常使用到期，已经发出去的兑换码仍然可以兑换。套餐不会被删除，随时可以恢复。' : '恢复后可以重新发放和生成兑换码。'}</p>
    <label className="reason-field">原因<input maxLength={255} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className={`button ${retire ? 'danger' : 'primary'}`} disabled={busy || submission.conflict}>{busy ? '提交中…' : retire ? '确认停用' : '确认恢复'}</button></div></form></Modal>;
}
