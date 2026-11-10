import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  centralBillingCatalog, centralEntitlements, centralMediaModels, centralModels, centralPlans, centralStatus, centralWallet,
} from '../lib/api';
import type { CentralBillingCatalog, CentralEntitlements, CentralMediaModel, CentralModel, CentralOffer, CentralPlan, CentralStatus, CentralWallet } from '../lib/api';
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
  return `${(value / 100n).toLocaleString()}${value % 100n ? `.${(value % 100n).toString().padStart(2, '0')}` : ''}`;
}
/** Text rates are microcredits per token, which equals points per million tokens. */
export function perMillion(rate: { numerator: string; denominator: string } | undefined): string {
  if (!rate || !/^\d+$/.test(rate.numerator) || !/^[1-9]\d*$/.test(rate.denominator)) return '—';
  const scaled = BigInt(rate.numerator) * 100n / BigInt(rate.denominator);
  return `${scaled / 100n}${scaled % 100n ? `.${(scaled % 100n).toString().padStart(2, '0').replace(/0$/, '')}` : ''}`;
}
export function multiplier(value: { numerator: string; denominator: string } | undefined | null): number | null {
  if (!value || !/^\d+$/.test(value.numerator) || !/^[1-9]\d*$/.test(value.denominator)) return null;
  return Number(value.numerator) / Number(value.denominator);
}
export type Period = 'year' | 'month' | 'once';
export const PERIODS: Period[] = ['year', 'month', 'once'];
/** Each plan can be bought yearly, monthly or once; an offer without a period is one-time. */
export const periodOfOffer = (offer: { period?: Period }): Period => offer.period || 'once';
/** Plans people can buy: not test plans, not the service's own owner and internal plans. */
export const sellable = (plan: CentralPlan) => !plan.test_only && !plan.id.startsWith('easel-');
/** "5x": how many times the first plan's points this plan gives, one decimal. */
export function ratioOf(plan: CentralPlan, base: CentralPlan): string | null {
  if (!/^\d+$/.test(plan.credits) || !/^[1-9]\d*$/.test(base.credits)) return null;
  const tenths = BigInt(plan.credits) * 10n / BigInt(base.credits);
  return tenths % 10n ? `${tenths / 10n}.${tenths % 10n}` : `${tenths / 10n}`;
}
/** Yearly saving compared with twelve months of the same-named monthly plan, in whole percent. */
export function yearlySaving(yearMinor: string, monthMinor: string): number | null {
  if (!/^\d+$/.test(yearMinor) || !/^[1-9]\d*$/.test(monthMinor)) return null;
  const full = BigInt(monthMinor) * 12n;
  if (BigInt(yearMinor) >= full) return null;
  return Number((full - BigInt(yearMinor)) * 100n / full);
}

const FEATURES: Record<string, string> = { text: '对话与脚本', image: '图片生成', video: '视频生成', voice: '配音', audio: '语音', music: '音乐生成', transcribe: '语音转写' };
const featureLabel = (feature: string) => FEATURES[feature] || feature;
const PERIOD_LABEL: Record<Period, string> = { year: '按年', month: '按月', once: '一次性' };

const Icon = ({ d }: { d: string }) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
const ICONS = {
  points: 'M12 3v18M7 7.5C7 5.6 9.2 4.5 12 4.5s5 1.1 5 3-2.2 3-5 3-5 1.1-5 3 2.2 3 5 3 5-1.1 5-3',
  speed: 'M4 15a8 8 0 1 1 16 0M12 15l4-5',
  models: 'M12 3l1.9 4.6L19 9l-3.8 3.3L16.4 18 12 15.4 7.6 18l1.2-5.7L5 9l5.1-1.4z',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  cross: 'M6 6l12 12M18 6L6 18',
  rate: 'M19 5L5 19M7 6.5a1.5 1.5 0 1 0 0 .01M17 17.5a1.5 1.5 0 1 0 0 .01',
  text: 'M4 6h16M4 12h10M4 18h7',
  image: 'M4 5h16v14H4zM4 16l4.5-4.5L13 16M14 13l2-2 4 4',
  video: 'M3 6h12v12H3zM15 10l6-3v10l-6-3',
  voice: 'M12 4v10M8 9v4a4 4 0 0 0 8 0V9M12 18v3',
  music: 'M9 18V6l11-2v12M9 18a3 3 0 1 1-3-3M20 16a3 3 0 1 1-3-3',
  clock: 'M12 7v5l3 2M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z',
};
const featureIcon = (feature: string) => (ICONS as Record<string, string>)[feature] || ICONS.check;

