import { useSyncExternalStore } from 'react';
import { DAY_THEME, getTheme, setTheme, subscribeTheme, THEMES } from '../lib/theme';
import { AppearanceModeSettings } from './AppearanceControls';
import paperPreview from '../assets/paper-preview.png';
import minimalPreview from '../assets/minimal-preview.png';
import forestPreview from '../assets/forest-preview.png';
import pastelPreview from '../assets/pastel-preview.png';
import swissPreview from '../assets/swiss-preview.png';

const previews = { paper: paperPreview, minimal: minimalPreview, forest: forestPreview, pastel: pastelPreview, swiss: swissPreview };

export default function ThemeSettings() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, () => DAY_THEME);
  return (
    <section className="settings-theme" aria-labelledby="theme-settings-title">
      <div className="settings-theme-heading">
        <span className="settings-theme-eyebrow">APPEARANCE</span>
        <h3 id="theme-settings-title">主题设置</h3>
      </div>
      <AppearanceModeSettings />
      <h4 className="theme-library-heading">主题风格</h4>
      <div className="settings-theme-options" aria-label="可选主题">
        {THEMES.map(option => (
          <button type="button" key={option.id}
            className={`theme-option${theme === option.id ? ' selected' : ''}`}
            disabled={!option.available} aria-pressed={theme === option.id}
            onClick={() => setTheme(option.id)}>
            <span className={`theme-preview theme-preview-${option.id}`} aria-hidden="true">
              <img src={previews[option.id]} alt="" loading="lazy" />
            </span>
            <span className="theme-option-info">
              <span className="theme-option-name">{option.name}</span>
              <span className="theme-option-status">{theme === option.id ? '当前使用 ✓' : option.id === DAY_THEME ? '日间默认' : option.id === 'forest' ? '夜间默认' : '点击使用'}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
