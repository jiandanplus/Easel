export interface DeliverableReference {
  path: string;
  name: string;
  sessionId?: string;
}

export interface VerifiedArtifact {
  version: 1;
  session_id: string;
  turn_id: string;
  path: string;
  name: string;
  size: number;
  sha256: string;
}

/** Accept only the host event's exact schema and the owning conversation turn. */
export function verifiedArtifact(value: unknown, sessionId?: string, turnId?: string): VerifiedArtifact | null {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !sessionId || !turnId
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(sessionId)
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(turnId)) return null;
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join(',') !== 'name,path,session_id,sha256,size,turn_id,version'
      || item.version !== 1 || item.session_id !== sessionId || item.turn_id !== turnId
      || typeof item.path !== 'string' || !item.path.startsWith('outputs/')
      || typeof item.name !== 'string' || item.name !== item.path.split('/').at(-1)
      || typeof item.size !== 'number' || !Number.isSafeInteger(item.size) || item.size < 0 || item.size > 256 * 1024
      || typeof item.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(item.sha256)
      || !deliverableReference(item.path.slice('outputs/'.length), item.name, sessionId)) return null;
  return { version: 1, session_id: sessionId, turn_id: turnId, path: item.path, name: item.name, size: item.size, sha256: item.sha256 };
}

export function mergeVerifiedArtifacts(current: unknown, incoming: unknown, sessionId?: string, turnId?: string): VerifiedArtifact[] {
  const found = new Map<string, VerifiedArtifact>();
  for (const group of [current, incoming]) {
    if (!Array.isArray(group)) continue;
    for (const value of group) {
      const item = verifiedArtifact(value, sessionId, turnId);
      if (item) { found.delete(item.path); found.set(item.path, item); }
    }
  }
  return [...found.values()].slice(-16);
}

export function verifiedArtifactReference(value: unknown, sessionId?: string, turnId?: string): DeliverableReference | null {
  const item = verifiedArtifact(value, sessionId, turnId);
  return item ? deliverableReference(item.path.slice('outputs/'.length), item.name, item.session_id) : null;
}

export function deliverableReference(path: string, name?: string, sessionId?: string): DeliverableReference | null {
  if (typeof path !== 'string' || !path || path.length > 4096 || path.includes('[已隐藏]')
      || Array.from(path).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
      || path.split('/').some(part => !part || part.startsWith('.') || part.startsWith('_'))) return null;
  const filename = path.split('/').at(-1)!;
  const source = typeof sessionId === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(sessionId) ? sessionId : undefined;
  return { path, name: typeof name === 'string' && name.trim() ? name : filename, ...(source ? { sessionId: source } : {}) };
}

export function deliverableFromUrl(url: string, sessionId?: string): DeliverableReference | null {
  if (typeof url !== 'string') return null;
  const prefix = url.startsWith('/api/media/') ? '/api/media/' : url.startsWith('#/outputs/') ? '#/outputs/' : null;
  if (!prefix) return null;
  try { return deliverableReference(decodeURIComponent(url.slice(prefix.length).split(/[?#]/)[0]), undefined, sessionId); }
  catch { return null; }
}

export function deliverableDownloadUrl(reference: DeliverableReference): string | null {
  const safe = deliverableReference(reference.path);
  return safe ? '/api/media/' + safe.path.split('/').map(encodeURIComponent).join('/') : null;
}

export function deliverableCopyLabel(name: string): string | null {
  if (/\.(md|markdown|txt)$/i.test(name)) return '复制文案';
  if (/\.(html?|json|csv|tsv|srt|vtt|xml|ya?ml|css|js|ts|py|log)$/i.test(name)) return '复制原文';
  return null;
}

export async function copyDeliverable(
  reference: DeliverableReference,
  load: (path: string) => Promise<{ path: string; content: string; isBinary: boolean }>,
  write: (text: string) => Promise<void>,
): Promise<void> {
  if (!deliverableReference(reference.path) || !deliverableCopyLabel(reference.name)) throw new Error('copy_unavailable');
  const document = await load(reference.path);
  if (document.path !== reference.path || document.isBinary !== false || typeof document.content !== 'string') throw new Error('copy_unavailable');
  await write(document.content);
}

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])';

export function trapPreviewKey(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>, dialog: HTMLDialogElement): void {
  if (event.key !== 'Tab') return;
  const controls = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(element => element.getClientRects().length > 0);
  const first = controls[0], last = controls.at(-1);
  if (!first || !last) { event.preventDefault(); dialog.focus(); return; }
  const active = dialog.ownerDocument.activeElement;
  if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
}

export function mountPreviewDialog(dialog: HTMLDialogElement, returnFocus?: HTMLElement | null): () => void {
  if (!dialog.open) dialog.showModal();
  dialog.querySelector<HTMLElement>('button')?.focus();
  return () => {
    if (dialog.open) dialog.close();
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
  };
}
