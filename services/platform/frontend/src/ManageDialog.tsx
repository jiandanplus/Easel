import { useEffect, useRef, useState } from 'react';
import { formatCredits, Submission, validReason } from './core';
import type { ConsoleClient, PageData, RecordData } from './core';
import { Modal } from './views';

export type Manage = { kind: 'disable' | 'enable' | 'logout' | 'note'; user: RecordData } | { kind: 'plan'; user: RecordData; wallet: RecordData };

const MAX_CREDITS = 1000000000000n;
export function issuable(plan: RecordData): boolean {
  const id = String(plan.id ?? ''), credits = typeof plan.credits === 'string' && /^\d+$/.test(plan.credits) ? BigInt(plan.credits) : 0n;
  return plan.retired !== true && !id.startsWith('easel-internal-') && !id.startsWith('easel-owner-allowance-') && credits > 0n && credits <= MAX_CREDITS
    && typeof plan.days === 'number' && plan.days >= 1 && plan.days <= 365;
}
export function parseTags(value: string): string[] {
  const tags = value.split(/[，,\s]+/).map(t => t.trim()).filter(Boolean);
  if (tags.length > 10 || tags.some(t => t.length > 20) || new Set(tags).size !== tags.length) throw new Error('标签最多 10 个，每个不超过 20 个字，不能重复');
  return tags;
}

const copy = {
  disable: ['停用账号', '停用后，这个用户会立刻退出所有设备，也不能再登录或使用模型。余额和记录都会保留，随时可以重新启用。', '停用原因', '确认停用'],
  enable: ['重新启用账号', '启用后，这个用户可以重新登录。之前的登录不会恢复，需要本人重新登录。', '启用原因', '确认启用'],
  logout: ['强制下线', '这个用户会退出所有设备，需要重新登录。账号本身不受影响。', '下线原因', '确认下线'],
  note: ['编辑备注', '备注和标签只有后台能看到，用户看不到。可以按标签搜索用户。', '', '保存备注'],
  plan: ['按套餐发放', '从已发布的套餐里选一个发给这个钱包。套餐的点数和天数立即生效，同一套餐没到期前不能重复发（要更换，先在“套餐与点数”里结束当前套餐）。这不是付款。', '发放原因', '确认发放'],
} as const;

export function ManageDialog({ client, action, onClose, onFailure, onDone }: { client: ConsoleClient; action: Manage; onClose: () => void; onFailure: (error: unknown) => string; onDone: (message: string) => void }) {
  const [title, description, reasonLabel, confirm] = copy[action.kind];
  const note = (action.user.note ?? null) as RecordData | null;
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [text, setText] = useState(typeof note?.note === 'string' ? note.note : ''), [tagText, setTagText] = useState(Array.isArray(note?.tags) ? (note.tags as string[]).join('，') : '');
  const [plans, setPlans] = useState<RecordData[] | null>(null), [planId, setPlanId] = useState('');
  const [submission] = useState(() => new Submission());
  const active = useRef(true);
  useEffect(() => {
    active.current = true; const controller = new AbortController();
    if (action.kind === 'plan') void client.read<PageData>('/api/plans?limit=100&offset=0', controller.signal).then(page => {
      if (!controller.signal.aborted) setPlans(page.items.filter(issuable));
    }).catch(() => { if (!controller.signal.aborted) setPlans([]); });
    return () => { active.current = false; controller.abort(); };
  }, [client, action.kind]);
  const user = encodeURIComponent(String(action.user.id));
  const submit = async () => {
    if (submission.busy || busy) return;
    setError('');
    try {
      let path: string, body: RecordData, permission: string, done: string;
      if (action.kind === 'note') {
        if (text.length > 500) throw new Error('备注最多 500 字');
        path = `/api/users/${user}/note`; body = { note: text, tags: parseTags(tagText), reason: '更新备注' }; permission = 'users:manage'; done = '备注已保存。';
      } else if (action.kind === 'plan') {
        if (!planId) throw new Error('请选择一个套餐');
        path = `/api/wallets/${encodeURIComponent(String(action.wallet.id))}/plans`; body = { plan_id: planId, reason: validReason(reason) }; permission = 'plans:issue'; done = '套餐已发放，操作已记入审计。';
      } else if (action.kind === 'logout') {
        path = `/api/users/${user}/logout`; body = { reason: validReason(reason) }; permission = 'users:manage'; done = '已让这个用户退出所有设备。';
      } else {
        path = `/api/users/${user}/status`; body = { active: action.kind === 'enable', reason: validReason(reason) }; permission = 'users:manage'; done = action.kind === 'enable' ? '账号已重新启用。' : '账号已停用，并已退出所有设备。';
      }
      setBusy(true);
      await submission.send(client, path, body, permission);
      if (active.current) onDone(done);
    } catch (cause) { if (active.current) setError(cause instanceof Error && !('status' in cause) ? cause.message : onFailure(cause)); }
    finally { if (active.current) setBusy(false); }
  };
  const locked = busy || submission.locked;
  return <Modal title={title} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <p>用户：<strong>{String(action.user.username ?? action.user.id)}</strong></p>
    <p className="muted">{description}</p>
    {action.kind === 'plan' && <label className="reason-field">套餐{plans === null ? <span className="muted">正在读取套餐…</span> : plans.length === 0 ? <span className="muted">没有可发放的套餐。请先在“模型与套餐”里发布套餐（不超过 365 天）。</span> :
      <select value={planId} disabled={locked} onChange={e => setPlanId(e.target.value)}><option value="">请选择</option>{plans.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name ?? p.id)} · {formatCredits(p.credits)} 点 · {String(p.days)} 天{p.test_only ? ' · 测试' : ''}</option>)}</select>}</label>}
    {action.kind === 'note' ? <>
      <label className="reason-field">备注<textarea maxLength={500} rows={4} value={text} disabled={locked} onChange={e => setText(e.target.value)} placeholder="例如：老朋友，先给文本额度" /></label>
      <label className="reason-field">标签<input value={tagText} disabled={locked} onChange={e => setTagText(e.target.value)} placeholder="用逗号或空格分隔，例如：朋友，内测" /></label>
    </> : <label className="reason-field">{reasonLabel}<textarea required maxLength={255} rows={2} value={reason} disabled={locked} onChange={e => setReason(e.target.value)} /></label>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {submission.locked && error && <p className="caption">重试会沿用同一次操作，不会重复执行。</p>}
  </div><div className="dialog-actions"><button type="button" className="button" onClick={onClose} disabled={busy}>取消</button>
    <button type="submit" className={`button ${action.kind === 'disable' ? 'danger' : 'primary'}`} disabled={busy || submission.conflict}>{busy ? '提交中…' : confirm}</button></div></form></Modal>;
}
