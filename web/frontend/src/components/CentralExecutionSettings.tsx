import { useEffect, useState } from 'react';
import { centralModels, centralExecutions } from '../lib/api';
import type { CentralModel, CentralExecution } from '../lib/api';
import CentralExecutionRecovery from './CentralExecutionRecovery';

function points(value?: string | null) {
  if (value == null) return '未知';
  if (!/^\d+$/.test(value)) return '数据无效';
  const number = BigInt(value);
  const fraction = (number % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${number / 1000000n}${fraction ? `.${fraction}` : ''}`;
}
const statusLabels: Record<string, string> = { reserved: '已预留', dispatched: '执行中', pending_reconciliation: '待核算', settled: '已结算', released: '已释放', cancelled: '已取消', waived: '平台承担费用' };

export default function CentralExecutionSettings({ authenticated, revision, scopeKey }: { authenticated: boolean; revision: number; scopeKey: string }) {
  const [storedModels, setModels] = useState<CentralModel[] | null>(null);
  const [storedExecutions, setExecutions] = useState<CentralExecution[] | null>(null);
  const [storedModelError, setModelError] = useState('');
  const [storedUsageError, setUsageError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [readScope, setReadScope] = useState(scopeKey);
  // Hide old identity data on the very first render, before effect cleanup.
  const current = readScope === scopeKey;
  const models = current ? storedModels : null;
  const executions = current ? storedExecutions : null;
  const modelError = current ? storedModelError : '';
  const usageError = current ? storedUsageError : '';
  useEffect(() => {
    const controller = new AbortController();
    setReadScope(scopeKey);
    setModels(null); setExecutions(null); setModelError(''); setUsageError('');
    if (authenticated) {
      void centralModels(controller.signal).then(value => { if (!controller.signal.aborted) setModels(value.models); })
        .catch(error => { if (!controller.signal.aborted) setModelError(error instanceof Error ? error.message : '模型目录暂不可用'); });
      void centralExecutions(controller.signal).then(value => { if (!controller.signal.aborted) setExecutions(value.executions); })
        .catch(error => { if (!controller.signal.aborted) setUsageError(error instanceof Error ? error.message : '调用记录暂不可用'); });
    }
    return () => controller.abort();
  }, [authenticated, revision, refresh, scopeKey]);

  return <section className="central-account-section" aria-label="订阅模型与执行记录">
    <div className="central-account-header"><h4>订阅模型与执行记录</h4><button type="button" className="btn btn-sm" disabled={!authenticated} onClick={() => setRefresh(value => value + 1)}>刷新调用记录</button></div>
    {!authenticated ? <p>登录后查看</p> : <>
      {models === null && <p role={modelError ? 'alert' : 'status'}>{modelError || '正在读取模型目录…'}</p>}
      {models?.length === 0 && <p>暂无可用订阅模型</p>}
      {models?.map(model => <article className="central-account-card" key={model.id}><strong>{model.name || model.id}</strong>
        <p>{model.test_only ? '测试模型 · 使用测试点数' : '正式模型'} · {model.ready !== false && (model.readiness ? ['ready', 'test', 'live'].includes(model.readiness) : model.ready === true) ? '可用' : '暂不可执行'}</p>
        <p className="desc">模型 {model.id} · 费率 {model.rate.version}</p><p>输入 {model.rate.input.numerator}/{model.rate.input.denominator} · 输出 {model.rate.output.numerator}/{model.rate.output.denominator} 点／百万 tokens</p>
        {model.limits && <p className="desc">单次输出上限 {model.limits.max_output_tokens} tokens</p>}
      </article>)}
      <h4>当前账号调用记录</h4>
      {executions === null && <p role={usageError ? 'alert' : 'status'}>{usageError || '正在读取调用记录…'}</p>}
      {executions?.length === 0 && <p>暂无调用记录。</p>}
      {executions?.map(item => <article className="central-account-card" key={item.id}><strong>{item.model} · {statusLabels[item.status] || '状态未知'}</strong>
        <p>{item.funding_mode === 'test' ? '测试点数' : item.funding_mode === 'live' ? '正式点数' : '点数来源未知'} · 预算 {points(item.amount)} 点 · {item.status === 'waived' ? '用户不扣点' : `实际扣点${points(item.actual)}`}</p>
        {item.status === 'waived' && <p className="central-account-notice">供应商用量仍未知 · 用户不扣点</p>}
        <p>输入 {item.input_tokens ?? '未知'} · 输出 {item.output_tokens ?? '未知'} tokens</p><p className="desc">费率 {item.rate_version || '未知'} · 请求 {item.id}</p>
      </article>)}
    </>}
    <CentralExecutionRecovery authenticated={authenticated} scopeKey={scopeKey} />
  </section>;
}
