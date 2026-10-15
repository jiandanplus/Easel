import { useRef, useState } from 'react';
import type { ChatSession } from '../lib/store';
import type { PersonaItem } from '../lib/api';
import {
  IconNewChat, IconEdit, IconArchive, IconUnarchive, IconTrash, IconChevron,
} from './icons';
import { IconGear } from './settingsIcons';
import { NAV_GROUPS } from './navigation';

export type Page = 'dashboard' | 'chat' | 'trends' | 'ideas' | 'calendar' | 'publish' | 'breakdown' | 'skills' | 'outputs' | 'accounts' | 'profile';

interface SidebarProps {
  currentPage: Page;
  onPageChange: (page: Page) => void;
  personas: PersonaItem[];
  selectedPersona: string;
  onPersonaChange: (persona: string) => void;
  onNewProfile: () => void;
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onSessionDelete: (id: string) => void;
  onSessionRename: (id: string, title: string) => void;
  onSessionArchive: (id: string, archived: boolean) => void;
  onNewChat: () => void;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  onOpenSettings: () => void;
  settingsOpen?: boolean;
}


export default function Sidebar({
  currentPage,
  onPageChange,
  sessions,
  activeSessionId,
  onSessionSelect,
  onSessionDelete,
  onSessionRename,
  onSessionArchive,
  onNewChat,
  gatewayStatus,
  apiDirect,
  agentRuntime,
  onOpenSettings,
  settingsOpen = false,
}: SidebarProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyToggleRef = useRef<HTMLButtonElement>(null);
  const [showArchived, setShowArchived] = useState(false);

  const startRename = (s: ChatSession) => { setRenamingId(s.id); setRenameValue(s.title); };
  const commitRename = () => {
    if (renamingId) onSessionRename(renamingId, renameValue);
    setRenamingId(null);
  };

  const active = sessions.filter((s) => !s.archived && (s.messages.length > 0 || s.id === activeSessionId));
  const archived = sessions.filter((s) => s.archived);

  const renderItem = (s: ChatSession, isArchived: boolean) => {
    if (renamingId === s.id) {
      return (
        <div key={s.id} className="session-item">
          <input
            className="session-rename-input"
            value={renameValue}
            autoFocus
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              else if (e.key === 'Escape') setRenamingId(null);
            }}
            onBlur={commitRename}
          />
        </div>
      );
    }
    return (
      <div
        key={s.id}
        className={`session-item ${s.id === activeSessionId ? 'active' : ''}`}
        onClick={() => onSessionSelect(s.id)}
      >
        <button className="session-item-title session-select" onClick={(e) => { e.stopPropagation(); onSessionSelect(s.id); setHistoryOpen(false); }}>{s.title}</button>
        <div className="session-actions">
          <button className="session-act" title="重命名"
            onClick={(e) => { e.stopPropagation(); startRename(s); }}><IconEdit size={14} /></button>
          <button className="session-act" title={isArchived ? '取消归档' : '归档'}
            onClick={(e) => { e.stopPropagation(); onSessionArchive(s.id, !isArchived); }}>
            {isArchived ? <IconUnarchive size={14} /> : <IconArchive size={14} />}
          </button>
          <button className="session-act danger" title="删除"
            onClick={(e) => { e.stopPropagation(); onSessionDelete(s.id); }}><IconTrash size={14} /></button>
        </div>
      </div>
    );
  };

  return (
    <aside className="sidebar" aria-label="主导航" onKeyDown={(event) => {
      if (event.key === 'Escape' && historyOpen) {
        setHistoryOpen(false);
        historyToggleRef.current?.focus();
      }
    }}>
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <h1>Easel</h1>
          <span className="sidebar-edition">创作工作室</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV_GROUPS.map((group) => <div className="nav-group" key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map(({ page, Icon, label }) => (
          <button
            key={page}
            className={`nav-item ${!settingsOpen && currentPage === page ? 'active' : ''}`}
            aria-current={!settingsOpen && currentPage === page ? 'page' : undefined}
            aria-label={label}
            onClick={() => onPageChange(page)}
          >
            <span className="nav-icon"><Icon size={18} /></span>
            <span className="nav-label">{label}</span>
          </button>
          ))}
          {group.label === '管理' && <button className={`nav-item ${settingsOpen ? 'active' : ''}`} onClick={onOpenSettings} aria-label="设置" aria-current={settingsOpen ? 'page' : undefined}>
            <span className="nav-icon"><IconGear size={18} /></span><span className="nav-label">设置</span>
          </button>}
        </div>)}
      </nav>

      {currentPage === 'chat' && !settingsOpen && <>
      <button ref={historyToggleRef} className="mobile-history-toggle" aria-expanded={historyOpen}
        aria-controls="session-history" onClick={() => setHistoryOpen(open => !open)}>对话历史</button>
      <div id="session-history" className={`sidebar-section ${historyOpen ? 'mobile-history-open' : ''}`} aria-label="对话历史">
        <button className="mobile-history-close" onClick={() => { setHistoryOpen(false); historyToggleRef.current?.focus(); }}>关闭历史</button>
        <div className="sidebar-section-header">
          <span className="sidebar-section-title">对话</span>
          <button className="new-chat-btn" onClick={() => { onNewChat(); setHistoryOpen(false); }} title="新建对话">
            <IconNewChat size={13} /> 新对话
          </button>
        </div>
        {active.map((s) => renderItem(s, false))}

        {archived.length > 0 && (
          <>
            <button className="archived-header" aria-expanded={showArchived} onClick={() => setShowArchived((v) => !v)}>
              <span className={`archived-chevron ${showArchived ? 'open' : ''}`}><IconChevron size={12} /></span>
              已归档 · {archived.length}
            </button>
            {showArchived && archived.map((s) => renderItem(s, true))}
          </>
        )}
      </div></>}

      <div className="sidebar-status">
        <span className={`status-dot ${gatewayStatus === 'connected' ? '' : 'offline'}`} />
        <span className="sidebar-status-text" title={gatewayStatus === 'connected'
          ? (apiDirect ? '仅聊天模式已配置' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 已安装` : '网关已连接')
          : gatewayStatus === 'disconnected'
            ? (apiDirect ? 'API 配置不完整' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 未配置` : '网关离线')
            : '连接中…'}>本地运行<span className="runtime-name">{agentRuntime}</span></span>
      </div>
    </aside>
  );
}
