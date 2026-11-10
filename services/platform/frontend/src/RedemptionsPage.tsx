import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatCredits, formatDate, formatDay, RequestGate, Submission, validReason } from './core';
import type { ConsoleClient, LoadState, PageData, RecordData } from './core';
import { issuable } from './ManageDialog';
import { Loading, Modal } from './views';

const states: Record<string, [string, string]> = { active: ['可兑换', 'positive'], expired: ['已过期', 'neutral'], disabled: ['已停用', 'neutral'] };

export function codesText(batch: RecordData): string {
  const codes = Array.isArray(batch.codes) ? batch.codes as string[] : [];
  return [`${String(batch.name ?? '兑换码')}（${formatCredits(batch.credits)} 点，${formatDay(batch.expires_at)} 前有效）`, ...codes].join('\n');
}

function CreateDialog({ client, onClose, onFailure, onCreated }: { client: ConsoleClient; onClose: () => void; onFailure: (error: unknown) => string; onCreated: (batch: RecordData) => void }) {
  const [plans, setPlans] = useState<RecordData[] | null>(null), [planId, setPlanId] = useState('');
  const [name, setName] = useState(''), [count, setCount] = useState('10'), [days, setDays] = useState('30'), [reason, setReason] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission());
  const active = useRef(true);
  useEffect(() => {
    active.current = true; const controller = new AbortController();
    void client.read<PageData>('/api/plans?limit=100&offset=0', controller.signal).then(page => { if (!controller.signal.aborted) setPlans(page.items.filter(issuable)); })
      .catch(() => { if (!controller.signal.aborted) setPlans([]); });
    return () => { active.current = false; controller.abort(); };
  }, [client]);
  const submit = async () => {
    if (busy || submission.busy) return; setError('');
    try {
      if (!planId) throw new Error('请选择一个套餐');
      if (!name.trim() || name.length > 60) throw new Error('请填写 1–60 个字的批次名称');
      const n = Number(count), d = Number(days);
      if (!Number.isInteger(n) || n < 1 || n > 100) throw new Error('数量要在 1 到 100 之间');
      if (!Number.isInteger(d) || d < 1 || d > 365) throw new Error('有效期要在 1 到 365 天之间');
      setBusy(true);
      const result = await submission.send(client, '/api/redemptions', { plan_id: planId, name: name.trim(), count: n, days: d, reason: validReason(reason) }, 'redemption:manage');
      if (active.current) onCreated(result);
    } catch (cause) { if (active.current) setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '生成失败'); }
    finally { if (active.current) setBusy(false); }
  };
  const locked = busy || submission.locked;
  return <Modal title="生成兑换码" onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <p className="muted">每个码只能用一次，同一个用户在一个批次里只能兑换一个。兑换码只在生成后显示这一次，请马上复制保存。</p>
    <label className="reason-field">套餐{plans === null ? <span className="muted">正在读取套餐…</span> : plans.length === 0 ? <span className="muted">没有可用的套餐。请先在“模型与套餐”里发布一个不超过 100 点、365 天的套餐。</span> :
      <select value={planId} disabled={locked} onChange={e => setPlanId(e.target.value)}><option value="">请选择</option>{plans.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name ?? p.id)} · {formatCredits(p.credits)} 点 · {String(p.days)} 天</option>)}</select>}</label>
    <label className="reason-field">批次名称<input value={name} maxLength={60} disabled={locked} onChange={e => setName(e.target.value)} placeholder="例如：十一月朋友试用" /></label>
    <div className="field-row"><label className="reason-field">数量<input inputMode="numeric" value={count} disabled={locked} onChange={e => setCount(e.target.value)} /></label>
      <label className="reason-field">兑换码有效期（天）<input inputMode="numeric" value={days} disabled={locked} onChange={e => setDays(e.target.value)} /></label></div>
    <label className="reason-field">原因<textarea required maxLength={255} rows={2} value={reason} disabled={locked} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || submission.conflict}>{busy ? '生成中…' : '生成'}</button></div></form></Modal>;
}

