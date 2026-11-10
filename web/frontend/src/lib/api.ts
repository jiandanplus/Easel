import { verifiedArtifact } from './deliverables';
import type { VerifiedArtifact } from './deliverables';
import type { UpdateStatus } from './update';

function getBasePath(): string {
  const path = window.location.pathname;
  const cleaned = path.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return cleaned;
}

const BASE = getBasePath();

export type MediaCapability = 'image' | 'video' | 'music' | 'voice';
export type MediaGatewayMode = 'custom' | 'new-api';
export interface MediaGatewayValues {
  baseUrl: string;
  modes: Record<MediaCapability, MediaGatewayMode>;
  image: { model: string };
  video: { model: string; profile: string };
  music: { provider: string; model: string };
  voice: { provider: string; model: string; voiceId: string };
  geminiTtsApi: string;
}
export interface MediaGatewaySettings extends MediaGatewayValues {
  keyConfigured: boolean;
  keyMasked: string;
  configured: Record<MediaCapability, boolean>;
  managed: Record<MediaCapability, boolean>;
}
export interface MediaGatewayPayload extends MediaGatewayValues { key?: string; clearKey?: boolean; adopt?: MediaCapability[] }
export const fetchMediaGateway = (): Promise<MediaGatewaySettings> => request('/api/settings/media-gateway');
export const saveMediaGateway = (value: MediaGatewayPayload): Promise<MediaGatewaySettings & { ok: boolean }> => request('/api/settings/media-gateway', {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});

export function apiErrorMessage(value: unknown): string {
  if (!value || typeof value !== 'object') return '';
  const body = value as { detail?: unknown; message?: unknown };
  const detail = body.detail ?? body.message;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
  return '';
}

