import { useEffect, useMemo, useState } from 'react';
import { ConsoleError, decimalUnits, Submission, validReason } from './core';
import type { ConsoleClient, PageData, RecordData } from './core';
import { Modal } from './views';

export type PayPeriod = 'year' | 'month' | 'once';
export const PERIODS: { id: PayPeriod; label: string; hint: string }[] = [
  { id: 'year', label: '按年', hint: '一次买 12 个周期，点数和有效期都是 12 倍。填一年的总价。' },
  { id: 'month', label: '按月', hint: '买 1 个周期。' },
  { id: 'once', label: '一次性', hint: '买 1 个周期，不续费，可用支付宝或微信支付。' },
];
export const periodLabel = (value: unknown) => PERIODS.find(item => item.id === (value || 'once'))?.label ?? '一次性';

/** Live prices of a plan, keyed by way of paying. Offers without a period are one-time. */
export function livePrices(offers: RecordData[], planId: string): Partial<Record<PayPeriod, RecordData>> {
  const result: Partial<Record<PayPeriod, RecordData>> = {};
  for (const offer of offers) {
    if (offer.plan_id !== planId || offer.withdrawn === true) continue;
    const period = (offer.period || 'once') as PayPeriod;
    if (!result[period]) result[period] = offer;  // The list is newest first.
  }
  return result;
}
export function yuanText(minor: unknown): string {
  if (typeof minor !== 'string' || !/^\d+$/.test(minor)) return '';
  const value = BigInt(minor);
  return `${value / 100n}${value % 100n ? `.${(value % 100n).toString().padStart(2, '0')}` : ''}`;
}
export function buildOffer(planId: string, period: PayPeriod, amount: string, description: string, stamp: string): RecordData {
  const minor = decimalUnits(amount.trim(), 2);
  const id = `offer-${planId}-${period}-${stamp}`.replace(/[^A-Za-z0-9_.-]/g, '-').slice(0, 120);
  return { id, plan_id: planId, version: `${period}-${stamp}`, amount_minor: minor, currency: 'CNY', test_only: false, period,
    terms: { refund_policy: 'unused_full_only', description: description.trim() || '仅全部未使用时支持全额退款。' } };
}

/** 设置价格：一个套餐的按年、按月、一次性三种价格一次填好。新价格替代同一付费方式的旧价格。 */
export function PriceEditor({ client, onClose, onFailure, onDone }: { client: ConsoleClient; onClose: () => void; onFailure: (e: unknown) => string; onDone: (message: string) => void }) {
  const [plans, setPlans] = useState<RecordData[] | null>(null), [offers, setOffers] = useState<RecordData[]>([]);
  const [planId, setPlanId] = useState(''), [amounts, setAmounts] = useState<Record<PayPeriod, string>>({ year: '', month: '', once: '' });
  const [description, setDescription] = useState(''), [reason, setReason] = useState('设置售卖价格'), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submissions] = useState(() => ({ year: new Submission(), month: new Submission(), once: new Submission() })), [stamp] = useState(() => Date.now().toString(36));
  useEffect(() => {
    const c = new AbortController();
    void Promise.all([client.read<PageData>('/api/plans?limit=100&offset=0', c.signal), client.read<PageData>('/api/offers?limit=100&offset=0', c.signal)]).then(([p, o]) => {
      if (c.signal.aborted) return;
      const usable = p.items.filter(plan => !String(plan.id).startsWith('easel-') && plan.retired !== true && plan.test_only !== true);
      setPlans(usable); setOffers(o.items);
      if (usable[0]) setPlanId(String(usable[0].id));
    }).catch(() => { if (!c.signal.aborted) setPlans([]); });
    return () => c.abort();
  }, [client]);
  const current = useMemo(() => livePrices(offers, planId), [offers, planId]);
  useEffect(() => { setAmounts({ year: yuanText(current.year?.amount_minor), month: yuanText(current.month?.amount_minor), once: yuanText(current.once?.amount_minor) }); }, [current]);
  const plan = plans?.find(item => item.id === planId);
  const saving = (() => {
    const year = Number(amounts.year), month = Number(amounts.month);
    return year > 0 && month > 0 && year < month * 12 ? Math.round((1 - year / (month * 12)) * 100) : 0;
  })();
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const why = validReason(reason);
      const changed = PERIODS.filter(({ id }) => amounts[id].trim() && amounts[id].trim() !== yuanText(current[id]?.amount_minor));
      if (!planId) throw new Error('请选择套餐');
      if (!changed.length) throw new Error('没有修改任何价格');
      const definitions = changed.map(({ id }) => [id, buildOffer(planId, id, amounts[id], description, stamp)] as const);
      setBusy(true);
      for (const [id, definition] of definitions) await submissions[id].send(client, '/api/offers', { definition, reason: why }, 'billing:publish');
      onDone(`已更新「${String(plan?.name ?? planId)}」的${changed.map(item => item.label).join('、')}价格。旧价格已自动撤下。`);
    } catch (cause) {
      const code = cause instanceof ConsoleError ? cause.code : '';
      setError(code === 'console_plan_retired' ? '这个套餐已经停用，不能设置价格。' : cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '保存失败');
    } finally { setBusy(false); }
  };
  return <Modal title="设置价格" onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {plans === null ? <p className="muted">正在读取套餐…</p> : plans.length === 0 ? <p className="muted">还没有可以定价的套餐，请先在“套餐”页新建。</p> : <>
      <label className="reason-field">套餐<select value={planId} disabled={busy} onChange={e => setPlanId(e.target.value)}>{plans.map(item => <option key={String(item.id)} value={String(item.id)}>{String(item.name)}</option>)}</select></label>
      {plan && <p className="muted">每个周期 {Number(BigInt(String(plan.credits ?? '0')) / 1000000n).toLocaleString()} 点 · {String(plan.days)} 天。留空的付费方式不出售；只改动的价格会发布。</p>}
      {PERIODS.map(({ id, label, hint }) => <label key={id} className="reason-field">{label}价格（元）{current[id] && <small className="muted">　当前 ¥{yuanText(current[id]!.amount_minor)}</small>}
        <input inputMode="decimal" value={amounts[id]} disabled={busy} placeholder={id === 'year' ? '一年的总价，例如 216' : '例如 20'} onChange={e => setAmounts({ ...amounts, [id]: e.target.value })} />
        <small className="muted">{hint}{id === 'year' && saving > 0 ? ` 比按月付 12 次便宜 ${saving}%。` : ''}</small></label>)}
      <label className="reason-field">购买说明（可选）<input maxLength={200} value={description} disabled={busy} onChange={e => setDescription(e.target.value)} placeholder="仅全部未使用时支持全额退款。" /></label>
      <p className="muted">付款开放前，用户能在桌面端看到价格，但还不能下单。</p>
    </>}
    <label className="reason-field">原因<input maxLength={255} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || !plans?.length}>{busy ? '保存中…' : '保存价格'}</button></div></form></Modal>;
}
