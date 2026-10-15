import { useSyncExternalStore } from 'react';
import { getTheme, setTheme, subscribeTheme, THEMES } from '../lib/theme';
import paperPreview from '../assets/paper-preview.png';
import minimalPreview from '../assets/minimal-preview.png';
import forestPreview from '../assets/forest-preview.png';
import pastelPreview from '../assets/pastel-preview.png';
import swissPreview from '../assets/swiss-preview.png';

const previews = { paper: paperPreview, minimal: minimalPreview, forest: forestPreview, pastel: pastelPreview, swiss: swissPreview };

export default function ThemeSettings() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, () => 'paper');
  return (
    <section className="settings-theme" aria-labelledby="theme-settings-title">
      <div className="settings-theme-heading">
        <span className="settings-theme-eyebrow">APPEARANCE</span>
        <h3 id="theme-settings-title">主题设置</h3>
        <p>为你的创作空间选择一种氛围。主题仅影响当前浏览器的外观，选择后自动保存。</p>
      </div>
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
              <span className="theme-option-status">{theme === option.id ? '当前使用 ✓' : option.id === 'paper' ? '默认主题' : '点击使用'}</span>
            </span>
            <span className="theme-option-description">{option.description}</span>
          </button>
        ))}
      </div>
      <p className="settings-theme-note">五套主题均已开放。A「纸感编辑室」为默认主题，选择后立即生效并自动保存。</p>
    </section>
  );
}
