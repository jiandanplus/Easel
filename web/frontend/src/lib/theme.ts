export type ThemeId = 'paper' | 'minimal' | 'forest' | 'pastel' | 'swiss';
export const THEME_KEY = 'easel_theme';
export const APPEARANCE_KEY = 'easel_appearance';
export type AppearanceMode = 'system' | 'light' | 'dark' | 'custom';
interface AppearancePreference { mode: AppearanceMode; theme: ThemeId }
export const THEMES: readonly { id: ThemeId; name: string; description: string; available: boolean }[] = [
  { id: 'paper', name: 'A · 纸感编辑室', description: '暖纸白 · 陶土橙 · 苔绿', available: true },
  { id: 'minimal', name: 'B · 极简效率台', description: '冷白 · 石墨 · 钴蓝', available: true },
  { id: 'forest', name: 'C · 深色创作舱', description: '深绿黑 · 薄荷绿 · 沉浸创作', available: true },
  { id: 'pastel', name: 'D · 柔和彩色工作室', description: '奶油白 · 鼠尾草绿 · 柔彩插画', available: true },
  { id: 'swiss', name: 'E · 瑞士网格系统', description: '黑白 · 信号橙 · 几何网格', available: true },
];
let currentTheme: ThemeId = 'paper';
let preference: AppearancePreference = { mode: 'system', theme: 'paper' };
let systemDark = false;
const subscribers = new Set<() => void>();
export function normalizeTheme(value: unknown): ThemeId {
  return THEMES.find(theme => theme.available && theme.id === value)?.id ?? 'paper';
}
function storage(): Storage | undefined {
  try { return window.localStorage; } catch { return undefined; }
}
function parsePreference(raw: string | null): AppearancePreference {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (value && typeof value === 'object' && 'mode' in value && typeof value.mode === 'string' &&
      ['system', 'light', 'dark', 'custom'].includes(value.mode)) {
      return { mode: value.mode as AppearanceMode, theme: normalizeTheme('theme' in value ? value.theme : undefined) };
    }
  } catch { /* Invalid preferences use the system default. */ }
  return { mode: 'system', theme: 'paper' };
}
function resolvedTheme(): ThemeId {
  if (preference.mode === 'custom') return preference.theme;
  return preference.mode === 'dark' || (preference.mode === 'system' && systemDark) ? 'forest' : 'paper';
}
function applyTheme(theme: ThemeId) {
  currentTheme = theme;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.appearanceMode = preference.mode;
  document.documentElement.style.colorScheme = theme === 'forest' ? 'dark' : 'light';
}
function persist() {
  // One authoritative value prevents two storage events from producing mixed mode/theme state.
  try { storage()?.setItem(APPEARANCE_KEY, JSON.stringify(preference)); } catch { /* Keep this visit usable. */ }
  try { storage()?.setItem(THEME_KEY, currentTheme); } catch { /* Compatibility for older tabs. */ }
}
export function getTheme(): ThemeId { return currentTheme; }
export function getAppearanceMode(): AppearanceMode { return preference.mode; }
export function subscribeTheme(subscriber: () => void): () => void {
  subscribers.add(subscriber);
  return () => { subscribers.delete(subscriber); };
}
export function setTheme(value: unknown): boolean {
  if (!THEMES.some(theme => theme.id === value && theme.available)) return false;
  preference = { mode: 'custom', theme: normalizeTheme(value) };
  applyTheme(resolvedTheme());
  persist();
  subscribers.forEach(subscriber => subscriber());
  return true;
}
export function setAppearanceMode(value: unknown): boolean {
  if (value !== 'system' && value !== 'light' && value !== 'dark') return false;
  preference = { ...preference, mode: value };
  applyTheme(resolvedTheme());
  persist();
  subscribers.forEach(subscriber => subscriber());
  return true;
}
/** Start once from main.tsx; cleanup removes OS and cross-tab listeners. */
export function initializeTheme(): () => void {
  let media: MediaQueryList | undefined;
  try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch { /* Older embedded browsers use day. */ }
  systemDark = media?.matches ?? false;
  let saved: string | null = null;
  try { saved = storage()?.getItem(APPEARANCE_KEY) ?? null; } catch { /* Storage can deny reads. */ }
  preference = parsePreference(saved);
  applyTheme(resolvedTheme());
  persist();
  const onSystemChange = (event: MediaQueryListEvent) => {
    systemDark = event.matches;
    if (preference.mode !== 'system') return;
    applyTheme(resolvedTheme());
    subscribers.forEach(subscriber => subscriber());
  };
  if (media?.addEventListener) media.addEventListener('change', onSystemChange);
  else media?.addListener(onSystemChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== APPEARANCE_KEY && event.key !== null) return;
    if (event.storageArea && event.storageArea !== storage()) return;
    // A queued event can predate this tab's newer selection; storage is authoritative.
    let saved = event.newValue;
    try {
      const currentStorage = storage();
      if (currentStorage) saved = currentStorage.getItem(APPEARANCE_KEY);
    } catch { /* Fall back to the event if reads became unavailable. */ }
    preference = parsePreference(saved);
    applyTheme(resolvedTheme());
    subscribers.forEach(subscriber => subscriber());
  };
  window.addEventListener('storage', onStorage);
  return () => {
    window.removeEventListener('storage', onStorage);
    if (media?.removeEventListener) media.removeEventListener('change', onSystemChange);
    else media?.removeListener(onSystemChange);
  };
}
