import { useEffect, useRef, useState } from 'react';
import { ConsoleError, formatCredits, formatDate, RequestGate, roundCredits } from './core';
import type { ConsoleClient, LoadState, RecordData } from './core';
import { BarChart, creditNumber, dayLabel, ModelBars } from './charts';
import { Loading } from './views';

const RANGES = [7, 30, 90];

export function StatsPage({ client, onFailure }: { client: ConsoleClient; onFailure: (error: unknown) => string }) {
  const [days, setDays] = useState(30), [revision, setRevision] = useState(0);
  const [state, setState] = useState<LoadState<RecordData>>({ kind: 'loading' });
  const gate = useRef(new RequestGate());
  useEffect(() => {
    const request = gate.current.begin(); setState({ kind: 'loading' });
    void client.read<RecordData>(`/api/stats?days=${days}`, request.signal).then(value => {
      if (!request.current()) return;
      if (!value || !Array.isArray(value.daily) || !Array.isArray(value.models)) throw new ConsoleError(502, 'invalid_response');
      setState({ kind: 'ready', data: value });
    }).catch(error => { if (request.current()) setState({ kind: 'error', message: onFailure(error) }); });
    return () => gate.current.cancel();
  }, [client, days, revision, onFailure]);
  const heading = <div className="page-heading"><div><h1>数据统计</h1><p>按天看新增用户、活跃账号、调用量和消耗。只统计正式点数，测试点数不计入。</p></div>
    <div className="segmented" role="group" aria-label="时间范围">{RANGES.map(d => <button key={d} className={d === days ? 'active' : ''} aria-pressed={d === days} onClick={() => setDays(d)}>近 {d} 天</button>)}</div></div>;
  if (state.kind !== 'ready') return <>{heading}<Loading state={state} onRetry={() => setRevision(x => x + 1)} /></>;
  const data = state.data, daily = data.daily as RecordData[], totals = (data.totals ?? {}) as RecordData;
  const sum = (key: string) => daily.reduce((n, row) => n + (Number(row[key]) || 0), 0);
  const spent = daily.reduce((n, row) => n + (typeof row.spent === 'string' && /^\d+$/.test(row.spent) ? BigInt(row.spent) : 0n), 0n).toString();
  const series = (key: string, unit: string, credit = false) => daily.map(row => {
    const value = credit ? creditNumber(row[key]) : Number(row[key]) || 0;
    return { label: dayLabel(row.day), value, title: `${dayLabel(row.day)}：${credit ? formatCredits(row[key]) : value} ${unit}` };
  });
  return <>{heading}
    <div className="stats-grid five">
      <div className="stat-card"><span>用户总数</span><strong>{String(totals.users ?? '—')}</strong></div>
      <div className="stat-card"><span>新增用户</span><strong>{sum('new_users')}</strong></div>
      <div className="stat-card"><span>活跃账号</span><strong>{String(totals.active_wallets ?? '—')}</strong></div>
      <div className="stat-card"><span>调用次数</span><strong>{sum('calls')}</strong></div>
      <div className="stat-card"><span>消耗（点）</span><strong>{roundCredits(spent)}</strong></div>
    </div>
    <div className="chart-grid">
      <section className="panel chart-panel"><h2>每天消耗（点）</h2><BarChart label="每天消耗" points={series('spent', '点', true)} /></section>
      <section className="panel chart-panel"><h2>每天调用次数</h2><BarChart label="每天调用次数" points={series('calls', '次')} /></section>
      <section className="panel chart-panel"><h2>每天活跃账号</h2><BarChart label="每天活跃账号" points={series('active_wallets', '个')} /></section>
      <section className="panel chart-panel"><h2>每天新增用户</h2><BarChart label="每天新增用户" points={series('new_users', '人')} /></section>
    </div>
    <section className="panel chart-panel"><h2>模型用量（前 10）</h2><ModelBars rows={data.models as RecordData[]} /></section>
    <p className="caption">数据截至 {formatDate(data.as_of)}，按北京时间分天。</p>
  </>;
}
