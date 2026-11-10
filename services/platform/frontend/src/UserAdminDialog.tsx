import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatCredits, formatDate, Submission, validReason } from './core';
import type { ConsoleClient, RecordData } from './core';
import { Modal } from './views';

/** Account and balance changes only an admin makes: they reach the sign-in service or the ledger. */
export type UserAdmin =
  | { kind: 'create' }
  | { kind: 'profile' | 'password' | 'role' | 'delete' | 'restore'; user: RecordData }
  | { kind: 'credits'; user: RecordData; wallet: string }
  | { kind: 'end'; user: RecordData; plan: RecordData };

const MAX_POINTS = 1000000;
/** '+12.5' / '-3' points, as the server takes them; at most six decimals and 1,000,000 points. */
export function pointsDelta(direction: 'add' | 'deduct', value: string): string {
  const text = value.trim();
  if (!/^\d{1,7}(\.\d{1,6})?$/.test(text) || Number(text) <= 0 || Number(text) > MAX_POINTS) throw new Error('请填写大于 0、不超过 1000000 的点数，最多六位小数');
  return (direction === 'add' ? '+' : '-') + text;
}
/** True for a server refusal that executed nothing (not a lost reply or an idempotency clash). */
export function refused(cause: unknown): boolean {
  return cause instanceof ConsoleError && cause.status >= 400 && cause.status < 500 && cause.status !== 401 && cause.code !== 'console_idempotency_conflict' && cause.code !== 'operation_locked';
}
export function usernameError(value: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(value) ? '' : '用户名只能用英文字母、数字和下划线，并以字母或下划线开头';
}
/** Logto stores phones with the country code; show a mainland number without the 86. */
export function phoneText(value: unknown): string {
  return typeof value === 'string' && /^861[3-9]\d{9}$/.test(value) ? value.slice(2) : typeof value === 'string' ? '+' + value : '';
}

const titles: Record<UserAdmin['kind'], string> = { create: '新建用户', profile: '编辑资料', password: '重置密码', role: '设置角色', delete: '删除账号', restore: '恢复账号', credits: '调整余额', end: '结束套餐' };
const done: Record<UserAdmin['kind'], string> = { create: '用户已创建，可以用用户名和密码登录。', profile: '资料已更新。', password: '密码已重置，请把新密码告诉本人。', role: '角色已更新。', delete: '账号已删除：已退出所有设备，不能再登录。记录都保留，可以恢复。', restore: '账号已恢复，本人可以重新登录。', credits: '余额已调整，已记入额度流水。', end: '套餐已结束，剩余点数已作废。' };

