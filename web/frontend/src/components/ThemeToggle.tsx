/** Appearance shortcut; selection lives in the settings page. */
export default function ThemeToggle({ onClick }: { onClick?: () => void }) {
  return (
    <button type="button" className="theme-toggle" aria-label="打开主题设置" title="主题设置" onClick={onClick}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 0 0 0 16Z" />
      </svg>
    </button>
  );
}
