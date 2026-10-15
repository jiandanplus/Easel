export type ThemeId = 'paper' | 'minimal' | 'forest' | 'pastel' | 'swiss';
export const THEME_KEY = 'easel_theme';
export const THEMES: readonly { id: ThemeId; name: string; description: string; available: boolean }[] = [
  { id: 'paper', name: 'A · 纸感编辑室', description: '暖纸白 · 陶土橙 · 苔绿', available: true },
  { id: 'minimal', name: 'B · 极简效率台', description: '冷白 · 石墨 · 钴蓝', available: true },
  { id: 'forest', name: 'C · 深色创作舱', description: '深绿黑 · 薄荷绿 · 沉浸创作', available: true },
  { id: 'pastel', name: 'D · 柔和彩色工作室', description: '奶油白 · 鼠尾草绿 · 柔彩插画', available: true },
  { id: 'swiss', name: 'E · 瑞士网格系统', description: '黑白 · 信号橙 · 几何网格', available: true },
];
let currentTheme: ThemeId = 'paper';
const subscribers = new Set<() => void>();
export function normalizeTheme(value: unknown): ThemeId {
  return THEMES.find(theme => theme.available && theme.id === value)?.id ?? 'paper';
}
function storage(): Storage | undefined {
  try { return window.localStorage; } catch { return undefined; }
}
function applyTheme(theme: ThemeId) {
  currentTheme = theme;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme === 'forest' ? 'dark' : 'light';
}
function persist(theme: ThemeId) {
  try { storage()?.setItem(THEME_KEY, theme); } catch { /* Keep this visit usable. */ }
}
export function getTheme(): ThemeId { return currentTheme; }
export function subscribeTheme(subscriber: () => void): () => void {
  subscribers.add(subscriber);
  return () => { subscribers.delete(subscriber); };
}
export function setTheme(value: unknown): boolean {
  if (!THEMES.some(theme => theme.id === value && theme.available)) return false;
  const theme = normalizeTheme(value);
  applyTheme(theme);
  persist(theme);
  subscribers.forEach(subscriber => subscriber());
  return true;
}
/** Start once from main.tsx; cleanup removes the cross-tab listener. */
export function initializeTheme(): () => void {
  let saved: unknown;
  try { saved = storage()?.getItem(THEME_KEY); } catch { /* Storage can deny reads. */ }
  const theme = normalizeTheme(saved);
  applyTheme(theme);
  persist(theme);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== THEME_KEY && event.key !== null) return;
    if (event.storageArea && event.storageArea !== storage()) return;
    const next = normalizeTheme(event.newValue);
    applyTheme(next);
    // Repair old values without a write feedback loop between tabs.
    if (event.key === THEME_KEY && event.newValue !== next) persist(next);
    subscribers.forEach(subscriber => subscriber());
  };
  window.addEventListener('storage', onStorage);
  return () => window.removeEventListener('storage', onStorage);
}
