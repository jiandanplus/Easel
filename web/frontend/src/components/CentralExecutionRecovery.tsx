import { useEffect, useState } from 'react';
import { centralExecutionRecoveries } from '../lib/api';
import type { CentralExecutionRecoveries } from '../lib/api';

const financialLabels: Record<string, string> = { reserved: '已预留', dispatched: '执行中', pending_reconciliation: '待核算', settled: '已结算', released: '已释放', waived: '平台承担费用' };
const resultLabels: Record<string, string> = { available: '结果可用', expired: '结果已过期', not_available: '结果暂不可用' };
function points(value?: string | null) {
  if (value == null) return '未知';
  if (!/^\d+$/.test(value)) return '数据无效';
  const amount = BigInt(value), fraction = (amount % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${amount / 1000000n}${fraction ? `.${fraction}` : ''}`;
}

function costLabel(status: string, amount: string | null | undefined, actual?: string | null): string {
  const budget = ['reserved', 'dispatched', 'pending_reconciliation'].includes(status)
    ? `预留 ${points(amount)} 点`
    : ['released', 'waived'].includes(status) ? `已释放 ${points(amount)} 点`
      : status === 'settled' ? '' : `预算 ${points(amount)} 点`;
  const charge = status === 'waived'
    ? `客户扣点 0 点 · ${actual == null ? '服务费用待确认' : `服务费用 ${points(actual)} 点`}`
    : actual == null ? '扣点待确认' : `已扣 ${points(actual)} 点`;
  return [budget, charge].filter(Boolean).join(' · ');
}

export default function CentralExecutionRecovery({ authenticated, scopeKey }: { authenticated: boolean; scopeKey: string }) {
  const [requestedScope, setRequestedScope] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [readScope, setReadScope] = useState(scopeKey);
  const [stored, setStored] = useState<CentralExecutionRecoveries | null>(null);
  const [storedError, setError] = useState('');
  const [storedBusy, setBusy] = useState(false);
  const current = readScope === scopeKey && authenticated;
  const data = current ? stored : null, error = current ? storedError : '', busy = current && storedBusy;
  useEffect(() => {
    const controller = new AbortController();
    setReadScope(scopeKey); setStored(null); setError(''); setBusy(false);
    if (!authenticated || requestedScope !== scopeKey) {
      setRequestedScope(null);
      return () => controller.abort();
    }
    if (refresh > 0) {
      setBusy(true);
      void centralExecutionRecoveries(controller.signal).then(value => {
        if (!controller.signal.aborted) { setStored(value); setBusy(false); }
      }).catch(reason => {
        if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : '请求状态暂不可用'); setBusy(false); }
      });
    }
    return () => controller.abort();
  }, [authenticated, scopeKey, requestedScope, refresh]);

  return <section className="central-account-section" aria-label="中断请求状态">
    <div className="central-account-header"><h4>中断请求状态</h4>
      <button type="button" className="btn btn-sm" disabled={!authenticated || busy} onClick={() => { setRequestedScope(scopeKey); setRefresh(value => value + 1); }}>{busy ? '正在查询…' : '查询中断请求'}</button>
    </div>
    {!authenticated && <p>登录后查询</p>}
    {busy && <p role="status">正在查询…</p>}
    {error && <p role="alert">{error}</p>}
    {data?.truncated && <p className="central-account-notice">显示最近 50 条记录</p>}
    {data?.recoveries.length === 0 && <p>暂无可查询记录</p>}
    {data?.recoveries.map(item => <article className="central-account-card" key={`${item.invocation_id}:${item.operation_key}`}>
      <strong>文本请求 · 文本模型</strong>
      {item.lookup_status === 'found' && item.execution ? <>
        <p>{financialLabels[item.execution.status] || '状态待确认'}</p>
        <p>{item.execution.funding_mode === 'test' ? '测试点数' : '点数'} · {costLabel(item.execution.status, item.execution.amount, item.execution.actual)}</p>
        {item.execution.status === 'pending_reconciliation' && item.execution.failure_code === 'upstream_timeout' && <p>模型响应超时</p>}
        <p className="desc">{resultLabels[item.execution.result_state] || '结果待确认'}</p>
      </> : <>
        <p>{item.lookup_status === 'not_found' ? '尚未查到记录' : '状态暂不可查询'}</p>
        <p className="desc">扣点待确认 · 结果暂不可用 · 请勿重复提交</p>
      </>}
    </article>)}
  </section>;
}
