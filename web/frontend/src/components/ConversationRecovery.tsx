import { useEffect, useRef, useState } from "react";
import { centralAccountBoundaryVersion, centralAccountSignature, centralMediaTurnRecoveries, centralStatus, centralTurnRecoveries, subscribeCentralAccountBoundary } from "../lib/api";
import type { CentralExecutionRecoveries, CentralMediaRecoveries } from "../lib/api";
import "./ConversationRecovery.css";

const financialLabels: Record<string, string> = { reserved: "已预留", dispatched: "执行中", pending_reconciliation: "待核算", settled: "已结算", released: "已释放", waived: "平台承担费用" };
const resultLabels: Record<string, string> = { available: "结果可用", expired: "结果已过期", not_available: "结果暂不可用" };
function points(value?: string | null): string {
  if (value == null) return "未知";
  if (!/^\d+$/.test(value)) return "数据无效";
  const amount = BigInt(value), fraction = (amount % 1000000n).toString().padStart(6, "0").replace(/0+$/, "");
  return `${amount / 1000000n}${fraction ? `.${fraction}` : ""}`;
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
function mediaLabels(capability?: string): [string, string] {
  if (capability?.startsWith('image.')) return ['生图请求', '图片模型'];
  const labels: Record<string, [string, string]> = {
    'video.generate': ['视频请求', '视频模型'],
    'music.generate': ['音乐请求', '音乐模型'],
    'audio.transcribe': ['转写请求', '语音模型'],
    'audio.speech': ['配音请求', '语音模型'],
    'voice.synthesize': ['配音请求', '语音模型'],
    'voice.enroll': ['音色请求', '语音模型'],
  };
  return labels[capability || ''] || ['媒体请求', '媒体模型'];
}
interface RecoveryView { target: string; busy: boolean; error?: string; data?: CentralExecutionRecoveries; media?: CentralMediaRecoveries; mediaError?: string }

export default function ConversationRecovery({ sessionId, turnId }: { sessionId: string; turnId: string }) {
  const target = JSON.stringify([sessionId, turnId]);
  const targetRef = useRef(target);
  targetRef.current = target;
  const active = useRef<AbortController | null>(null);
  const [stored, setStored] = useState<RecoveryView>({ target, busy: false });
  const view = stored.target === target ? stored : { target, busy: false };

  useEffect(() => {
    setStored({ target, busy: false });
    const unsubscribe = subscribeCentralAccountBoundary(() => {
      active.current?.abort(); active.current = null;
      setStored(previous => ({ target, busy: false, ...(previous.target === target && (previous.busy || previous.data) ? { error: "账号状态已变化" } : {}) }));
    });
    return () => { unsubscribe(); active.current?.abort(); active.current = null; };
  }, [target]);

  const query = () => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const boundary = centralAccountBoundaryVersion();
    const current = () => !controller.signal.aborted && active.current === controller && targetRef.current === target && centralAccountBoundaryVersion() === boundary;
    setStored({ target, busy: true });
    const timeout = setTimeout(() => {
      if (current()) { controller.abort(); active.current = null; setStored({ target, busy: false, error: "查询超时" }); }
    }, 90000);
    void (async () => {
      try {
        const before = await centralStatus(controller.signal);
        if (!current()) return;
        const signature = centralAccountSignature(before);
        if (!signature) throw new Error("请先登录");
        const [textResult, mediaResult] = await Promise.allSettled([
          centralTurnRecoveries(sessionId, turnId, controller.signal),
          centralMediaTurnRecoveries(sessionId, turnId, controller.signal),
        ]);
        if (!current()) return;
        const after = await centralStatus(controller.signal);
        if (!current()) return;
        if (signature !== centralAccountSignature(after)) throw new Error("账号状态已变化");
        setStored({ target, busy: false,
          ...(textResult.status === 'fulfilled' ? { data: textResult.value } : { error: textResult.reason instanceof Error ? textResult.reason.message : '文本状态暂不可查询' }),
          ...(mediaResult.status === 'fulfilled' ? { media: mediaResult.value } : { mediaError: '媒体状态暂不可查询' }),
        });
      } catch (reason) {
        if (current()) setStored({ target, busy: false, error: reason instanceof Error ? reason.message : "暂不可查询" });
      } finally {
        clearTimeout(timeout);
        if (active.current === controller) active.current = null;
      }
    })();
  };

  return <section className="conversation-recovery" aria-label="本次请求状态">
    <button type="button" className="conversation-recovery-trigger" onClick={query} disabled={view.busy}>{view.busy ? "正在查询本次请求状态…" : "查看本次请求状态"}</button>
    {view.busy && <p role="status">正在查询…</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.mediaError && <p role="alert">{view.mediaError}</p>}
    {view.data && <div className="conversation-recovery-results">
      {view.data.truncated && <p role="status">显示前 50 笔</p>}
      {view.data.recoveries.length === 0 && <p>{view.media?.recoveries.length || view.mediaError ? '未找到文本记录' : '未找到关联记录'}</p>}
      {view.data.recoveries.map((item, index) => <article className="conversation-recovery-operation" key={`${item.invocation_id}:${item.operation_key}`}>
        <strong>文本请求 {index + 1} · 文本模型</strong>
        {item.lookup_status === "found" && item.execution ? <>
          <p>{financialLabels[item.execution.status] || "状态待确认"}</p>
          <p>{item.execution.funding_mode === "test" ? "测试点数" : "点数"} · {costLabel(item.execution.status, item.execution.amount, item.execution.actual)}</p>
          {item.execution.status === "pending_reconciliation" && item.execution.failure_code === "upstream_timeout" && <p>模型响应超时</p>}
          <p>{resultLabels[item.execution.result_state] || "结果待确认"}</p>
        </> : <>
          <p>{item.lookup_status === "not_found" ? "尚未查到记录" : "状态暂不可查询"}</p>
          <p>扣点待确认 · 结果暂不可用 · 请勿重复提交</p>
        </>}
      </article>)}
    </div>}
    {view.media && <div className="conversation-recovery-results">
      {view.media.truncated && <p role="status">媒体记录显示前 50 笔</p>}
      {view.media.recoveries.map(item => {
        const capability = item.capability || item.job?.capability;
        const [requestLabel, modelLabel] = mediaLabels(capability);
        return <article className="conversation-recovery-operation" key={`media:${item.operation_key}`}>
        <strong>{requestLabel} · {modelLabel}</strong>
        {item.lookup_status === 'found' && item.job ? <>
          <p>{financialLabels[item.job.financial_state] || '状态待确认'} · {item.job.funding_mode === 'test' ? '测试点数' : '点数'} · {costLabel(item.job.financial_state, item.job.amount, item.job.actual)}</p>
          <p>{(capability?.startsWith('image.')
            ? { available: '图片可用', expired: '图片已过期', unavailable: '暂无图片', processing: '生成中' }
            : { available: '结果可用', expired: '结果已过期', unavailable: '结果暂不可用', processing: '生成中' } as Record<string, string>)[item.job.delivery_state] || '结果待确认'}</p>
        </> : <p>{item.lookup_status === 'not_found' ? '尚未查到记录' : '媒体状态暂不可查询'} · 扣点待确认 · 请勿重复提交</p>}
      </article>;
      })}
    </div>}
  </section>;
}
