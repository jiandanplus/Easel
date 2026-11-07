import { useCallback, useEffect, useRef, useState } from 'react';
import { buildDefinition, ConsoleClient, ConsoleError, csvText, formatCredits, listPath, RequestGate, Submission, titles, userFilters, userSorts, validReason, writePermissions } from './core';
import type { AuthState, Catalog, Domain, Fields, LoadState, PageData, RecordData, Session } from './core';
import { ReconciliationPage } from './ReconciliationPage';
import { ReviewRegistry } from './reconciliation';
import { AuthScreen, DataPage, DefinitionFields, DetailFields, initialFields, Loading, Mark, Modal, Overview } from './views';
import { StatsPage } from './StatsPage';
import { UserDetail } from './UserDetail';
import { ManageDialog } from './ManageDialog';
import { RedemptionsPage } from './RedemptionsPage';
import { AdminsPage } from './AdminsPage';
import type { Manage } from './ManageDialog';
import { AllowanceDialog } from './AllowanceDialog';

type Page = Domain | 'overview' | 'reconciliation' | 'stats' | 'redemptions' | 'admins';
type Mutation = { domain: Catalog; row?: RecordData };
const mainPages: Page[] = ['overview', 'stats', 'users', 'redemptions', 'executions', 'models', 'audits'];
const advancedPages: Page[] = ['admins', 'reconciliation', 'wallets', 'orders', 'providers', 'products'];
const catalogPages: Page[] = ['models', 'plans', 'offers'];
const navLabel = (item: Page) => item === 'models' ? '模型与套餐' : titles[item];
const navIcons: Partial<Record<Page, string>> = { overview: 'M3 10l7-6 7 6M5 9v7h10V9', stats: 'M3 17h14M6 14V9m4 5V5m4 9v-6', redemptions: 'M3 7h14v3a2 2 0 000 4v3H3v-3a2 2 0 000-4V7zM8 7v10', admins: 'M10 3l6 3v4c0 4-3 6-6 7-3-1-6-3-6-7V6l6-3z', users: 'M10 10a3 3 0 100-6 3 3 0 000 6zM4 17c0-3 3-5 6-5s6 2 6 5', executions: 'M4 16V10m6 6V5m6 11v-8', models: 'M10 3l7 4-7 4-7-4 7-4zM3 12l7 4 7-4', audits: 'M10 5v5l3 2M10 17a7 7 0 100-14 7 7 0 000 14z' };
export default function App() {
  const [client] = useState(() => new ConsoleClient());
  const [reviewRegistry] = useState(() => new ReviewRegistry());
  const [auth, setAuth] = useState<AuthState>({ kind: 'loading' });
  const [page, setPage] = useState<Page>('overview');
  const [query, setQuery] = useState(''), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0);
  const [data, setData] = useState<LoadState<PageData | RecordData>>({ kind: 'loading' });
  const [detail, setDetail] = useState<LoadState<RecordData> | null>(null);
  const [mutation, setMutation] = useState<Mutation | null>(null);
  const [notice, setNotice] = useState(''), [logoutBusy, setLogoutBusy] = useState(false), [allowance, setAllowance] = useState<RecordData | null>(null);
  const [manage, setManage] = useState<Manage | null>(null);
  const [userStatus, setUserStatus] = useState(''), [userSort, setUserSort] = useState('created'), [exporting, setExporting] = useState(false);
  const authGate = useRef(new RequestGate()), dataGate = useRef(new RequestGate()), detailGate = useRef(new RequestGate());
  const fail = useCallback((error: unknown) => {
    if (error instanceof ConsoleError && [401, 403].includes(error.status)) {
      client.setSession(null); dataGate.current.cancel(); detailGate.current.cancel(); setData({ kind: 'loading' }); setDetail(null); setMutation(null); setNotice('');
      setAuth(error.status === 401 ? { kind: 'anonymous' } : { kind: 'error', message: error.message });
    }
    return error instanceof Error ? error.message : '读取未完成，请重试';
  }, [client]);
  const checkSession = useCallback(async () => {
    const request = authGate.current.begin(); setAuth({ kind: 'loading' });
    try {
      const session = await client.read<Session>('/api/session', request.signal);
      if (!request.current()) return;
      client.setSession(session);
      if (!session.admin.permissions.includes('console:read')) throw new ConsoleError(403, 'permission_denied');
      setAuth({ kind: 'authenticated', session });
    } catch (error) { if (request.current()) { const message = fail(error); if (!(error instanceof ConsoleError && [401, 403].includes(error.status))) setAuth({ kind: 'error', message }); } }
  }, [client, fail]);
  useEffect(() => { void checkSession(); return () => { authGate.current.cancel(); dataGate.current.cancel(); detailGate.current.cancel(); }; }, [checkSession]);
  useEffect(() => {
    if (auth.kind !== 'authenticated' || page === 'reconciliation' || page === 'stats' || page === 'redemptions' || page === 'admins') { dataGate.current.cancel(); return; }
    const request = dataGate.current.begin(); setData({ kind: 'loading' });
    void client.read<PageData | RecordData>(page === 'overview' ? '/api/overview' : listPath(page, query, offset, { status: userStatus, sort: userSort }), request.signal).then(value => {
      if (!request.current()) return;
      if (!value || typeof value !== 'object' || (page !== 'overview' && (!Array.isArray(value.items) || typeof value.has_more !== 'boolean'))) throw new ConsoleError(502, 'invalid_response');
      setData({ kind: 'ready', data: value });
    }).catch(error => { if (request.current()) setData({ kind: 'error', message: fail(error) }); });
    return () => dataGate.current.cancel();
  }, [auth, page, query, offset, revision, client, fail, userStatus, userSort]);
  const inspect = async (row: RecordData) => {
    const request = detailGate.current.begin();
    if (!['users', 'wallets', 'orders'].includes(page)) { setDetail({ kind: 'ready', data: row }); return; }
    setDetail({ kind: 'loading' });
    try {
      const value = await client.read<RecordData>(`/api/${page}/${encodeURIComponent(String(row.id))}`, request.signal);
      // One person normally owns one wallet; the summary shows balance and usage from it.
      const owned = page === 'users' && Array.isArray(value.wallets) ? (value.wallets as RecordData[]).slice(0, 3) : [];
      const wallet_details = await Promise.all(owned.map(wallet => client.read<RecordData>(`/api/wallets/${encodeURIComponent(String(wallet.id))}`, request.signal)));
      if (request.current()) setDetail({ kind: 'ready', data: page === 'users' ? { ...value, wallet_details } : value });
    }
    catch (error) { if (request.current()) setDetail({ kind: 'error', message: fail(error) }); }
  };
  const closeDetail = () => { detailGate.current.cancel(); setDetail(null); };
  const navigate = (next: Page) => { dataGate.current.cancel(); setData({ kind: 'loading' }); closeDetail(); setPage(next); setQuery(''); setOffset(0); setNotice(''); setUserStatus(''); setUserSort('created'); setRevision(x => x + 1); };
  const exportUsers = async () => {
    if (exporting) return; setExporting(true);
    try {
      const rows: RecordData[] = [];
      for (let start = 0; start < 2000; start += 100) {
        const page = await client.read<PageData>(listPath('users', query, start, { status: userStatus, sort: userSort, limit: 100 }));
        rows.push(...page.items); if (!page.has_more) break;
      }
      const header: [string, string][] = [['id', '用户编号'], ['username', '用户名'], ['email', '邮箱'], ['spendable_available', '余额（点）'], ['spent', '已用（点）'], ['calls', '调用次数'], ['last_used_at', '最近使用'], ['expires_at', '额度到期'], ['active', '账号可用'], ['created_at', '注册时间']];
      const readable = rows.map(row => ({ ...row, spendable_available: formatCredits(row.spendable_available), spent: formatCredits(row.spent), active: row.active === false ? '否' : '是' }));
      const url = URL.createObjectURL(new Blob([csvText(readable, header)], { type: 'text/csv;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `优创用户-${new Date().toISOString().slice(0, 10)}.csv`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(`已导出 ${rows.length} 个用户${rows.length >= 2000 ? '（只导出前 2000 个，请缩小筛选范围）' : ''}。`);
    } catch (error) { setNotice(fail(error)); }
    finally { setExporting(false); }
  };
  const userToolbar = <div className="filter-group">
    <label><span className="sr-only">筛选</span><select value={userStatus} onChange={e => { setUserStatus(e.target.value); setOffset(0); }}>{userFilters.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
    <label><span className="sr-only">排序</span><select value={userSort} onChange={e => { setUserSort(e.target.value); setOffset(0); }}>{userSorts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
    <button className="button" disabled={exporting} onClick={() => void exportUsers()}>{exporting ? '导出中…' : '导出表格'}</button></div>;
  const openWallet = async (id: string) => {
    navigate('wallets'); setQuery(id);
    const request = detailGate.current.begin(); setDetail({ kind: 'loading' });
    try { const value = await client.read<RecordData>(`/api/wallets/${encodeURIComponent(id)}`, request.signal); if (request.current()) setDetail({ kind: 'ready', data: value }); }
    catch (error) { if (request.current()) setDetail({ kind: 'error', message: fail(error) }); }
  };
  const logout = async () => {
    if (logoutBusy) return; setLogoutBusy(true);
    try { await client.logout(); dataGate.current.cancel(); detailGate.current.cancel(); setDetail(null); setMutation(null); setData({ kind: 'loading' }); setAuth({ kind: 'anonymous' }); }
    catch (error) { setNotice(fail(error)); }
    finally { setLogoutBusy(false); }
  };
  if (auth.kind !== 'authenticated') return <AuthScreen state={auth} onRetry={() => void checkSession()} />;
  const navButton = (item: Page) => { const active = page === item || (item === 'models' && catalogPages.includes(page)); return <button key={item} className={`nav-link ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} onClick={() => navigate(item)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d={navIcons[item] ?? 'M5 7h10M5 10h10M5 13h6'} /></svg>{navLabel(item)}</button>; };
  return <div className="console-shell"><a className="skip-link" href="#main">跳到主内容</a><aside className="sidebar"><a className="brand" href="/"><Mark /><span>优创 <small>后台</small></span></a><nav aria-label="运营导航"><section className="nav-group">{mainPages.map(navButton)}</section><details className="nav-advanced" open={advancedPages.includes(page)}><summary>高级</summary>{advancedPages.filter(item => item !== 'admins' || auth.session.admin.permissions.includes('admins:manage')).map(navButton)}</details></nav><div className="sidebar-bottom"><span className="avatar">{auth.session.admin.role === 'viewer' ? '阅' : '管'}</span><div><strong>{auth.session.admin.role === 'admin' ? '管理员' : auth.session.admin.role === 'operator' ? '运营者' : '只读查看者'}</strong></div><button className="text-button" disabled={logoutBusy} onClick={() => void logout()}>{logoutBusy ? '退出中' : '退出'}</button></div></aside><div className="workspace"><header className="topbar"><strong>{titles[page]}</strong><span className="secure-session"><span className="status-dot" />已登录</span></header><main id="main" className="main" tabIndex={-1}>{notice && <div className="notice" role="status">{notice}</div>}{page === 'stats' ? <StatsPage client={client} onFailure={fail} /> : page === 'redemptions' ? <RedemptionsPage client={client} permissions={auth.session.admin.permissions} onFailure={fail} /> : page === 'admins' ? <AdminsPage client={client} onFailure={fail} /> : page === 'reconciliation' ? <ReconciliationPage client={client} session={auth.session} registry={reviewRegistry} onFailure={fail} onWallet={id => void openWallet(id)} /> : page === 'overview' ? <Overview state={data as LoadState<RecordData>} onRetry={() => setRevision(x => x + 1)} onNavigate={navigate} /> : <>{catalogPages.includes(page) && <div className="page-tabs" role="group" aria-label="模型与套餐">{catalogPages.map(item => <button key={item} className={page === item ? 'active' : ''} aria-pressed={page === item} onClick={() => navigate(item)}>{titles[item]}</button>)}</div>}<DataPage toolbar={page === 'users' ? userToolbar : undefined} domain={page} state={data as LoadState<PageData>} permissions={auth.session.admin.permissions} query={query} offset={offset} onSearch={q => { setQuery(q); setOffset(0); }} onPage={setOffset} onInspect={row => void inspect(row)} onPublish={() => setMutation({ domain: page as Catalog })} onAction={row => setMutation({ domain: page as Catalog, row })} onRetry={() => setRevision(x => x + 1)} /></>}<footer className="page-footer"><span>优创后台</span><span>所有改动都会留下操作记录</span></footer></main></div>{detail && <Modal title={page === 'users' ? '用户详情' : `${titles[page]}详情`} onClose={closeDetail} wide>{detail.kind === 'ready' ? <div className="dialog-content">{page === 'users' ? <UserDetail client={client} data={detail.data} permissions={auth.session.admin.permissions} onManage={setManage} onFailure={fail} onAllowance={auth.session.admin.permissions.includes('owner:allowance') ? wallet => setAllowance(wallet) : undefined} /> : <DetailFields data={detail.data} />}<Truncation data={detail.data} /></div> : <Loading state={detail} />}</Modal>}{mutation && <PublishDialog client={client} mutation={mutation} onClose={() => setMutation(null)} onFailure={fail} onDone={() => { setMutation(null); setRevision(x => x + 1); setNotice('操作已确认，目录与审计记录已更新。'); }} />}{allowance && <AllowanceDialog client={client} wallet={allowance} onClose={() => setAllowance(null)} onFailure={fail} onDone={() => { setAllowance(null); closeDetail(); setRevision(x => x + 1); setNotice('已发放所有者额度：100 点，365 天。操作已记入审计。'); }} />}{manage && <ManageDialog client={client} action={manage} onClose={() => setManage(null)} onFailure={fail} onDone={message => { const user = manage.user; setManage(null); setNotice(message); setRevision(x => x + 1); void inspect(user); }} />}</div>;
}
function Truncation({ data }: { data: RecordData }) {
  const more = data.has_more;
  return more === true || more && typeof more === 'object' && Object.values(more).some(Boolean) ? <p className="notice">详情达到展示上限，部分记录未显示。请通过对应列表缩小查询范围。</p> : null;
}
function PublishDialog({ client, mutation, onClose, onFailure, onDone }: { client: ConsoleClient; mutation: Mutation; onClose: () => void; onFailure: (error: unknown) => string; onDone: () => void }) {
  const [fields, setFields] = useState<Fields>({ ...initialFields }), [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<LoadState<PageData>>({ kind: 'loading' });
  const [submission] = useState(() => new Submission());
  const active = useRef(true);
  useEffect(() => {
    active.current = true; const controller = new AbortController();
    if (mutation.domain === 'models' && !mutation.row) void client.read<PageData>('/api/providers?limit=100&offset=0', controller.signal).then(value => { if (!controller.signal.aborted) setProviders({ kind: 'ready', data: value }); }).catch(() => { if (!controller.signal.aborted) setProviders({ kind: 'error', message: '供应商目录不可用' }); });
    return () => { active.current = false; controller.abort(); };
  }, [client, mutation]);
  const title = mutation.row ? mutation.domain === 'offers' ? '撤回报价' : mutation.row.enabled ? '停用模型' : '启用模型' : `发布${titles[mutation.domain]}`;
  const submit = async () => {
    if (submission.busy || busy) return;
    setError('');
    try {
      const checkedReason = validReason(reason);
      let body: RecordData, path = `/api/${mutation.domain}`;
      if (mutation.row) {
        path += `/${encodeURIComponent(String(mutation.row.id))}/${mutation.domain === 'offers' ? 'withdraw' : 'state'}`;
        body = mutation.domain === 'offers' ? { reason: checkedReason } : { enabled: !mutation.row.enabled, reason: checkedReason, expected_updated_at: mutation.row.updated_at };
      } else {
        if (mutation.domain === 'models' && (providers.kind !== 'ready' || !providers.data.items.some(p => p.id === fields.provider_id))) throw new Error('请选择当前服务端允许的供应商');
        body = { definition: buildDefinition(mutation.domain, fields), reason: checkedReason };
      }
      setBusy(true);
      await submission.send(client, path, body, writePermissions[mutation.domain]);
      if (active.current) onDone();
    } catch (cause) { if (active.current) setError(onFailure(cause)); }
    finally { if (active.current) setBusy(false); }
  };
  return <Modal title={title} onClose={onClose} busy={busy} wide={!mutation.row}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">{mutation.row ? <><p>目标：<strong>{String(mutation.row.name ?? mutation.row.id)}</strong></p><p className="muted">{mutation.domain === 'offers' ? '撤回会停止该报价的新购买入口；已有订单和账本保持原记录。' : '状态更新使用当前版本时间戳，发生并发变更时会拒绝覆盖。'}</p></> : <DefinitionFields kind={mutation.domain} fields={fields} onChange={setFields} providers={providers} locked={submission.locked || busy} />}<label className="reason-field">操作原因<textarea required maxLength={255} rows={3} disabled={submission.locked || busy} value={reason} onChange={e => setReason(e.target.value)} placeholder="用单行文字说明这次目录变更的业务目的" /></label>{error && <p className="notice error" role="alert">{error}</p>}{submission.locked && error && <p className="caption">已保留本次操作的幂等键和原始内容。重试不会生成新操作。关闭后请先核对审计结果，避免重复发布。</p>}</div><div className="dialog-actions"><button className="button" type="button" disabled={busy} onClick={onClose}>关闭</button><button className="button primary" type="submit" disabled={busy || submission.conflict}>{busy ? '提交中…' : submission.conflict ? '请刷新后核对冲突' : submission.locked ? '重试同一操作' : `确认${title}`}</button></div></form></Modal>;
}
