/* eslint-disable react/only-export-components -- Exact formatting and order states are shared with the rendering contract tests. */
import { useEffect, useRef, useState } from 'react';
import { centralBillingCatalog, centralOrders, centralOrder, centralCreateOrder, centralRefreshOrder, centralCloseOrder } from '../lib/api';
import type { CentralStatus, CentralBillingCatalog, CentralOrder, CentralOffer, CentralCreateOrder } from '../lib/api';
import './CentralBillingSettings.css';

export function formatCny(value: string): string {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value)) return '金额无效';
  const cents = BigInt(value);
  return `¥${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`;
}
export function formatCredits(value: string): string {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value)) return '点数未知';
  const points = BigInt(value), fraction = (points % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${points / 1000000n}${fraction ? `.${fraction}` : ''}`;
}
const date = (value: string) => { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '未知' : parsed.toLocaleString(); };
const message = (error: unknown) => error instanceof Error ? error.message : '服务暂不可用，请重试';
const waiting = (order: CentralOrder) => ['creating', 'pending'].includes(order.status) || (order.status === 'paid' && order.credit_state !== 'granted');
export function orderLabel(order: CentralOrder): string {
  if (order.status === 'paid') return order.credit_state === 'granted' ? '已确认到账' : '付款已确认，权益入账处理中';
  return ({ creating: '正在生成收款码', pending: '等待渠道确认付款', closed: '订单已关闭', reconciliation_required: '待人工核算', refunded: '已退款' } as Record<string, string>)[order.status] || '状态待确认，请查询或联系运营者';
}
const safeQrDataUrl = (value: unknown): value is string => typeof value === 'string' && value.length <= 1000000 && /^data:image\/svg\+xml;base64,[A-Za-z0-9+/=]+$/.test(value);
const orderRevision = (order: CentralOrder) => typeof order.revision === 'string' && /^(0|[1-9]\d*)$/.test(order.revision) ? BigInt(order.revision) : null;
const manualReview = (order: CentralOrder) => order.status === 'reconciliation_required' || order.credit_state === 'manual_review' || ['reconciliation_required', 'manual_reconciliation', 'manual_review'].includes(order.refund?.status || '');
const refundStage = (order: CentralOrder) => ({ pending: 1, refund_pending: 1, failed: 2, refund_failed: 2, succeeded: 3, refunded: 3 } as Record<string, number>)[order.refund?.status || ''] || 0;
function mergeOrder(previous: CentralOrder | undefined, next: CentralOrder): CentralOrder {
  if (!previous) return next;
  const before = orderRevision(previous), after = orderRevision(next);
  // A server revision covers all financial fields. Lists omit the derived QR
  // image, so a matching detail may enrich that image at the same revision.
  if (before !== null && after !== null) {
    if (after > before) return next;
    const checkout = previous.checkout, detail = next.checkout;
    if (after === before && checkout && detail && !checkout.qr_data_url && checkout.qr_code === detail.qr_code && checkout.expires_at === detail.expires_at && safeQrDataUrl(detail.qr_data_url)) {
      return { ...previous, checkout: { ...checkout, qr_data_url: detail.qr_data_url } };
    }
    return previous;
  }
  // Older projections may lack revisions. Keep financial facts conservatively,
  // while always exposing a newly observed accounting problem after a refund.
  if (manualReview(next)) return next;
  if (manualReview(previous)) return previous;
  if (previous.status === 'refunded' && next.status !== 'refunded') return previous;
  if (previous.status === 'paid' && ['creating', 'pending', 'closed'].includes(next.status)) return previous;
  if (previous.status === 'closed' && ['creating', 'pending'].includes(next.status)) return previous;
  if (previous.status === 'paid' && previous.credit_state === 'granted' && next.status === 'paid' && next.credit_state !== 'granted') return previous;
  if (previous.refund && (!next.refund || refundStage(next) < refundStage(previous))) return previous;
  return next;
}
function financialState(order: CentralOrder): string | null {
  if (manualReview(order) || order.refund || order.status === 'refunded' || order.credit_state === 'revoked' || (order.status === 'paid' && order.credit_state === 'granted')) {
    return JSON.stringify([order.status, order.credit_state, order.refund?.id || null, order.refund?.status || null]);
  }
  return null;
}
export function BillingOrderView({ order, now }: { order: CentralOrder; now: number }) {
  const qr = order.checkout;
  const expired = !!qr && (!Number.isFinite(new Date(qr.expires_at).getTime()) || new Date(qr.expires_at).getTime() <= now);
  const qrDataUrl = qr?.qr_data_url;
  const safeQr = safeQrDataUrl(qrDataUrl) ? qrDataUrl : undefined;
  return <article className="central-billing-checkout" aria-label="当前订单">
    <div className="central-billing-order-heading"><strong>{order.offer.name}</strong><span className="central-billing-price">{formatCny(order.amount_minor)}</span></div>
    <p className={order.mode === 'test' ? 'central-billing-test' : 'desc'}>{order.mode === 'test' ? '模拟支付 · 仅产生测试权益' : '正式订单 · 人工扫码支付'}</p>
    <p role="status">{orderLabel(order)}</p><p>{formatCredits(order.offer.credits)} 点 · {order.offer.days} 天 · 并发 {order.offer.concurrency}</p>
    <p>渠道：{order.channel === 'wechat' ? '微信' : '支付宝'} · 套餐版本 {order.offer.version}</p>
    <p className="desc">订单 {order.id}<br />创建 {date(order.created_at)} · 到期 {date(order.expires_at)}</p>
    <p>退款条款：仅全部未使用时支持全额退款。{order.offer.terms.description}</p>
    {order.refund && <p role="status">退款状态：{({ pending: '退款处理中，点数已冻结', refund_pending: '退款处理中，点数已冻结', refunded: '已退款', succeeded: '已退款', failed: '退款未完成，请联系运营者', reconciliation_required: '待人工核算', manual_reconciliation: '待人工核算', manual_review: '待人工核算', refund_failed: '退款未完成，请联系运营者' } as Record<string, string>)[order.refund.status] || '待确认，请联系运营者'}</p>}
    {waiting(order) && qr && (expired ? <p role="status">二维码已过期，请查询订单状态后重新下单。</p> : safeQr ? <figure><img className="central-billing-qr" src={safeQr} alt={`${order.channel === 'wechat' ? '微信' : '支付宝'}支付二维码`} /><figcaption>请使用对应渠道扫码。二维码有效至 {date(qr.expires_at)}。</figcaption></figure> : <p role="status">收款码暂未就绪，请查询订单状态。</p>)}
  </article>;
}
type Props = { status: CentralStatus; onFinancialChange: () => void };
export default function CentralBillingSettings(props: Props) {
  const scope = props.status.scope;
  const identity = [props.status.url, props.status.authenticated, scope?.user_id, scope?.tenant_id, scope?.billing_account_id, scope?.session_id, scope?.authorization_epoch].join('|');
  return <BillingSession key={identity} {...props} />;
}
export function BillingSession({ status, onFinancialChange }: Props) {
  const [catalog, setCatalog] = useState<CentralBillingCatalog | null>(null);
  const [orders, setOrders] = useState<CentralOrder[]>([]);
  const [selected, setSelected] = useState('');
  const [channel, setChannel] = useState<'wechat' | 'alipay'>('wechat');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const [revision, setRevision] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(false);
  const lock = useRef(false);
  const attempts = useRef(new Map<string, string>());
  const snapshots = useRef(new Map<string, CentralOrder>());
  const callback = useRef(onFinancialChange); callback.current = onFinancialChange;
  const accept = (incoming: CentralOrder) => {
    if (!active.current) return;
    const previous = snapshots.current.get(incoming.id);
    const order = mergeOrder(previous, incoming);
    if (order === previous) return;
    snapshots.current.set(order.id, order);
    setOrders(current => [order, ...current.filter(item => item.id !== order.id)]);
    const financial = financialState(order);
    if (financial !== null && financial !== (previous ? financialState(previous) : null)) callback.current();
  };
  useEffect(() => {
    active.current = true;
    const current = new AbortController(); controller.current = current;
    return () => { active.current = false; current.abort(); };
  }, []);
  useEffect(() => {
    const current = new AbortController(); setLoading(true); setError('');
    void centralBillingCatalog(current.signal).then(value => {
      if (current.signal.aborted) return;
      setCatalog(value);
      const first = value.channels.find(item => item.enabled);
      if (first) setChannel(first.id);
    }).catch(failure => { if (!current.signal.aborted) setError(message(failure)); }).finally(() => { if (!current.signal.aborted) setLoading(false); });
    if (status.authenticated) void centralOrders(current.signal).then(value => {
      if (!current.signal.aborted) value.orders.forEach(accept);
    }).catch(failure => { if (!current.signal.aborted) setError(`订单列表暂不可用：${message(failure)}`); });
    return () => current.abort();
    // A keyed BillingSession owns exactly one identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision]);
  const order = orders.find(item => item.id === selected);
  useEffect(() => {
    if (!order || !waiting(order)) return;
    const current = new AbortController(); let timer: ReturnType<typeof setTimeout>; let count = 0;
    const poll = async () => {
      setNow(Date.now());
      try {
        const value = await centralOrder(order.id, current.signal);
        if (current.signal.aborted) return;
        accept(value);
      } catch (failure) { if (!current.signal.aborted) setError(`自动查询暂不可用：${message(failure)}`); }
      if (!current.signal.aborted && ++count < 180) timer = setTimeout(() => void poll(), 2000);
    };
    timer = setTimeout(() => void poll(), 2000);
    return () => { current.abort(); clearTimeout(timer); };
    // Only order identity and state restart the serialized, bounded polling cycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, order?.status, order?.credit_state]);
  useEffect(() => {
    if (!order || !waiting(order) || !order.checkout) return;
    const expiry = new Date(order.checkout.expires_at).getTime();
    if (!Number.isFinite(expiry)) return;
    const remaining = expiry - Date.now();
    if (remaining <= 0) { setNow(Date.now()); return; }
    // Separate from network polling so a slow request cannot keep an expired QR visible.
    const timer = setTimeout(() => setNow(Date.now()), Math.min(remaining + 1, 2147483647));
    return () => clearTimeout(timer);
    // Only the QR lifetime changes this timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, order?.status, order?.checkout?.expires_at]);
  const mutate = async (action: (signal: AbortSignal) => Promise<CentralOrder>) => {
    if (lock.current || !controller.current || !status.authenticated) return;
    lock.current = true; setBusy(true); setError('');
    try { const value = await action(controller.current.signal); if (active.current) { accept(value); setSelected(value.id); setNow(Date.now()); } }
    catch (failure) { if (active.current) setError(message(failure)); }
    finally { lock.current = false; if (active.current) setBusy(false); }
  };
  const purchase = (offer: CentralOffer) => mutate(async signal => {
    const attempt = `${offer.id}|${channel}`;
    const idempotency_key = attempts.current.get(attempt) || crypto.randomUUID();
    attempts.current.set(attempt, idempotency_key);
    const body: CentralCreateOrder = { offer_id: offer.id, channel, idempotency_key };
    const result = await centralCreateOrder(body, signal);
    attempts.current.delete(attempt);
    return result;
  });
  const enabled = catalog?.mode === 'test' || catalog?.mode === 'live';
  const purchasable = (offer: CentralOffer) => enabled && status.authenticated && offer.currency === 'CNY' && /^[1-9]\d*$/.test(offer.amount_minor) && offer.terms.refund_policy === 'unused_full_only' && (catalog?.mode === 'test' ? offer.test_only : !offer.test_only);
  return <section className="central-billing central-account-section" aria-label="购买套餐与订单" aria-busy={loading}>
    <div className="central-account-header"><h4>购买套餐与订单</h4><button type="button" className="btn btn-sm" disabled={busy || loading} onClick={() => setRevision(value => value + 1)}>刷新目录和订单</button></div>
    {loading && <p role="status">正在读取支付配置…</p>}
    {error && <p className="env-error" role="alert">{error}</p>}
    {catalog?.mode === 'disabled' && <p role="status">支付暂未开放</p>}
    {catalog?.mode === 'test' && <p className="central-billing-test" role="status">模拟支付 · 测试权益 · 非真实收款</p>}
    {catalog?.mode === 'live' && <p className="desc">微信／支付宝扫码支付 · 无自动扣款</p>}
    {!status.authenticated && <p>登录后购买或查看订单</p>}
    {enabled && <fieldset className="central-billing-channels" disabled={busy || !status.authenticated}><legend>支付渠道</legend>{catalog.channels.map(item => <label key={item.id}><input type="radio" name="central-payment-channel" checked={channel === item.id} disabled={!item.enabled} onChange={() => setChannel(item.id)} />{item.label}{!item.enabled ? '（暂不可用）' : ''}</label>)}</fieldset>}
    {catalog?.offers.map(offer => <article className="central-account-card" key={offer.id}><div className="central-billing-order-heading"><strong>{offer.name}</strong><span className="central-billing-price">{formatCny(offer.amount_minor)}</span></div><p>{offer.test_only ? '测试套餐' : '正式套餐'} · {formatCredits(offer.credits)} 点 · {offer.days} 天 · 并发 {offer.concurrency}</p><p>覆盖产品：{offer.included_products.join('、')} · 能力：{offer.features.join('、') || '暂无'}</p><p className="desc">仅全部未使用时支持全额退款。{offer.terms.description}</p>{purchasable(offer) && <button type="button" className="btn btn-sm" disabled={busy || !catalog.channels.some(item => item.id === channel && item.enabled)} onClick={() => void purchase(offer)}>{offer.test_only ? '模拟购买' : '购买套餐'}</button>}</article>)}
    {enabled && catalog?.offers.length === 0 && <p>暂无可购买套餐，请联系运营者。</p>}
    {order && <><BillingOrderView order={order} now={now} /><div className="central-billing-actions"><button type="button" className="btn btn-sm" disabled={busy} onClick={() => void mutate(signal => centralRefreshOrder(order.id, signal))}>查询支付状态</button>{waiting(order) && <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void mutate(signal => centralCloseOrder(order.id, signal))}>关闭此订单</button>}</div></>}
    {status.authenticated && <div className="central-billing-history"><h4>订单记录</h4>{orders.length === 0 && <p className="desc">暂无订单。</p>}{orders.map(item => <article className="central-account-card" key={item.id}><strong>{item.offer.name} · {formatCny(item.amount_minor)}</strong><p>{item.mode === 'test' ? '测试订单' : '正式订单'} · {orderLabel(item)} · {date(item.created_at)}</p><button type="button" className="btn btn-sm" disabled={busy} onClick={() => { setSelected(item.id); setNow(Date.now()); }}>查看订单</button></article>)}</div>}
  </section>;
}
