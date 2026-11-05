import type { ChatModelCatalog, ChatTurnSelection } from './api';

/** Catalogue revision binds the choice to its actual credential source and capabilities. */
export function resolveChatModelSelection(catalog: ChatModelCatalog, previous?: ChatTurnSelection): ChatTurnSelection | undefined {
  if (!catalog.ready || !catalog.revision) return undefined;
  if (previous && previous.catalog_revision !== catalog.revision) return undefined;
  const id = previous?.model || catalog.selected_model;
  const model = catalog.models.find(item => item.id === id);
  if (!model) return undefined;
  const effort = previous?.reasoning_effort;
  return { model: model.id, catalog_revision: catalog.revision,
    ...(effort && model.reasoning?.status === 'supported' && model.reasoning.efforts.includes(effort)
      ? { reasoning_effort: effort } : {}) };
}
export function selectChatModel(catalog: ChatModelCatalog, model: string, _previous?: ChatTurnSelection): ChatTurnSelection | undefined {
  if (!catalog.ready || !catalog.models.some(item => item.id === model)) return undefined;
  return { model, catalog_revision: catalog.revision };
}
export function selectChatEffort(catalog: ChatModelCatalog, selection: ChatTurnSelection, effort: string): ChatTurnSelection | undefined {
  const valid = resolveChatModelSelection(catalog, selection);
  if (!valid) return undefined;
  if (!effort) return { model: valid.model, catalog_revision: valid.catalog_revision };
  const model = catalog.models.find(item => item.id === valid.model);
  if (model?.reasoning?.status !== 'supported' || !model.reasoning.efforts.includes(effort)) return undefined;
  return { ...valid, reasoning_effort: effort };
}
export function snapshotChatModelSelection(selection?: ChatTurnSelection): ChatTurnSelection | undefined {
  return selection ? Object.freeze({ ...selection }) : undefined;
}
export function reasoningEffortLabel(effort: string): string {
  return ({ none: '关闭', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '极高', max: '最高' } as Record<string, string>)[effort] || effort;
}

/** `reason` is a deliberately public server message; thrown transport details stay hidden. */
export function chatModelUnavailableMessage(reason: unknown): string {
  const aliases: Record<string, string> = {
    authentication_required: '登录后可加载订阅模型',
    not_authenticated: '登录后可加载订阅模型',
    '云端执行尚未开放': '云端执行尚未开放，请在设置中选择本地执行',
  };
  if (typeof reason !== 'string' || !reason.trim() || reason.length > 200 || Array.from(reason).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127))
    return '当前渠道暂不可用，请检查登录、权益或接口配置后刷新';
  return aliases[reason] || reason;
}
