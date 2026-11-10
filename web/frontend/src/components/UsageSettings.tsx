import { useEffect, useMemo, useState } from 'react';
import { centralEntitlements, centralPlans, centralStatus, centralUsage, centralWallet, fetchSystem } from '../lib/api';
import type { CentralEntitlements, CentralPlan, CentralStatus, CentralUsage, CentralWallet, SystemInfo } from '../lib/api';
import PlatformAccountSettings from './PlatformAccountSettings';
import { formatBytes } from './SettingsPages';
import { pointsText } from './PlanSettings';

/** What kind of call a charge was for, from the model's capability (media) or the operation (text). */
export function kindOf(item: CentralUsage): string {
  if (item.operation === 'execution') return 'text';
  const capability = item.capability || '';
  if (capability.startsWith('image')) return 'image';
  if (capability.startsWith('video')) return 'video';
  if (capability.startsWith('music')) return 'music';
  if (capability === 'audio.transcribe') return 'transcribe';
  if (capability.startsWith('audio') || capability.startsWith('voice')) return 'voice';
  return 'media';
}
export const KINDS: Record<string, string> = { text: '对话', image: '生图', video: '视频', voice: '配音', music: '音乐', transcribe: '语音转写', media: '媒体任务' };
const TRIGGER: Record<string, string> = { text: '聊天消息', image: '生成图片', video: '生成视频', voice: '生成配音', music: '生成音乐', transcribe: '语音转文字', media: '媒体任务' };

/** Small charges need more precision than balances: up to four decimals, never a misleading 0. */
export function finePoints(micro: bigint): string {
  if (micro <= 0n) return '0';
  const scaled = micro / 100n;  // 1/10000 point
  if (scaled === 0n) return '<0.0001';
  const fraction = (scaled % 10000n).toString().padStart(4, '0').replace(/0+$/, '');
  return `${(scaled / 10000n).toLocaleString()}${fraction ? `.${fraction}` : ''}`;
}
/** Points charged: the settled amount, or the amount still held while a call runs. */
export function chargedMicro(item: CentralUsage): bigint {
  const value = item.status === 'waived' ? '0' : item.actual ?? (item.status === 'held' ? item.amount : null);
  return typeof value === 'string' && /^\d+$/.test(value) ? BigInt(value) : 0n;
}
export const toDate = (value: number | string) => new Date(typeof value === 'number' ? value * 1000 : value);
export function dayKey(date: Date): string { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }

/** Points spent per calendar day, oldest first, filling empty days so the chart keeps its scale. */
export function dailySpend(items: CentralUsage[], days: number | null, now = new Date()): { day: string; micro: bigint }[] {
  const totals = new Map<string, bigint>();
  for (const item of items) { const key = dayKey(toDate(item.created_at)); totals.set(key, (totals.get(key) ?? 0n) + chargedMicro(item)); }
  const keys = [...totals.keys()].sort();
  const start = days ? new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + 1) : keys.length ? new Date(`${keys[0]}T00:00:00`) : now;
  const result: { day: string; micro: bigint }[] = [];
  for (const d = new Date(start); d <= now; d.setDate(d.getDate() + 1)) { const key = dayKey(d); result.push({ day: key, micro: totals.get(key) ?? 0n }); }
  return result;
}
export function tokenTone(total: number): 'low' | 'mid' | 'high' { return total >= 100000 ? 'high' : total >= 10000 ? 'mid' : 'low'; }
export function untilText(target: number | string | undefined, now = Date.now()): string {
  if (target === undefined) return '';
  const ms = toDate(target).getTime() - now;
  if (!Number.isFinite(ms)) return '';
  if (ms <= 0) return '已到期';
  const days = Math.floor(ms / 86400000), hours = Math.floor((ms % 86400000) / 3600000);
  return days > 0 ? `${days} 天 ${hours} 小时后到期` : `${hours} 小时后到期`;
}

