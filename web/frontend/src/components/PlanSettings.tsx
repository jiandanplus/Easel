import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  centralBillingCatalog, centralEntitlements, centralMediaModels, centralModels, centralPlans, centralStatus, centralWallet,
} from '../lib/api';
import type { CentralBillingCatalog, CentralEntitlements, CentralMediaModel, CentralModel, CentralPlan, CentralStatus, CentralWallet } from '../lib/api';
import { RedeemSection } from './CentralAccountSettings';
import { mediaPrice } from './CentralMediaSettings';

/** Points are stored as microcredits; show whole points with up to two decimals. */
export function pointsText(micro: string | null | undefined): string {
  if (typeof micro !== 'string' || !/^\d+$/.test(micro)) return '—';
  const value = BigInt(micro), whole = value / 1000000n, cents = (value % 1000000n) / 10000n;
  return `${whole.toLocaleString()}${cents ? `.${cents.toString().padStart(2, '0').replace(/0$/, '')}` : ''}`;
}
export function yuan(minor: string): string {
  if (!/^\d+$/.test(minor)) return '—';
  const value = BigInt(minor);
  return `${value / 100n}${value % 100n ? `.${(value % 100n).toString().padStart(2, '0')}` : ''}`;
}
/** Text rates are microcredits per token, which equals points per million tokens. */
export function perMillion(rate: { numerator: string; denominator: string } | undefined): string {
  if (!rate || !/^\d+$/.test(rate.numerator) || !/^[1-9]\d*$/.test(rate.denominator)) return '—';
  const scaled = BigInt(rate.numerator) * 100n / BigInt(rate.denominator);
  return `${scaled / 100n}${scaled % 100n ? `.${(scaled % 100n).toString().padStart(2, '0').replace(/0$/, '')}` : ''}`;
}
export function multiplier(value: { numerator: string; denominator: string } | undefined): number | null {
  if (!value || !/^\d+$/.test(value.numerator) || !/^[1-9]\d*$/.test(value.denominator)) return null;
  return Number(value.numerator) / Number(value.denominator);
}
export type Period = 'month' | 'year' | 'other';
export const periodOf = (days: number): Period => days >= 300 ? 'year' : days >= 25 && days <= 62 ? 'month' : 'other';
const PERIOD_LABEL: Record<Period, string> = { month: '按月', year: '按年', other: '其他' };
const FEATURES: Record<string, string> = { text: '文本对话与脚本', image: '图片生成', video: '视频生成', audio: '配音与语音', music: '音乐生成', voice: '声音复刻' };
const featureLabel = (feature: string) => FEATURES[feature] || feature;
/** Plans people can be given: not test plans, not the service's own owner and internal plans. */
export const sellable = (plan: CentralPlan) => !plan.test_only && !plan.id.startsWith('easel-');

type Load = { status: CentralStatus | null; plans: CentralPlan[] | null; catalog: CentralBillingCatalog | null; entitlements: CentralEntitlements | null; wallet: CentralWallet | null; text: CentralModel[]; media: CentralMediaModel[]; error: string };
const EMPTY: Load = { status: null, plans: null, catalog: null, entitlements: null, wallet: null, text: [], media: [], error: '' };

