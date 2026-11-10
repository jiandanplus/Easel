export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}
export interface DraftAttachment {
  id: string;
  name: string;
  path: string;
  available?: boolean;
  size?: number;
}
export interface DraftSubmission {
  clientTurnId: string;
  submittedRevision: number;
  state: 'confirming';
  text: string;
  attachments: DraftAttachment[];
  requestHash?: string;
}
export interface DraftEnvelope {
  version: 1;
  scopeKey: string;
  sessionId: string;
  revision: number;
  text: string;
  attachments: DraftAttachment[];
  updatedAt: number;
  submission?: DraftSubmission;
  persisted: boolean;
}
export interface DraftUpdate { text?: string; attachments?: DraftAttachment[] }
export const DRAFT_SAVE_DELAY_MS = 300;
const PREFIX = 'easel_composer_draft_v1:';
const MAX_TEXT = 1024 * 1024;
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const validKey = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 512
  && !Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const revisionNumber = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
function attachments(value: unknown): DraftAttachment[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 128).filter(item => isRecord(item) && validKey(item.id) && typeof item.name === 'string'
    && item.name.length <= 1024 && typeof item.path === 'string' && item.path.length > 0 && item.path.length <= 4096)
    .map(item => ({ id: item.id, name: item.name, path: item.path,
      ...(typeof item.available === 'boolean' ? { available: item.available } : {}),
      ...(revisionNumber(item.size) ? { size: item.size } : {}) }));
}
const clone = (draft: DraftEnvelope): DraftEnvelope => ({ ...draft, attachments: draft.attachments.map(item => ({ ...item })),
  ...(draft.submission ? { submission: { ...draft.submission, attachments: draft.submission.attachments.map(item => ({ ...item })) } } : {}) });

/** Scope inputs are opaque backend identifiers, never identity credentials. */
export function draftScopeKey(workspaceScope: string, sourceScope: string): string {
  if (!validKey(workspaceScope) || !validKey(sourceScope)) throw Error('invalid_draft_scope');
  return JSON.stringify([workspaceScope, sourceScope]);
}

