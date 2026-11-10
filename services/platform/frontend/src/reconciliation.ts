import { ConsoleClient, ConsoleError } from './core';

export const reviewStates = ['investigating', 'awaiting_evidence', 'disputed'] as const;
export type ReviewState = typeof reviewStates[number];
export type ReviewFilter = ReviewState | 'unreviewed' | 'all';
export type FinancialFilter = 'pending' | 'finished' | 'all';
export type FundingFilter = 'all' | 'test' | 'live';
export type ReconciliationFilters = { financial_status?: FinancialFilter; review_state?: ReviewFilter; funding_mode?: FundingFilter; product_id?: string; q?: string; offset?: number; limit?: number };
export type ReconciliationRow = { id: string; model: string; product_id: string; billing_account_id: string; status: string; amount: string; actual: string | null; funding_mode: string; rate_version: string | null; created_at: string | null; updated_at: string | null; review_state: ReviewState | 'unreviewed'; review_revision: number; resolution?: 'platform_absorbed'; customer_charge?: '0'; provider_usage_known?: false; rate?: { version: string; input: { numerator: string; denominator: string }; output: { numerator: string; denominator: string } } };
export type ReconciliationList = { items: ReconciliationRow[]; limit: number; offset: number; has_more: boolean };
export type Review = { state: ReviewState; revision: number; actor_id: string; reason: string; snapshot_hash: string; updated_at: string };
export type ReviewResult = { operation_id: string; job_id: string; review: Review };
export type ReconciliationDetail = { job: ReconciliationRow; review: Review | null; observed_at: string; snapshot_hash: string; usage: { present: boolean; input_tokens: string | null; output_tokens: string | null; diagnostic: string }; history: { items: { id: string; actor_id: string; reason: string; created_at: string; state: ReviewState; revision: number; snapshot_hash: string }[]; has_more: boolean } };
export type ReviewBody = { state: ReviewState; reason: string; expected_revision: number; snapshot_hash: string };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sha256 = /^[0-9a-f]{64}$/;
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const money = (value: unknown) => typeof value === 'string' && /^\d+$/.test(value);
const dateOrMissing = (value: unknown) => value === null || typeof value === 'string';
const isState = (value: unknown): value is ReviewState => (reviewStates as readonly unknown[]).includes(value);
const isReview = (value: unknown): value is Review => record(value) && isState(value.state) && integer(value.revision) && value.revision > 0 && typeof value.actor_id === 'string' && typeof value.reason === 'string' && typeof value.snapshot_hash === 'string' && sha256.test(value.snapshot_hash) && typeof value.updated_at === 'string';
function isRow(value: unknown): value is ReconciliationRow {
  return record(value) && typeof value.id === 'string' && uuid.test(value.id) && ['model', 'product_id', 'billing_account_id', 'status', 'funding_mode'].every(key => typeof value[key] === 'string') && money(value.amount) && (value.actual === null || money(value.actual)) && (value.rate_version === null || typeof value.rate_version === 'string') && dateOrMissing(value.created_at) && dateOrMissing(value.updated_at) && (value.review_state === 'unreviewed' || isState(value.review_state)) && integer(value.review_revision) && (value.status !== 'waived' || value.actual === null && value.customer_charge === '0' && value.resolution === 'platform_absorbed' && value.provider_usage_known === false);
}
export function reconciliationListPath(filters: ReconciliationFilters): string {
  const { financial_status = 'pending', review_state = 'all', funding_mode = 'all', product_id = '', q = '', offset = 0, limit = 25 } = filters;
  if (!['pending', 'finished', 'all'].includes(financial_status) || !['all', 'unreviewed', ...reviewStates].includes(review_state) || !['all', 'test', 'live'].includes(funding_mode) || !integer(offset) || offset > 100000 || !integer(limit) || limit < 1 || limit > 100 || q.length > 128 || product_id.length > 128 || /[\x00-\x1f\x7f]/.test(product_id)) throw new Error('筛选条件无效');
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), financial_status, review_state, funding_mode });
  if (product_id.trim()) params.set('product_id', product_id.trim());
  if (q.trim()) params.set('q', q.trim());
  return `/api/reconciliation-cases?${params}`;
}
export async function readReconciliationList(client: ConsoleClient, filters: ReconciliationFilters, signal?: AbortSignal): Promise<ReconciliationList> {
  const value = await client.read<unknown>(reconciliationListPath(filters), signal);
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(isRow) || !integer(value.limit) || value.limit < 1 || value.limit > 100 || !integer(value.offset) || typeof value.has_more !== 'boolean') throw new ConsoleError(502, 'invalid_response');
  return value as ReconciliationList;
}
export async function readReconciliationDetail(client: ConsoleClient, jobId: string, signal?: AbortSignal): Promise<ReconciliationDetail> {
  if (!uuid.test(jobId)) throw new ConsoleError(400, 'invalid_job');
  const value = await client.read<unknown>(`/api/reconciliation-cases/${jobId}`, signal);
  if (!record(value) || !isRow(value.job) || value.job.id !== jobId || !(value.review === null || isReview(value.review)) || typeof value.observed_at !== 'string' || typeof value.snapshot_hash !== 'string' || !sha256.test(value.snapshot_hash) || !record(value.usage) || typeof value.usage.present !== 'boolean' || ![value.usage.input_tokens, value.usage.output_tokens].every(token => token === null || money(token)) || !['central_usage_missing', 'settled_usage_present', 'settled_usage_incomplete', 'usage_without_settlement', 'waived_usage_unknown'].includes(String(value.usage.diagnostic)) || value.job.status === 'waived' && (value.usage.present || value.usage.input_tokens !== null || value.usage.output_tokens !== null || value.usage.diagnostic !== 'waived_usage_unknown') || !record(value.history) || !Array.isArray(value.history.items) || value.history.items.length > 50 || !value.history.items.every(item => record(item) && typeof item.id === 'string' && typeof item.created_at === 'string' && isReview({ ...item, updated_at: item.created_at })) || typeof value.history.has_more !== 'boolean') throw new ConsoleError(502, 'invalid_response');
  return value as ReconciliationDetail;
}
export function buildReviewBody(state: ReviewState, reason: string, detail: ReconciliationDetail): ReviewBody {
  if (!isState(state) || !reason.trim() || Array.from(reason).length > 500 || /[\x00-\x1f\x7f]/.test(reason)) throw new Error('请填写 1–500 字符的单行处理说明，不包含控制字符');
  const expected_revision = detail.review?.revision ?? 0;
  if (!integer(expected_revision) || !sha256.test(detail.snapshot_hash) || detail.job.status !== 'pending_reconciliation') throw new Error('详情已变化，请重新读取后核查');
  return { state, reason: reason.trim(), expected_revision, snapshot_hash: detail.snapshot_hash };
}
export class ReviewSubmission {
  status: 'idle' | 'submitting' | 'unknown' | 'confirmed' | 'conflict' | 'rejected' = 'idle';
  state: ReviewState = 'investigating';
  reason = '';
  busy = false;
  result?: ReviewResult;
  body?: Readonly<ReviewBody>;
  private operation?: ReturnType<ConsoleClient['reviewOperation']>;
  private readonly sessionRevision: number;
  constructor(private readonly client: ConsoleClient, readonly jobId: string) { this.sessionRevision = client.sessionRevision; }
  private checkSession() {
    if (this.client.sessionRevision !== this.sessionRevision || !this.client.session) throw new ConsoleError(401, 'session_changed');
    if (!this.client.session.admin.permissions.includes('execution:review')) throw new ConsoleError(403, 'permission_denied');
  }
  private confirmed(value: unknown): ReviewResult {
    if (!record(value) || typeof value.operation_id !== 'string' || !value.operation_id || value.job_id !== this.jobId || !isReview(value.review) || !this.body || value.review.state !== this.body.state || value.review.reason !== this.body.reason || value.review.snapshot_hash !== this.body.snapshot_hash || value.review.revision !== this.body.expected_revision + 1 || value.review.actor_id !== this.client.session?.admin.id) throw new ConsoleError(502, 'invalid_operation_result');
    return value as ReviewResult;
  }
  async send(body: ReviewBody) {
    this.checkSession();
    if (this.status !== 'idle' || this.busy) throw new ConsoleError(409, 'operation_locked');
    this.body = Object.freeze({ state: body.state, reason: body.reason, expected_revision: body.expected_revision, snapshot_hash: body.snapshot_hash });
    this.state = body.state; this.reason = body.reason;
    this.operation = this.client.reviewOperation(this.jobId, this.body);
    this.status = 'submitting'; this.busy = true;
    try { this.result = this.confirmed(await this.operation.send()); this.status = 'confirmed'; return this.result; }
    catch (error) { this.status = error instanceof ConsoleError && error.status === 409 ? 'conflict' : error instanceof ConsoleError && error.status > 0 && error.status < 500 ? 'rejected' : 'unknown'; throw error; }
    finally { this.busy = false; }
  }
  async lookup() {
    this.checkSession();
    if (!this.operation || this.status !== 'unknown' || this.busy) throw new ConsoleError(409, 'operation_locked');
    this.busy = true;
    try {
      const value = await this.operation.lookup();
      if (!record(value) || typeof value.found !== 'boolean' || value.found === false && value.result !== null) throw new ConsoleError(502, 'invalid_operation_result');
      if (!value.found) return null;
      this.result = this.confirmed(value.result); this.status = 'confirmed'; return this.result;
    } finally { this.busy = false; }
  }
}
export class ReviewRegistry {
  private sessions = new Map<number, Map<string, ReviewSubmission>>();
  get(client: ConsoleClient, jobId: string) {
    let jobs = this.sessions.get(client.sessionRevision);
    if (!jobs) { jobs = new Map(); this.sessions.set(client.sessionRevision, jobs); }
    let submission = jobs.get(jobId);
    if (!submission) { submission = new ReviewSubmission(client, jobId); jobs.set(jobId, submission); }
    return submission;
  }
  startAfterRefresh(client: ConsoleClient, jobId: string) {
    const previous = this.get(client, jobId);
    if (previous.busy || ['submitting', 'unknown'].includes(previous.status)) throw new Error('原操作结果仍未知，只能查询原操作');
    const next = new ReviewSubmission(client, jobId); next.reason = previous.reason; next.state = previous.state;
    this.sessions.get(client.sessionRevision)!.set(jobId, next); return next;
  }
}