function PlanCard({ plan, previous, price, current, rank, onRedeem, onBuy, authenticated }: { plan: CentralPlan; previous: CentralPlan | null; price: string | null; current: boolean; rank: number; onRedeem: () => void; onBuy: (() => void) | null; authenticated: boolean }) {
  const features = previous ? plan.features.filter(feature => !previous.features.includes(feature)) : plan.features;
  const ratio = previous && /^\d+$/.test(previous.credits) && BigInt(previous.credits) > 0n ? Number(BigInt(plan.credits) * 10n / BigInt(previous.credits)) / 10 : null;
  const rate = multiplier(plan.pricing?.default);
  return <article className={`plan-card tone-${Math.min(rank, 3)}${current ? ' current' : ''}`} aria-label={plan.name}>
    <header className="plan-card-head">
      <div className="plan-card-title"><h4>{plan.name}</h4>{current ? <span className="plan-badge">当前套餐</span> : rate !== null && rate < 1 && <span className="plan-badge">{Math.round(rate * 100) / 10} 折</span>}</div>
      <div className="plan-price">{price === null ? <span className="plan-price-none">暂未开售</span> : <><small>¥</small><strong>{price}</strong><span>/ {plan.days} 天</span></>}</div>
      <p className="plan-sub">{pointsText(plan.credits)} 点 · {plan.days} 天有效</p>
    </header>
    <div className="plan-card-body">
      {current ? <button type="button" className="plan-cta" disabled>正在使用</button>
        : !authenticated ? <button type="button" className="plan-cta" onClick={onRedeem}>登录后开通</button>
          : onBuy ? <button type="button" className="plan-cta" onClick={onBuy}>购买</button>
            : <button type="button" className="plan-cta" onClick={onRedeem}>用兑换码开通</button>}
      {previous && <p className="plan-inherit">包含「{previous.name}」的全部内容，另外：</p>}
      {ratio !== null && ratio >= 1.5 && <div className="plan-ratio"><strong>{ratio % 1 ? ratio.toFixed(1) : ratio}x</strong><span>点数是「{previous!.name}」的 {ratio % 1 ? ratio.toFixed(1) : ratio} 倍</span></div>}
      <ul className="plan-features">
        {!previous && <li>{pointsText(plan.credits)} 点额度，{plan.days} 天内使用</li>}
        {previous && !(ratio !== null && ratio >= 1.5) && <li>{pointsText(plan.credits)} 点额度</li>}
        {(!previous || plan.concurrency !== previous.concurrency) && <li>同时运行 {plan.concurrency} 个任务</li>}
        {features.map(feature => <li key={feature}>{featureLabel(feature)}</li>)}
        {Array.isArray(plan.models) ? <li>可用 {plan.models.length} 个模型</li> : !previous && <li>所含功能的全部模型</li>}
        {rate !== null && rate !== 1 && <li>按模型单价 {Math.round(rate * 100)}% 扣点</li>}
      </ul>
    </div>
  </article>;
}