function Ring({ share }: { share: number }) {
  const r = 9, c = 2 * Math.PI * r;
  return <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><circle cx="13" cy="13" r={r} fill="none" stroke="var(--border)" strokeWidth="3" />
    <circle cx="13" cy="13" r={r} fill="none" stroke="var(--accent-end)" strokeWidth="3" strokeLinecap="round" strokeDasharray={`${c * Math.min(1, Math.max(0, share))} ${c}`} transform="rotate(-90 13 13)" /></svg>;
}

function Chart({ points }: { points: { day: string; micro: bigint }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = points.reduce((m, p) => (p.micro > m ? p.micro : m), 0n);
  if (max === 0n) return <div className="usage-empty">暂无消费记录</div>;
  const width = 100 / points.length;
  return <div className="usage-chart" onMouseLeave={() => setHover(null)}>
    <div className="usage-bars">{points.map((point, index) => <div key={point.day} className="usage-bar-slot" style={{ width: `${width}%` }} onMouseEnter={() => setHover(index)}>
      <div className={`usage-bar${hover === index ? ' on' : ''}`} style={{ height: `${point.micro === 0n ? 0 : Math.max(2, Number(point.micro * 1000n / max) / 10)}%` }} />
    </div>)}</div>
    <div className="usage-axis"><span>{points[0].day.slice(5)}</span><span>{points[points.length - 1].day.slice(5)}</span></div>
    {hover !== null && <div className="usage-tip" style={{ left: `${Math.min(88, Math.max(4, (hover + 0.5) * width))}%` }}><b>{finePoints(points[hover].micro)}</b> 点<span>{points[hover].day}</span></div>}
  </div>;
}

type Load = { status: CentralStatus | null; usage: CentralUsage[]; wallet: CentralWallet | null; entitlements: CentralEntitlements | null; plans: CentralPlan[]; system: SystemInfo | null; error: string; ready: boolean };

/** 用量：总览（额度、点数、存储）、使用情况图表和明细表，参考 LobeHub 的用量页。 */
export default function UsageSettings({ onOpenPlans }: { onOpenPlans?: () => void }) {
  const [data, setData] = useState<Load>({ status: null, usage: [], wallet: null, entitlements: null, plans: [], system: null, error: '', ready: false });
  const [range, setRange] = useState<'30' | 'all'>('30');
  const [query, setQuery] = useState(''), [kind, setKind] = useState(''), [from, setFrom] = useState(''), [to, setTo] = useState('');
  const [sort, setSort] = useState<{ key: 'time' | 'tokens'; desc: boolean }>({ key: 'time', desc: true });
  const [page, setPage] = useState(1), [size, setSize] = useState(10);
  useEffect(() => {
    const controller = new AbortController(), signal = controller.signal;
    const soft = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
    void (async () => {
      try {
        const status = await centralStatus(signal);
        const system = await soft(fetchSystem(), null);
        if (!status.authenticated) { if (!signal.aborted) setData(d => ({ ...d, status, system, ready: true })); return; }
        const [usage, wallet, entitlements, plans] = await Promise.all([soft(centralUsage(signal), { usage: [] }), soft(centralWallet(signal), null), soft(centralEntitlements(signal), null), soft(centralPlans(signal), { plans: [] })]);
        if (!signal.aborted) setData({ status, usage: usage.usage, wallet, entitlements, plans: plans.plans, system, error: '', ready: true });
      } catch (failure) { if (!signal.aborted) setData(d => ({ ...d, error: failure instanceof Error ? failure.message : '读取失败', ready: true })); }
    })();
    return () => controller.abort();
  }, []);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const start = from ? new Date(`${from}T00:00:00`).getTime() : -Infinity, end = to ? new Date(`${to}T23:59:59`).getTime() : Infinity;
    const list = data.usage.filter(item => {
      const at = toDate(item.created_at).getTime();
      return (!needle || (item.model || '').toLowerCase().includes(needle)) && (!kind || kindOf(item) === kind) && at >= start && at <= end;
    });
    const tokens = (item: CentralUsage) => (item.input_tokens ?? 0) + (item.output_tokens ?? 0);
    return list.sort((a, b) => { const diff = sort.key === 'time' ? toDate(a.created_at).getTime() - toDate(b.created_at).getTime() : tokens(a) - tokens(b); return sort.desc ? -diff : diff; });
  }, [data.usage, query, kind, from, to, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / size)), current = Math.min(page, pages);
  const visible = rows.slice((current - 1) * size, current * size);
  const chart = useMemo(() => dailySpend(data.usage, range === '30' ? 30 : null), [data.usage, range]);

  if (!data.ready) return <section className="st-sec active settings-page-body"><p role="status">正在读取用量…</p></section>;
  if (data.error) return <section className="st-sec active settings-page-body"><p role="alert" className="env-error">用量暂不可用：{data.error}</p></section>;
  if (!data.status?.authenticated) return <PlatformAccountSettings view="usage" />;

  const wallet = data.wallet;
  const big = (value?: string | null) => (typeof value === 'string' && /^\d+$/.test(value) ? BigInt(value) : 0n);
  const granted = wallet ? big(wallet.issued) - big(wallet.expired) - big(wallet.refunded) : 0n;
  const remaining = wallet && granted > 0n ? Number(big(wallet.available) * 1000n / granted) / 10 : 0;
  const memberships = data.entitlements?.memberships ?? [];
  const soonest = memberships.map(item => item.expires_at).sort((a, b) => toDate(a).getTime() - toDate(b).getTime())[0];
  const planNames = memberships.map(item => data.plans.find(plan => plan.id === item.plan_id)?.name || (item.funding_source === 'internal_experience' ? '内部体验额度' : item.plan_id));
  const storage = data.system?.storage ?? [];
  const sizeOf = (key: string) => storage.find(place => place.key === key);
  const totalBytes = storage.reduce((sum, place) => sum + place.bytes, 0) || 1;
  const sortButton = (key: 'time' | 'tokens') => <button type="button" className={`usage-sort${sort.key === key ? ' on' : ''}`} aria-label="排序" onClick={() => { setSort(s => ({ key, desc: s.key === key ? !s.desc : true })); setPage(1); }}>{sort.key === key ? (sort.desc ? '↓' : '↑') : '⇅'}</button>;
  const reset = () => { setQuery(''); setKind(''); setFrom(''); setTo(''); setPage(1); };

  return <section className="st-sec active usage-page" aria-label="用量">
    <div className="usage-block">
      <div className="usage-block-head"><h3>总览</h3><div className="usage-plan">当前套餐：<span>{planNames.length ? planNames.join('、') : '暂无'}</span>{onOpenPlans && <button type="button" className="usage-upgrade" onClick={onOpenPlans}>升级</button>}</div></div>
      <div className="settings-card usage-card">
        <div className="usage-section"><p className="usage-label">限额</p>
          <div className="usage-limit"><div className="usage-limit-row"><span>套餐额度</span><b>剩余 {wallet ? `${remaining}%` : '—'}</b></div>
            <div className="usage-progress"><span style={{ width: `${Math.min(100, remaining)}%` }} className={remaining < 10 ? 'low' : remaining < 30 ? 'mid' : ''} /></div>
            <p className="desc">{untilText(soonest) || '没有生效中的套餐'}</p></div></div>
        <div className="usage-section usage-two"><p className="usage-label">额度</p>
          <div><span>可用点数</span><strong>{pointsText(wallet?.available)}</strong></div>
          <div><span>累计已用</span><strong>{pointsText(wallet?.spent)}</strong></div></div>
        <div className="usage-section usage-two"><p className="usage-label">存储</p>
          {[['outputs', '内容库'], ['sessions', '对话记录']].map(([key, label]) => { const place = sizeOf(key); return <div key={key} className="usage-store"><Ring share={place ? place.bytes / totalBytes : 0} /><div><span>{label}</span><strong>{place ? `${formatBytes(place.bytes)} · ${place.files} 个文件` : '—'}</strong></div></div>; })}</div>
      </div>
    </div>

    <div className="usage-block">
      <div className="usage-block-head"><h3>使用情况</h3><div className="usage-range" role="tablist">{([['30', '过去 30 天'], ['all', '所有时间']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={range === value} className={range === value ? 'active' : ''} onClick={() => setRange(value)}>{label}</button>)}</div></div>
      <div className="settings-card usage-card"><Chart points={chart} /></div>
    </div>

    <div className="usage-block">
      <div className="usage-block-head"><h3>明细</h3></div>
      <div className="settings-card usage-card usage-table-card">
        <div className="usage-filters">
          <input className="usage-search" value={query} placeholder="搜索模型" aria-label="搜索模型" onChange={e => { setQuery(e.target.value); setPage(1); }} />
          <select value={kind} aria-label="类型" onChange={e => { setKind(e.target.value); setPage(1); }}><option value="">全部类型</option>{Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <div className="usage-dates"><input type="date" value={from} aria-label="开始日期" onChange={e => { setFrom(e.target.value); setPage(1); }} /><span>→</span><input type="date" value={to} aria-label="结束日期" onChange={e => { setTo(e.target.value); setPage(1); }} /></div>
          <button type="button" className="btn btn-sm" onClick={reset}>↺ 重置</button>
        </div>
        <div className="usage-table-scroll"><table className="usage-table">
          <thead><tr><th>时间 {sortButton('time')}</th><th>类型</th><th>触发方式</th><th>模型</th><th>Token 使用量 {sortButton('tokens')}</th><th>计费</th></tr></thead>
          <tbody>
            {visible.length === 0 && <tr><td colSpan={6} className="usage-empty">{data.usage.length ? '没有符合条件的记录' : '暂无消费记录'}</td></tr>}
            {visible.map(item => { const k = kindOf(item), at = toDate(item.created_at), tokens = (item.input_tokens ?? 0) + (item.output_tokens ?? 0), charged = chargedMicro(item); return <tr key={item.id}>
              <td className="usage-time">{at.getMonth() + 1} 月 {at.getDate()} {at.toTimeString().slice(0, 8)}</td>
              <td><span className={`usage-kind k-${k}`}>{KINDS[k]}</span></td>
              <td>{TRIGGER[k]}{item.status === 'held' && <span className="muted">（进行中）</span>}{item.status === 'waived' && <span className="muted">（平台承担）</span>}</td>
              <td className="usage-model">{item.model || <span className="muted">—</span>}</td>
              <td>{item.input_tokens != null || item.output_tokens != null ? <span className="usage-tokens"><span className={`tok ${tokenTone(tokens)}`}>{tokens.toLocaleString()}</span> = <span>↓ {(item.input_tokens ?? 0).toLocaleString()}</span> + <span>↑ {(item.output_tokens ?? 0).toLocaleString()}</span></span> : <span className="muted">--</span>}</td>
              <td>{charged > 0n ? `${finePoints(charged)} 点` : <span className="muted">--</span>}</td>
            </tr>; })}
          </tbody>
        </table></div>
        <div className="usage-pager"><span>第 {rows.length ? (current - 1) * size + 1 : 0}-{Math.min(current * size, rows.length)} 条，共 {rows.length} 条</span>
          <div><select value={size} aria-label="每页条数" onChange={e => { setSize(Number(e.target.value)); setPage(1); }}>{[10, 20, 50].map(n => <option key={n} value={n}>{n} 条/页</option>)}</select>
            <button type="button" disabled={current <= 1} onClick={() => setPage(current - 1)} aria-label="上一页">‹</button>
            {Array.from({ length: pages }, (_, i) => i + 1).filter(n => n === 1 || n === pages || Math.abs(n - current) <= 2).map(n => <button type="button" key={n} className={n === current ? 'on' : ''} onClick={() => setPage(n)}>{n}</button>)}
            <button type="button" disabled={current >= pages} onClick={() => setPage(current + 1)} aria-label="下一页">›</button></div></div>
      </div>
    </div>
  </section>;
}