export interface PlatformStatus {
  configured: boolean; url: string; authenticated: boolean;
  account: { id: string; email: string; email_verified: boolean } | null;
  session_id?: string; expires_at?: number; refresh_expires_at?: number;
  error?: { code: string; message: string };
}
export interface PlatformPlan { id: string; name: string; tokens: number; days: number; concurrency: number; models: string[]; test_only: boolean }
export interface PlatformGrant { id: string; plan_id: string; status: string; starts_at: number; ends_at: number; remaining_tokens: number; used_tokens: number; reserved_tokens: number; models: string[] }
export interface PlatformUsage { used_tokens: number; reserved_tokens: number; pending_reconciliation: number }
export interface PlatformDevice { id: string; device_name: string; created_at: number; refresh_expires_at: number; revoked: boolean }
const platformPost = <T,>(path: string, body?: unknown): Promise<T> => request(`/api/platform/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
export const platformStatus = (): Promise<PlatformStatus> => request('/api/platform/status');
export const platformConfigure = (url: string): Promise<PlatformStatus> => platformPost('configure', { url });
export const platformAuthenticate = (mode: 'login' | 'register', body: { email: string; password: string; device_name: string }): Promise<PlatformStatus> => platformPost(mode, body);
export const platformLogout = (): Promise<PlatformStatus> => platformPost('logout');
export const platformPlans = (): Promise<{ plans: PlatformPlan[] }> => request('/api/platform/plans');
export const platformEntitlements = (): Promise<{ entitlements: PlatformGrant[] }> => request('/api/platform/entitlements');
export const platformUsage = (): Promise<PlatformUsage> => request('/api/platform/usage');
export const platformDevices = (): Promise<{ sessions: PlatformDevice[] }> => request('/api/platform/devices');
export const platformRevoke = (id: string): Promise<{ ok: boolean }> => request(`/api/platform/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const platformModels = (): Promise<{ models: { id: string }[]; ready: boolean; reason: string }> => request('/api/platform/models');

export interface CentralScope {
  user_id: string; product_id: string; tenant_id: string; billing_account_id: string; session_id: string; authorization_epoch: number;
}
export interface CentralStatus {
  configured: boolean; url: string; authenticated: boolean;
  account: { id: string; email: string | null; email_verified: boolean; username?: string | null } | null;
  scope: CentralScope | null;
  error?: { code: string; message: string };
  login: { pending: boolean; expires_at?: number; error?: { code: string; message: string } };
  capabilities?: { auth_mode: 'logto'; ledger_mode: 'central_credit'; billing_stage: 'test' | 'live' | 'disabled'; payments_enabled: boolean; subscription_execution_enabled: boolean; test_execution_enabled?: boolean; execution_stage?: 'disabled' | 'test' | 'live' };
}
export interface CentralPlan {
  id: string; name: string; version: string; included_products: string[]; features: string[];
  credits: string; days: number; concurrency: number; test_only: boolean;
}
export interface CentralWallet {
  unit: 'microcredit'; microcredits_per_point: '1000000'; available: string; reserved: string;
  spent: string; expired: string; issued: string; pending_reconciliation: number; test_only: boolean;
  active_requests?: number; pending_reconciliation_limit?: number;
  frozen?: string; refunded?: string; funding_mode?: 'empty' | 'test' | 'live' | 'mixed'; test_available?: string; live_available?: string;
  experience_available?: string; experience_issued?: string; experience_expires_at?: string | null;
}
export interface CentralEntitlements {
  active: boolean; product_id: string; test_only: boolean; features: string[]; concurrency: number;
  experience_active?: boolean;
  memberships: { id: string; plan_id: string; expires_at: number | string; funding_source?: 'internal_experience' | 'test' | 'live' }[];
}
export interface CentralUsage {
  id: string; operation: string; product_id: string; amount: string; actual: string | null; status: string;
  rate_version: string; created_at: number | string; updated_at: number | string;
}
export interface CentralDevice { id: string; client_id: string; product_id: string; expires_at: string; current: boolean }
const centralPost = <T,>(path: string): Promise<T> => request(`/api/platform/central/${path}`, { method: 'POST' });
// In-memory only: financial reads must never outlive their authenticated account.
let centralBoundaryVersion = 0;
let observedCentralSignature: string | null | undefined;
const centralBoundaryListeners = new Set<() => void>();
export const centralAccountBoundaryVersion = () => centralBoundaryVersion;
export function subscribeCentralAccountBoundary(listener: () => void): () => void {
  centralBoundaryListeners.add(listener);
  return () => { centralBoundaryListeners.delete(listener); };
}
function invalidateCentralAccountBoundary() {
  centralBoundaryVersion += 1;
  for (const listener of centralBoundaryListeners) listener();
}
export function centralAccountSignature(status: CentralStatus): string | null {
  if (!status.authenticated || !status.account?.id || !status.scope || !status.url) return null;
  const scope = status.scope;
  if (![scope.user_id, scope.product_id, scope.tenant_id, scope.billing_account_id, scope.session_id].every(value => typeof value === 'string' && value.length > 0) || !Number.isSafeInteger(scope.authorization_epoch)) return null;
  return JSON.stringify([status.url.trim().replace(/\/+$/, ''), status.account.id, scope.user_id, scope.product_id, scope.tenant_id, scope.billing_account_id, scope.session_id, scope.authorization_epoch]);
}
export async function centralStatus(signal?: AbortSignal): Promise<CentralStatus> {
  const version = centralBoundaryVersion;
  const status = await request<CentralStatus>('/api/platform/central/status', { signal });
  if (version === centralBoundaryVersion && !signal?.aborted) {
    const signature = centralAccountSignature(status);
    const changed = observedCentralSignature !== undefined && observedCentralSignature !== signature;
    observedCentralSignature = signature;
    if (changed) invalidateCentralAccountBoundary();
  }
  return status;
}
async function centralAccountMutation<T>(perform: () => Promise<T>): Promise<T> {
  invalidateCentralAccountBoundary();
  try { return await perform(); }
  finally { invalidateCentralAccountBoundary(); }
}
export const centralStartLogin = (): Promise<{ authorization_url: string; expires_at: number }> => centralAccountMutation(() => centralPost('auth/start'));
export const centralCancelLogin = (): Promise<CentralStatus> => centralAccountMutation(() => centralPost('auth/cancel'));
export const centralLogout = (): Promise<CentralStatus> => centralAccountMutation(() => centralPost('logout'));
export const centralPlans = (signal?: AbortSignal): Promise<{ plans: CentralPlan[] }> => request('/api/platform/central/plans', { signal });
export const centralWallet = (signal?: AbortSignal): Promise<CentralWallet> => request('/api/platform/central/wallet', { signal });
export const centralEntitlements = (signal?: AbortSignal): Promise<CentralEntitlements> => request('/api/platform/central/entitlements', { signal });
export const centralUsage = (signal?: AbortSignal): Promise<{ usage: CentralUsage[] }> => request('/api/platform/central/usage', { signal });
export const centralDevices = (signal?: AbortSignal): Promise<{ sessions: CentralDevice[] }> => request('/api/platform/central/devices', { signal });
export const centralRevoke = (id: string): Promise<{ ok: boolean }> => centralAccountMutation(() => request(`/api/platform/central/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }));

export interface CentralModel {
  id: string; name?: string; capability: 'text'; test_only: boolean; readiness?: string; ready?: boolean;
  rate: { version: string; input: { numerator: string; denominator: string }; output: { numerator: string; denominator: string } };
  limits?: { max_input_bytes: number; max_input_tokens: number; max_output_tokens: number };
}
export type CentralExecutionFailureCode = 'upstream_timeout' | 'upstream_unavailable' | 'upstream_error' | 'invalid_upstream_usage' | 'request_cancelled';
export interface CentralExecution {
  id: string; model: string; status: string; amount?: string; actual?: string | null;
  input_tokens?: number | null; output_tokens?: number | null; rate_version?: string;
  funding_mode?: 'test' | 'live'; created_at?: string;
  failure_code?: CentralExecutionFailureCode;
  resolution?: 'platform_absorbed'; customer_charge?: '0'; provider_usage_known?: false;
}
export const centralModels = (signal?: AbortSignal): Promise<{ models: CentralModel[]; ready?: boolean; reason?: string }> => request('/api/platform/central/models', { signal });
export interface CentralMediaModel {
  id: string; name: string; version: string; capability: string; readiness: string; test_only: boolean;
  rate: { version: string; meters: { unit: string; basis: string; numerator: string; denominator: string }[] };
  limits: { max_input_bytes: number; max_assets: number; max_artifact_bytes: number; max_units: Record<string, number> };
  parameters: Record<string, unknown>;
}
export interface CentralMediaJob {
  id: string; model: string; capability: string; execution_state: string; delivery_state: string;
  status: string; financial_state: string; amount: string; actual: string | null; funding_mode: string;
  artifacts: { id: string; sha256: string; size: number; mime: string; expires_at?: string; purged_at?: string | null }[];
}
export interface CentralMediaRecoveries {
  recoveries: { operation_key: string; model: string; capability: string; local_status: string; lookup_status: string; job?: CentralMediaJob }[];
  truncated: boolean;
}
export const centralMediaModels = (signal?: AbortSignal): Promise<{ models: CentralMediaModel[] }> => request('/api/platform/central/media-models', { signal });
export const centralMediaRecoveries = (signal?: AbortSignal): Promise<CentralMediaRecoveries> => request('/api/platform/central/media-recoveries', { signal });
export function centralMediaTurnRecoveries(sessionId: string, turnId: string, signal?: AbortSignal): Promise<CentralMediaRecoveries> {
  if (![sessionId, turnId].every(value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value))) return Promise.reject(new Error('会话或轮次标识无效'));
  return request(`/api/platform/central/media-recoveries/turn?session_id=${encodeURIComponent(sessionId)}&turn_id=${encodeURIComponent(turnId)}`, { signal });
}
export const centralExecutions = (signal?: AbortSignal): Promise<{ executions: CentralExecution[] }> => request('/api/platform/central/executions', { signal });
export interface CentralExecutionRecoveryItem {
  operation_key: string; invocation_id: string; model: string; local_status: string;
  lookup_status: 'found' | 'not_found' | 'unavailable';
  execution?: CentralExecution & { result_state: 'available' | 'expired' | 'not_available' };
}
export interface CentralExecutionRecoveries { recoveries: CentralExecutionRecoveryItem[]; truncated: boolean }
export const centralExecutionRecoveries = (signal?: AbortSignal): Promise<CentralExecutionRecoveries> => request('/api/platform/central/execution-recoveries', { signal });
export function centralTurnRecoveries(sessionId: string, turnId: string, signal?: AbortSignal): Promise<CentralExecutionRecoveries> {
  if (![sessionId, turnId].every(value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value))) return Promise.reject(new Error('会话或轮次标识无效'));
  return request(`/api/platform/central/execution-recoveries/turn?session_id=${encodeURIComponent(sessionId)}&turn_id=${encodeURIComponent(turnId)}`, { signal });
}

export interface CentralOffer extends Omit<CentralPlan, 'id'> {
  id: string; plan_id: string; currency: 'CNY'; amount_minor: string;
  terms: { refund_policy: 'unused_full_only'; description: string };
}
export interface CentralBillingCatalog { mode: 'disabled' | 'test' | 'live'; channels: { id: 'wechat' | 'alipay'; label: string; enabled: boolean }[]; offers: CentralOffer[] }
export interface CentralOrder {
  /** Monotonic decimal revision covering order and refund changes. */
  revision: string;
  id: string; status: string; channel: 'wechat' | 'alipay'; mode: 'test' | 'live'; currency: 'CNY'; amount_minor: string; offer: CentralOffer;
  created_at: string; expires_at: string; paid_at: string | null; credit_state: string;
  checkout: { qr_code: string; qr_data_url?: string | null; expires_at: string } | null;
  refund: { id: string; status: string; amount_minor?: string; reason?: string } | null;
}
export interface CentralCreateOrder { offer_id: string; channel: 'wechat' | 'alipay'; idempotency_key: string }
export const centralBillingCatalog = (signal?: AbortSignal): Promise<CentralBillingCatalog> => request('/api/platform/central/billing/catalog', { signal });
export const centralOrders = (signal?: AbortSignal): Promise<{ orders: CentralOrder[] }> => request('/api/platform/central/orders', { signal });
export const centralOrder = (id: string, signal?: AbortSignal): Promise<CentralOrder> => request(`/api/platform/central/orders/${encodeURIComponent(id)}`, { signal });
export interface CentralRedemption { plan_id: string; name?: string | null; credits: string; expires_at: string; redeemed_at: string; replayed: boolean }
export const centralRedeem = (code: string, signal?: AbortSignal): Promise<CentralRedemption> => request('/api/platform/central/redemptions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }), signal });
export const centralCreateOrder = (body: CentralCreateOrder, signal?: AbortSignal): Promise<CentralOrder> => request('/api/platform/central/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
export const centralRefreshOrder = (id: string, signal?: AbortSignal): Promise<CentralOrder> => request(`/api/platform/central/orders/${encodeURIComponent(id)}/refresh`, { method: 'POST', signal });
export const centralCloseOrder = (id: string, signal?: AbortSignal): Promise<CentralOrder> => request(`/api/platform/central/orders/${encodeURIComponent(id)}/close`, { method: 'POST', signal });

export const updateStatus = (signal?: AbortSignal): Promise<UpdateStatus> => request('/api/update/status', { signal });
export const checkUpdate = (): Promise<UpdateStatus> => request('/api/update/check', { method: 'POST' });
export const downloadUpdate = (): Promise<UpdateStatus> => request('/api/update/download', { method: 'POST' });
export const applyUpdate = (): Promise<{ state: string; version: number }> => request('/api/update/apply', { method: 'POST' });

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  // 全部是动态应用接口（登录态/数据等），禁止浏览器 HTTP 缓存——否则 /api/accounts 等可能被启发式
  // 缓存住旧响应（曾表现为“扫码登录后卡片仍显示未登录”）。调用方可用 options.cache 覆盖。
  const res = await fetch(`${BASE}${url}`, { cache: 'no-store', ...options });
  if (!res.ok) {
    // 优先显示后端返回的实质错误信息（FastAPI 的 {detail}），而不是无意义的 "API error: 400"
    let detail = '';
    let code = '';
    try {
      const j = await res.json();
      detail = apiErrorMessage(j);
      if (j?.detail && typeof j.detail === 'object' && typeof j.detail.code === 'string') code = j.detail.code;
    } catch {
      /* 响应体不是 JSON，忽略 */
    }
    throw Object.assign(new Error(detail || `请求失败（${res.status} ${res.statusText}）`), { code });
  }
  return res.json() as Promise<T>;
}

export interface StatusResponse {
  gateway: boolean;
  transport?: string;
  runtime?: string;
  runtime_detail?: string;
  skills: SkillItem[];
  personas: PersonaItem[];
  workbench?: WorkbenchContract;
}

export interface WorkbenchContract {
  version: number; build_id: string; workspace_scope: string; source_scope: string;
}
export interface ChatAcceptedReceipt {
  version: 1; session_id: string; turn_id: string; request_hash: string; receipt_id: string;
}
export interface ChatCompletion {
  version: 1; session_id: string; turn_id: string; state: 'completed' | 'stopped' | 'failed' | 'unknown';
  clean_end?: boolean; stop_reason?: string;
}
export interface ChatRejectedReceipt { version: 1; session_id: string; turn_id: string; request_hash: string; not_dispatched: true }
export interface ChatSubmissionError extends Error { status?: number; rejection?: ChatRejectedReceipt; completion?: ChatCompletion }
async function chatRequestHash(body: Record<string, unknown>): Promise<string | undefined> {
  if (typeof crypto === 'undefined' || !crypto.subtle) return;
  const normalized: Record<string, unknown> = {};
  for (const field of ['attachments', 'catalog_revision', 'execution_target', 'message', 'model', 'persona', 'reasoning_effort', 'service_source', 'sessionId', 'turnId']) {
    normalized[field] = field === 'attachments' ? ((body.attachments || []) as UploadedFile[]).map(item => ({ id: item.id, name: item.name, path: item.path })) : body[field] ?? null;
  }
  const canonical = (value: unknown): string => {
    if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
    if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(',')}}`;
    return JSON.stringify(value);
  };
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(normalized)));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}
export interface ChatTurnStatus extends Partial<ChatAcceptedReceipt> {
  version: 1; session_id: string; turn_id: string;
  state: 'unknown' | 'accepted' | 'running' | 'completed' | 'stopped' | 'failed' | 'rejected';
  accepted: boolean; text?: string; clean_end?: boolean; stop_reason?: string; last_event_id?: number;
  not_dispatched?: boolean;
}
const validTurnIdentifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value);
function acceptedReceipt(value: unknown, sessionId: string, turnId?: string): ChatAcceptedReceipt | undefined {
  if (!value || typeof value !== 'object') return;
  const row = value as Partial<ChatAcceptedReceipt>;
  if (row.version === 1 && row.session_id === sessionId && row.turn_id === turnId
    && typeof row.request_hash === 'string' && /^[a-f0-9]{64}$/.test(row.request_hash)
    && typeof row.receipt_id === 'string' && /^[a-f0-9]{64}$/.test(row.receipt_id))
    return { version: 1, session_id: sessionId, turn_id: turnId!, request_hash: row.request_hash, receipt_id: row.receipt_id };
}
export async function fetchChatTurn(sessionId: string, turnId: string, signal?: AbortSignal): Promise<ChatTurnStatus> {
  if (!validTurnIdentifier(sessionId) || !validTurnIdentifier(turnId)) throw new Error('请求编号无效');
  const value = await request<ChatTurnStatus>(`/api/chat/turns/${encodeURIComponent(sessionId)}/${encodeURIComponent(turnId)}`, { cache: 'no-store', signal });
  return checkedChatTurn(value, sessionId, turnId);
}
function checkedChatTurn(value: ChatTurnStatus, sessionId: string, turnId: string): ChatTurnStatus {
  if (!value || value.version !== 1 || value.session_id !== sessionId || value.turn_id !== turnId
    || !['unknown', 'accepted', 'running', 'completed', 'stopped', 'failed', 'rejected'].includes(value.state)
    || typeof value.accepted !== 'boolean' || (value.accepted && !acceptedReceipt(value, sessionId, turnId))) throw new Error('请求状态暂不可确认');
  if (value.state === 'rejected' && (value.accepted || value.not_dispatched !== true || !acceptedReceipt(value, sessionId, turnId))) throw new Error('请求状态暂不可确认');
  return value;
}
export async function withdrawChatTurn(sessionId: string, turnId: string, requestHash: string): Promise<ChatTurnStatus> {
  if (!validTurnIdentifier(sessionId) || !validTurnIdentifier(turnId) || !/^[a-f0-9]{64}$/.test(requestHash)) throw new Error('请求编号无效');
  const value = await request<ChatTurnStatus>(`/api/chat/turns/${encodeURIComponent(sessionId)}/${encodeURIComponent(turnId)}/withdraw`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_hash: requestHash }), cache: 'no-store',
  });
  return checkedChatTurn(value, sessionId, turnId);
}
export const validateDraftAttachments = (sessionId: string, attachments: UploadedFile[], signal?: AbortSignal): Promise<{ version: 1; session_id: string; attachments: (UploadedFile & { state: 'available' | 'missing' | 'forbidden' })[] }> =>
  request('/api/workbench/attachments/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId, attachments }), signal });
export const fetchArtifactReference = (path: string, sessionId?: string): Promise<UploadedFile & { size: number; kind: string; state: 'available' }> =>
  request(`/api/artifacts/reference?path=${encodeURIComponent(path)}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''}`, { cache: 'no-store' });

export type ServiceSource = 'custom' | 'subscription';
export type SubscriptionBackend = 'central' | 'legacy';
export type ExecutionTarget = 'local' | 'cloud';
export interface ServiceRoute { service_source: ServiceSource; execution_target: ExecutionTarget; subscription_backend?: SubscriptionBackend }
export interface ChatTurnSelection { model: string; catalog_revision: string; reasoning_effort?: string }
export interface ChatModelOption {
  id: string; label: string; default: boolean;
  reasoning: { status: 'supported' | 'unsupported' | 'unknown'; efforts: string[] };
}
export interface ChatModelCatalog extends Partial<ServiceRoute> {
  revision: string; source_revision: string; expires_in: number; runtime: string; selected_model: string; switchable: boolean; ready: boolean; reason: string;
  models: ChatModelOption[];
}
export const fetchChatModels = (signal?: AbortSignal, sessionId?: string, refresh = false): Promise<ChatModelCatalog> => {
  const query = [sessionId ? `session_id=${encodeURIComponent(sessionId)}` : '', refresh ? 'refresh=true' : ''].filter(Boolean).join('&');
  return request(`/api/chat/models${query ? `?${query}` : ''}`, { signal });
};
export interface TaskRoute extends ServiceRoute { runtime: string; model: string; model_resolution: 'explicit' | 'native-default' | 'gateway-agent-default'; chat: boolean; tools: boolean }

export interface HarnessSettingsResponse extends ServiceRoute {
  availability: (ServiceRoute & { available: boolean; reason: string })[];
  runtime: string;
  models: Record<string, string>;
  subscription_model?: string;
  sandbox: string;
  codex_auto_approve: boolean;
  locked: boolean;
  agents: { id: string; label: string; protocol: string; installed: boolean; install_hint: string; origin?: 'bundled' | 'override' | 'external'; version?: string; resolution_error?: string; subscription_backends?: SubscriptionBackend[] }[];
}

export function fetchHarnessSettings(): Promise<HarnessSettingsResponse> {
  return request('/api/settings/harness');
}

export interface BundledProviderState {
  configured: boolean; base_url: string; model: string; key_present: boolean;
}
export function fetchBundledProvider(): Promise<BundledProviderState> {
  return request('/api/settings/harness/provider');
}
export function saveBundledProvider(value: { base_url: string; model: string; api_key: string;
  secret_action: 'preserve' | 'replace' | 'clear' }): Promise<BundledProviderState> {
  return request('/api/settings/harness/provider', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
}

export interface HarnessModel {
  id: string;
  label: string;
  default: boolean;
}

export function fetchHarnessModels(runtime: string): Promise<{ runtime: string; models: HarnessModel[] }> {
  return request(`/api/settings/harness/models/${encodeURIComponent(runtime)}`, { method: 'POST' });
}

export function saveHarnessSettings(runtime: string, model: string, sandbox: string, codexAutoApprove?: boolean, route?: Partial<ServiceRoute> & { subscription_model?: string }): Promise<HarnessSettingsResponse> {
  return request('/api/settings/harness', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runtime, model, sandbox, codex_auto_approve: codexAutoApprove, ...route }),
  });
}

