import { useEffect, useState } from 'react';
import { platformStatus, platformConfigure, platformAuthenticate, platformLogout, platformPlans, platformEntitlements, platformUsage, platformDevices, platformRevoke } from '../lib/api';
import type { PlatformStatus, PlatformPlan, PlatformGrant, PlatformUsage, PlatformDevice } from '../lib/api';
import './PlatformAccountSettings.css';

export default function PlatformAccountSettings() {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [url, setUrl] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [plans, setPlans] = useState<PlatformPlan[]>([]);
  const [grants, setGrants] = useState<PlatformGrant[]>([]);
  const [usage, setUsage] = useState<PlatformUsage | null>(null);
  const [devices, setDevices] = useState<PlatformDevice[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setPlans([]); setGrants([]); setUsage(null); setDevices([]);
    void (async () => {
      try {
        const current = await platformStatus();
        if (!alive) return;
        setStatus(current); setUrl(current.url || '');
        if (current.error) setError(current.error.message);
        if (current.configured) {
          const catalog = await platformPlans();
          if (alive) setPlans(catalog.plans);
        }
        if (current.authenticated) {
          const [entitlements, measured, sessions] = await Promise.all([platformEntitlements(), platformUsage(), platformDevices()]);
          if (!alive) return;
          setGrants(entitlements.entitlements); setUsage(measured); setDevices(sessions.sessions);
        }
      } catch (failure) { if (alive) setError(failure instanceof Error ? failure.message : '读取失败'); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [revision]);
  const mutate = async (action: () => Promise<unknown>) => {
    if (busy || loading) return;
    setBusy(true); setError('');
    try { await action(); setRevision(value => value + 1); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '操作失败'); }
    finally { setPassword(''); setBusy(false); }
  };
  const authenticate = (mode: 'register' | 'login') => {
    const submitted = password;
    setPassword('');
    return mutate(async () => {
      try { return await platformAuthenticate(mode, { email, password: submitted, device_name: 'Easel 本机' }); }
      catch (failure) {
        if (mode === 'login' && (failure as Error & { code?: string }).code === 'invalid_authentication') throw new Error('登录失败，请检查邮箱和密码');
        throw failure;
      }
    });
  };
  const now = Date.now() / 1000;
  const available = grants.filter(grant => grant.status === 'active' && grant.starts_at <= now && grant.ends_at > now)
    .reduce((total, grant) => total + grant.remaining_tokens, 0);
  const date = (seconds: number) => new Date(seconds * 1000).toLocaleString();
  return <section className="platform-account st-sec active" aria-label="账号与订阅" aria-busy={loading || busy}>
    <div className="panel-top"><h3>账号与订阅</h3><span className="spacer" /><button type="button" className="btn btn-sm" disabled={busy || loading} onClick={() => setRevision(value => value + 1)}>刷新账号状态</button></div>
    <p className="desc">自定义服务无需 Easel 账号。订阅服务当前仅支持 Codex 在本机执行；云端执行与订阅媒体通道暂未开放。</p>
    {loading && <p role="status">正在读取平台状态…</p>}
    {error && <p role="alert" className="env-error">{error}</p>}
    <form onSubmit={event => { event.preventDefault(); void mutate(() => platformConfigure(url)); }} className="platform-form">
      <label htmlFor="platform-url">平台服务地址</label><input id="platform-url" className="mock" type="url" value={url} disabled={busy || loading || status?.authenticated} required onChange={event => setUrl(event.target.value)} placeholder="https://平台地址；本地测试可用 http://127.0.0.1:端口" />
      <button type="submit" className="btn btn-sm" disabled={busy || loading || status?.authenticated}>保存服务地址</button>
    </form>
    {!status?.authenticated && status?.session_id && <div><p className="desc">本机仍保留已失效的登录，请先清除后重新登录。网络故障时清除失败会保留原会话，可稍后重试。</p><button type="button" className="btn btn-sm" disabled={busy || loading} onClick={() => void mutate(platformLogout)}>清除失效登录</button></div>}
    {!status?.configured && !loading && <p role="status">平台服务未配置；自定义服务仍可使用。</p>}
    {status?.authenticated ? <>
      <p><strong>{status.account?.email}</strong> · {status.account?.email_verified ? '邮箱已验证' : '邮箱未验证'}</p>
      <p className="desc">更换服务地址请先退出账号。任务接受后，账号与服务来源会固定；任务期间修改由服务端拒绝。</p>
      <button type="button" className="btn btn-sm" disabled={busy || loading} onClick={() => void mutate(platformLogout)}>退出账号</button>
      <dl className="platform-usage"><div><dt>当前可用 tokens</dt><dd>{available}</dd></div><div><dt>累计已用 tokens</dt><dd>{usage?.used_tokens ?? '—'}</dd></div><div><dt>预留 tokens（非实际消耗）</dt><dd>{usage?.reserved_tokens ?? '—'}</dd></div><div><dt>待核算请求</dt><dd>{usage?.pending_reconciliation ?? '—'}</dd></div></dl>
      <h4>测试权益</h4>{!grants.length && <p>当前无测试权益；请联系运营者授予测试套餐。</p>}
      {grants.map(grant => <article className="platform-card" key={grant.id}><strong>{grant.plan_id}</strong><p>{grant.status !== 'active' ? grant.status === 'revoked' ? '已撤销' : grant.status : grant.ends_at <= now ? '已过期' : grant.starts_at > now ? '尚未生效' : '生效中'} · 到期 {date(grant.ends_at)}</p><p>已用 {grant.used_tokens} · 预留 {grant.reserved_tokens} · 账面剩余 {grant.remaining_tokens}</p><p>模型：{grant.models.join('、')}</p></article>)}
      <h4>设备会话</h4>{devices.map(device => <article className="platform-card" key={device.id}><strong>{device.device_name}</strong><p>{device.id === status.session_id ? '当前设备 · ' : ''}{device.revoked ? '已撤销' : `刷新期限 ${date(device.refresh_expires_at)}`}</p><button type="button" className="btn btn-sm" disabled={busy || loading || device.revoked} onClick={() => void mutate(() => platformRevoke(device.id))}>撤销此设备</button></article>)}
    </> : <form className="platform-form" onSubmit={event => { event.preventDefault(); void authenticate('login'); }}>
      <label htmlFor="platform-email">邮箱</label><input id="platform-email" className="mock" type="email" autoComplete="username" value={email} required disabled={busy || loading || !status?.configured} onChange={event => setEmail(event.target.value)} />
      <label htmlFor="platform-password">密码（15–128 个字符）</label><input id="platform-password" className="mock" type="password" autoComplete="off" minLength={15} maxLength={128} required value={password} disabled={busy || loading || !status?.configured} onChange={event => setPassword(event.target.value)} />
      <div className="platform-actions"><button type="submit" className="btn btn-sm" disabled={busy || loading || !status?.configured}>登录</button><button type="button" className="btn btn-sm" disabled={busy || loading || !status?.configured || !email || password.length < 15} onClick={() => void authenticate('register')}>注册测试账号</button></div>
    </form>}
    <h4>服务端测试套餐目录</h4><p className="desc">注册不会自动获得模型额度。测试权益仅由运营者通过命令行授予，目录展示不代表当前账号已获授权。</p>
    {!plans.length && <p>暂无测试套餐。</p>}{plans.map(plan => <article className="platform-card" key={plan.id}><strong>{plan.name}</strong><p>测试权益 · {plan.tokens} tokens · {plan.days} 天 · 并发 {plan.concurrency}</p><p>模型：{plan.models.join('、')}</p></article>)}
  </section>;
}