type Load = { status: CentralStatus | null; plans: CentralPlan[] | null; catalog: CentralBillingCatalog | null; entitlements: CentralEntitlements | null; wallet: CentralWallet | null; text: CentralModel[]; media: CentralMediaModel[]; error: string };
const EMPTY: Load = { status: null, plans: null, catalog: null, entitlements: null, wallet: null, text: [], media: [], error: '' };

function Item({ icon, children, off = false }: { icon: string; children: ReactNode; off?: boolean }) {
  return <li className={off ? 'off' : ''}><Icon d={off ? ICONS.cross : icon} /><span>{children}</span></li>;
}

function PlanCard({ plan, base, previous, period, offer, saving, current, tone, modelNames, onUpgrade }: {
  plan: CentralPlan; base: CentralPlan; previous: CentralPlan | null; period: Period; offer: CentralOffer | null; saving: number | null; current: boolean; tone: number;
  modelNames: (ids: string[]) => string[]; onUpgrade: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rate = multiplier(plan.pricing?.default), ratio = previous ? ratioOf(plan, base) : null;
  const models = Array.isArray(plan.models) ? plan.models : null;
  const added = previous ? plan.features.filter(feature => !previous.features.includes(feature)) : plan.features;
  const price = offer ? yuan(offer.amount_minor) : null;
  const monthly = offer && period === 'year' ? yuan((BigInt(offer.amount_minor) / 12n).toString()) : null;
  const items: ReactNode[] = [
    <Item key="points" icon={ICONS.points}>{period === 'year' ? `每月 ${pointsText(plan.credits)} 点，一年共 ${pointsText((BigInt(/^\d+$/.test(plan.credits) ? plan.credits : '0') * 12n).toString())} 点` : `${pointsText(plan.credits)} 点额度`}{previous ? `，「${previous.name}」的 ${ratioOf(plan, previous) ?? '—'} 倍` : ''}</Item>,
    <Item key="days" icon={ICONS.clock}>{period === 'year' ? `${plan.days * 12} 天内有效（12 个周期）` : `${plan.days} 天内有效`}</Item>,
    <Item key="speed" icon={ICONS.speed}>同时运行 {plan.concurrency} 个任务{previous && plan.concurrency !== previous.concurrency ? `（「${previous.name}」为 ${previous.concurrency} 个）` : ''}</Item>,
    <Item key="models" icon={ICONS.models}>{models ? `可用 ${models.length} 个模型` : '所含功能的全部模型'}{previous && Array.isArray(previous.models) && models && models.length > previous.models.length ? `，比「${previous.name}」多 ${models.length - previous.models.length} 个` : ''}</Item>,
    ...added.map(feature => <Item key={feature} icon={featureIcon(feature)}>{featureLabel(feature)}</Item>),
    ...(rate !== null && rate !== 1 ? [<Item key="rate" icon={ICONS.rate}>按模型单价的 {Math.round(rate * 100)}% 扣点</Item>] : []),
  ];
  const shown = open ? items : items.slice(0, 5);
  return <article className={`plan-card tone-${tone}${current ? ' current' : ''}`} aria-label={plan.name}>
    <header className="plan-card-head">
      <div className="plan-card-title"><h4>{plan.name}</h4>{current ? <span className="plan-badge">当前套餐</span> : saving ? <span className="plan-badge">优惠 {saving}%</span> : null}</div>
      {price === null ? <div className="plan-price"><strong className="plan-price-none">价格待定</strong></div>
        : <div className="plan-price"><small>¥</small><strong>{monthly ?? price}</strong><span>{period === 'once' ? `/${plan.days} 天` : '/月'}</span></div>}
      <p className="plan-sub">{price === null ? `暂未设置${PERIOD_LABEL[period]}价格` : period === 'year' ? `¥${price} / 每年` : period === 'month' ? `按月付费 · 每月 ${pointsText(plan.credits)} 点` : '一次性付费，不自动续费'}</p>
    </header>
    <div className="plan-card-body">
      <button type="button" className="plan-cta" disabled={current} onClick={onUpgrade}>{current ? '当前套餐' : '升级'}</button>
      <div className="plan-highlight"><strong>{previous ? `${ratio ?? '—'}x` : '标准'}</strong><span>{previous ? `「${base.name}」的 ${ratio ?? '—'} 倍点数` : `${pointsText(plan.credits)} 点额度`}</span></div>
      <hr />
      <p className="plan-inherit">{previous ? `包含「${previous.name}」的所有权益，另加：` : '核心权益'}</p>
      <ul className="plan-features">{shown}</ul>
      {open && models && models.length > 0 && <div className="plan-model-list"><p className="plan-inherit">可用模型</p><div className="profile-chips">{modelNames(models).map(name => <span key={name} className="profile-chip">{name}</span>)}</div></div>}
      {(items.length > 5 || (models && models.length > 0)) && <button type="button" className="plan-more" aria-expanded={open} onClick={() => setOpen(!open)}><span aria-hidden="true">{open ? '⌃' : '⌄'}</span>{open ? '收起' : '查看全部功能'}</button>}
    </div>
  </article>;
}

/** 套餐：参考 LobeHub 的套餐页——周期切换、套餐卡片、套餐权益对比、模型价格。数据都来自中央后台。 */
export default function PlanSettings({ onOpenBilling }: { onOpenBilling?: () => void }) {
  const [data, setData] = useState<Load>(EMPTY), [revision, setRevision] = useState(0), [period, setPeriod] = useState<Period | null>(null), [notice, setNotice] = useState('');
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

  const plans = useMemo(() => (data.plans ?? []).filter(sellable).sort((a, b) => (BigInt(a.credits) < BigInt(b.credits) ? -1 : BigInt(a.credits) > BigInt(b.credits) ? 1 : a.days - b.days)), [data.plans]);
  const offerFor = (plan: CentralPlan, way: Period) => data.catalog?.offers.find(offer => offer.plan_id === plan.id && periodOfOffer(offer) === way) ?? null;
  const priced = (way: Period) => plans.some(plan => offerFor(plan, way));
  const shownPeriod: Period = period ?? (priced('year') ? 'year' : priced('month') ? 'month' : priced('once') ? 'once' : 'month');
  const shown = plans;
  const savingFor = (plan: CentralPlan) => {
    const year = offerFor(plan, 'year'), month = offerFor(plan, 'month');
    return year && month ? yearlySaving(year.amount_minor, month.amount_minor) : null;
  };
  const bestSaving = Math.max(0, ...plans.map(plan => savingFor(plan) ?? 0));
  const currentIds = new Set((data.entitlements?.memberships ?? []).map(item => item.plan_id));
  const currentPlans = (data.plans ?? []).filter(plan => currentIds.has(plan.id));
  const membership = data.entitlements?.memberships.find(item => currentIds.has(item.plan_id));
  const payable = Boolean(data.catalog && data.catalog.mode !== 'disabled' && data.catalog.channels.some(channel => channel.enabled));
  const authenticated = Boolean(data.status?.authenticated);
  const names = useMemo(() => new Map<string, string>([...data.text.map(model => [model.id, model.name || model.id] as [string, string]), ...data.media.map(model => [model.id, model.name || model.id] as [string, string])]), [data.text, data.media]);
  const modelNames = useCallback((ids: string[]) => ids.map(id => names.get(id) || id), [names]);
  const date = (value: number | string | undefined) => { if (value === undefined) return '未知'; const d = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(d.getTime()) ? '未知' : d.toLocaleDateString(); };
  const upgrade = (plan: CentralPlan) => {
    setNotice('');
    if (!authenticated) { setNotice('请先在「个人中心」登录账号，再升级套餐。'); return; }
    if (!offerFor(plan, shownPeriod)) { setNotice(`「${plan.name}」还没有设置${PERIOD_LABEL[shownPeriod]}价格，暂时不能购买。`); return; }
    if (!payable || !onOpenBilling) { setNotice('在线支付还没有开放，开放后点「升级」就能直接购买。'); return; }
    onOpenBilling();
  };
  // Every feature any plan has, in a stable order, for the comparison table.
  const allFeatures = useMemo(() => [...new Set(plans.flatMap(plan => plan.features))].sort((a, b) => Object.keys(FEATURES).indexOf(a) - Object.keys(FEATURES).indexOf(b)), [plans]);
  const allModels = useMemo(() => [...new Set(plans.flatMap(plan => plan.models ?? []))], [plans]);
  const [showModels, setShowModels] = useState(false);

  if (data.error) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">套餐暂不可用：{data.error}</p><button type="button" className="btn btn-sm" onClick={reload}>重试</button></section>;
  if (!data.plans) return <section className="st-sec active settings-page-body"><p role="status">正在读取套餐…</p></section>;
  return <section className="st-sec active plan-page" aria-label="套餐">
    <div className="plan-hero">
      {authenticated && <div className="plan-current">
        {currentPlans.length ? <><span>当前</span><strong>{currentPlans.map(plan => plan.name).join('、')}</strong><span>· {date(membership?.expires_at)} 到期</span></> : <span>当前没有生效的套餐</span>}
        {data.wallet && <><span className="sep" /><span>可用</span><strong>{pointsText(data.wallet.available)}</strong><span>点</span></>}
      </div>}
      {plans.length > 0 && <div className="plan-period" role="tablist" aria-label="付费方式">{PERIODS.map(value => <button key={value} type="button" role="tab" aria-selected={value === shownPeriod} className={value === shownPeriod ? 'active' : ''} onClick={() => setPeriod(value)}>
        {PERIOD_LABEL[value]}{value === 'year' && bestSaving > 0 && <small>最高优惠 {bestSaving}%</small>}{value === 'once' && <span className="plan-pay-marks" aria-label="支持支付宝和微信支付"><i className="pay alipay">支</i><i className="pay wechat">微</i></span>}
      </button>)}</div>}
    </div>

    {notice && <p role="status" className="central-account-notice plan-notice">{notice}</p>}
    {shown.length === 0 ? <div className="settings-card settings-empty"><strong>暂时没有可开通的套餐</strong><p className="desc">运营者发布套餐后会显示在这里。</p></div>
      : <div className="plan-grid">{shown.map((plan, index) => <PlanCard key={plan.id} plan={plan} base={shown[0]} previous={index > 0 ? shown[index - 1] : null} period={shownPeriod} offer={offerFor(plan, shownPeriod)} saving={shownPeriod === 'year' ? savingFor(plan) : null}
          current={currentIds.has(plan.id)} tone={index % 3} modelNames={modelNames} onUpgrade={() => upgrade(plan)} />)}</div>}

    {plans.length > 0 && <div className="plan-compare">
      <h3>套餐权益对比</h3>
      <div className="settings-card plan-table-card"><table className="plan-table plan-compare-table">
        <thead><tr><th>权益</th>{plans.map(plan => <th key={plan.id}>{plan.name}</th>)}</tr></thead>
        <tbody>
          {PERIODS.map(way => <tr key={way}><td>{PERIOD_LABEL[way]}价格</td>{plans.map(plan => { const offer = offerFor(plan, way); return <td key={plan.id}>{offer ? <><b>¥{yuan(offer.amount_minor)}</b><span className="muted">{way === 'year' ? ' / 年' : way === 'month' ? ' / 月' : ''}</span></> : <span className="muted">未设置</span>}</td>; })}</tr>)}
          <tr><td>点数额度</td>{plans.map(plan => <td key={plan.id}><b>{pointsText(plan.credits)}</b> 点</td>)}</tr>
          <tr><td>有效期</td>{plans.map(plan => <td key={plan.id}>{plan.days} 天</td>)}</tr>
          <tr><td>同时运行的任务</td>{plans.map(plan => <td key={plan.id}>{plan.concurrency} 个</td>)}</tr>
          <tr><td>扣点倍率</td>{plans.map(plan => { const rate = multiplier(plan.pricing?.default); return <td key={plan.id}>{rate === null || rate === 1 ? '按单价' : `${Math.round(rate * 100)}%`}</td>; })}</tr>
          {allFeatures.map(feature => <tr key={feature}><td>{featureLabel(feature)}</td>{plans.map(plan => <td key={plan.id}>{plan.features.includes(feature) ? <span className="yes"><Icon d={ICONS.check} /></span> : <span className="no"><Icon d={ICONS.cross} /></span>}</td>)}</tr>)}
          <tr><td>可用模型</td>{plans.map(plan => <td key={plan.id}>{Array.isArray(plan.models) ? `${plan.models.length} 个` : '全部'}</td>)}</tr>
          {allModels.length > 0 && <tr className="plan-compare-toggle"><td colSpan={plans.length + 1}><button type="button" className="plan-more" aria-expanded={showModels} onClick={() => setShowModels(!showModels)}><span aria-hidden="true">{showModels ? '⌃' : '⌄'}</span>{showModels ? '收起模型明细' : `展开 ${allModels.length} 个模型的明细`}</button></td></tr>}
          {showModels && allModels.map(id => <Fragment key={id}><tr className="plan-model-row"><td>{names.get(id) || id}</td>{plans.map(plan => <td key={plan.id}>{!Array.isArray(plan.models) || plan.models.includes(id) ? <span className="yes"><Icon d={ICONS.check} /></span> : <span className="no"><Icon d={ICONS.cross} /></span>}</td>)}</tr></Fragment>)}
        </tbody>
      </table></div>
    </div>}

    {authenticated && (data.text.length > 0 || data.media.length > 0) && <div className="plan-prices">
      <div className="plan-prices-intro"><h3>模型价格</h3><p className="desc">每次调用按模型单价扣点；套餐设置了倍率时按倍率折算。这里列出你当前能使用的模型。</p></div>
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
