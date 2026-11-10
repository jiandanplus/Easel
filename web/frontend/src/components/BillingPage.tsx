import { Fragment, useEffect, useMemo, useState } from 'react';
import { centralBillingCatalog, centralEntitlements, centralMediaModels, centralModels, centralOrders, centralPlans, centralStatus } from '../lib/api';
import type { CentralBillingCatalog, CentralEntitlements, CentralMediaModel, CentralModel, CentralOrder, CentralPlan, CentralStatus } from '../lib/api';
import CentralBillingSettings, { BillingOrderView, orderLabel } from './CentralBillingSettings';
import PlatformAccountSettings from './PlatformAccountSettings';
import { pointsText, yuan } from './PlanSettings';

const FEATURES: Record<string, string> = { text: '对话与脚本', image: '图片生成', video: '视频生成', voice: '配音', music: '音乐生成', transcribe: '语音转写', audio: '语音' };
const featureLabel = (feature: string) => FEATURES[feature] || feature;
/** Order status in a few words for the history table; the order detail keeps the long explanation. */
export function orderStatus(order: Pick<CentralOrder, 'status' | 'credit_state'>): { text: string; tone: 'ok' | 'wait' | 'bad' | 'muted' } {
  if (order.status === 'paid') return order.credit_state === 'granted' ? { text: '已支付', tone: 'ok' } : { text: '入账中', tone: 'wait' };
  return ({ creating: { text: '待支付', tone: 'wait' }, pending: { text: '待支付', tone: 'wait' }, closed: { text: '已关闭', tone: 'muted' }, refunded: { text: '已退款', tone: 'muted' }, reconciliation_required: { text: '核算中', tone: 'bad' } } as Record<string, { text: string; tone: 'ok' | 'wait' | 'bad' | 'muted' }>)[order.status] ?? { text: '待确认', tone: 'bad' };
}

type Load = { status: CentralStatus | null; orders: CentralOrder[]; catalog: CentralBillingCatalog | null; entitlements: CentralEntitlements | null; plans: CentralPlan[]; text: CentralModel[]; media: CentralMediaModel[]; ready: boolean; error: string };

