import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { fetchChatModels } from '../lib/api';
import type { ChatModelCatalog, ChatTurnSelection, WorkbenchContract } from '../lib/api';
import { chatModelUnavailableMessage, reasoningEffortLabel, resolveChatModelSelection, selectChatEffort, selectChatModel, snapshotChatModelSelection } from '../lib/chatModels';
import { resolveWorkbenchReadiness } from '../lib/workbenchReadiness';
import type { WorkbenchReadiness } from '../lib/workbenchReadiness';
import './ComposerModelPicker.css';

interface Props {
  sessionId: string; initialSelection?: ChatTurnSelection; initialSelectionSourceRevision?: string; disabled: boolean; refreshKey: number | string;
  onChange: (selection: ChatTurnSelection | undefined, ready: boolean, sourceRevision?: string) => void;
  workbench?: WorkbenchContract;
  expectedSourceScope?: string;
  workbenchChecking?: boolean;
  workbenchError?: boolean;
  onReadiness?: (value: WorkbenchReadiness) => void;
}
export default function ComposerModelPicker({ sessionId, initialSelection, initialSelectionSourceRevision, disabled, refreshKey, onChange,
  workbench, expectedSourceScope, workbenchChecking, workbenchError, onReadiness }: Props) {
  const target = JSON.stringify([sessionId, workbench?.workspace_scope, workbench?.source_scope]);
  const targetRef = useRef(target); targetRef.current = target;
  const [storedCatalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [storedSelection, setSelection] = useState<ChatTurnSelection | undefined>(initialSelection);
  const [storedLoading, setLoading] = useState(true);
  const [storedFresh, setFresh] = useState(false);
  const [storedError, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<'main' | 'models' | 'reasoning'>('main');
  const [search, setSearch] = useState('');
  const [position, setPosition] = useState<{ right: number; bottom?: number; top?: number; maxHeight: number }>({ right: 12, bottom: 100, maxHeight: 420 });
  const trigger = useRef<HTMLButtonElement>(null), panel = useRef<HTMLElement>(null);
  const pageAnchor = useRef<HTMLButtonElement>(null);
  const selected = useRef(initialSelection), selectionSource = useRef(initialSelectionSourceRevision), sequence = useRef(0);
  const previouslyDisabled = useRef(disabled);
  const previousRefreshKey = useRef(refreshKey);
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange;
  const onReadinessRef = useRef(onReadiness); onReadinessRef.current = onReadiness;
  const contractRef = useRef({ workbench, workbenchChecking, workbenchError }); contractRef.current = { workbench, workbenchChecking, workbenchError };
  const expectedSource = useRef({ sessionId, scope: workbench?.source_scope });
  if (expectedSource.current.sessionId !== sessionId) expectedSource.current = { sessionId, scope: workbench?.source_scope };
  if (expectedSource.current.scope === undefined && workbench?.source_scope) expectedSource.current.scope = workbench.source_scope;
  const snapshot = useRef<{ target: string; catalog: ChatModelCatalog | null; selection?: ChatTurnSelection; expiresAt: number; loading: boolean; refreshing: boolean; error: boolean }>
    ({ target, catalog: null, selection: initialSelection, expiresAt: 0, loading: true, refreshing: false, error: false });
  const report = useCallback(() => {
    const current = snapshot.current.target === targetRef.current ? snapshot.current
      : { catalog: null, selection: undefined, expiresAt: 0, loading: true, refreshing: false, error: false };
    const contract = contractRef.current;
    const managed = !!onReadinessRef.current || !!contract.workbench || contract.workbenchChecking !== undefined || contract.workbenchError !== undefined;
    if (!managed) {
      onChangeRef.current(current.selection, !current.error && !!current.catalog?.ready && current.expiresAt > Date.now()
        && (!current.catalog.switchable || !!current.selection), current.catalog?.source_revision);
      return;
    }
    const expected = expectedSourceScope || expectedSource.current.scope;
    const sourceChanged = !!expected && contract.workbench?.source_scope !== expected;
    const value = resolveWorkbenchReadiness({ contractVersion: contract.workbench?.version,
      loading: !sourceChanged && (!!contract.workbenchChecking || (current.loading && !current.catalog)), error: !!contract.workbenchError || current.error,
      sourceKey: contract.workbench?.source_scope, expectedSourceKey: expected,
      catalog: current.catalog, selection: current.selection, catalogExpiresAt: current.expiresAt, now: Date.now(), refreshing: current.refreshing });
    onReadinessRef.current?.(value);
    onChangeRef.current(current.selection, value.canSubmit, current.catalog?.source_revision);
  }, [expectedSourceScope]);
  const disabledRef = useRef(disabled); disabledRef.current = disabled;
  const reloadRef = useRef<() => void>(() => {});

  useEffect(() => {
    let alive = true;
    const forceRefresh = snapshot.current.target === target && previousRefreshKey.current !== refreshKey;
    previousRefreshKey.current = refreshKey;
    if (snapshot.current.target !== target) {
      selected.current = undefined; selectionSource.current = undefined;
      snapshot.current = { target, catalog: null, selection: undefined, expiresAt: 0, loading: true, refreshing: false, error: false };
      setCatalog(null); setSelection(undefined); setLoading(true); setFresh(false); setError(''); setOpen(false);
    }
    let activeController: AbortController | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined, expiryTimer: ReturnType<typeof setTimeout> | undefined;
    const clearLeaseTimers = () => { clearTimeout(refreshTimer); clearTimeout(expiryTimer); };
    const expire = () => {
      if (target !== targetRef.current) return;
      snapshot.current = { ...snapshot.current, expiresAt: 0, error: true };
      setFresh(false); setError('模型列表已过期'); report();
    };
    const load = (refresh = false) => {
      if (disabledRef.current || target !== targetRef.current) return;
      clearTimeout(refreshTimer);
      const requestedAt = Date.now();
      const current = ++sequence.current;
      activeController?.abort(); const abort = new AbortController(); activeController = abort;
      const validLease = snapshot.current.expiresAt > Date.now() && snapshot.current.catalog?.ready && !snapshot.current.error;
      snapshot.current = { ...snapshot.current, expiresAt: validLease ? snapshot.current.expiresAt : 0, loading: true, refreshing: !!validLease, error: false };
      setLoading(true); if (!validLease) setFresh(false); setError(''); report();
      void fetchChatModels(abort.signal, sessionId, refresh).then(value => {
        if (!alive || target !== targetRef.current || current !== sequence.current || abort.signal.aborted) return;
        if (!value || typeof value.revision !== 'string' || !Array.isArray(value.models) || value.models.some(item => !item || typeof item.id !== 'string')) throw new Error('Invalid catalogue');
        setCatalog(value);
        snapshot.current = { ...snapshot.current, catalog: value, loading: false, refreshing: false };
        if (!value.ready) {
          clearLeaseTimers(); snapshot.current = { ...snapshot.current, selection: undefined, expiresAt: 0 };
          setSelection(undefined); setFresh(false); setError(chatModelUnavailableMessage(value.reason)); report(); return;
        }
        const remaining = Math.min(120000, value.expires_in * 1000) - Math.max(0, Date.now() - requestedAt);
        if (!Number.isFinite(remaining) || remaining <= 0 || typeof value.source_revision !== 'string' || !value.source_revision) { expire(); return; }
        clearLeaseTimers(); snapshot.current = { ...snapshot.current, expiresAt: Date.now() + remaining, error: false };
        setFresh(true);
        refreshTimer = setTimeout(() => { if (alive && current === sequence.current && !disabledRef.current) load(true); }, Math.max(1, remaining - 5000));
        const leaseExpiry = snapshot.current.expiresAt;
        expiryTimer = setTimeout(() => { if (alive && snapshot.current.expiresAt === leaseExpiry) expire(); }, remaining);
        if (!value.switchable) {
          selected.current = undefined; selectionSource.current = value.source_revision; setSelection(undefined);
          snapshot.current = { ...snapshot.current, selection: undefined }; report(); return;
        }
        const candidate = selected.current && selectionSource.current === value.source_revision
          ? { ...selected.current, catalog_revision: value.revision } : selected.current;
        const authorityChanged = selectionSource.current !== undefined && selectionSource.current !== value.source_revision;
        const validated = authorityChanged ? undefined : resolveChatModelSelection(value, candidate);
        const resolved = candidate?.reasoning_effort && validated?.reasoning_effort !== candidate.reasoning_effort ? undefined : validated;
        if (resolved) { selected.current = resolved; selectionSource.current = value.source_revision; }
        setSelection(resolved);
        snapshot.current = { ...snapshot.current, selection: resolved }; report();
        if (!resolved) setError('请重新选择模型');
      }).catch(() => {
        if (!alive || target !== targetRef.current || current !== sequence.current || abort.signal.aborted) return;
        clearLeaseTimers(); snapshot.current = { ...snapshot.current, error: true, loading: false, refreshing: false };
        setFresh(false); setError('模型加载失败'); report();
      }).finally(() => { if (alive && target === targetRef.current && current === sequence.current && !abort.signal.aborted) setLoading(false); });
    };
    reloadRef.current = () => load(true);
    const focus = () => { if (!disabledRef.current) load(true); };
    if (disabledRef.current) { setLoading(false); onChangeRef.current(undefined, false); } else load(forceRefresh);
    window.addEventListener('focus', focus);
    return () => { alive = false; clearLeaseTimers(); activeController?.abort(); window.removeEventListener('focus', focus); };
  }, [sessionId, target, refreshKey, report]);

  useEffect(() => { report(); }, [workbench?.version, workbench?.source_scope, expectedSourceScope, workbenchChecking, workbenchError, report]);

  useEffect(() => {
    if (disabled) setOpen(false);
    else if (previouslyDisabled.current) reloadRef.current();
    previouslyDisabled.current = disabled;
  }, [disabled]);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect(); if (!rect) return;
      const bottom = Math.max(12, window.innerHeight - rect.top + 8);
      const maxHeight = window.innerHeight - bottom - 12;
      setPosition({ right: Math.max(12, Math.min(window.innerWidth - rect.right, window.innerWidth - 332)),
        ...(maxHeight < 190 ? { top: 12, maxHeight: Math.max(120, window.innerHeight - 24) } : { bottom, maxHeight: Math.min(460, maxHeight) }) });
    };
    const close = (restore: boolean) => { setOpen(false); if (restore) trigger.current?.focus(); };
    const outside = (event: Event) => { if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) close(false); };
    const keydown = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); close(true); } };
    place(); panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    document.addEventListener('pointerdown', outside); document.addEventListener('focusin', outside); document.addEventListener('keydown', keydown);
    window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside); document.removeEventListener('keydown', keydown); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open]);

  const changePage = (next: 'main' | 'models' | 'reasoning') => {
    // The clicked row/option unmounts on navigation. Keep focus on a persistent
    // element before that commit, so focusin does not mistake it for Tab out.
    pageAnchor.current?.focus({ preventScroll: true });
    setPage(next);
  };
  const sameTarget = snapshot.current.target === target;
  const catalog = sameTarget ? storedCatalog : null, selection = sameTarget ? storedSelection : undefined;
  const loading = !sameTarget || storedLoading, fresh = sameTarget && storedFresh, error = sameTarget ? storedError : '';
  const model = catalog?.models.find(item => item.id === selection?.model);
  const effort = selection?.reasoning_effort;
  const reasoning = model?.reasoning;
  const canChoose = fresh && !loading && !error && catalog?.ready === true && catalog.switchable;
  const pickModel = (id: string) => {
    if (disabled || loading || !fresh || !catalog?.switchable) return;
    const value = selectChatModel(catalog, id); if (!value) return;
    selected.current = value; selectionSource.current = catalog.source_revision; setSelection(value); setError('');
    snapshot.current = { ...snapshot.current, selection: snapshotChatModelSelection(value), error: false }; report(); changePage('main');
  };
  const pickEffort = (value: string) => {
    if (disabled || !catalog || !selection || !canChoose) return;
    const next = selectChatEffort(catalog, selection, value); if (!next) return;
    selected.current = next; setSelection(next); snapshot.current = { ...snapshot.current, selection: snapshotChatModelSelection(next) }; report(); changePage('main');
  };
  const label = model?.label || selection?.model || (loading ? '加载模型…' : !catalog?.switchable && catalog?.ready ? '当前默认模型' : '选择模型');
  return <>
    <button type="button" id={`model-picker-${sessionId}`} className="composer-model-trigger" ref={trigger} disabled={disabled}
      aria-label={`模型与推理强度：${label}${effort ? `，${reasoningEffortLabel(effort)}` : ''}`}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => { setPage('main'); setSearch(''); setOpen(value => !value); }}>
      <span className="composer-model-name">{label}</span>{effort && <span className="composer-model-effort">{reasoningEffortLabel(effort)}</span>}<span aria-hidden="true">⌄</span>
    </button>
    {open && createPortal(<section ref={panel} className="composer-model-popover" role="dialog" aria-label="模型与推理强度" style={position}>
      <div className="composer-model-heading"><button type="button" className="composer-model-back" ref={pageAnchor} onClick={() => { changePage('main'); setSearch(''); }} aria-label={page === 'main' ? '模型设置' : '返回模型设置'}>{page === 'main' ? '对话模型' : '‹ 返回'}</button><button type="button" className="composer-model-close" aria-label="关闭模型选择" onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button></div>
      {page === 'main' ? <div className="composer-model-main">
        <button type="button" className="composer-model-row" disabled={loading || !fresh || !catalog?.switchable || !catalog.ready} onClick={() => changePage('models')}><span>模型</span><span className="composer-model-row-value">{label}<span aria-hidden="true">›</span></span></button>
        <button type="button" className="composer-model-row" disabled={!canChoose || reasoning?.status !== 'supported' || !reasoning.efforts.length} onClick={() => changePage('reasoning')}><span>推理强度</span><span className="composer-model-row-value">{effort ? reasoningEffortLabel(effort) : '默认'}<span aria-hidden="true">›</span></span></button>
      </div> : page === 'models' ? <>
        <input type="search" className="composer-model-search" aria-label="搜索模型" placeholder="搜索可用模型…" value={search} onChange={event => setSearch(event.target.value)} />
        <div className="composer-model-options" role="group" aria-label="可用模型">{catalog?.models.filter(item => `${item.label} ${item.id}`.toLowerCase().includes(search.toLowerCase())).map(item => <button type="button" key={item.id} className="composer-model-option" aria-pressed={item.id === selection?.model} disabled={loading || !fresh} onClick={() => pickModel(item.id)}><span><strong>{item.label}</strong>{item.label !== item.id && <small>{item.id}</small>}</span><span aria-hidden="true">{item.id === selection?.model ? '✓' : ''}</span></button>)}{!catalog?.models.some(item => `${item.label} ${item.id}`.toLowerCase().includes(search.toLowerCase())) && <p className="composer-model-note">没有匹配的模型</p>}</div>
      </> : <div className="composer-model-options" role="group" aria-label="可用推理强度"><button type="button" className="composer-model-option" aria-pressed={!effort} onClick={() => pickEffort('')}><span>默认</span><span aria-hidden="true">{!effort ? '✓' : ''}</span></button>{reasoning?.status === 'supported' && reasoning.efforts.map(value => <button type="button" key={value} className="composer-model-option" aria-pressed={effort === value} onClick={() => pickEffort(value)}><span>{reasoningEffortLabel(value)}</span><span aria-hidden="true">{effort === value ? '✓' : ''}</span></button>)}</div>}
      {(loading || error) && <p className="composer-model-feedback" role="status">{loading ? '正在加载' : error}</p>}
      <div className="composer-model-footer"><span>{catalog?.service_source === 'subscription' ? '订阅渠道' : '自定义接口'}</span><button type="button" disabled={loading} onClick={() => reloadRef.current()}>{loading ? '加载中…' : '刷新模型'}</button></div>
    </section>, document.body)}
  </>;
}
