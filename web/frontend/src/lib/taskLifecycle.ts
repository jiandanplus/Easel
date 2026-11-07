export type TaskPhase = 'draft' | 'submitting' | 'confirming' | 'accepted' | 'running' | 'stopping' | 'stopped' | 'done' | 'failed' | 'disconnected' | 'unknown' | 'waiting';
export interface TaskLifecycleInput {
  phase: TaskPhase;
  cleanEnd?: boolean;
  stopReason?: string;
  financialState?: string;
  resultState?: string;
  confirmedNotSent?: boolean;
}
export interface TaskLifecycleState {
  phase: TaskPhase;
  label: string;
  financialLabel: string;
  resultLabel: string;
  canQuery: boolean;
  canReconnect: boolean;
  canStop: boolean;
  canSend: boolean;
  canRerun: boolean;
}
const LABELS: Record<TaskPhase, string> = {
  draft: '草稿', submitting: '正在提交', confirming: '正在确认', accepted: '已接受', running: '正在处理',
  stopping: '正在停止', stopped: '已停止', done: '已完成', failed: '未完成', disconnected: '连接中断', unknown: '待确认', waiting: '等待补充',
};

/** Execution permission is independent of financial and output availability. */
export function taskLifecycle(input: TaskLifecycleInput): TaskLifecycleState {
  let phase = input.phase;
  if (phase === 'done') {
    if (input.stopReason === 'stopped' || input.stopReason === 'user_stopped' || input.stopReason === 'cancelled') phase = 'stopped';
    else if (input.stopReason === 'error' || input.stopReason === 'timeout' || input.stopReason === 'spawn_failed') phase = 'failed';
    else if (input.cleanEnd !== true) phase = 'unknown';
  }
  const uncertain = ['submitting', 'confirming', 'disconnected', 'unknown'].includes(phase);
  const running = phase === 'running' || phase === 'accepted';
  const settled = input.financialState === 'settled';
  const pending = input.financialState === 'pending_reconciliation';
  return {
    phase,
    label: LABELS[phase] || '待确认',
    financialLabel: pending ? '费用待核对' : input.financialState === 'waived' ? '平台承担' : settled ? '已结算' : '',
    resultLabel: input.resultState === 'not_available' ? '结果不可用' : input.resultState === 'expired' ? '结果已过期' : '',
    canQuery: uncertain || running || phase === 'stopping' || pending,
    canReconnect: uncertain || running || phase === 'stopping',
    canStop: running,
    canSend: ['draft', 'done', 'stopped', 'failed', 'waiting'].includes(phase),
    canRerun: phase === 'failed' && input.confirmedNotSent === true,
  };
}

export interface AcceptedTaskReceipt {
  version: 1;
  session_id: string;
  turn_id: string;
  request_hash: string;
  receipt_id: string;
}
/** Only the durable, exact-turn server receipt acknowledges a draft snapshot. */
export function isAcceptedTaskReceipt(value: unknown, sessionId: string, turnId: string): value is AcceptedTaskReceipt {
  if (!value || typeof value !== 'object') return false;
  const receipt = value as Partial<AcceptedTaskReceipt>;
  return receipt.version === 1 && receipt.session_id === sessionId && receipt.turn_id === turnId
    && typeof receipt.request_hash === 'string' && /^[a-f0-9]{64}$/.test(receipt.request_hash)
    && typeof receipt.receipt_id === 'string' && /^[a-f0-9]{64}$/.test(receipt.receipt_id);
}