/** 账单：账单摘要、当前套餐（包含与不包含的权益）、账单历史。参考 LobeHub 的账单页。 */
export default function BillingPage({ onOpenUsage, onOpenPlans }: { onOpenUsage?: () => void; onOpenPlans?: () => void }) {
  const [data, setData] = useState<Load>({ status: null, orders: [], catalog: null, entitlements: null, plans: [], text: [], media: [], ready: false, error: '' });
  const [revision, setRevision] = useState(0), [open, setOpen] = useState(''), [page, setPage] = useState(1);
  useEffect(() => {
    const controller = new AbortController(), signal = controller.signal;
    const soft = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
    void (async () => {
      try {
        const status = await centralStatus(signal);
        if (!status.authenticated) { if (!signal.aborted) setData(d => ({ ...d, status, ready: true })); return; }
        const [orders, catalog, entitlements, plans, text, media] = await Promise.all([soft(centralOrders(signal), { orders: [] }), soft(centralBillingCatalog(signal), null), soft(centralEntitlements(signal), null), soft(centralPlans(signal), { plans: [] }), soft(centralModels(signal), { models: [] }), soft(centralMediaModels(signal), { models: [] })]);
        if (!signal.aborted) setData({ status, orders: orders.orders, catalog, entitlements, plans: plans.plans, text: text.models, media: media.models, ready: true, error: '' });
      } catch (failure) { if (!signal.aborted) setData(d => ({ ...d, ready: true, error: failure instanceof Error ? failure.message : '读取失败' })); }
    })();
    return () => controller.abort();
  }, [revision]);
  const orders = useMemo(() => [...data.orders].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)), [data.orders]);

  if (!data.ready) return <section className="st-sec active settings-page-body"><p role="status">正在读取账单…</p></section>;
  if (data.error) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">账单暂不可用：{data.error}</p></section>;
  if (!data.status?.authenticated) return <PlatformAccountSettings view="billing" />;

  const memberships = data.entitlements?.memberships ?? [];
  const current = memberships.map(item => ({ item, plan: data.plans.find(plan => plan.id === item.plan_id) })).sort((a, b) => Date.parse(String(b.item.expires_at)) - Date.parse(String(a.item.expires_at)))[0];
  const plan = current?.plan ?? null;
  const allFeatures = [...new Set(data.plans.filter(p => !p.test_only).flatMap(p => p.features))];
  const included = plan ? plan.features : data.entitlements?.features ?? [];
  const missing = allFeatures.filter(feature => !included.includes(feature));
  const names = new Map<string, string>([...data.text.map(m => [m.id, m.name || m.id] as [string, string]), ...data.media.map(m => [m.id, m.name || m.id] as [string, string])]);
  const models = plan && Array.isArray(plan.models) ? plan.models.map(id => names.get(id) || id) : [...names.values()];
  const payable = Boolean(data.catalog && data.catalog.mode !== 'disabled' && data.catalog.channels.some(channel => channel.enabled));
  const size = 10, pages = Math.max(1, Math.ceil(orders.length / size)), shownPage = Math.min(page, pages);
  const date = (value: string | number | null | undefined) => { if (value == null) return '—'; const d = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(); };
  const lastPaid = orders.find(order => order.status === 'paid');

  return <section className="st-sec active usage-page billing-page" aria-label="账单">
    {!payable && <div className="billing-banner"><span aria-hidden="true">▭</span><div><strong>在线支付即将开放</strong><p className="desc">开放后可以在这里用微信或支付宝购买和续订套餐，付款记录会显示在下方的账单历史里。</p></div>{onOpenPlans && <button type="button" className="btn btn-sm" onClick={onOpenPlans}>查看套餐</button>}</div>}

    <div className="usage-block">
      <div className="usage-block-head"><h3>账单摘要</h3></div>
      <div className="settings-card usage-card billing-summary">
        <div><p className="usage-label">你的下次付款</p><strong className="balance-amount">¥0.00</strong>
          <p className="desc">目前没有自动续费，套餐到期后需要手动续订。{onOpenUsage && <button type="button" className="billing-link" onClick={onOpenUsage}>查看本月使用情况</button>}</p></div>
        <div className="billing-info"><p className="usage-label">账单信息</p>
          <p>当前套餐：<b>{plan?.name || (current ? '内部额度' : '未订阅')}</b></p>
          {current && <p>到期时间：{date(current.item.expires_at)}</p>}
          {lastPaid && <p>最近付款：¥{yuan(lastPaid.amount_minor)} · {date(lastPaid.paid_at)}</p>}
          {onOpenPlans && <button type="button" className="billing-link" onClick={onOpenPlans}>升级套餐</button>}</div>
      </div>
    </div>

    <div className="usage-block">
      <div className="usage-block-head"><h3>当前套餐</h3>{onOpenPlans && <button type="button" className="btn btn-sm" onClick={onOpenPlans}>升级</button>}</div>
      <div className="settings-card usage-card billing-plan">
        <div className="billing-plan-head"><strong>{plan?.name || (current ? '内部额度' : '未订阅')}</strong>
          <p className="desc">{plan ? `每个周期 ${pointsText(plan.credits)} 点 · ${plan.days} 天 · 同时运行 ${plan.concurrency} 个任务` : current ? '由运营者发放的额度' : '开通套餐后可以使用中央接口的模型'}</p></div>
        <div className="billing-features">
          <ul>
            {plan && <li className="yes">额度 · {pointsText(plan.credits)} 点，{plan.days} 天内使用</li>}
            {included.map(feature => <li key={feature} className="yes">{featureLabel(feature)}</li>)}
            {models.length > 0 && <li className="yes">模型 {models.slice(0, 6).join(' / ')}{models.length > 6 ? ` 等 ${models.length} 个` : ''}</li>}
          </ul>
          <ul>{missing.map(feature => <li key={feature} className="no">{featureLabel(feature)}</li>)}{missing.length === 0 && included.length > 0 && <li className="yes">已包含全部功能</li>}</ul>
        </div>
      </div>
    </div>

    <div className="usage-block">
      <div className="usage-block-head"><h3>账单历史</h3></div>
      <div className="settings-card usage-card usage-table-card">
        <div className="usage-table-scroll"><table className="usage-table billing-table">
          <thead><tr><th>订单编号</th><th>套餐</th><th>交易状态</th><th>金额</th><th>付款日期</th><th /></tr></thead>
          <tbody>
            {orders.length === 0 && <tr><td colSpan={6} className="usage-empty">暂无数据</td></tr>}
            {orders.slice((shownPage - 1) * size, shownPage * size).map(order => { const state = orderStatus(order); return <Fragment key={order.id}>
              <tr><td className="mono">{order.id.slice(0, 12)}…</td><td>{order.offer?.name ?? '—'}{order.mode === 'test' && <span className="muted">（测试）</span>}</td>
                <td><span className={`billing-state ${state.tone}`}>{state.text}</span></td><td>¥{yuan(order.amount_minor)}</td><td>{date(order.paid_at ?? order.created_at)}</td>
                <td><button type="button" className="billing-link" onClick={() => setOpen(open === order.id ? '' : order.id)}>{open === order.id ? '收起' : '详情'}</button></td></tr>
              {open === order.id && <tr className="billing-detail"><td colSpan={6}><p className="desc">{orderLabel(order)}</p><BillingOrderView order={order} now={Date.now()} /></td></tr>}
            </Fragment>; })}
          </tbody>
        </table></div>
        {orders.length > size && <div className="usage-pager"><span>共 {orders.length} 条</span><div>
          <button type="button" disabled={shownPage <= 1} onClick={() => setPage(shownPage - 1)} aria-label="上一页">‹</button>
          {Array.from({ length: pages }, (_, i) => i + 1).map(n => <button type="button" key={n} className={n === shownPage ? 'on' : ''} onClick={() => setPage(n)}>{n}</button>)}
          <button type="button" disabled={shownPage >= pages} onClick={() => setPage(shownPage + 1)} aria-label="下一页">›</button></div></div>}
      </div>
    </div>

    {payable && data.status && <div className="usage-block"><div className="usage-block-head"><h3>购买套餐</h3></div><div className="settings-card usage-card billing-buy"><CentralBillingSettings status={data.status} onFinancialChange={() => setRevision(value => value + 1)} /></div></div>}
  </section>;
}
