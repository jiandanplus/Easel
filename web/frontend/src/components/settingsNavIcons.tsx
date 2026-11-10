// 设置侧栏图标（Lucide 风格线性图标，MIT 路径）。
import type { ReactNode } from 'react';

const Svg = ({ children, size = 16 }: { children: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);

export const NAV_ICONS: Record<string, ReactNode> = {
  profile: <Svg><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></Svg>,
  theme: <Svg><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" /></Svg>,
  shortcuts: <Svg><rect x="2" y="6" width="20" height="12" rx="2" /><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" /></Svg>,
  stats: <Svg><path d="M3 3v18h18" /><path d="M7 16v-4M12 16V8M17 16v-7" /></Svg>,
  plan: <Svg><path d="m12 3 9 5-9 5-9-5 9-5z" /><path d="m3 13 9 5 9-5" /></Svg>,
  usage: <Svg><path d="M21 12A9 9 0 1 1 12 3" /><path d="M21 12h-9V3a9 9 0 0 1 9 9z" /></Svg>,
  balance: <Svg><circle cx="8" cy="8" r="5" /><path d="M18.1 10.4A5 5 0 1 1 10.4 18.1" /><path d="M8 6v4M6 8h4" /></Svg>,
  billing: <Svg><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20" /></Svg>,
  model: <Svg><path d="M12 5a3 3 0 0 0-6 .5A3 3 0 0 0 4 11a3 3 0 0 0 2 5 3 3 0 0 0 6 2V5z" /><path d="M12 5a3 3 0 0 1 6 .5 3 3 0 0 1 2 5.5 3 3 0 0 1-2 5 3 3 0 0 1-6 2" /></Svg>,
  skills: <Svg><path d="m12 3 1.9 5.8L20 10l-5 3.6L16.5 20 12 16.5 7.5 20 9 13.6 4 10l6.1-1.2z" /></Svg>,
  connectors: <Svg><path d="M9 2v6M15 2v6" /><path d="M6 8h12v4a6 6 0 0 1-12 0z" /><path d="M12 18v4" /></Svg>,
  chat: <Svg><path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z" /></Svg>,
  accounts: <Svg><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6" /></Svg>,
  devices: <Svg><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></Svg>,
  env: <Svg><path d="M21 8 12 3 3 8v8l9 5 9-5z" /><path d="m3 8 9 5 9-5M12 13v8" /></Svg>,
  trash: <Svg><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" /></Svg>,
  storage: <Svg><path d="M22 12H2" /><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z" /><path d="M6 16h.01M10 16h.01" /></Svg>,
};
