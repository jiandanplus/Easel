import { useCallback, useEffect, useMemo, useState } from 'react';
import { avatarUrl, centralEntitlements, centralPlans, centralStatus, centralWallet, hasAvatar } from '../lib/api';
import type { CentralEntitlements, CentralGrant, CentralPlan, CentralStatus, CentralWallet } from '../lib/api';
import { RedeemSection } from './CentralAccountSettings';
import PlatformAccountSettings from './PlatformAccountSettings';
import { pointsText } from './PlanSettings';
import { initialOf } from './ProfileSettings';
import { untilText } from './UsageSettings';

export const SOURCES: Record<CentralGrant['source'], string> = { purchase: '购买套餐', redemption: '兑换码', console: '后台发放', owner: '所有者额度', internal: '内部体验', test: '测试额度', other: '其他' };
export type GrantState = 'active' | 'used' | 'expired';
/** Expired or revoked grants are over; a grant with nothing left is used up; the rest can still be spent. */
export function grantState(grant: CentralGrant, now = Date.now()): GrantState {
  if (grant.revoked || new Date(grant.expires_at).getTime() <= now) return 'expired';
  return /^0*$/.test(grant.available) ? 'used' : 'active';
}
export function remainingShare(grant: CentralGrant): number {
  if (!/^\d+$/.test(grant.issued) || grant.issued === '0' || !/^\d+$/.test(grant.available)) return 0;
  return Number(BigInt(grant.available) * 1000n / BigInt(grant.issued)) / 10;
}

type Load = { status: CentralStatus | null; wallet: CentralWallet | null; entitlements: CentralEntitlements | null; plans: CentralPlan[]; ready: boolean; error: string };

