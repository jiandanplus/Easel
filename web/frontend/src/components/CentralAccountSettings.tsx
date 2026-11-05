import { useEffect, useRef, useState } from 'react';
import { centralAccountSignature, centralStatus, centralStartLogin, centralCancelLogin, centralLogout, centralPlans, centralWallet, centralEntitlements, centralUsage, centralDevices, centralRevoke } from '../lib/api';
import type { CentralStatus, CentralPlan, CentralWallet, CentralEntitlements, CentralUsage, CentralDevice } from '../lib/api';
import './CentralAccountSettings.css';
import CentralBillingSettings from './CentralBillingSettings';
import CentralExecutionSettings from './CentralExecutionSettings';
import CentralMediaSettings from './CentralMediaSettings';

/** Decimal string arithmetic keeps every microcredit, including values above Number.MAX_SAFE_INTEGER. */
function formatMicrocredits(value?: string | null): string {
  if (value == null) return '未知';
  if (typeof value !== 'string' || !/^-?\d+$/.test(value)) return '数据无效';
  const integer = BigInt(value);
  const magnitude = integer < 0n ? -integer : integer;
  const fraction = (magnitude % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${integer < 0n ? '-' : ''}${magnitude / 1000000n}${fraction ? `.${fraction}` : ''}`;
}

type ReadState<T> = { data: T | null; error: string; loading: boolean };
function formatRequestCount(value: unknown, minimum = 0): string {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum ? String(value) : '未知';
}
const unread = <T,>(): ReadState<T> => ({ data: null, error: '', loading: true });
const failureMessage = (failure: unknown): string => failure instanceof Error ? failure.message : '暂不可用，请稍后重试。';
function displayDate(value?: number | string | null): string {
  if (value == null) return '未知';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '未知' : date.toLocaleString();
}

export default function CentralAccountSettings() {
  const [status, setStatus] = useState<CentralStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pollCycle, setPollCycle] = useState(0);
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [plans, setPlans] = useState<ReadState<CentralPlan[]>>(unread);
  const [wallet, setWallet] = useState<ReadState<CentralWallet>>(unread);
  const [entitlements, setEntitlements] = useState<ReadState<CentralEntitlements>>(unread);
  const [usage, setUsage] = useState<ReadState<CentralUsage[]>>(unread);
  const [devices, setDevices] = useState<ReadState<CentralDevice[]>>(unread);
  const mounted = useRef(true);
  const actionLock = useRef(false);
  const pollGeneration = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setStatusLoading(true); setStatusError('');
    void centralStatus(controller.signal).then(current => {
      if (!controller.signal.aborted) { setStatus(current); if (!current.login.pending) setAuthorizationUrl(''); }
    }).catch(failure => {
      if (!controller.signal.aborted) setStatusError(`账号状态暂不可用：${failureMessage(failure)}`);
    }).finally(() => { if (!controller.signal.aborted) setStatusLoading(false); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    const read = <T,>(fetchValue: () => Promise<T>, update: (value: ReadState<T>) => void) => {
      update(unread());
      void fetchValue().then(data => {
        if (!controller.signal.aborted) update({ data, error: '', loading: false });
      }).catch(failure => {
        if (!controller.signal.aborted) update({ data: null, error: failureMessage(failure), loading: false });
      });
    };
    setPlans(unread()); setWallet(unread()); setEntitlements(unread()); setUsage(unread()); setDevices(unread());
    if (status?.configured) read(async () => (await centralPlans(controller.signal)).plans, setPlans);
    if (status?.authenticated) {
      read(() => centralWallet(controller.signal), setWallet);
      read(() => centralEntitlements(controller.signal), setEntitlements);
      read(async () => (await centralUsage(controller.signal)).usage, setUsage);
      read(async () => (await centralDevices(controller.signal)).sessions, setDevices);
    }
    return () => controller.abort();
  }, [status?.configured, status?.authenticated, status?.scope?.user_id, status?.scope?.tenant_id, status?.scope?.billing_account_id, status?.scope?.session_id, status?.scope?.authorization_epoch, revision]);

  useEffect(() => {
    if (!status?.login.pending) return;
    const generation = ++pollGeneration.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const live = () => !controller.signal.aborted && generation === pollGeneration.current;
    const poll = async () => {
      try {
        const current = await centralStatus(controller.signal);
        if (!live()) return;
        setStatus(current); setStatusError('');
        if (!current.login.pending) { setAuthorizationUrl(''); return; }
      } catch (failure) {
        if (!live()) return;
        setStatusError(`登录状态暂不可用：${failureMessage(failure)}`);
      }
      if (live()) timer = setTimeout(() => void poll(), 1000);
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [status?.login.pending, pollCycle]);

  const mutate = async (action: () => Promise<void>) => {
    if (actionLock.current || statusLoading) return;
    actionLock.current = true; setBusy(true); setActionError('');
    try { await action(); }
    catch (failure) { if (mounted.current) setActionError(failureMessage(failure)); }
    finally { actionLock.current = false; if (mounted.current) setBusy(false); }
  };
  const startLogin = () => mutate(async () => {
    const result = await centralStartLogin();
    if (!mounted.current) return;
    const destination = new URL(result.authorization_url);
    if (destination.username || destination.password || !(destination.protocol === 'https:' || (destination.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(destination.hostname)))) throw new Error('登录地址无效，请联系平台运营者。');
    setAuthorizationUrl(destination.href);
    setStatus(current => current && ({ ...current, login: { pending: true, expires_at: result.expires_at } }));
  });
  const cancelLogin = () => mutate(async () => {
    pollGeneration.current++;
    try {
      const current = await centralCancelLogin();
      if (mounted.current) { setStatus(current); setAuthorizationUrl(''); }
    } catch (failure) {
      if (mounted.current) setPollCycle(value => value + 1);
      throw failure;
    }
  });
  const logout = () => mutate(async () => {
    pollGeneration.current++;
    const current = await centralLogout();
    if (mounted.current) { setStatus(current); setAuthorizationUrl(''); setRevision(value => value + 1); }
  });
  const revoke = (id: string) => mutate(async () => {
    await centralRevoke(id);
    if (mounted.current) setRevision(value => value + 1);
  });
  const readMessage = <T,>(value: ReadState<T>, label: string) => value.loading ? `正在读取${label}…` : `${label}未知：${value.error || '暂不可用，请刷新重试。'}`;
  const pending = status?.login.pending;

  return <section className="central-account st-sec active" aria-label="中央账号与订阅">
    <div className="central-account-header"><div><h3>账号与订阅</h3></div><button type="button" className="btn btn-sm" disabled={busy || statusLoading || pending} onClick={() => setRevision(value => value + 1)}>刷新账号状态</button></div>
    {!statusLoading && !statusError && status?.configured && !status.error && <p className="central-account-notice">{status.capabilities?.subscription_execution_enabled ? '文本订阅已开放' : status.capabilities?.test_execution_enabled ? '文本订阅 · 测试点数' : '订阅暂未开放'}</p>}
    {statusLoading && <p role="status">正在读取账号状态…</p>}
    {statusError && <p className="env-error" role="alert">{statusError}</p>}
    {status?.error && <p className="env-error" role="alert">{status.error.message}</p>}
    {actionError && <p className="env-error" role="alert">{actionError}</p>}
    {!statusLoading && status && !status.configured && <p role="status">账号服务暂不可用</p>}
    {status?.authenticated ? <div className="central-account-identity"><div><strong>{status.account?.email || status.account?.id || '统一账号'}</strong><p className="desc">{status.account?.email_verified ? '邮箱已验证' : '邮箱未验证'} · 产品 {status.scope?.product_id || '未知'}</p></div><button type="button" className="btn btn-sm" disabled={busy || statusLoading} onClick={() => void logout()}>退出账号</button></div> : <div className="central-account-login">
      {status?.scope && <><p className="desc">登录状态待确认，请刷新或重新登录</p><button type="button" className="btn btn-sm" disabled={busy || statusLoading} onClick={() => void logout()}>退出本机账号</button></>}
      <button type="button" className="btn btn-sm" disabled={busy || statusLoading || !status?.configured || pending} onClick={() => void startLogin()}>登录／注册</button>
      {pending && <><p role="status">等待统一账号站完成登录…{status?.login.expires_at ? ` 期限 ${displayDate(status.login.expires_at)}` : ''}</p>{authorizationUrl && <a className="btn btn-sm" href={authorizationUrl} target="_blank" rel="noopener noreferrer">打开统一账号站</a>}<button type="button" className="btn btn-sm" disabled={busy} onClick={() => void cancelLogin()}>取消登录</button></>}
      {status?.login.error && <p role="alert" className="env-error">{status.login.error.message}；可重新登录。</p>}
    </div>}
    {status?.authenticated && <>
      <section className="central-account-section" aria-label="共享钱包" aria-busy={wallet.loading}>
        <h4>共享钱包</h4><p className="central-account-notice">{wallet.data?.funding_mode === 'mixed' ? '测试和正式点数混合，请分别查看来源' : wallet.data?.funding_mode === 'live' ? '正式点数钱包' : wallet.data?.funding_mode === 'empty' ? '当前钱包为空' : wallet.data?.test_only ? '测试点数钱包 · 不代表正式余额' : '点数来源暂未知'}</p>
        {wallet.data === null && <p role={wallet.error ? 'alert' : 'status'}>{readMessage(wallet, '钱包')}</p>}
        <dl className="central-account-balances">
          <div><dt>可用点数</dt><dd>{formatMicrocredits(wallet.data?.available)}</dd></div><div><dt>预留点数</dt><dd>{formatMicrocredits(wallet.data?.reserved)}</dd></div><div><dt>累计已用点数</dt><dd>{formatMicrocredits(wallet.data?.spent)}</dd></div><div><dt>已过期点数</dt><dd>{formatMicrocredits(wallet.data?.expired)}</dd></div><div><dt>累计发放点数</dt><dd>{formatMicrocredits(wallet.data?.issued)}</dd></div><div><dt>冻结点数</dt><dd>{formatMicrocredits(wallet.data?.frozen)}</dd></div><div><dt>已退款点数</dt><dd>{formatMicrocredits(wallet.data?.refunded)}</dd></div>{wallet.data?.test_available != null && <div><dt>测试可用点数</dt><dd>{formatMicrocredits(wallet.data.test_available)}</dd></div>}{wallet.data?.live_available != null && <div><dt>正式可用点数</dt><dd>{formatMicrocredits(wallet.data.live_available)}</dd></div>}<div><dt>待核算请求</dt><dd>{wallet.data?.pending_reconciliation ?? '未知'}</dd></div>
          <div><dt>执行中请求（不含待核算）</dt><dd>{formatRequestCount(wallet.data?.active_requests)}</dd></div>
          <div><dt>待核算上限</dt><dd>{formatRequestCount(wallet.data?.pending_reconciliation_limit, 1)}</dd></div>
        </dl>
        {wallet.data?.experience_issued != null && /^\d+$/.test(wallet.data.experience_issued) && BigInt(wallet.data.experience_issued) > 0n && <aside className="central-account-notice" aria-label="内部体验额度">
          <strong>内部体验额度 · 非付款</strong>
          <dl className="central-account-balances"><div><dt>内部体验可用点数</dt><dd>{formatMicrocredits(wallet.data.experience_available)}</dd></div><div><dt>内部体验累计发放</dt><dd>{formatMicrocredits(wallet.data.experience_issued)}</dd></div><div><dt>体验额度到期</dt><dd>{displayDate(wallet.data.experience_expires_at)}</dd></div></dl>
        </aside>}
      </section>
      <section className="central-account-section" aria-label="当前权益" aria-busy={entitlements.loading}>
        <h4>当前权益</h4>{entitlements.data === null ? <p role={entitlements.error ? 'alert' : 'status'}>{readMessage(entitlements, '测试权益')}</p> : <>
          <p className="desc">{entitlements.data.test_only ? '测试权益 · 不代表正式订阅' : '包含正式权益 · 点数来源见共享钱包'}</p><p>{entitlements.data.active ? '权益生效中' : '当前无生效权益'} · 产品 {entitlements.data.product_id} · 并发 {entitlements.data.concurrency}</p><p>能力：{entitlements.data.features.map(feature => ({ text: '文本对话', image: '图片生成' } as Record<string, string>)[feature] || feature).join('、') || '暂无'}</p>
          {entitlements.data.memberships.map(membership => <article className="central-account-card" key={membership.id}><strong>{membership.funding_source === 'internal_experience' ? '内部体验额度 · 非付款' : membership.plan_id}</strong><p>到期 {displayDate(membership.expires_at)}</p></article>)}
        </>}
      </section>
      <section className="central-account-section" aria-label="点数明细" aria-busy={usage.loading}>
        <h4>点数明细</h4>{usage.data === null && <p role={usage.error ? 'alert' : 'status'}>{readMessage(usage, '点数明细')}</p>}{usage.data?.length === 0 && <p>暂无点数明细。</p>}
        {usage.data?.map(item => <article className="central-account-card" key={item.id}><strong>{({ execution: '文本对话', media: '媒体任务' } as Record<string, string>)[item.operation] || item.operation} · {item.product_id}</strong><p>状态 {item.status === 'waived' ? '平台承担费用' : item.status} · 预算点数 {formatMicrocredits(item.amount)} · {item.status === 'waived' ? '用户不扣点' : `实际扣点${formatMicrocredits(item.actual)}`}</p>{item.status === 'waived' && <p className="central-account-notice">供应商用量仍未知 · 用户不扣点</p>}<p className="desc">费率 {item.rate_version} · {displayDate(item.created_at)}</p></article>)}
      </section>
      <section className="central-account-section" aria-label="设备会话" aria-busy={devices.loading}>
        <h4>设备会话</h4>{devices.data === null && <p role={devices.error ? 'alert' : 'status'}>{readMessage(devices, '设备会话')}</p>}{devices.data?.length === 0 && <p>暂无设备会话。</p>}
        {devices.data?.map(device => <article className="central-account-card" key={device.id}><strong>{device.client_id}</strong><p>{device.current ? '当前设备 · ' : ''}{device.product_id} · 会话期限 {displayDate(device.expires_at)}</p><button type="button" className="btn btn-sm" disabled={busy || statusLoading} onClick={() => void revoke(device.id)}>撤销此设备</button></article>)}
      </section>
    </>}
    {status?.configured && <CentralExecutionSettings authenticated={status.authenticated} revision={revision} scopeKey={JSON.stringify([status.url, status.account?.id, status.scope])} />}
    {status?.configured && <CentralMediaSettings accountSignature={centralAccountSignature(status)} revision={revision} />}
    {status?.configured && <CentralBillingSettings status={status} onFinancialChange={() => setRevision(value => value + 1)} />}
    {status?.configured && <section className="central-account-section" aria-label="套餐权益目录" aria-busy={plans.loading}>
      <h4>套餐权益目录</h4>
      {plans.data === null && <p role={plans.error ? 'alert' : 'status'}>{readMessage(plans, '套餐权益目录')}</p>}{plans.data?.length === 0 && <p>暂无套餐。</p>}
      {plans.data?.map(plan => <article className="central-account-card" key={plan.id}><strong>{plan.name}</strong><p>{plan.test_only ? '测试套餐' : '正式套餐'} · 版本 {plan.version} · {formatMicrocredits(plan.credits)} 点 · {plan.days} 天 · 并发 {plan.concurrency}</p><p>覆盖产品：{plan.included_products.join('、')}</p><p>能力：{plan.features.join('、') || '暂无'}</p></article>)}
    </section>}
  </section>;
}
