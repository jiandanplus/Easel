import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { avatarUrl, centralLogout, centralStatus, fetchAccounts, fetchPersonas, hasAvatar, removeAvatar, uploadAvatar } from '../lib/api';
import type { AccountItem, CentralStatus, PersonaItem } from '../lib/api';
import PlatformAccountSettings from './PlatformAccountSettings';

const failureText = (failure: unknown) => failure instanceof Error ? failure.message : '操作失败，请稍后重试';

/** The letter shown when there is no avatar: first character of the name. */
export function initialOf(name: string | null | undefined): string {
  const text = (name || '').trim();
  return text ? Array.from(text)[0].toUpperCase() : '优';
}

function Row({ label, children, action }: { label: string; children: ReactNode; action?: ReactNode }) {
  return <div className="profile-row"><div className="profile-label">{label}</div><div className="profile-value">{children}</div>{action && <div className="profile-action">{action}</div>}</div>;
}

/** 个人中心：头像、用户名、手机号、邮箱、账号画像、密码、绑定的自媒体平台。 */
export default function ProfileSettings({ onOpenPersonas, onOpenAccounts }: { onOpenPersonas?: () => void; onOpenAccounts?: () => void }) {
  const [status, setStatus] = useState<CentralStatus | null>(null), [statusError, setStatusError] = useState('');
  const [personas, setPersonas] = useState<PersonaItem[] | null>(null), [platforms, setPlatforms] = useState<AccountItem[] | null>(null);
  const [avatar, setAvatar] = useState(0), [avatarBusy, setAvatarBusy] = useState(false), [note, setNote] = useState(''), [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const refreshAvatar = useCallback(() => { void hasAvatar().then(found => setAvatar(found ? Date.now() : 0)).catch(() => setAvatar(0)); }, []);
  useEffect(() => {
    let alive = true;
    void centralStatus().then(value => { if (alive) setStatus(value); }).catch(failure => { if (alive) setStatusError(failureText(failure)); });
    void fetchPersonas().then(value => { if (alive) setPersonas(value); }).catch(() => { if (alive) setPersonas([]); });
    void fetchAccounts().then(value => { if (alive) setPlatforms(value.filter(item => item.supported)); }).catch(() => { if (alive) setPlatforms([]); });
    refreshAvatar();
    return () => { alive = false; };
  }, [refreshAvatar]);

  const choose = async (file: File | undefined) => {
    if (!file || avatarBusy) return;
    setError(''); setNote('');
    if (file.size > 2 * 1024 * 1024) { setError('头像不能超过 2 MB'); return; }
    setAvatarBusy(true);
    try { await uploadAvatar(file); setAvatar(Date.now()); setNote('头像已更新。'); }
    catch (failure) { setError(failureText(failure)); }
    finally { setAvatarBusy(false); if (picker.current) picker.current.value = ''; }
  };
  const clearAvatar = async () => {
    setError(''); setNote(''); setAvatarBusy(true);
    try { await removeAvatar(); setAvatar(0); }
    catch (failure) { setError(failureText(failure)); }
    finally { setAvatarBusy(false); }
  };
  const logout = async () => {
    if (busy || !window.confirm('退出这台电脑上的账号？退出后套餐、余额等需要重新登录才能查看。')) return;
    setBusy(true); setError('');
    try { setStatus(await centralLogout()); } catch (failure) { setError(failureText(failure)); } finally { setBusy(false); }
  };

  if (statusError) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">账号状态暂不可用：{statusError}</p></section>;
  if (!status) return <section className="st-sec active settings-page-body"><p role="status">正在读取账号…</p></section>;
  // Not signed in: the existing login flow (统一账号站) handles sign-in and sign-up.
  if (!status.authenticated) return <PlatformAccountSettings view="profile" />;

  const account = status.account;
  const name = account?.username || (account?.email ? account.email.split('@')[0] : '');
  const signedIn = platforms?.filter(item => item.loggedIn) ?? [];
  return <section className="st-sec active settings-page-body profile-page" aria-label="个人中心">
    <div className="settings-page-head"><div><h3>个人中心</h3><p className="desc">你的统一账号资料。头像只保存在这台电脑上。</p></div>
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void logout()}>{busy ? '退出中…' : '退出登录'}</button></div>
    {error && <p role="alert" className="env-error">{error}</p>}
    {note && <p role="status" className="central-account-notice">{note}</p>}
    <div className="settings-card profile-card">
      <Row label="头像" action={<div className="profile-avatar-actions">
        <input ref={picker} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={event => void choose(event.target.files?.[0])} />
        <button type="button" className="btn btn-sm" disabled={avatarBusy} onClick={() => picker.current?.click()}>{avatarBusy ? '处理中…' : '更换头像'}</button>
        {avatar > 0 && <button type="button" className="btn btn-sm" disabled={avatarBusy} onClick={() => void clearAvatar()}>移除</button>}
      </div>}>
        <span className="profile-avatar" aria-label="头像">{avatar > 0 ? <img src={avatarUrl(avatar)} alt="" onError={() => setAvatar(0)} /> : initialOf(name)}</span>
      </Row>
      <Row label="用户名">{account?.username ? <span className="profile-field">{account.username}</span> : <span className="muted">未设置</span>}</Row>
      <Row label="手机号"><span className="muted">未绑定</span></Row>
      <Row label="邮箱">{account?.email ? <span className="profile-field">{account.email}<span className={`settings-tag${account.email_verified ? '' : ' warn'}`}>{account.email_verified ? '已验证' : '未验证'}</span></span> : <span className="muted">未绑定</span>}</Row>
      <Row label="账号画像" action={onOpenPersonas && <button type="button" className="btn btn-sm" onClick={onOpenPersonas}>管理画像</button>}>
        {personas === null ? <span className="muted">正在读取…</span> : personas.length === 0 ? <span className="muted">还没有画像，建一个画像可以让内容更贴合你的账号定位</span>
          : <span className="profile-chips">{personas.map(persona => <span key={persona.name} className="profile-chip" title={persona.description}>{persona.name}</span>)}</span>}
      </Row>
      <Row label="密码"><span className="profile-field">已设置<span className="muted">· 修改密码即将支持</span></span></Row>
      <Row label="绑定的自媒体平台" action={onOpenAccounts && <button type="button" className="btn btn-sm" onClick={onOpenAccounts}>{signedIn.length ? '管理' : '去绑定'}</button>}>
        {platforms === null ? <span className="muted">正在读取…</span> : <span className="profile-chips">{platforms.map(item => <span key={item.platform} className={`profile-chip${item.loggedIn ? ' on' : ''}`}><i className="dot" />{item.name}</span>)}</span>}
        {platforms && <p className="desc">已登录 {signedIn.length} 个，共 {platforms.length} 个平台</p>}
      </Row>
    </div>
  </section>;
}