export function createComposerDraftStore(storage?: DraftStorage, now: () => number = () => Date.now()) {
  const memory = new Map<string, DraftEnvelope>();
  const keyFor = (scopeKey: string, sessionId: string) => {
    if (!validKey(scopeKey) || !validKey(sessionId)) throw Error('invalid_draft_scope');
    return `${PREFIX}${JSON.stringify([scopeKey, sessionId])}`;
  };
  const fresh = (scopeKey: string, sessionId: string): DraftEnvelope =>
    ({ version: 1, scopeKey, sessionId, revision: 0, text: '', attachments: [], updatedAt: now(), persisted: false });
  const load = (scopeKey: string, sessionId: string): DraftEnvelope => {
    const key = keyFor(scopeKey, sessionId);
    const current = memory.get(key);
    if (current) return clone(current);
    let draft = fresh(scopeKey, sessionId);
    try {
      const raw = storage?.getItem(key);
      if (raw && raw.length <= MAX_TEXT * 3) {
        const data: unknown = JSON.parse(raw);
        if (isRecord(data) && data.version === 1 && data.scopeKey === scopeKey && data.sessionId === sessionId
          && revisionNumber(data.revision) && typeof data.text === 'string' && data.text.length <= MAX_TEXT
          && typeof data.updatedAt === 'number' && Number.isFinite(data.updatedAt) && data.updatedAt >= 0
          && Array.isArray(data.attachments)) {
          draft = { version: 1, scopeKey, sessionId, revision: data.revision, text: data.text,
            attachments: attachments(data.attachments), updatedAt: data.updatedAt, persisted: true };
          const submission = data.submission;
          if (isRecord(submission) && validKey(submission.clientTurnId) && revisionNumber(submission.submittedRevision)
            && submission.submittedRevision <= draft.revision && submission.state === 'confirming'
            && typeof submission.text === 'string' && submission.text.length <= MAX_TEXT && Array.isArray(submission.attachments))
            draft.submission = { clientTurnId: submission.clientTurnId, submittedRevision: submission.submittedRevision,
              state: 'confirming', text: submission.text, attachments: attachments(submission.attachments),
              ...(typeof submission.requestHash === 'string' && /^[a-f0-9]{64}$/.test(submission.requestHash) ? { requestHash: submission.requestHash } : {}) };
        }
      }
    } catch { /* Keep the current in-memory draft when storage is unavailable. */ }
    memory.set(key, draft);
    return clone(draft);
  };
  const persist = (scopeKey: string, sessionId: string): DraftEnvelope => {
    const draft = load(scopeKey, sessionId), key = keyFor(scopeKey, sessionId);
    draft.persisted = false;
    try {
      if (storage) {
        const { persisted: _persisted, ...payload } = draft;
        storage.setItem(key, JSON.stringify(payload));
        draft.persisted = true;
      }
    } catch { /* Content stays in memory; callers can display the unsaved state. */ }
    memory.set(key, draft);
    return clone(draft);
  };
  const save = (draft: DraftEnvelope, shouldPersist = true) => {
    draft.persisted = false;
    memory.set(keyFor(draft.scopeKey, draft.sessionId), clone(draft));
    return shouldPersist ? persist(draft.scopeKey, draft.sessionId) : clone(draft);
  };
  const update = (scopeKey: string, sessionId: string, patch: DraftUpdate, options: { persist?: boolean } = {}): DraftEnvelope => {
    const draft = load(scopeKey, sessionId);
    if (draft.revision === Number.MAX_SAFE_INTEGER) throw Error('draft_revision_exhausted');
    if (patch.text !== undefined) {
      if (typeof patch.text !== 'string' || patch.text.length > MAX_TEXT) throw Error('invalid_draft_text');
      draft.text = patch.text;
    }
    if (patch.attachments !== undefined) draft.attachments = attachments(patch.attachments);
    draft.revision += 1;
    draft.updatedAt = now();
    return save(draft, options.persist !== false);
  };
  const prepareSubmission = (scopeKey: string, sessionId: string, clientTurnId: string): DraftEnvelope => {
    if (!validKey(clientTurnId)) throw Error('invalid_turn_id');
    const draft = load(scopeKey, sessionId);
    if (draft.submission) {
      if (draft.submission.clientTurnId !== clientTurnId) throw Error('draft_submission_pending');
      return draft;
    }
    draft.submission = { clientTurnId, submittedRevision: draft.revision, state: 'confirming',
      text: draft.text, attachments: draft.attachments.map(item => ({ ...item })) };
    return save(draft);
  };
  const acknowledge = (scopeKey: string, sessionId: string, turnId: string, submittedRevision: number): DraftEnvelope => {
    const draft = load(scopeKey, sessionId), submission = draft.submission;
    if (!submission || submission.clientTurnId !== turnId || submission.submittedRevision !== submittedRevision) return draft;
    if (draft.revision === submittedRevision) {
      draft.text = '';
      draft.attachments = [];
      draft.revision += 1;
    }
    delete draft.submission;
    draft.updatedAt = now();
    return save(draft);
  };
  const abandonSubmission = (scopeKey: string, sessionId: string, turnId: string): DraftEnvelope => {
    const draft = load(scopeKey, sessionId);
    if (draft.submission?.clientTurnId !== turnId) return draft;
    delete draft.submission;
    return save(draft);
  };
  const bindSubmission = (scopeKey: string, sessionId: string, turnId: string, requestHash: string): DraftEnvelope => {
    const draft = load(scopeKey, sessionId);
    if (draft.submission?.clientTurnId !== turnId || !/^[a-f0-9]{64}$/.test(requestHash)) return draft;
    if (draft.submission.requestHash && draft.submission.requestHash !== requestHash) return draft;
    draft.submission.requestHash = requestHash;
    return save(draft);
  };
  const setAttachmentAvailability = (scopeKey: string, sessionId: string, values: Record<string, boolean>): DraftEnvelope => {
    const draft = load(scopeKey, sessionId);
    draft.attachments = draft.attachments.map(item => typeof values[item.id] === 'boolean' ? { ...item, available: values[item.id] } : item);
    return save(draft);
  };
  return { load, persist, update, prepareSubmission, acknowledge, abandonSubmission, bindSubmission, setAttachmentAvailability };
}
