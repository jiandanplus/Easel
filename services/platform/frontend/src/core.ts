export type RecordData = Record<string, unknown>;
export type Session = { admin: { id: string; role: string; permissions: string[] }; csrf_token: string };
export type LoadState<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; data: T };
export type PageData = { items: RecordData[]; limit: number; offset: number; has_more: boolean };
export type AuthState = { kind: 'loading' | 'anonymous' } | { kind: 'pending'; requestId: string } | { kind: 'error'; message: string } | { kind: 'authenticated'; session: Session };
export const domains = ['users', 'products', 'plans', 'wallets', 'orders', 'offers', 'models', 'providers', 'executions', 'audits'] as const;
export type Domain = typeof domains[number];
export type Catalog = 'plans' | 'offers' | 'models';
export const titles: Record<Domain | 'overview' | 'reconciliation' | 'stats' | 'redemptions' | 'admins', string> = { overview: '今天', stats: '数据统计', redemptions: '兑换码', admins: '后台账号', reconciliation: '结果不明的任务', users: '用户', products: '产品', plans: '套餐', wallets: '钱包明细', orders: '订单与支付', offers: '售卖价格', models: '模型', providers: '供应商', executions: '用量与费用', audits: '操作记录' };
export const writePermissions: Record<Catalog, string> = { plans: 'plans:publish', offers: 'billing:publish', models: 'execution:publish' };

