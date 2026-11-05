import { useSyncExternalStore } from 'react';
import { getAppearanceMode, getTheme, setAppearanceMode, subscribeTheme } from '../lib/theme';

function AppearanceIcon({ mode }: { mode: 'system' | 'light' | 'dark' }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {mode === 'system' ? <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8M12 17v4" /></>
      : mode === 'dark' ? <path d="M20.8 13.2A9 9 0 0 1 10.8 3.2a9 9 0 1 0 10 10Z" />
        : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>}
  </svg>;
}

export function AppearanceToggle() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, getTheme);
  const mode = useSyncExternalStore(subscribeTheme, getAppearanceMode, getAppearanceMode);
  const night = theme === 'forest';
  const label = night ? '切换到日间模式' : '切换到夜间模式';
  return <button type="button" className="appearance-toggle" aria-label={label}
    title={`${mode === 'system' ? '跟随系统 · ' : ''}当前${night ? '夜间' : '日间'}；${label}（主题 ${night ? 'A' : 'C'}）`}
    onClick={() => setAppearanceMode(night ? 'light' : 'dark')}>
    <AppearanceIcon mode={night ? 'dark' : 'light'} /><span>{night ? '夜间' : '日间'}</span>
  </button>;
}

const MODES = [
  { id: 'system', label: '跟随系统', detail: '自动切换日间与夜间' },
  { id: 'light', label: '日间模式', detail: 'A · 纸感编辑室' },
  { id: 'dark', label: '夜间模式', detail: 'C · 深色创作舱' },
] as const;

export function AppearanceModeSettings() {
  const mode = useSyncExternalStore(subscribeTheme, getAppearanceMode, getAppearanceMode);
  const theme = useSyncExternalStore(subscribeTheme, getTheme, getTheme);
  return <div className="appearance-settings" aria-label="显示模式">
    <div className="appearance-settings-heading"><h4>显示模式</h4><span>默认跟随系统</span></div>
    <div className="appearance-mode-options" role="group" aria-label="选择显示模式">
      {MODES.map(option => <button key={option.id} type="button"
        className={`appearance-mode-option${mode === option.id ? ' selected' : ''}`}
        aria-pressed={mode === option.id} onClick={() => setAppearanceMode(option.id)}>
        <AppearanceIcon mode={option.id} /><span><strong>{option.label}</strong><small>{option.detail}</small></span>
        {mode === option.id && <span className="appearance-mode-check" aria-hidden="true">✓</span>}
      </button>)}
    </div>
    <p className="appearance-mode-description" role="status">
      {mode === 'system' ? `正在跟随系统，当前为${theme === 'forest' ? '夜间模式 · 主题 C' : '日间模式 · 主题 A'}`
        : mode === 'custom' ? '当前使用自选主题'
          : `已固定为${mode === 'dark' ? '夜间模式 · 主题 C' : '日间模式 · 主题 A'}`}
    </p>
  </div>;
}
