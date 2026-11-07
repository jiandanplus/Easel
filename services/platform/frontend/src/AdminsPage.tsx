import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatDate, RequestGate, Submission, validReason } from './core';
import type { ConsoleClient, LoadState, RecordData } from './core';
import { Loading, Modal } from './views';

const roles: Record<string, string> = { admin: '管理员', operator: '运营者', viewer: '只读查看者' };
type Action = { kind: 'approve'; request: RecordData } | { kind: 'disable'; admin: RecordData };

function ActionDialog({ client, action, onClose, onFailure, onDone }: { client: ConsoleClient; action: Action; onClose: () => void; onFailure: (error: unknown) => string; onDone: (message: string) => void }) {
  const [role, setRole] = useState('operator'), [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission());
  const submit = async () => {
    if (busy) return; setError('');
    try {
      const checked = validReason(reason); setBusy(true);
      if (action.kind === 'approve') {
        await submission.send(client, `/api/admin-requests/${encodeURIComponent(String(action.request.id))}/approve`, { role, reason: checked }, 'admins:manage');
        onDone(`已批准为${roles[role]}。对方重新登录后台即可使用。`);
      } else {
        await submission.send(client, `/api/admins/${encodeURIComponent(String(action.admin.id))}/disable`, { reason: checked }, 'admins:manage');
        onDone('已停用这个后台账号，对方会立即退出。');
      }
    } catch (cause) { setError(cause instanceof ConsoleError ? onFailure(cause) : cause instanceof Error ? cause.message : '操作失败'); }
    finally { setBusy(false); }
  };
  return <Modal title={action.kind === 'approve' ? '批准后台申请' : '停用后台账号'} onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    {action.kind === 'approve' ? <><p>申请编号 <code>{String(action.request.id)}</code></p><p className="muted">请先和申请人核对这个编号，确认是你认识的人。网页上只能批准运营者或只读查看者；管理员仍需在服务器上批准。</p>
      <label className="reason-field">角色<select value={role} disabled={busy} onChange={e => setRole(e.target.value)}><option value="operator">运营者：能看数据、发布套餐、管理用户，不能发点数</option><option value="viewer">只读查看者：只能看</option></select></label></>
      : <p className="muted">停用后对方立即退出后台。之后要恢复，需要重新申请并批准。</p>}
    <label className="reason-field">原因<textarea required maxLength={255} rows={2} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
  </div><div className="dialog-actions"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
    <button type="submit" className={`button ${action.kind === 'disable' ? 'danger' : 'primary'}`} disabled={busy || submission.conflict}>{busy ? '提交中…' : action.kind === 'approve' ? '批准' : '停用'}</button></div></form></Modal>;
}

export function AdminsPage({ client, onFailure }: { client: ConsoleClient; onFailure: (error: unknown) => string }) {
  const [state, setState] = useState<LoadState<RecordData>>({ kind: 'loading' }), [revision, setRevision] = useState(0);
  const [action, setAction] = useState<Action | null>(null), [notice, setNotice] = useState('');
  const gate = useRef(new RequestGate());
  useEffect(() => {
    const request = gate.current.begin(); setState({ kind: 'loading' });
    void client.read<RecordData>('/api/admins', request.signal).then(v => { if (request.current()) setState({ kind: 'ready', data: v }); })
      .catch(e => { if (request.current()) setState({ kind: 'error', message: onFailure(e) }); });
    return () => gate.current.cancel();
  }, [client, revision, onFailure]);
  const heading = <div className="page-heading"><div><h1>后台账号</h1><p>新同事在后台登录页注册后会得到一个申请编号，在这里核对并批准。</p></div><button className="button" onClick={() => setRevision(x => x + 1)}>刷新</button></div>;
  if (state.kind !== 'ready') return <>{heading}<Loading state={state} onRetry={() => setRevision(x => x + 1)} /></>;
  const admins = (state.data.admins ?? []) as RecordData[], requests = (state.data.requests ?? []) as RecordData[], self = state.data.self;
  return <>{heading}{notice && <div className="notice" role="status">{notice}</div>}
    <section className="panel"><h2 className="panel-title">待批准的申请</h2>{requests.length === 0 ? <div className="empty-state"><strong>没有待批准的申请</strong></div> :
      <ul className="plain-list">{requests.map(r => <li key={String(r.id)}><span><code>{String(r.id)}</code> · 申请于 {formatDate(r.verified_at)} · {formatDate(r.expires_at)} 过期</span><button className="button primary" onClick={() => setAction({ kind: 'approve', request: r })}>批准</button></li>)}</ul>}</section>
    <section className="panel"><h2 className="panel-title">已有账号</h2><div className="table-scroll"><table><thead><tr><th>账号编号</th><th>角色</th><th>状态</th><th>开通时间</th><th>最近登录</th><th>操作</th></tr></thead><tbody>
      {admins.map(a => <tr key={String(a.id)}><td><code>{String(a.id).slice(0, 12)}…</code>{a.id === self && <span className="muted"> （你）</span>}</td><td>{roles[String(a.role)] ?? String(a.role)}</td>
        <td>{a.active ? <span className="badge positive">正常</span> : <span className="badge neutral">已停用</span>}</td><td>{formatDate(a.created_at)}</td><td>{a.last_login_at ? formatDate(a.last_login_at) : '—'}</td>
        <td>{a.active && a.role !== 'admin' && a.id !== self ? <button className="text-button" onClick={() => setAction({ kind: 'disable', admin: a })}>停用</button> : <span className="muted">—</span>}</td></tr>)}
    </tbody></table></div></section>
    {action && <ActionDialog client={client} action={action} onClose={() => setAction(null)} onFailure={onFailure} onDone={message => { setAction(null); setNotice(message); setRevision(x => x + 1); }} />}
  </>;
}
