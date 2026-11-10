import { useCallback, useEffect, useRef, useState } from 'react';
import { DRAFT_SAVE_DELAY_MS } from './composerDrafts';
import type { createComposerDraftStore, DraftUpdate } from './composerDrafts';

export type ComposerDraftStore = ReturnType<typeof createComposerDraftStore>;
export interface ComposerSubmission { scopeKey: string; turnId: string; revision: number }

export function useComposerDraft(scopeKey: string, sessionId: string, store: ComposerDraftStore, refreshKey = 0) {
  const [draft, setDraft] = useState(() => store.load(scopeKey, sessionId));
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flush = useCallback(() => {
    clearTimeout(timer.current);
    const value = store.persist(scopeKey, sessionId);
    setDraft(value);
    return value;
  }, [store, scopeKey, sessionId]);
  const update = useCallback((patch: DraftUpdate) => {
    setDraft(store.update(scopeKey, sessionId, patch, { persist: false }));
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, DRAFT_SAVE_DELAY_MS);
  }, [store, scopeKey, sessionId, flush]);
  const prepare = useCallback((turnId: string) => {
    clearTimeout(timer.current);
    const value = store.prepareSubmission(scopeKey, sessionId, turnId);
    setDraft(value);
    return value;
  }, [store, scopeKey, sessionId]);
  useEffect(() => { setDraft(store.load(scopeKey, sessionId)); }, [store, scopeKey, sessionId, refreshKey]);
  useEffect(() => {
    const save = () => { clearTimeout(timer.current); store.persist(scopeKey, sessionId); };
    const beforeLeave = (event: BeforeUnloadEvent) => {
      const value = store.persist(scopeKey, sessionId);
      if (!value.persisted && (value.text || value.attachments.length)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('pagehide', save);
    window.addEventListener('beforeunload', beforeLeave);
    return () => { save(); window.removeEventListener('pagehide', save); window.removeEventListener('beforeunload', beforeLeave); };
  }, [store, scopeKey, sessionId]);
  return { draft, update, prepare, flush };
}
