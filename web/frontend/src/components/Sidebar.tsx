import type { RefObject } from 'react';
import { IconArchive } from './icons';
import { NAV_GROUPS } from './navigation';
import SidebarAccount from './SidebarAccount';

export type Page = 'dashboard' | 'chat' | 'trends' | 'ideas' | 'calendar' | 'publish' | 'breakdown' | 'skills' | 'outputs' | 'accounts' | 'profile';

interface SidebarProps {
  currentPage: Page;
  onPageChange: (page: Page) => void;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  runtimeDetail?: string;
  onOpenSettings: () => void;
  onOpenAccount: () => void;
  settingsOpen?: boolean;
  historyOpen?: boolean;
  historyToggleRef?: RefObject<HTMLButtonElement | null>;
  onToggleHistory?: () => void;
}


export default function Sidebar({
  currentPage,
  onPageChange,
  gatewayStatus,
  apiDirect,
  agentRuntime,
  runtimeDetail = '',
  onOpenSettings,
  onOpenAccount,
  settingsOpen = false,
  historyOpen = false,
  historyToggleRef,
  onToggleHistory,
}: SidebarProps) {
  return (
    <aside className="sidebar workspace-toolrail" aria-label="主导航">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <h1>Easel</h1>
          <span className="sidebar-edition">创作工作室</span>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="功能工具栏">
        {NAV_GROUPS.map((group) => <div className="nav-group" key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map(({ page, Icon, label }) => (
          <button
            key={page}
            className={`nav-item ${!settingsOpen && currentPage === page ? 'active' : ''}`}
            aria-current={!settingsOpen && currentPage === page ? 'page' : undefined}
            aria-label={label}
            title={label}
            onClick={() => onPageChange(page)}
          >
            <span className="nav-icon"><Icon size={18} /></span>
            <span className="nav-label">{label}</span>
          </button>
          ))}
        </div>)}
      </nav>

      {currentPage === 'chat' && !settingsOpen && <button
        ref={historyToggleRef} type="button" className="workspace-history-toggle"
        aria-label="对话历史" title="对话历史" aria-expanded={historyOpen}
        aria-controls="session-history" onClick={onToggleHistory}>
        <IconArchive size={18} /><span>历史</span>
      </button>}

      <div className="sidebar-footer">
        <SidebarAccount onOpenAccount={onOpenAccount} onOpenSettings={onOpenSettings} settingsOpen={settingsOpen}
          runtime={{ name: agentRuntime, connected: gatewayStatus === 'connected', detail: runtimeDetail || (gatewayStatus === 'connected'
          ? (apiDirect ? '仅聊天模式已配置' : agentRuntime === 'pi' ? '内置 Pi 供应商已配置' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 已安装` : '网关已连接')
          : gatewayStatus === 'disconnected'
            ? (apiDirect ? 'API 配置不完整' : agentRuntime === 'pi' ? '内置 Pi 配置待完善' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 未配置` : '网关离线')
            : '连接中…') }} />
      </div>
    </aside>
  );
}
