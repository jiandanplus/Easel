import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatCredits, formatDate, RequestGate } from './core';
import type { ConsoleClient, LoadState, PageData, RecordData } from './core';
import { BarChart, creditNumber, dayLabel, ModelBars } from './charts';
import { Badge, DetailFields, Loading, UserSummary } from './views';

type Tab = 'overview' | 'usage' | 'ledger' | 'sessions' | 'audits' | 'raw';
const TABS: [Tab, string][] = [['overview', '概览'], ['usage', '调用记录'], ['ledger', '额度流水'], ['sessions', '登录记录'], ['audits', '后台操作'], ['raw', '全部字段']];
const operations: Record<string, string> = { execution: '文本', media: '媒体' };
const journalKinds: Record<string, string> = { issue: '发放额度', reserve: '预留（调用开始）', settle: '结算（调用完成）', release: '退回预留', expire: '额度到期' };
const planNames: Record<string, string> = { 'easel-owner-allowance-v1': '所有者额度', 'easel-internal-experience-v1': '内部体验额度' };
const usageStatuses: [string, string][] = [['', '全部结果'], ['settled', '成功'], ['released', '已退回'], ['pending_reconciliation', '结果不明'], ['dispatched', '进行中']];

function signedCredits(value: unknown): string {
  const text = formatCredits(value);
  return typeof value === 'string' && /^\d+$/.test(value) && value !== '0' ? '+' + text : text;
}

function useRead<T>(client: ConsoleClient, path: string | null, onFailure: (error: unknown) => string): [LoadState<T>, () => void] {
  const [state, setState] = useState<LoadState<T>>({ kind: 'loading' }), [revision, setRevision] = useState(0);
  const gate = useRef(new RequestGate());
  useEffect(() => {
    if (!path) return;
    const request = gate.current.begin(); setState({ kind: 'loading' });
    void client.read<T>(path, request.signal).then(value => {
      if (!request.current()) return;
      if (!value || typeof value !== 'object') throw new ConsoleError(502, 'invalid_response');
      setState({ kind: 'ready', data: value });
    }).catch(error => { if (request.current()) setState({ kind: 'error', message: onFailure(error) }); });
    return () => gate.current.cancel();
  }, [client, path, revision, onFailure]);
  return [state, () => setRevision(x => x + 1)];
}

function Pager({ offset, page, onPage }: { offset: number; page: PageData; onPage: (offset: number) => void }) {
  return <div className="pagination"><span>第 {Math.floor(offset / 25) + 1} 页 · 本页 {page.items.length} 条</span><div>
    <button className="button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - 25))}>上一页</button>
    <button className="button" disabled={!page.has_more} onClick={() => onPage(offset + 25)}>下一页</button></div></div>;
}

function Activity({ client, base, onFailure }: { client: ConsoleClient; base: string; onFailure: (error: unknown) => string }) {
  const [state, retry] = useRead<RecordData>(client, `${base}/activity?days=30`, onFailure);
  if (state.kind !== 'ready') return <Loading state={state} onRetry={retry} />;
  const daily = Array.isArray(state.data.daily) ? state.data.daily as RecordData[] : [];
  const points = daily.map(row => ({ label: dayLabel(row.day), value: creditNumber(row.spent), title: `${dayLabel(row.day)}：${formatCredits(row.spent)} 点，${String(row.calls)} 次` }));
  return <><h3>近 30 天消耗（点）</h3><BarChart label="近 30 天消耗" points={points} empty="近 30 天没有使用" />
    <h3>常用模型</h3><ModelBars rows={Array.isArray(state.data.models) ? state.data.models as RecordData[] : []} /></>;
}

function Usage({ client, base, onFailure }: { client: ConsoleClient; base: string; onFailure: (error: unknown) => string }) {
  const [status, setStatus] = useState(''), [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ limit: '25', offset: String(offset) }); if (status) params.set('status', status);
  const [state, retry] = useRead<PageData>(client, `${base}/usage?${params}`, onFailure);
  return <><div className="tab-toolbar"><label>结果 <select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}>{usageStatuses.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label></div>
    {state.kind !== 'ready' ? <Loading state={state} onRetry={retry} /> : state.data.items.length === 0 ? <div className="empty-state"><strong>没有调用记录</strong></div> : <>
      <div className="table-scroll"><table><thead><tr><th>时间</th><th>类型</th><th>模型</th><th>结果</th><th>预计（点）</th><th>实际（点）</th><th>点数</th></tr></thead><tbody>
        {state.data.items.map(row => <tr key={String(row.id)}><td>{formatDate(row.created_at)}</td><td>{operations[String(row.operation)] ?? String(row.operation)}</td><td>{String(row.model ?? '—')}</td><td><Badge value={row.status} /></td><td>{formatCredits(row.amount)}</td><td>{row.actual == null ? '—' : formatCredits(row.actual)}</td><td><Badge value={row.funding_mode} /></td></tr>)}
      </tbody></table></div><Pager offset={offset} page={state.data} onPage={setOffset} /></>}</>;
}