export function UserAdminDialog({ client, action, onClose, onFailure, onDone }: { client: ConsoleClient; action: UserAdmin; onClose: () => void; onFailure: (error: unknown) => string; onDone: (message: string, result: RecordData) => void }) {
  const user = action.kind === 'create' ? null : action.user;
  const profile = (user?.profile ?? null) as RecordData | null;
  const identity = Array.isArray(user?.identities) ? (user.identities as RecordData[])[0] : undefined;
  const [username, setUsername] = useState(String(profile?.username ?? identity?.username ?? ''));
  const [phone, setPhone] = useState(phoneText(profile?.phone));
  const [email, setEmail] = useState(String(profile?.email ?? identity?.email ?? ''));
  const [password, setPassword] = useState(''), [role, setRole] = useState(user?.role === 'admin' ? 'user' : 'admin');
  const [direction, setDirection] = useState<'add' | 'deduct'>('add'), [points, setPoints] = useState('');
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission, setSubmission] = useState(() => new Submission());
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const id = user ? encodeURIComponent(String(user.id)) : '';
  const submit = async () => {
    if (busy || submission.busy) return;
    setError('');
    try {
      let path: string, body: RecordData, permission = 'admins:manage';
      if (action.kind === 'create' || action.kind === 'profile') {
        const problem = usernameError(username.trim()); if (problem) throw new Error(problem);
        if (action.kind === 'create' && password && password.length < 8) throw new Error('密码至少 8 位');
        const fields = { username: username.trim(), phone: phone.trim(), email: email.trim() };
        path = action.kind === 'create' ? '/api/users' : `/api/users/${id}/profile`;
        body = action.kind === 'create' ? { ...fields, password, reason: validReason(reason) } : { ...fields, reason: validReason(reason) };
      } else if (action.kind === 'password') {
        if (password.length < 8) throw new Error('密码至少 8 位');
        path = `/api/users/${id}/password`; body = { password, reason: validReason(reason) };
      } else if (action.kind === 'role') {
        path = `/api/users/${id}/role`; body = { role, reason: validReason(reason) };
      } else if (action.kind === 'delete' || action.kind === 'restore') {
        path = `/api/users/${id}/delete`; body = { deleted: action.kind === 'delete', reason: validReason(reason) };
      } else if (action.kind === 'credits') {
        path = `/api/wallets/${encodeURIComponent(action.wallet)}/credits`; body = { points: pointsDelta(direction, points), reason: validReason(reason) }; permission = 'plans:issue';
      } else if (action.kind === 'end') {
        path = `/api/wallets/${encodeURIComponent(String(action.plan.wallet_id))}/plans/end`; body = { membership_id: String(action.plan.id), reason: validReason(reason) }; permission = 'plans:issue';
      } else throw new Error('未知操作');
      setBusy(true);
      const result = await submission.send(client, path, body, permission) as RecordData;
      if (active.current) onDone(done[action.kind], result ?? {});
    } catch (cause) {
      if (!active.current) return;
      setError(cause instanceof Error && !('status' in cause) ? cause.message : onFailure(cause));
      // A definite refusal changed nothing, so the form unlocks and the next try is a new operation.
      if (refused(cause)) setSubmission(new Submission());
    }
    finally { if (active.current) setBusy(false); }
  };
  const locked = busy || submission.locked;
  const who = user ? String(user.username ?? identity?.username ?? user.id) : '';
  return <Modal title={titles[action.kind]} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {user && <p>用户：<strong>{who}</strong></p>}
    {(action.kind === 'create' || action.kind === 'profile') && <>
      <p className="muted">{action.kind === 'create' ? '在登录服务里建一个账号，本人可以用用户名和密码登录；填了手机号或邮箱，也能用验证码登录。建好后可以直接发套餐。' : '改的是登录账号本身，本人下次登录就会看到。手机号和邮箱留空表示去掉。'}</p>
      <label className="reason-field">用户名<input required maxLength={128} value={username} disabled={locked} autoComplete="off" onChange={e => setUsername(e.target.value)} placeholder="例如 zhang_san" /></label>
      <label className="reason-field">手机号（可选）<input maxLength={24} inputMode="tel" value={phone} disabled={locked} onChange={e => setPhone(e.target.value)} placeholder="例如 13800138000，境外号码以 + 开头" /></label>
      <label className="reason-field">邮箱（可选）<input maxLength={254} type="email" value={email} disabled={locked} onChange={e => setEmail(e.target.value)} /></label>
      {action.kind === 'create' && <label className="reason-field">初始密码（可选）<input type="password" maxLength={128} value={password} disabled={locked} autoComplete="new-password" onChange={e => setPassword(e.target.value)} placeholder="至少 8 位；不填则只能用验证码登录" /></label>}
    </>}
    {action.kind === 'password' && <><p className="muted">新密码立即生效，旧密码作废。密码不会记录在后台，请自己转告本人。</p>
      <label className="reason-field">新密码<input required type="password" maxLength={128} value={password} disabled={locked} autoComplete="new-password" onChange={e => setPassword(e.target.value)} placeholder="至少 8 位" /></label></>}
    {action.kind === 'role' && <><p className="muted">管理员能登录这个后台，管理用户、套餐和模型。不能取消自己的管理员，也不能取消最后一个管理员。</p>
      <label className="reason-field">角色<select value={role} disabled={locked} onChange={e => setRole(e.target.value)}><option value="admin">管理员</option><option value="user">用户</option></select></label></>}
    {action.kind === 'delete' && <p className="notice error">删除后，这个用户立刻退出所有设备，不能再登录，也不会出现在用户列表里（在“已删除”里能找到）。余额、套餐和记录都保留，随时可以恢复。管理员要先取消管理员才能删除。</p>}
    {action.kind === 'restore' && <p className="muted">恢复后本人可以重新登录，余额和套餐照旧。之前的登录不会恢复。</p>}
    {action.kind === 'credits' && <><p className="muted">增加的点数挂在用户当前的正式套餐上，跟套餐一起到期，能用的模型也跟套餐一样；没有生效套餐时请先发套餐。扣减从最早到期的点数扣起，不会扣成负数。每一笔都记入额度流水。</p>
      <div className="field-row"><label className="reason-field">方向<select value={direction} disabled={locked} onChange={e => setDirection(e.target.value as 'add' | 'deduct')}><option value="add">增加</option><option value="deduct">扣减</option></select></label>
        <label className="reason-field">点数<input required inputMode="decimal" value={points} disabled={locked} onChange={e => setPoints(e.target.value)} placeholder="例如 100" /></label></div></>}
    {action.kind === 'end' && <><p className="notice error">立即结束“{String(action.plan.plan_name ?? action.plan.plan_id)}”（原定 {formatDate(action.plan.expires_at)} 到期）：这个套餐给的功能马上失效，剩余 {formatCredits(action.plan.available)} 点作废。有正在进行的调用时不能结束，请稍后再试。</p></>}
    <label className="reason-field">操作原因<textarea required maxLength={255} rows={2} value={reason} disabled={locked} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
    {submission.locked && error && <p className="caption">重试会沿用同一次操作，不会重复执行。</p>}
  </div><div className="dialog-actions"><button type="button" className="button" onClick={onClose} disabled={busy}>取消</button>
    <button type="submit" className={`button ${action.kind === 'delete' || action.kind === 'end' ? 'danger' : 'primary'}`} disabled={busy || submission.conflict}>{busy ? '提交中…' : action.kind === 'create' ? '创建' : '确认'}</button></div></form></Modal>;
}
