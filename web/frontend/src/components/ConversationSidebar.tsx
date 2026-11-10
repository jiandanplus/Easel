import { useEffect, useRef, useState } from 'react';
import type { ChatSession } from '../lib/store';
import {
  IconNewChat, IconEdit, IconArchive, IconUnarchive, IconTrash, IconChevron,
} from './icons';

interface ConversationSidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onSessionDelete: (id: string) => void;
  onSessionRename: (id: string, title: string) => void;
  onSessionArchive: (id: string, archived: boolean) => void;
  onNewChat: () => void;
  mobileOpen: boolean;
  onClose: () => void;
}

interface RenameDraft {
  id: string;
  value: string;
}

export default function ConversationSidebar({
  sessions,
  activeSessionId,
  onSessionSelect,
  onSessionDelete,
  onSessionRename,
  onSessionArchive,
  onNewChat,
  mobileOpen,
  onClose,
}: ConversationSidebarProps) {
  const [editing, setEditing] = useState<RenameDraft | null>(null);
  const renameDraft = useRef<RenameDraft | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (mobileOpen) closeButtonRef.current?.focus();
  }, [mobileOpen]);

  const startRename = (session: ChatSession) => {
    const draft = { id: session.id, value: session.title };
    renameDraft.current = draft;
    setEditing(draft);
  };

  const finishRename = (id: string, commit: boolean) => {
    const draft = renameDraft.current;
    if (!draft || draft.id !== id) return;
    // Settle synchronously: unmount/blur can still invoke the old input handler.
    renameDraft.current = null;
    setEditing(null);
    if (commit) onSessionRename(draft.id, draft.value);
  };

  const selectSession = (id: string) => {
    onSessionSelect(id);
    onClose();
  };

  const active = sessions.filter(session => !session.archived && (session.messages.length > 0 || session.hasDraft || session.id === activeSessionId));
  const archived = sessions.filter(session => session.archived);

  const renderItem = (session: ChatSession, isArchived: boolean) => {
    if (editing?.id === session.id) {
      return <div key={session.id} className="session-item">
        <input
          className="session-rename-input"
          aria-label={`重命名对话：${session.title}`}
          value={editing.value}
          autoFocus
          onClick={event => event.stopPropagation()}
          onChange={event => {
            if (renameDraft.current?.id !== session.id) return;
            const draft = { id: session.id, value: event.target.value };
            renameDraft.current = draft;
            setEditing(draft);
          }}
          onKeyDown={event => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === 'Enter' || event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              finishRename(session.id, event.key === 'Enter');
            }
          }}
          onBlur={() => finishRename(session.id, true)}
        />
      </div>;
    }

    return <div key={session.id}
      className={`session-item ${session.id === activeSessionId ? 'active' : ''}`}
      onClick={() => selectSession(session.id)}>
      <button type="button" className="session-item-title session-select" title={session.title}
        aria-current={session.id === activeSessionId ? 'true' : undefined}
        onClick={event => { event.stopPropagation(); selectSession(session.id); }}>{session.title}</button>
      <div className="session-actions">
        <button type="button" className="session-act" title="重命名" aria-label={`重命名：${session.title}`}
          onClick={event => { event.stopPropagation(); startRename(session); }}><IconEdit size={14} /></button>
        <button type="button" className="session-act" title={isArchived ? '取消归档' : '归档'}
          aria-label={`${isArchived ? '取消归档' : '归档'}：${session.title}`}
          onClick={event => { event.stopPropagation(); onSessionArchive(session.id, !isArchived); }}>
          {isArchived ? <IconUnarchive size={14} /> : <IconArchive size={14} />}
        </button>
        <button type="button" className="session-act danger" title="删除" aria-label={`删除：${session.title}`}
          onClick={event => { event.stopPropagation(); onSessionDelete(session.id); }}><IconTrash size={14} /></button>
      </div>
    </div>;
  };

  return <aside id="session-history" className={`conversation-sidebar${mobileOpen ? ' mobile-open' : ''}`}
    aria-label="对话历史" onKeyDown={event => {
      if (event.key === 'Escape' && mobileOpen) {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    }}>
    <header className="conversation-sidebar-header">
      <h2>对话</h2>
      <button type="button" className="new-chat-btn" title="新建对话"
        onClick={() => { onNewChat(); onClose(); }}><IconNewChat size={15} /> 新对话</button>
      <button type="button" ref={closeButtonRef} className="conversation-history-close" title="关闭历史"
        aria-label="关闭历史" onClick={onClose}>×</button>
    </header>
    <div className="conversation-list-scroll">
      {active.length === 0 && <p className="conversation-empty">暂无进行中的对话</p>}
      {active.map(session => renderItem(session, false))}
      {archived.length > 0 && <>
        <button type="button" className="archived-header" aria-expanded={showArchived}
          onClick={() => setShowArchived(value => !value)}>
          <span className={`archived-chevron ${showArchived ? 'open' : ''}`}><IconChevron size={12} /></span>
          已归档 · {archived.length}
        </button>
        {showArchived && archived.map(session => renderItem(session, true))}
      </>}
    </div>
  </aside>;
}