function Ledger({ client, base, onFailure }: { client: ConsoleClient; base: string; onFailure: (error: unknown) => string }) {
  const [offset, setOffset] = useState(0);
  const [state, retry] = useRead<PageData>(client, `${base}/ledger?limit=25&offset=${offset}`, onFailure);
  if (state.kind !== 'ready') return <Loading state={state} onRetry={retry} />;
  if (state.data.items.length === 0) return <div className="empty-state"><strong>还没有额度变动</strong></div>;
  return <><p className="caption">每一笔额度变化都在这里，记录不能修改。“可用变化”是这一笔让余额增加或减少的点数。</p>
    <div className="table-scroll"><table><thead><tr><th>时间</th><th>变动</th><th>说明</th><th>可用变化（点）</th><th>已用变化（点）</th></tr></thead><tbody>
      {state.data.items.map(row => <tr key={String(row.id)}><td>{formatDate(row.created_at)}</td><td>{journalKinds[String(row.kind)] ?? String(row.kind)}</td>
        <td>{row.plan_id ? planNames[String(row.plan_id)] ?? String(row.plan_id) : '—'}</td>
        <td className={String(row.available).startsWith('-') ? 'negative' : 'positive-text'}>{signedCredits(row.available)}</td><td>{row.spent === '0' ? '—' : formatCredits(row.spent)}</td></tr>)}
    </tbody></table></div><Pager offset={offset} page={state.data} onPage={setOffset} /></>;
}

function Sessions({ client, base, onFailure }: { client: ConsoleClient; base: string; onFailure: (error: unknown) => string }) {
  const [state, retry] = useRead<RecordData>(client, `${base}/sessions`, onFailure);
  if (state.kind !== 'ready') return <Loading state={state} onRetry={retry} />;
  const items = Array.isArray(state.data.items) ? state.data.items as RecordData[] : [];
  if (!items.length) return <div className="empty-state"><strong>还没有登录记录</strong></div>;
  return <ul className="plain-list">{items.map((row, i) => <li key={i}><span>{row.product_id === 'easel' ? '优创桌面版' : String(row.product_id)} · 登录于 {formatDate(row.created_at)}</span>
    <span>{row.active ? <span className="badge positive">有效</span> : row.revoked_at ? <span className="badge neutral">已退出</span> : <span className="badge neutral">已过期</span>}</span></li>)}</ul>;
}

function Audits({ client, base, onFailure }: { client: ConsoleClient; base: string; onFailure: (error: unknown) => string }) {
  const [state, retry] = useRead<RecordData>(client, `${base}/audits`, onFailure);
  if (state.kind !== 'ready') return <Loading state={state} onRetry={retry} />;
  const items = Array.isArray(state.data.items) ? state.data.items as RecordData[] : [];
  if (!items.length) return <div className="empty-state"><strong>后台还没有对这个用户做过操作</strong></div>;
  const actions: Record<string, string> = { 'allowance.issue': '发放所有者额度' };
  return <ul className="plain-list">{items.map(row => <li key={String(row.id)}><span>{formatDate(row.created_at)} · {actions[String(row.action)] ?? String(row.action)}</span><span className="muted">{String(row.reason ?? '')}</span></li>)}</ul>;
}

export function UserDetail({ client, data, onAllowance, onFailure }: { client: ConsoleClient; data: RecordData; onAllowance?: (wallet: RecordData) => void; onFailure: (error: unknown) => string }) {
  const [tab, setTab] = useState<Tab>('overview');
  const base = `/api/users/${encodeURIComponent(String(data.id))}`;
  return <div className="user-detail">
    <div className="page-tabs" role="tablist" aria-label="用户详情">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}</button>)}</div>
    <div role="tabpanel">
      {tab === 'overview' && <><UserSummary data={data} onAllowance={onAllowance} /><Activity client={client} base={base} onFailure={onFailure} /></>}
      {tab === 'usage' && <Usage client={client} base={base} onFailure={onFailure} />}
      {tab === 'ledger' && <Ledger client={client} base={base} onFailure={onFailure} />}
      {tab === 'sessions' && <Sessions client={client} base={base} onFailure={onFailure} />}
      {tab === 'audits' && <Audits client={client} base={base} onFailure={onFailure} />}
      {tab === 'raw' && <DetailFields data={data} />}
    </div>
  </div>;
}