export interface PersonaItem {
  name: string;
  description: string;
}

export interface PersonaDetail {
  name: string;
  content: string;
}

export interface SkillItem {
  name: string;
  description: string;
  layer: string;
  needsApi: boolean;
  apiConfigured: boolean;
}

export interface ApiKeySpec {
  env: string;
  label: string;
  required: boolean;
  secret: boolean;
  configured: boolean;
  masked: string;   // 脱敏值或非 secret 明文
  choices: string[];
}

export interface ApiProviderSpec {
  id: string;
  name: string;
  keys: ApiKeySpec[];
}

export interface ApiSpec {
  label: string;
  settings: ApiKeySpec[];
  providers: ApiProviderSpec[];
}

export interface SkillDetail {
  name: string;
  layer: string;
  description: string;
  body: string;
  needsApi: boolean;
  apiConfigured: boolean;
  apiSpec: ApiSpec | null;
}

export type FileKind = 'text' | 'image' | 'video' | 'audio' | 'binary';

/** 顶层项目目录的 .easel.json 展示头（供内容库富展示）。 */
export interface OutputMeta {
  title?: string;
  summary?: string;
  platform?: string;
  kind?: string;             // article|xhs-note|video|cards|poster|audio|other
  status?: string;           // draft|ready|published
  tags?: string[];
  cover?: string;            // outputs 下相对路径（后端已解析存在性）
  deliverables?: string[];   // 成品文件名（项目根相对）
  deliverablePaths?: string[];  // 成品的 outputs 相对路径（后端已解析存在性）
}

