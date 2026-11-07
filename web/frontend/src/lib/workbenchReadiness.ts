import type { ChatModelCatalog, ChatTurnSelection } from './api';

export const SUPPORTED_WORKBENCH_CONTRACT = 1;
export type WorkbenchReadinessState = 'checking' | 'ready' | 'disconnected' | 'settings' | 'confirm-model' | 'source-changed' | 'incompatible';
export type WorkbenchReadinessAction = 'none' | 'reconnect' | 'settings' | 'confirm-model' | 'new-chat';
export interface WorkbenchReadiness {
  state: WorkbenchReadinessState;
  canSubmit: boolean;
  label: string;
  action: WorkbenchReadinessAction;
}
export interface WorkbenchReadinessInput {
  contractVersion?: unknown;
  loading?: boolean;
  refreshing?: boolean;
  error?: unknown;
  configured?: boolean;
  connected?: boolean;
  sourceKey?: string;
  expectedSourceKey?: string;
  catalog?: ChatModelCatalog | null;
  selection?: ChatTurnSelection;
  catalogExpiresAt?: number;
  now?: number;
}

const result = (state: WorkbenchReadinessState, label: string, action: WorkbenchReadinessAction = 'none'): WorkbenchReadiness =>
  ({ state, label, action, canSubmit: state === 'ready' });

/** One submission decision for every composer; this performs no network work. */
export function resolveWorkbenchReadiness(input: WorkbenchReadinessInput): WorkbenchReadiness {
  if (input.loading) return result('checking', '正在连接');
  if (input.contractVersion !== SUPPORTED_WORKBENCH_CONTRACT)
    return result('incompatible', '需要更新工作台', 'reconnect');
  if (input.expectedSourceKey && input.sourceKey !== input.expectedSourceKey)
    return result('source-changed', '账号或渠道已变化', 'new-chat');
  if (input.configured === false) return result('settings', '尚未配置', 'settings');
  if (input.error || input.connected === false) return result('disconnected', '连接失败', 'reconnect');
  const catalog = input.catalog;
  if (!catalog) return result('checking', '正在连接');
  if (!catalog.ready) {
    const reason = typeof catalog.reason === 'string' ? catalog.reason : '';
    if (/authentication_required|not_authenticated|未登录|登录|未配置|尚未配置/.test(reason))
      return result('settings', '需要设置', 'settings');
    if (/source_changed|scope_mismatch|账号.*变化|来源.*变化/.test(reason))
      return result('source-changed', '账号或渠道已变化', 'new-chat');
    return result('disconnected', '暂不可用', 'reconnect');
  }
  if (!Array.isArray(catalog.models) || catalog.models.some(item => !item || typeof item.id !== 'string'))
    return result('disconnected', '连接失败', 'reconnect');
  const now = input.now ?? Date.now();
  if (!Number.isFinite(input.catalogExpiresAt) || (input.catalogExpiresAt as number) <= now)
    return result('disconnected', '连接已过期', 'reconnect');
  if (!catalog.switchable) return result('ready', input.refreshing ? '正在更新' : '可以开始');
  const selected = input.selection;
  if (!selected || selected.catalog_revision !== catalog.revision)
    return result('confirm-model', '请选择模型', 'confirm-model');
  const model = catalog.models.find(item => item.id === selected.model);
  if (!model) return result('confirm-model', '请选择模型', 'confirm-model');
  if (selected.reasoning_effort && (model.reasoning?.status !== 'supported' || !Array.isArray(model.reasoning.efforts) || !model.reasoning.efforts.includes(selected.reasoning_effort)))
    return result('confirm-model', '请确认推理强度', 'confirm-model');
  return result('ready', input.refreshing ? '正在更新' : '可以开始');
}