/** 套餐：参考 LobeHub 的套餐页，卡片 + 模型价格表。价格与点数都来自中央后台。 */
export default function PlanSettings({ onOpenBilling }: { onOpenBilling?: () => void }) {
  const [data, setData] = useState<Load>(EMPTY), [revision, setRevision] = useState(0), [period, setPeriod] = useState<Period | null>(null);
  useEffect(() => {
    const controller = new AbortController(), signal = controller.signal;
    const soft = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
    void (async () => {
      try {
        const [status, plans, catalog] = await Promise.all([centralStatus(signal), centralPlans(signal), soft(centralBillingCatalog(signal), null)]);
        let extra: Partial<Load> = {};
        if (status.authenticated) {
          const [entitlements, wallet, text, media] = await Promise.all([soft(centralEntitlements(signal), null), soft(centralWallet(signal), null), soft(centralModels(signal), { models: [] }), soft(centralMediaModels(signal), { models: [] })]);
          extra = { entitlements, wallet, text: text.models, media: media.models };
        }
        if (!signal.aborted) setData({ ...EMPTY, status, plans: plans.plans, catalog, ...extra });
      } catch (failure) { if (!signal.aborted) setData({ ...EMPTY, error: failure instanceof Error ? failure.message : '读取失败' }); }
    })();
    return () => controller.abort();
  }, [revision]);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const scrollToRedeem = () => document.getElementById('plan-redeem')?.scrollIntoView({ behavior: 'smooth', block: 'center' });

  const plans = useMemo(() => (data.plans ?? []).filter(sellable).sort((a, b) => (BigInt(a.credits) < BigInt(b.credits) ? -1 : BigInt(a.credits) > BigInt(b.credits) ? 1 : a.days - b.days)), [data.plans]);
  const periods = useMemo(() => [...new Set(plans.map(plan => periodOf(plan.days)))], [plans]);
  const shownPeriod = period && periods.includes(period) ? period : periods.includes('month') ? 'month' : periods[0];
  const shown = periods.length > 1 ? plans.filter(plan => periodOf(plan.days) === shownPeriod) : plans;
  const currentIds = new Set((data.entitlements?.memberships ?? []).filter(item => item.funding_source !== 'internal_experience').map(item => item.plan_id));
  const currentPlans = (data.plans ?? []).filter(plan => currentIds.has(plan.id));
  const membership = data.entitlements?.memberships.find(item => currentIds.has(item.plan_id));
  const offerFor = (plan: CentralPlan) => data.catalog?.offers.find(offer => offer.plan_id === plan.id) ?? null;
  const canBuy = data.catalog?.mode !== undefined && data.catalog.mode !== 'disabled';
  const authenticated = Boolean(data.status?.authenticated);
  const date = (value: number | string | undefined) => { if (value === undefined) return '未知'; const d = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(d.getTime()) ? '未知' : d.toLocaleDateString(); };

  if (data.error) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">套餐暂不可用：{data.error}</p><button type="button" className="btn btn-sm" onClick={reload}>重试</button></section>;
  if (!data.plans) return <section className="st-sec active settings-page-body"><p role="status">正在读取套餐…</p></section>;
  return <section className="st-sec active plan-page" aria-label="套餐">
    <div className="plan-hero">
      <h3>套餐与价格</h3>
      <p className="desc">开通套餐后获得点数，用来调用对话、生图、视频、配音等模型。点数用完或到期后可以续费或换套餐。</p>
      {authenticated && <div className="plan-current">
        {currentPlans.length ? <><span>当前套餐</span><strong>{currentPlans.map(plan => plan.name).join('、')}</strong><span>· 到期 {date(membership?.expires_at)}</span></> : <span>当前没有生效的套餐</span>}
        {data.wallet && <><span className="sep" /><span>可用</span><strong>{pointsText(data.wallet.available)}</strong><span>点</span></>}
      </div>}
      {periods.length > 1 && <div className="plan-period" role="tablist" aria-label="计费周期">{periods.map(value => <button key={value} type="button" role="tab" aria-selected={value === shownPeriod} className={value === shownPeriod ? 'active' : ''} onClick={() => setPeriod(value)}>{PERIOD_LABEL[value]}</button>)}</div>}
    </div>

    {shown.length === 0 ? <div className="settings-card settings-empty"><strong>暂时没有可开通的套餐</strong><p className="desc">运营者发布套餐后会显示在这里。</p></div>
      : <div className="plan-grid">{shown.map((plan, index) => { const offer = offerFor(plan); return <PlanCard key={plan.id} plan={plan} previous={index > 0 ? shown[index - 1] : null} rank={index} authenticated={authenticated}
          price={offer ? yuan(offer.amount_minor) : null} current={currentIds.has(plan.id)} onRedeem={scrollToRedeem} onBuy={canBuy && offer && onOpenBilling ? onOpenBilling : null} />; })}</div>}
    {!canBuy && shown.length > 0 && <p className="desc plan-note">在线购买暂未开放，目前通过兑换码开通套餐。</p>}

    {authenticated && <div id="plan-redeem" className="settings-card plan-redeem"><RedeemSection onRedeemed={reload} /></div>}

    {authenticated && (data.text.length > 0 || data.media.length > 0) && <div className="plan-prices">
      <div className="plan-prices-intro"><h3>模型价格</h3><p className="desc">每次调用按模型单价扣点；套餐里设置了倍率时按倍率折算。下面是你当前可以使用的模型。</p></div>
      <div className="settings-card plan-table-card"><table className="plan-table">
        <thead><tr><th>模型</th><th>类型</th><th>价格</th></tr></thead>
        <tbody>
          {data.text.map(model => <tr key={model.id}><td>{model.name || model.id}</td><td>文本</td><td>输入 <b>{perMillion(model.rate?.input)}</b> 点 · 输出 <b>{perMillion(model.rate?.output)}</b> 点 <span className="muted">/ 百万 token</span></td></tr>)}
          {data.media.map(model => <tr key={model.id}><td>{model.name || model.id}</td><td>{({ 'image.generate': '生图', 'image.edit': '改图', 'video.generate': '视频', 'music.generate': '音乐', 'audio.speech': '配音', 'audio.transcribe': '语音转写', 'voice.enroll': '声音复刻', 'voice.synthesize': '复刻配音' } as Record<string, string>)[model.capability] || model.capability}</td><td>{mediaPrice(model)}</td></tr>)}
        </tbody>
      </table></div>
    </div>}
  </section>;
}