/** 产物树节点：文件或目录（目录带 children，可无限嵌套点开）。 */
export interface OutputNode {
  name: string;
  type: 'dir' | 'file';
  path: string;              // outputs 下的相对路径，如 "short-drama/监控诡影/episodes/ep01"
  mtime?: number;
  kind?: FileKind;           // 仅 file
  size?: number;             // 仅 file
  children?: OutputNode[];   // 仅 dir
  fileCount?: number;        // 仅 dir：递归文件数
  meta?: OutputMeta;         // 仅顶层项目 dir
}

/** @deprecated 用 OutputNode（type==='file'）。保留别名减少改动面。 */
export type OutputFile = OutputNode;

export interface OutputContent {
  path: string;
  content: string;
  kind: FileKind;
  isBinary: boolean;
}

export interface SkillResponse {
  response: string;
}

export interface ProfileBuildResponse {
  created: boolean;
  name: string;
  async?: boolean;
  status?: string;
  log?: string;
}

export interface ProfileBuildStatus {
  state: 'running' | 'done' | 'failed' | 'unknown';
  log: string;
}

export function fetchStatus(): Promise<StatusResponse> {
  return request<StatusResponse>('/api/status');
}

export function fetchPersonas(): Promise<PersonaItem[]> {
  return request<PersonaItem[]>('/api/personas');
}

export function fetchPersonaDetail(name: string): Promise<PersonaDetail> {
  return request<PersonaDetail>(`/api/persona/${encodeURIComponent(name)}`);
}

export interface PersonaFile {
  filename: string;
  content: string;
}

export function fetchPersonaFiles(name: string): Promise<{ name: string; files: PersonaFile[] }> {
  return request(`/api/persona/${encodeURIComponent(name)}/files`);
}

export function savePersonaFile(name: string, filename: string, content: string): Promise<{ ok: boolean }> {
  return request(`/api/persona/${encodeURIComponent(name)}/file`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, content }),
  });
}