export function formatCredits(value: unknown): string {
  if (value == null) return '未知';
  if (typeof value !== 'string' || !/^-?\d+$/.test(value)) return '数据无效';
  const n = BigInt(value), a = n < 0n ? -n : n;
  const fraction = (a % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${n < 0n ? '-' : ''}${a / 1000000n}${fraction ? '.' + fraction : ''}`;
}
// Two decimals for summaries; exact values stay in tables and the ledger.
export function roundCredits(value: unknown): string {
  if (typeof value !== 'string' || !/^-?\d+$/.test(value)) return formatCredits(value);
  const n = BigInt(value), sign = n < 0n ? '-' : '', a = (n < 0n ? -n : n) + 5000n, whole = a / 1000000n, cents = (a % 1000000n) / 10000n;
  return `${sign}${whole.toLocaleString('en-US')}${cents ? '.' + cents.toString().padStart(2, '0').replace(/0$/, '') : ''}`;
}
export function decimalUnits(value: string, precision: number): string {
  if (!new RegExp(`^\\d+(?:\\.\\d{1,${precision}})?$`).test(value)) throw new Error(`请输入最多 ${precision} 位小数的非负金额`);
  const [whole, fraction = ''] = value.split('.');
  const n = BigInt(whole) * 10n ** BigInt(precision) + BigInt(fraction.padEnd(precision, '0'));
  if (n <= 0n || n > 9223372036854775807n) throw new Error('金额超出有效范围');
  return n.toString();
}
export function display(value: unknown): string {
  if (value == null) return '未知';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) return value.map(display).join('、') || '无';
  if (typeof value === 'object') return '查看详情';
  return String(value);
}
export function formatDate(value: unknown): string {
  if (typeof value !== 'string') return '未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '数据无效' : date.toLocaleString('zh-CN', { hour12: false });
}
export function formatDay(value: unknown): string {
  if (typeof value !== 'string') return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '数据无效' : date.toLocaleDateString('zh-CN');
}
export class ConsoleError extends Error {
  constructor(public status: number, public code: string) {
    super(status === 401 ? '登录已失效，请重新登录' : status === 403 ? '当前账号没有此操作权限，请联系运营者' : status === 409 ? '版本或幂等操作冲突。请核对原操作结果，再刷新数据' : status === 422 ? '提交内容未通过校验，请检查字段与版本' : status === 0 ? '连接中断，操作结果未知。请使用同一操作重试' : '服务暂不可用，请稍后重试');
  }
}
export class ConsoleClient {
  session: Session | null = null;
  private epoch = 0;
  constructor(private fetcher: typeof fetch = (...args) => fetch(...args), private newKey = () => crypto.randomUUID()) {}
  setSession(value: Session | null) {
    if (value && (!value.admin || typeof value.admin.id !== 'string' || !Array.isArray(value.admin.permissions) || !value.admin.permissions.every(p => typeof p === 'string') || typeof value.csrf_token !== 'string' || !value.csrf_token)) throw new ConsoleError(502, 'invalid_session');
    this.session = value; this.epoch++;
  }
  get sessionRevision() { return this.epoch; }
  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!/^\/api\/(?:session|overview|stats|catalog|provider-configs|redemptions|admins|admin-requests|users|products|plans|wallets|orders|offers|models|providers|executions|audits|reconciliation-cases)(?:[/?]|$)/.test(path) && path !== '/auth/logout') throw new ConsoleError(400, 'invalid_path');
    const epoch = this.epoch;
    let response: Response;
    try { response = await this.fetcher(path, { ...init, headers: new Headers(init.headers), credentials: 'same-origin', cache: 'no-store', redirect: 'error' }); }
    catch (error) { if (init.signal?.aborted) throw error; throw new ConsoleError(0, 'network_error'); }
    if (!response.ok) {
      let code = 'request_failed';
      try { const body = await response.json(); if (typeof body?.error?.code === 'string') code = body.error.code; } catch { /* Never surface raw proxy HTML or exceptions. */ }
      if (response.status === 401 && epoch === this.epoch) this.setSession(null);
      throw new ConsoleError(response.status, code);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; } catch { throw new ConsoleError(502, 'invalid_response'); }
  }
  read<T>(path: string, signal?: AbortSignal) { return this.request<T>(path, { signal }); }
  reviewOperation(jobId: string, value: unknown) {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(jobId)) throw new ConsoleError(400, 'invalid_job');
    if (!this.session?.admin.permissions.includes('execution:review')) throw new ConsoleError(403, 'permission_denied');
    const session = this.session, epoch = this.epoch, key = this.newKey(), body = JSON.stringify(value);
    const current = () => this.epoch === epoch && this.session === session;
    const check = () => { if (!current()) throw new ConsoleError(401, 'session_changed'); if (!session.admin.permissions.includes('execution:review')) throw new ConsoleError(403, 'permission_denied'); };
    const path = `/api/reconciliation-cases/${jobId}`;
    let sent = false;
    return { key, current, send: async () => {
      check();
      if (sent) throw new ConsoleError(409, 'operation_already_sent');
      sent = true;
      const result = await this.request<unknown>(`${path}/reviews`, { method: 'POST', body, headers: { 'Content-Type': 'application/json', 'X-Console-CSRF': session.csrf_token, 'Idempotency-Key': key } });
      check(); return result;
    }, lookup: async () => {
      check(); const result = await this.request<unknown>(`${path}/review-operations`, { headers: { 'Idempotency-Key': key } });
      check(); return result;
    } };
  }
  operation(path: string, value: unknown, permission: string) {
    if (!this.session?.admin.permissions.includes(permission)) throw new ConsoleError(403, 'permission_denied');
    const session = this.session, epoch = this.epoch, key = this.newKey(), body = JSON.stringify(value);
    if (!/^\/api\/(?:(?:plans|offers|models)(?:\/[A-Za-z0-9%_.~-]+\/(?:withdraw|state|revise))?|wallets\/[A-Za-z0-9%_.~-]+\/(?:allowance|plans)|users\/[A-Za-z0-9%_.~-]+\/(?:status|logout|note)|redemptions(?:\/[A-Za-z0-9%_.~-]+\/disable)?|admin-requests\/[A-Za-z0-9%_.~-]+\/approve|admins\/[A-Za-z0-9%_.~-]+\/disable|provider-configs\/(?:chat|transcribe|speech|image|video|music)|catalog\/models|catalog\/import|catalog\/(?:execution|media)\/[A-Za-z0-9%_.~:-]+\/(?:price|trial)|catalog\/media\/[A-Za-z0-9%_.~:-]+\/state)$/.test(path)) throw new ConsoleError(400, 'invalid_operation');
    return { key, send: async () => {
      if (this.epoch !== epoch || this.session !== session) throw new ConsoleError(401, 'session_changed');
      return this.request<RecordData>(path, { method: 'POST', body, headers: { 'Content-Type': 'application/json', 'X-Console-CSRF': session.csrf_token, 'Idempotency-Key': key } });
    } };
  }
  async logout() {
    if (!this.session) return;
    await this.request('/auth/logout', { method: 'POST', headers: { 'X-Console-CSRF': this.session.csrf_token } });
    this.setSession(null);
  }
}
export class RequestGate {
  private currentController: AbortController | undefined;
  begin() {
    this.cancel(); const controller = new AbortController(); this.currentController = controller;
    return { signal: controller.signal, current: () => this.currentController === controller && !controller.signal.aborted };
  }
  cancel() { this.currentController?.abort(); this.currentController = undefined; }
}
export class Submission {
  private operation: ReturnType<ConsoleClient['operation']> | undefined;
  busy = false;
  conflict = false;
  get locked() { return Boolean(this.operation); }
  async send(client: ConsoleClient, path: string, body: unknown, permission: string) {
    if (this.busy || this.conflict) throw new ConsoleError(409, 'operation_locked');
    this.operation ??= client.operation(path, body, permission);
    this.busy = true;
    try { return await this.operation.send(); }
    catch (error) { if (error instanceof ConsoleError && error.status === 409) this.conflict = true; throw error; }
    finally { this.busy = false; }
  }
}
export const userFilters: [string, string][] = [['', '全部用户'], ['funded', '有余额'], ['empty', '没有余额'], ['expiring', '7 天内到期'], ['disabled', '已停用']];
export const userSorts: [string, string][] = [['created', '最新注册'], ['last_used', '最近使用'], ['balance', '余额最多'], ['spent', '用得最多']];
export type ListOptions = { status?: string; sort?: string; limit?: number };
export function listPath(domain: Domain, query: string, offset: number, options: ListOptions = {}): string {
  const limit = options.limit ?? 25;
  if (!(domains as readonly string[]).includes(domain) || query.length > 128 || !Number.isInteger(offset) || offset < 0 || offset > 100000 || !Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('筛选条件无效');
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query.trim()) params.set('q', query.trim());
  if (domain === 'users') {
    if (options.status && userFilters.some(([value]) => value === options.status)) params.set('status', options.status);
    if (options.sort && options.sort !== 'created' && userSorts.some(([value]) => value === options.sort)) params.set('sort', options.sort);
  }
  return `/api/${domain}?${params}`;
}
export function csvText(rows: RecordData[], columns: [string, string][]): string {
  const cell = (value: unknown) => { const text = value == null ? '' : String(value); return /[",\n\r]/.test(text) || /^[=+\-@]/.test(text) ? `"${(/^[=+\-@]/.test(text) ? "'" : '') + text.replace(/"/g, '""')}"` : text; };
  return '\ufeff' + [columns.map(([, label]) => cell(label)).join(','), ...rows.map(row => columns.map(([key]) => cell(row[key])).join(','))].join('\r\n');
}
export type Fields = Record<string, string | boolean>;
export function validReason(value: string): string {
  if (!value.trim() || value.length > 255 || /[\x00-\x1f\x7f]/.test(value)) throw new Error('请填写 1–255 字符的单行操作原因，不包含换行或控制字符');
  return value.trim();
}
export function buildDefinition(kind: Catalog, f: Fields): RecordData {
  const text = (key: string) => { const value = f[key]; if (typeof value !== 'string' || !value.trim() || value.length > 255) throw new Error('请填写有效的必填字段'); return value.trim(); };
  const number = (key: string, max: number) => { const s = text(key); if (!/^\d+$/.test(s) || +s < 1 || +s > max) throw new Error('整数参数超出有效范围'); return +s; };
  const list = (key: string) => { const values = text(key).split(/[,，\s]+/).filter(Boolean); if (!values.length || new Set(values).size !== values.length) throw new Error('列表需非空且不能重复'); return values; };
  const common = { id: text('id'), version: text('version'), test_only: f.test_only === true };
  if (kind === 'plans') return { ...common, name: text('name'), included_products: list('products'), features: list('features'), credits: decimalUnits(text('credits'), 6), days: number('days', 3650), concurrency: number('concurrency', 10000) };
  if (kind === 'offers') return { ...common, plan_id: text('plan_id'), amount_minor: decimalUnits(text('amount'), 2), currency: 'CNY', terms: { refund_policy: 'unused_full_only', description: text('description') } };
  const rate = (key: string) => { const n = text(key + '_numerator'), d = text(key + '_denominator'); if (!/^\d+$/.test(n) || !/^\d+$/.test(d) || BigInt(d) === 0n || BigInt(n) > 9223372036854775807n || BigInt(d) > 9223372036854775807n) throw new Error('费率必须是有效的非负整数分数'); return { numerator: BigInt(n).toString(), denominator: BigInt(d).toString() }; };
  const input = rate('input'), output = rate('output');
  if (input.numerator === '0' && output.numerator === '0') throw new Error('输入和输出费率不能同时为零');
  return { ...common, name: text('name'), capability: 'text', feature: text('feature'), products: list('products'), provider_id: text('provider_id'), provider_version: text('provider_version'), verification: common.test_only ? 'fixture' : 'live', verification_ref: text('verification_ref'), rate: { version: text('rate_version'), input, output }, limits: { max_input_bytes: number('max_input_bytes', 1048576), max_input_tokens: number('max_input_tokens', 65536), max_output_tokens: number('max_output_tokens', 4096) } };
}