/** 余额：可用点数与订阅卡、充值方式、我的充值。参考 LobeHub 的余额页。 */
export default function BalanceSettings({ onOpenUsage, onOpenPlans }: { onOpenUsage?: () => void; onOpenPlans?: () => void }) {
  const [data, setData] = useState<Load>({ status: null, wallet: null, entitlements: null, plans: [], ready: false, error: '' });
  const [revision, setRevision] = useState(0), [avatar, setAvatar] = useState(0);
  const [source, setSource] = useState(''), [order, setOrder] = useState<'newest' | 'expiring'>('newest'), [tab, setTab] = useState<GrantState>('active');
  useEffect(() => {
    const controller = new AbortController(), signal = controller.signal;
    const soft = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
    void hasAvatar().then(found => setAvatar(found ? Date.now() : 0)).catch(() => setAvatar(0));
    void (async () => {
      try {
        const status = await centralStatus(signal);
        if (!status.authenticated) { if (!signal.aborted) setData(d => ({ ...d, status, ready: true })); return; }
        const [wallet, entitlements, plans] = await Promise.all([soft(centralWallet(signal), null), soft(centralEntitlements(signal), null), soft(centralPlans(signal), { plans: [] })]);
        if (!signal.aborted) setData({ status, wallet, entitlements, plans: plans.plans, ready: true, error: '' });
      } catch (failure) { if (!signal.aborted) setData(d => ({ ...d, ready: true, error: failure instanceof Error ? failure.message : '读取失败' })); }
    })();
    return () => controller.abort();
  }, [revision]);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const grants = useMemo(() => data.wallet?.grants ?? [], [data.wallet]);
  const counts = useMemo(() => ({ active: grants.filter(g => grantState(g) === 'active').length, used: grants.filter(g => grantState(g) === 'used').length, expired: grants.filter(g => grantState(g) === 'expired').length }), [grants]);
  const shown = useMemo(() => grants.filter(g => grantState(g) === tab && (!source || g.source === source))
    .sort((a, b) => order === 'expiring' ? new Date(a.expires_at).getTime() - new Date(b.expires_at).getTime() : new Date(b.starts_at ?? b.expires_at).getTime() - new Date(a.starts_at ?? a.expires_at).getTime()), [grants, tab, source, order]);

  if (!data.ready) return <section className="st-sec active settings-page-body"><p role="status">正在读取余额…</p></section>;
  if (data.error) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">余额暂不可用：{data.error}</p></section>;
  if (!data.status?.authenticated) return <PlatformAccountSettings view="balance" />;

  const wallet = data.wallet;
  const big = (value?: string | null) => (typeof value === 'string' && /^\d+$/.test(value) ? BigInt(value) : 0n);
  const granted = wallet ? big(wallet.issued) - big(wallet.expired) - big(wallet.refunded) : 0n;
  const remaining = wallet && granted > 0n ? Number(big(wallet.available) * 1000n / granted) / 10 : 0;
  const memberships = data.entitlements?.memberships ?? [];
  const soonest = memberships.map(item => item.expires_at).sort((a, b) => new Date(a).getTime() - new Date(b).getTime())[0];
  const plan = memberships.map(item => data.plans.find(p => p.id === item.plan_id)?.name || (item.funding_source === 'internal_experience' ? '内部体验' : '')).filter(Boolean)[0] || '未订阅';
  const account = data.status.account;
  const name = account?.username || (account?.email ? account.email.split('@')[0] : '优创用户');
  const date = (value: string | null) => value ? new Date(value).toLocaleDateString() : '—';

  return <section className="st-sec active usage-page balance-page" aria-label="余额">
    <div className="usage-block">
      <div className="usage-block-head"><h3>余额</h3></div>
      <div className="settings-card usage-card balance-card">
        <div className="balance-main">
          <p className="usage-label">可用点数</p>
          <strong className="balance-amount">{pointsText(wallet?.available)}<small> 点</small></strong>
          <p className="usage-label">剩余用量</p>
          <div className="usage-limit-row"><span>套餐额度</span><b>剩余 {wallet ? `${remaining}%` : '—'}</b></div>
          <div className="usage-progress"><span style={{ width: `${Math.min(100, remaining)}%` }} className={remaining < 10 ? 'low' : remaining < 30 ? 'mid' : ''} /></div>
          <p className="desc">{untilText(soonest) || '没有生效中的套餐'}{wallet && big(wallet.reserved) > 0n ? ` · 进行中的任务预留 ${pointsText(wallet.reserved)} 点` : ''}</p>
          <div className="balance-links">
            {onOpenUsage && <button type="button" onClick={onOpenUsage}>◔ 查看使用情况</button>}
            <button type="button" onClick={() => document.getElementById('balance-history')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>↺ 充值记录</button>
          </div>
        </div>
        <div className="balance-sub-card" aria-label="订阅">
          <div className="balance-sub-top"><span>YOUCHUANG SUBSCRIPTION</span><span aria-hidden="true">)))</span></div>
          <strong>{plan}</strong>
          <div className="balance-sub-user"><span className="balance-avatar">{avatar > 0 ? <img src={avatarUrl(avatar)} alt="" onError={() => setAvatar(0)} /> : initialOf(name)}</span>{name}</div>
        </div>
      </div>
    </div>

    <div className="usage-block">
      <div className="usage-block-head"><div><h3>充值余额</h3><p className="desc">获得更多点数的方式。在线充值点数包暂未开放。</p></div></div>
      <div className="settings-card usage-card balance-topup">
        <div className="balance-option"><div><strong>升级套餐</strong><p className="desc">开通或升级套餐，每个周期获得一份点数，按年购买更划算。</p></div>{onOpenPlans && <button type="button" className="usage-upgrade" onClick={onOpenPlans}>查看套餐</button>}</div>
        <div className="balance-option balance-redeem"><RedeemSection onRedeemed={reload} /></div>
      </div>
    </div>

    <div className="usage-block" id="balance-history">
      <div className="usage-block-head"><h3>我的充值</h3>
        <div className="balance-filters">
          <select value={source} aria-label="来源" onChange={e => setSource(e.target.value)}><option value="">全部来源</option>{Object.entries(SOURCES).filter(([key]) => grants.some(g => g.source === key)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          <select value={order} aria-label="排序" onChange={e => setOrder(e.target.value as 'newest' | 'expiring')}><option value="newest">最新</option><option value="expiring">即将到期</option></select>
        </div></div>
      <div className="settings-card usage-card">
        <div className="balance-tabs" role="tablist">{([['active', '有效'], ['used', '已用尽'], ['expired', '已过期']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={tab === value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}（{counts[value]}）</button>)}</div>
        {shown.length === 0 ? <div className="usage-empty"><strong>{grants.length ? '这里没有记录' : '暂无充值记录'}</strong><p className="desc">{grants.length ? '换个分类看看。' : '开通套餐或兑换兑换码后，点数会记录在这里。'}</p></div>
          : <ul className="balance-list">{shown.map(grant => { const share = remainingShare(grant); return <li key={grant.id}>
            <div className="balance-grant-head"><span className={`usage-kind k-${grant.source === 'purchase' ? 'image' : grant.source === 'redemption' ? 'voice' : 'text'}`}>{SOURCES[grant.source]}</span><strong>{grant.plan_name || grant.plan_id || '点数'}</strong>{grant.frozen && <span className="settings-tag warn">已冻结</span>}</div>
            <div className="balance-grant-bar"><div className="usage-progress"><span style={{ width: `${share}%` }} className={share < 10 ? 'low' : share < 30 ? 'mid' : ''} /></div><span>剩余 {pointsText(grant.available)} / {pointsText(grant.issued)} 点</span></div>
            <p className="desc">发放于 {date(grant.starts_at)} · {grantState(grant) === 'expired' ? `${grant.revoked ? '已撤销' : '已于'} ${date(grant.expires_at)}${grant.revoked ? '' : ' 到期'}` : `${date(grant.expires_at)} 到期`} · 已用 {pointsText(grant.spent)} 点</p>
          </li>; })}</ul>}
      </div>
    </div>
  </section>;
}
