export type WorkbenchPage = 'dashboard' | 'chat' | 'trends' | 'ideas' | 'calendar' | 'publish' | 'breakdown' | 'skills' | 'outputs' | 'accounts' | 'profile';
export interface WorkbenchLocation {
  page: WorkbenchPage;
  sessionId?: string;
  artifactId?: string;
  legacyOutputPath?: string;
}
const PAGES = new Set(['dashboard', 'chat', 'trends', 'ideas', 'calendar', 'publish', 'breakdown', 'skills', 'outputs', 'accounts', 'profile']);
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value);
const outputPath = (value: string): boolean => value.length <= 4096 && value.startsWith('outputs/')
  && !value.includes('\\') && !Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
  && !value.split('/').some(part => part === '..' || part === '.');

export function parseWorkbenchHash(hash: string): WorkbenchLocation | null {
  if (!hash || hash === '#' || hash === '#/') return { page: 'dashboard' };
  if (hash.startsWith('#/outputs/')) {
    try {
      const path = decodeURIComponent(hash.slice('#/outputs/'.length));
      const normalized = path.startsWith('outputs/') ? path : `outputs/${path}`;
      return outputPath(normalized) && !path.startsWith('/') ? { page: 'outputs', legacyOutputPath: normalized.slice('outputs/'.length) } : null;
    } catch { return null; }
  }
  const match = hash.match(/^#\/([a-z]+)(?:\?([^#]*))?$/);
  if (!match || !PAGES.has(match[1])) return null;
  const query = new URLSearchParams(match[2] || '');
  if (query.getAll('session').length > 1 || query.getAll('artifact').length > 1) return null;
  const sessionId = query.get('session'), artifactId = query.get('artifact');
  if ((sessionId !== null && !identifier(sessionId)) || (artifactId !== null && !identifier(artifactId))) return null;
  return { page: match[1] as WorkbenchPage, ...(sessionId ? { sessionId } : {}), ...(artifactId ? { artifactId } : {}) };
}

/** New URLs carry only allowlisted pages and opaque identifiers. */
export function hashForWorkbenchLocation(location: WorkbenchLocation): string {
  const page = PAGES.has(location.page) ? location.page : 'dashboard';
  const params = [];
  if (identifier(location.sessionId)) params.push(`session=${encodeURIComponent(location.sessionId)}`);
  if (identifier(location.artifactId)) params.push(`artifact=${encodeURIComponent(location.artifactId)}`);
  return `#/${page}${params.length ? `?${params.join('&')}` : ''}`;
}