function CodesDialog({ batch, onClose }: { batch: RecordData; onClose: () => void }) {
  const [copied, setCopied] = useState('');
  const text = codesText(batch), codes = Array.isArray(batch.codes) ? batch.codes as string[] : [];
  const copy = async () => { try { await navigator.clipboard.writeText(text); setCopied('已复制'); } catch { setCopied('复制失败，请手动选中复制'); } };
  const download = () => { const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = `兑换码-${String(batch.name ?? '')}.txt`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
  return <Modal title="兑换码已生成" onClose={onClose}><div className="dialog-content">
    {codes.length ? <><p className="notice">这些兑换码只显示这一次，关闭后无法再查看。请复制或下载保存。</p><textarea className="codes" readOnly rows={Math.min(12, codes.length + 1)} value={text} /></>
      : <p className="notice error">这一批已经生成过，兑换码不能再次显示。如果没有保存，请停用这一批，再生成新的。</p>}
  </div><div className="dialog-actions">{codes.length > 0 && <><span className="muted">{copied}</span><button className="button" onClick={() => void copy()}>复制全部</button><button className="button" onClick={download}>下载文本</button></>}<button className="button primary" onClick={onClose}>我已保存</button></div></Modal>;
}

function BatchDialog({ client, id, canManage, onClose, onFailure, onChanged }: { client: ConsoleClient; id: string; canManage: boolean; onClose: () => void; onFailure: (error: unknown) => string; onChanged: (message: string) => void }) {
  const [state, setState] = useState<LoadState<RecordData>>({ kind: 'loading' }), [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission());
  useEffect(() => {
    const controller = new AbortController();
    void client.read<RecordData>(`/api/redemptions/${encodeURIComponent(id)}`, controller.signal).then(v => { if (!controller.signal.aborted) setState({ kind: 'ready', data: v }); })
      .catch(e => { if (!controller.signal.aborted) setState({ kind: 'error', message: onFailure(e) }); });
    return () => controller.abort();
  }, [client, id, onFailure]);
  const disable = async () => {
    if (busy) return; setError('');
    try { const checked = validReason(reason); setBusy(true); await submission.send(client, `/api/redemptions/${encodeURIComponent(id)}/disable`, { reason: checked }, 'redemption:manage'); onChanged('这一批兑换码已停用，未使用的码不能再兑换。'); }
    catch (cause) { setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '停用失败'); }
    finally { setBusy(false); }
  };
  return <Modal title="兑换码批次" onClose={onClose} busy={busy} wide>{state.kind !== 'ready' ? <Loading state={state} /> : <div className="dialog-content">
    <p><strong>{String(state.data.name)}</strong> · {String(state.data.plan_name ?? state.data.plan_id)} · {formatCredits(state.data.credits)} 点 · {formatDay(state.data.expires_at)} 前有效{state.data.disabled_at ? ' · 已停用' : ''}</p>
    <p className="muted">原因：{String(state.data.reason ?? '')} · 生成于 {formatDate(state.data.created_at)}。兑换码本身不保存，这里只显示每个码的编号开头。</p>
    <div className="table-scroll"><table><thead><tr><th>码编号</th><th>状态</th><th>兑换人</th><th>兑换时间</th></tr></thead><tbody>
      {(state.data.codes as RecordData[]).map(c => <tr key={String(c.code_ref)}><td><code>{String(c.code_ref)}…</code></td><td>{c.redeemed_at ? <span className="badge positive">已兑换</span> : <span className="badge neutral">未使用</span>}</td>
        <td>{c.redeemed_at ? String(c.username ?? c.email ?? c.user_id) : '—'}</td><td>{c.redeemed_at ? formatDate(c.redeemed_at) : '—'}</td></tr>)}
    </tbody></table></div>
    {canManage && !state.data.disabled_at && <><label className="reason-field">停用原因<textarea maxLength={255} rows={2} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
      {error && <p className="notice error" role="alert">{error}</p>}<div className="dialog-actions"><button className="button danger" disabled={busy || submission.conflict} onClick={() => void disable()}>{busy ? '停用中…' : '停用这一批'}</button></div></>}
  </div>}</Modal>;
}

export function RedemptionsPage({ client, permissions, onFailure }: { client: ConsoleClient; permissions: string[]; onFailure: (error: unknown) => string }) {
  const [state, setState] = useState<LoadState<PageData>>({ kind: 'loading' }), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false), [created, setCreated] = useState<RecordData | null>(null), [open, setOpen] = useState<string | null>(null), [notice, setNotice] = useState('');
  const gate = useRef(new RequestGate()), canManage = permissions.includes('redemption:manage');
  useEffect(() => {
    const request = gate.current.begin(); setState({ kind: 'loading' });
    void client.read<PageData>(`/api/redemptions?limit=25&offset=${offset}`, request.signal).then(v => { if (request.current()) setState({ kind: 'ready', data: v }); })
      .catch(e => { if (request.current()) setState({ kind: 'error', message: onFailure(e) }); });
    return () => gate.current.cancel();
  }, [client, offset, revision, onFailure]);
  return <><div className="page-heading"><div><h1>兑换码</h1><p>付款开放前，用兑换码给别人发额度：生成一批码发出去，对方在桌面版“账号与订阅”里输入即可领取。</p></div>
    {canManage && <button className="button primary" onClick={() => setCreating(true)}>生成兑换码</button>}</div>
    {notice && <div className="notice" role="status">{notice}</div>}
    <section className="panel">{state.kind !== 'ready' ? <Loading state={state} onRetry={() => setRevision(x => x + 1)} /> : state.data.items.length === 0 ? <div className="empty-state"><strong>还没有兑换码</strong><p>{canManage ? '点右上角“生成兑换码”开始。' : '管理员生成后会显示在这里。'}</p></div> : <>
      <div className="table-scroll"><table><thead><tr><th>批次</th><th>套餐</th><th>每码点数</th><th>已兑换</th><th>有效期至</th><th>状态</th><th>生成时间</th><th>操作</th></tr></thead><tbody>
        {state.data.items.map(b => { const [label, tone] = states[String(b.state)] ?? ['未知', 'neutral']; return <tr key={String(b.id)}><td><strong>{String(b.name)}</strong></td><td>{String(b.plan_name ?? b.plan_id)}</td><td>{formatCredits(b.credits)}</td>
          <td>{String(b.redeemed)} / {String(b.code_count)}</td><td>{formatDay(b.expires_at)}</td><td><span className={`badge ${tone}`}>{label}</span></td><td>{formatDay(b.created_at)}</td>
          <td><button className="text-button" onClick={() => setOpen(String(b.id))}>查看</button></td></tr>; })}
      </tbody></table></div>
      <div className="pagination"><span>第 {Math.floor(offset / 25) + 1} 页</span><div><button className="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</button><button className="button" disabled={!state.data.has_more} onClick={() => setOffset(offset + 25)}>下一页</button></div></div></>}</section>
    {creating && <CreateDialog client={client} onClose={() => setCreating(false)} onFailure={onFailure} onCreated={batch => { setCreating(false); setCreated(batch); setRevision(x => x + 1); }} />}
    {created && <CodesDialog batch={created} onClose={() => setCreated(null)} />}
    {open && <BatchDialog client={client} id={open} canManage={canManage} onClose={() => setOpen(null)} onFailure={onFailure} onChanged={message => { setOpen(null); setNotice(message); setRevision(x => x + 1); }} />}
  </>;
}