export function deletePersona(name: string): Promise<{ ok: boolean; deleted: string }> {
  return request(`/api/persona/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

// ---- 热点雷达 ----
export interface TrendItem { title: string; hot: string; url: string; }
export interface TrendGroup { platform: string; label: string; items: TrendItem[]; ok?: boolean; }
export function fetchTrends(platforms: string, limit = 12): Promise<{ trends: TrendGroup[]; updated: number }> {
  return request(`/api/trends?platforms=${encodeURIComponent(platforms)}&limit=${limit}`);
}

// ---- 内容排期 ----
export interface ScheduleItem {
  id: string; title: string; date: string; platform: string;
  time: string; status: string; note: string;
  kind?: string;          // content（内容/发布）| event（平台活动/节日/特殊日期）
  url?: string;           // 已发布链接
  source?: string;        // manual | publish-page | chat | scheduler
  event_type?: string;    // event 专属：节日/电商/平台活动/行业
  end_date?: string;      // event 专属：活动区间结束日
}
export type ScheduleInput = Omit<ScheduleItem, 'id'>;
export interface ScheduleContext {
  today?: string; window_days?: number; published_recent?: number;
  per_platform?: { platform: string; recent_count: number; last_date: string | null; days_since_last: number | null }[];
  upcoming_schedules?: ScheduleItem[];
  upcoming_events?: ScheduleItem[];
  suggestions?: string[];
}
export function fetchSchedule(): Promise<ScheduleItem[]> {
  return request('/api/schedule');
}
export function fetchScheduleContext(days = 14): Promise<ScheduleContext> {
  return request(`/api/schedule/context?days=${days}`);
}
export function createSchedule(item: ScheduleInput): Promise<ScheduleItem> {
  return request('/api/schedule', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
  });
}
export function updateSchedule(id: string, item: ScheduleInput): Promise<ScheduleItem> {
  return request(`/api/schedule/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
  });
}
export function deleteSchedule(id: string): Promise<{ ok: boolean }> {
  return request(`/api/schedule/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// ---- 选题库 ----
export interface Idea {
  id: string; title: string; note: string; source: string; status: string; created: number;
}
export type IdeaInput = { title: string; note?: string; source?: string; status?: string };
export function fetchIdeas(): Promise<Idea[]> { return request('/api/ideas'); }
export function createIdea(item: IdeaInput): Promise<Idea> {
  return request('/api/ideas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item) });
}
export function updateIdea(id: string, item: IdeaInput): Promise<Idea> {
  return request(`/api/ideas/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item) });
}
export function deleteIdea(id: string): Promise<{ ok: boolean }> {
  return request(`/api/ideas/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function fetchSkills(): Promise<SkillItem[]> {
  return request<SkillItem[]>('/api/skills');
}

export function fetchSkillDetail(name: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/api/skill/${encodeURIComponent(name)}`);
}

/** 保存 API key 到项目根 .env（仅注册表内变量）。返回各需 API 的 skill 是否已就绪。 */
export function saveEnv(updates: Record<string, string>): Promise<{ ok: boolean; skills: Record<string, boolean> }> {
  return request('/api/env', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  });
}

export function executeSkill(skill: string, input: string, persona?: string): Promise<SkillResponse> {
  return request<SkillResponse>('/api/skill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skill, input, persona: persona || undefined }),
  });
}

/** 一次性 agent 任务（返回整段 markdown）——用于一稿多改 / 爆款拆解 / 发布预检等工具型调用。 */
export function runAgent(message: string, persona?: string): Promise<{ response: string }> {
  return request('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, persona: persona || undefined }),
  });
}

/** 从表单构建画像（首次引导）。后端写基线 + agent 分析社媒链接增强。 */
export function buildProfile(name: string, form: Record<string, unknown>): Promise<ProfileBuildResponse> {
  return request<ProfileBuildResponse>('/api/profile/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, form }),
  });
}

/** 轮询画像 AI 增强进度（构建异步化后用）。 */
export function profileBuildStatus(name: string): Promise<ProfileBuildStatus> {
  return request<ProfileBuildStatus>(`/api/profile/build/status/${encodeURIComponent(name)}`);
}

export function fetchOutputs(): Promise<OutputNode[]> {
  return request<OutputNode[]>('/api/outputs');
}

export function fetchArtifactArchive(path: string, signal?: AbortSignal): Promise<{ paths: string[] }> {
  return request<{ paths: string[] }>(`/api/artifacts/archive?path=${encodeURIComponent(path)}`, { signal });
}

export function fetchOutputContent(path: string): Promise<OutputContent> {
  return request<OutputContent>(`/api/output/${path.split('/').map(encodeURIComponent).join('/')}`);
}

/** 媒体文件（图片/视频/音频/HTML/PDF）的原样 URL，用于 <img>/<video>/iframe/下载。 */
export function mediaUrl(path: string): string {
  return `${BASE}/api/media/${path.split('/').map(encodeURIComponent).join('/')}`;
}

/** 删除内容库里的文件或整个项目目录（系统数据受保护，后端会拒）。 */
export function deleteOutput(path: string): Promise<{ ok: boolean; deleted: string }> {
  return request(`/api/output/${path.split('/').map(encodeURIComponent).join('/')}`, { method: 'DELETE' });
}

export interface UploadedFile { id: string; name: string; path: string; }

/** OpenClaw ask_user 问答题（SSE question 事件 payload）。 */
export interface ChatQuestionOption { label: string; description?: string; }
export interface ChatQuestionItem {
  questionId: string;
  header?: string;
  question: string;
  options?: ChatQuestionOption[];
  multiSelect?: boolean;
}
export interface ChatQuestion {
  id: string;              // gateway question record id (ask_...)
  questions: ChatQuestionItem[];
  expiresAtMs?: number;
}
export async function answerQuestion(
  payload: { questionId: string; answers: Record<string, string[]>; resolvedBy?: string },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${BASE}/api/chat/question/answer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({})) as { ok?: boolean; error?: string };
  return { ok: data.ok === true, error: data.error };
}

/** 批量查 question 状态：过滤重放中已解决/已过期的旧题。 */
export async function questionStatus(
  questionIds: string[],
): Promise<Record<string, { status: string }>> {
  if (!questionIds.length) return {};
  try {
    const res = await fetch(`${BASE}/api/chat/question/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questionIds }),
    });
    const data = await res.json().catch(() => ({})) as { ok?: boolean; questions?: Record<string, { status: string }> };
    return data.questions || {};
  } catch {
    return {};
  }
}
/** 上传素材到当前会话的隔离 inbox，返回后端可校验的附件引用。 */
export async function uploadFiles(files: File[], sessionId: string): Promise<UploadedFile[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('sessionId', sessionId);
  const r = await request<{ ok: boolean; files: UploadedFile[] }>('/api/upload', { method: 'POST', body: fd });
  return r.files;
}

/** 超限文件复制通道：返回与 uploadFiles 同构的附件引用。 */
export interface AdoptedFile { id: string; name: string; path: string; }
export async function adoptOversize(files: File[], sessionId: string): Promise<AdoptedFile[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('sessionId', sessionId);
  const r = await request<{ ok: boolean; files: AdoptedFile[] }>('/api/upload/local', { method: 'POST', body: fd });
  return r.files;
}

export function deleteSession(sessionKey: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/session/${encodeURIComponent(sessionKey)}`, {
    method: 'DELETE',
  });
}

// ---- 账号登录 ----
export interface AccountItem {
  platform: string;
  name: string;
  backend: string;      // xhs | web | biliup | unsupported
  supported: boolean;
  loggedIn: boolean;
  note: string;
}

export interface LoginStart {
  mode: 'qr' | 'terminal' | 'credentials';   // credentials = 凭证式（公众号 AppID/AppSecret）
  state?: string;       // starting | qr_ready | success | expired | error | unknown
  message?: string;
  qr?: string;          // outputs 下相对路径，用 mediaUrl() 取图
  qrTs?: number;
  configured?: boolean; // 凭证式：是否已配置
}

// ---- 凭证式账号（微信公众号 AppID/AppSecret）----
export interface CredentialStatus {
  configured: boolean;
  appIdMasked: string;
  name: string;
  author: string;
}

/** 读取凭证式平台（公众号）已配置状态（AppSecret 不回传）。 */
export function getCredentials(platform: string): Promise<CredentialStatus> {
  return request<CredentialStatus>(`/api/accounts/${encodeURIComponent(platform)}/credentials`);
}

/** 保存公众号 AppID/AppSecret（后端会调官方接口验证连通性）。 */
export function saveCredentials(
  platform: string,
  payload: { appId: string; appSecret: string; name?: string; author?: string },
): Promise<{ ok: boolean; message: string }> {
  return request(`/api/accounts/${encodeURIComponent(platform)}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** 启动「公众号后台」扫码登录（数据中心取数用，独立于 AppID 凭证）。返回二维码状态。 */
export function startMpLogin(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/accounts/${encodeURIComponent(platform)}/mp-login`, { method: 'POST' });
}

/** 轮询公众号后台扫码登录状态。 */
export function mpLoginStatus(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/accounts/${encodeURIComponent(platform)}/mp-login/status`,
    { signal: AbortSignal.timeout(8000) });
}

export interface LoginStatus {
  mode: 'qr';
  state: string;
  message: string;
  qr: string;
  qrTs?: number;
}

export function fetchAccounts(): Promise<AccountItem[]> {
  return request<AccountItem[]>('/api/accounts');
}

export interface AccountWhoami {
  loggedIn: boolean;
  name: string;
  avatar: string;   // 头像 URL（http）或空
}

/** 真校验某平台登录态 + 拉昵称/头像（后端起 headless 浏览器，数秒）。 */
export function accountWhoami(platform: string, force = false): Promise<AccountWhoami> {
  return request<AccountWhoami>(`/api/accounts/${encodeURIComponent(platform)}/whoami${force ? '?force=true' : ''}`);
}

/** 退出登录：删该平台持久化登录态。 */
export function logoutAccount(platform: string): Promise<{ ok: boolean; deleted: string[] }> {
  return request(`/api/logout/${encodeURIComponent(platform)}`, { method: 'POST' });
}

export interface PublishResult {
  ok?: boolean;
  message: string;
  detail?: string;
  async?: boolean;   // true = 异步发布（抖音，可能触发短信验证），需轮询 publishStatus
  pending?: boolean;
}

/** 一键发布到某平台（真发布，--exec）。media 为 outputs 相对路径数组。
 * 抖音返回 {async:true}，需轮询 publishStatus；其他平台同步返回结果。 */
export function publishNow(
  platform: string,
  payload: { title: string; body: string; media: string[]; tags?: string },
): Promise<PublishResult> {
  return request<PublishResult>(`/api/publish/${encodeURIComponent(platform)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export interface PublishStatus {
  mode: 'publish';
  state: string;     // starting | sms_required | verifying | success | error | unknown
  message: string;
}

/** 轮询异步发布状态（抖音）。 */
export function publishStatus(platform: string): Promise<PublishStatus> {
  return request<PublishStatus>(`/api/publish/${encodeURIComponent(platform)}/status`);
}

/** 发布触发短信墙时回填验证码。 */
export function submitPublishSms(platform: string, code: string): Promise<{ ok: boolean }> {
  return request(`/api/publish/${encodeURIComponent(platform)}/sms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

export function startLogin(platform: string): Promise<LoginStart> {
  return request<LoginStart>(`/api/login/${encodeURIComponent(platform)}?restart=true`, { method: 'POST' });
}

export function loginStatus(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/login/${encodeURIComponent(platform)}/status`,
    { signal: AbortSignal.timeout(8000) });
}

// ---- 归因层：账号创作数据 ----
export interface AnalyticsPlatform {
  platform: string;
  name: string;
  loggedIn: boolean;
}

export interface AccountAnalytics {
  platform: string;
  name: string;
  nickname: string;
  loggedIn: boolean;
  followers: number | null;
  likes: number | null;
  following: number | null;
  posts: number | null;
  metrics: { label: string; value: string | number; vs: string }[];
  notes: { title: string; url: string; cover?: string; stat?: string }[];
  growth: Record<'last' | 'day' | 'week' | 'month' | 'year',
    { followers: number | null; likes: number | null; posts: number | null; since_days: number | null } | null>;
  fetched_at: number;
  warning?: string;
}

/** 支持抓数据的平台 + 各自登录态。 */
export function fetchAnalyticsPlatforms(): Promise<AnalyticsPlatform[]> {
  return request<AnalyticsPlatform[]>('/api/analytics/platforms');
}

/** 抓某平台已登录账号的创作数据，客户端等待时限略长于后端。 */
export function fetchAccountAnalytics(platform: string): Promise<AccountAnalytics> {
  return request<AccountAnalytics>(`/api/analytics/${encodeURIComponent(platform)}`,
    { signal: AbortSignal.timeout(platform === 'wechat-oa' ? 195000 : 105000) });
}

/** 回填短信验证码（登录风控短信墙）：提交后 runner 读走填码继续登录。 */
export function submitLoginSms(platform: string, code: string): Promise<{ ok: boolean }> {
  return request(`/api/login/${encodeURIComponent(platform)}/sms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

/** 用户显式停止：终止后端正在跑的对话 agent，释放会话锁，令下一句能立刻发。 */
export function stopChat(sessionId: string, turnId?: string): Promise<{ stopped: boolean; state?: 'stopping' | 'stopped' | 'completed' | 'unknown' }> {
  return request('/api/chat/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, ...(turnId ? { turnId } : {}) }),
  });
}

export function streamChat(
  message: string,
  persona: string | undefined,
  sessionId: string,
  onToken: (chunk: string) => void,
  onDone: (sessionKey?: string, completion?: ChatCompletion) => void,
  onError: (err: Error) => void,
  onThinking?: (chunk: string) => void,
  onActivity?: (status: string) => void,
  onInterrupted?: () => void,
  turnId?: string,
  resumeOnly = false,
  onRecoveryUnavailable?: () => void,
  attachments: UploadedFile[] = [],
  onQuestion?: (q: ChatQuestion) => void,
  onHeartbeat?: (note: string) => void,
  route?: Partial<ServiceRoute> & Partial<ChatTurnSelection>,
  onCentralRecovery?: (hint: { version: 1; turn_id: string }) => void,
  onAccepted?: (receipt: ChatAcceptedReceipt) => void,
  onRequestPrepared?: (binding: { session_id: string; turn_id: string; request_hash: string }) => void,
  onArtifact?: (artifact: VerifiedArtifact) => void,
): AbortController {
  const controller = new AbortController();
  let lastEventId = 0;
  const requestBody = { message, persona: persona || undefined, sessionId, turnId, attachments, ...route };
  let requestHash: string | undefined;
  let acceptedSeen = false;
  let terminalError: ChatSubmissionError | undefined;
  const artifacts = new Map<string, VerifiedArtifact>();

  const consume = async (res: Response, initialRequest: boolean): Promise<boolean> => {
      if (!res.ok) {
        let detail = `Stream error: ${res.status}`;
        let rejection: ChatRejectedReceipt | undefined;
        try {
          const payload: unknown = await res.json();
          detail = apiErrorMessage(payload) || detail;
          const marker = (payload as { rejection?: Partial<ChatRejectedReceipt> } | null)?.rejection;
          if (initialRequest && requestHash && marker?.version === 1 && marker.session_id === sessionId && marker.turn_id === turnId
            && marker.not_dispatched === true && marker.request_hash === requestHash)
            rejection = { version: 1, session_id: sessionId, turn_id: turnId!, request_hash: requestHash, not_dispatched: true };
        } catch { /* non-JSON error */ }
        const error = new Error(detail) as ChatSubmissionError;
        error.status = res.status;
        if (rejection) error.rejection = rejection;
        throw error;
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentId = 0;
      let dataLines: string[] = [];

      const dispatch = () => {
        if (!currentEvent || dataLines.length === 0) return false;
        if (currentId > lastEventId) lastEventId = currentId;
        const data = dataLines.join('\n');
        if (currentEvent === 'token') {
          try { onToken(JSON.parse(data) as string); } catch { onToken(data); }
        } else if (currentEvent === 'thinking' && onThinking) {
          try { onThinking(JSON.parse(data) as string); } catch { onThinking(data); }
        } else if (currentEvent === 'activity' && onActivity) {
          try { onActivity(JSON.parse(data) as string); } catch { onActivity(data); }
        } else if (currentEvent === 'question' && onQuestion) {
          // 防御：question 事件负载必须是对象且带 questions 数组。旧后端重放流曾把
          // question 双层序列化（JSON.parse 后得到字符串），透传会渲染出无法作答的
          // 空壳卡片——宁可不显示，交给用户手动追问。
          try {
            const q = JSON.parse(data) as ChatQuestion;
            if (q && typeof q === 'object' && Array.isArray(q.questions)) onQuestion(q);
          } catch { /* 解析失败忽略 */ }
        } else if (currentEvent === 'artifact' && onArtifact) {
          try {
            const item = verifiedArtifact(JSON.parse(data), sessionId, turnId);
            if (item) {
              const previous = artifacts.get(item.path);
              if (!previous || previous.size !== item.size || previous.sha256 !== item.sha256) {
                artifacts.delete(item.path); artifacts.set(item.path, item);
                if (artifacts.size > 16) artifacts.delete(artifacts.keys().next().value!);
                onArtifact(item);
              }
            }
          } catch { /* malformed and foreign artifacts cannot create delivery cards */ }
        } else if (currentEvent === 'accepted') {
          try { const receipt = acceptedReceipt(JSON.parse(data), sessionId, turnId); if (receipt) { acceptedSeen = true; onAccepted?.(receipt); } }
          catch { /* invalid receipts cannot clear a draft */ }
        } else if (currentEvent === 'central_recovery' && onCentralRecovery) {
          try {
            const hint: unknown = JSON.parse(data);
            if (hint && typeof hint === 'object' && 'version' in hint && hint.version === 1 && 'turn_id' in hint && typeof hint.turn_id === 'string' && hint.turn_id === turnId && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(hint.turn_id)) {
              onCentralRecovery({ version: 1, turn_id: hint.turn_id });
            }
          } catch { /* malformed or unrelated hints are ignored */ }
        } else if (currentEvent === 'heartbeat') {
          // 防呆心跳：独立于 activity/thinking，仅作「未卡住」提示，不覆盖真实状态。
          if (onHeartbeat) { try { onHeartbeat(JSON.parse(data) as string); } catch { onHeartbeat(data); } }
        } else if (currentEvent === 'error') {
          let msg = '执行失败';
          try { msg = JSON.parse(data) as string; } catch { msg = data; }
          if (acceptedSeen) terminalError = new Error(msg);
          else { onError(new Error(msg)); return true; }
        } else if (currentEvent === 'done') {
          let value: Partial<ChatCompletion> & { sessionKey?: string } = {};
          try { value = JSON.parse(data); } catch { /* legacy empty terminal */ }
          const completion = value?.version === 1 && value.session_id === sessionId && value.turn_id === turnId
            && ['completed', 'stopped', 'failed', 'unknown'].includes(value.state || '')
            ? { version: 1 as const, session_id: sessionId, turn_id: turnId!, state: value.state!, clean_end: value.clean_end, stop_reason: value.stop_reason } : undefined;
          if (terminalError) { if (completion) terminalError.completion = completion; onError(terminalError); }
          else onDone(value?.sessionKey, completion);
          return true;
        }
        return false;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line === '') {
            if (dispatch()) return true;
            currentEvent = ''; currentId = 0; dataLines = [];
          } else if (line.startsWith('id:')) currentId = Number(line.slice(3).trim()) || 0;
          else if (line.startsWith('event:')) currentEvent = line.slice(6).trim();
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
        }
      }
      buffer += decoder.decode();
      if (buffer) {
        for (const line of buffer.split(/\r?\n/)) {
          if (line.startsWith('id:')) currentId = Number(line.slice(3).trim()) || 0;
          else if (line.startsWith('event:')) currentEvent = line.slice(6).trim();
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
        }
      }
      return dispatch();
  };

  void (async () => {
    try { requestHash = await chatRequestHash(requestBody); } catch { /* No rejection acknowledgment without a trustworthy digest. */ }
    if (!resumeOnly && requestHash && turnId) {
      try { onRequestPrepared?.({ session_id: sessionId, turn_id: turnId, request_hash: requestHash }); }
      catch { onError(new Error('无法保存提交记录')); return; }
    }
    const started = Date.now();
    let first = !resumeOnly;
    while (!controller.signal.aborted) {
      try {
        const res = first
          ? await fetch(`${BASE}/api/chat/stream`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(requestBody),
              signal: controller.signal,
            })
          : await fetch(`${BASE}/api/chat/jobs/${encodeURIComponent(turnId || '')}/stream?after=${lastEventId}`, {
              signal: controller.signal,
            });
        if (!first && res.status === 404 && onRecoveryUnavailable) {
          onRecoveryUnavailable();
          return;
        }
        if (await consume(res, first)) return;
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return;
        const status = (err as Error & { status?: number })?.status;
        if (first && status && status >= 400 && status < 500) {
          onError(err instanceof Error ? err : new Error('请求失败'));
          return;
        }
      }
      first = false;
      // Backend chat runs may spend 5 minutes waiting for a session lock and then
      // run for 2 hours. Keep reconnecting for the same end-to-end budget.
      if (!turnId || Date.now() - started >= 130 * 60 * 1000) {
        onError(new Error('连接中断，自动重连超时'));
        return;
      }
      onInterrupted?.();
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  })();

  return controller;
}

/** 取某会话最近一轮的完整结果（SSE 断线后据此取回）。 */
export function fetchLastTurn(sessionId: string, turnId?: string): Promise<{ status: string; text: string; turn_id?: string; artifacts?: VerifiedArtifact[]; route?: TaskRoute; clean_end?: boolean; central_recovery?: { version: number; turn_id: string } }> {
  const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : '';
  return request(`/api/chat/last/${encodeURIComponent(sessionId)}${query}`);
}

// ═══ 设置面板 · 环境安装（install_tool 引擎桥） ═══

export interface EnvTool {
  id: string;
  state: 'ok' | 'missing' | 'no_dir' | 'fail';
  version: string | null;
  detail: string | null;
  name: string;
  group: string;
  group_name?: string;
  desc: string;
  big?: boolean;
}

export interface EnvToolsResponse {
  python: string;
  tools: EnvTool[];
  cachedAt?: number;
}

export function fetchEnvTools(refresh = false): Promise<EnvToolsResponse> {
  return request(`/api/env/tools${refresh ? '?refresh=1' : ''}`);
}

export interface EnvJobResult {
  id: string;
  state: 'ok' | 'fail';
  version?: string | null;
  strategy?: string | null;
  detail?: string | null;
}

export interface EnvJob {
  jobId: string;
  id: string;
  state: 'running' | 'ok' | 'fail';
  lines: string[];
  result: EnvJobResult | null;
  started: number;
  ended: number | null;
}

export function startEnvInstall(id: string): Promise<{ jobId: string; id: string; state: string }> {
  return request('/api/env/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
}

export function fetchEnvJob(jobId: string): Promise<EnvJob> {
  return request(`/api/env/job/${encodeURIComponent(jobId)}`);
}

// ═══ 设置面板 · 模型通道（只读 + 真自测） ═══

export interface ModelRow {
  slot?: string;
  order: number;
  name: string;
  sub: string;
  type: string;
  model: string;
  baseUrl: string;
  keyMasked: string;
  role: string;
  result: string;
  protocol?: string;
  thinking?: boolean;
  thinkingFormat?: string;
  keyNew?: string;
  keyNew2?: string;
  key2Label?: string;
  key2Masked?: string;
  modelEditable?: boolean;
  baseEditable?: boolean;
  baseOptional?: boolean;
  adv?: boolean;
  deletable?: boolean;
}

export interface ModelSaveRow {
  slot: string;
  name?: string;
  model: string;
  baseUrl: string;
  key: string;
  key2?: string;
  primary?: boolean;
  /** 自定义供应商上游协议：openai（默认）| anthropic */
  protocol?: string;
  thinking?: boolean;
  thinkingFormat?: string;
}

export interface ModelSaveResponse extends ModelChannelsResponse {
  ok: boolean;
  note?: string;
}

export function saveModelConfig(channel: string, rows: ModelSaveRow[]): Promise<ModelSaveResponse> {
  return request('/api/settings/models/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, rows }),
  });
}

export interface ModelChannelRows { rows: ModelRow[] }

export interface ModelChannelsResponse {
  transport?: 'api' | 'openclaw';
  channels: {
    chat: ModelChannelRows;
    transcribe: ModelChannelRows;
    image?: ModelChannelRows;
    video?: ModelChannelRows;
    music?: ModelChannelRows;
    speech?: ModelChannelRows;
  };
  primary: string;
}

export function fetchModelChannels(runtime?: string): Promise<ModelChannelsResponse> {
  return request(`/api/settings/models${runtime ? `?runtime=${encodeURIComponent(runtime)}` : ''}`);
}

export function saveChatTransport(transport: 'api' | 'openclaw'): Promise<ModelChannelsResponse> {
  return request('/api/settings/chat/transport', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transport }),
  });
}

// （保存接口见上方 saveModelConfig）

export interface SelftestResult { baseUrl: string; ok: boolean; ms: number; detail?: string }

export function runChannelSelftest(channel: string): Promise<{ channel: string; results: SelftestResult[]; testedAt: number }> {
  return request('/api/settings/models/selftest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel }),
  });
}

export interface LocalAgentModel {
  id: string;
  name: string;
  contextWindow?: number | null;
}

export interface LocalAgentInfo {
  id: string;
  label: string;
  installed: boolean;
  command: string;
  path: string;
  openclawProvider: string | null;
  configProvider?: string | null;
  supported: boolean;
  configured: boolean;
  usableWithoutKey: boolean;
  loginHint: string;
  models?: LocalAgentModel[];
}

export function fetchLocalAgents(): Promise<{
  agents: LocalAgentInfo[];
  installedCount: number;
  usableWithoutKeyCount: number;
  usableWithoutKey: string[];
}> {
  return request('/api/settings/local-agents');
}

export function enableLocalAgent(id: string, model = ''): Promise<{ ok: boolean; note: string; agent: LocalAgentInfo }> {
  return request('/api/settings/local-agents/enable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, model }),
  });
}

export interface ModelsFetchResponse { baseUrl: string; models: string[]; fetchedAt: number }

/**
 * 拉取某供应商的模型列表（自定义供应商免手打模型名）。
 * key 留空时后端按 slot 从已存配置里取（用户不用为了拉列表重贴一遍 Key）。
 */
export function fetchAvailableModels(
  baseUrl: string, key: string, protocol: string, slot = '',
  name = '',
): Promise<ModelsFetchResponse> {
  return request('/api/settings/models/available', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ baseUrl, key, protocol, slot, name }),
  });
}

// ── 设置：本机、数据存储、回收站 ───────────────────────────
export interface SystemPlace { key: string; label: string; desc: string; path: string; exists: boolean; bytes: number; files: number }
export interface SystemInfo { device: { name: string; os: string; arch: string; python: string }; workspace: string; storage: SystemPlace[] }
export function fetchSystem(): Promise<SystemInfo> { return request<SystemInfo>('/api/system'); }
export function openSystemPlace(key: string): Promise<{ ok: boolean }> {
  return request('/api/system/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
}
export interface TrashItem { id: string; name: string; original: string; kind: 'dir' | 'file'; bytes: number; files: number; deleted_at: number; expires_at: number }
export function fetchTrash(): Promise<{ items: TrashItem[]; keep_days: number }> { return request('/api/trash'); }
export function restoreTrash(id: string): Promise<{ id: string; restored: string }> { return request(`/api/trash/${encodeURIComponent(id)}/restore`, { method: 'POST' }); }
export function removeTrash(id: string): Promise<{ ok: boolean }> { return request(`/api/trash/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
export function emptyTrash(): Promise<{ ok: boolean; removed: number }> { return request('/api/trash', { method: 'DELETE' }); }

// ── 个人中心：头像只存在这台电脑上 ───────────────────────────
export function avatarUrl(version: number): string { return `${BASE}/api/profile/avatar?v=${version}`; }
export async function hasAvatar(): Promise<boolean> {
  const res = await fetch(`${BASE}/api/profile/avatar`, { method: 'GET', cache: 'no-store' });
  return res.ok;
}
export function uploadAvatar(file: File): Promise<{ ok: boolean }> {
  const form = new FormData(); form.append('file', file);
  return request('/api/profile/avatar', { method: 'POST', body: form });
}
export function removeAvatar(): Promise<{ ok: boolean }> { return request('/api/profile/avatar', { method: 'DELETE' }); }
