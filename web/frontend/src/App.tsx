import { requestFailureMessage } from './lib/requestFailureCopy';
import { useState, useEffect, useCallback, useRef } from 'react';
import Sidebar from './components/Sidebar';
import ConversationSidebar from './components/ConversationSidebar';
import type { Page } from './components/Sidebar';
import ChatPage from './components/ChatPage';
import SkillPage from './components/SkillPage';
import OutputsPage from './components/OutputsPage';
import AccountsPage from './components/AccountsPage';
import ProfilePage from './components/ProfilePage';
import DashboardPage from './components/DashboardPage';
import TrendsPage from './components/TrendsPage';
import CalendarPage from './components/CalendarPage';
import IdeasPage from './components/IdeasPage';
import PublishPage from './components/PublishPage';
import BreakdownPage from './components/BreakdownPage';
import AppTopbar from './components/AppTopbar';
import OnboardingWizard from './components/OnboardingWizard';
import SettingsPanel from './components/SettingsPanel';
import type { Sec as SettingsSection } from './components/SettingsPanel';
import { fetchStatus, fetchPersonas, streamChat, fetchLastTurn, stopChat, fetchChatTurn, withdrawChatTurn, fetchArtifactReference, subscribeCentralAccountBoundary } from './lib/api';
import type { PersonaItem, UploadedFile, ChatQuestion, ChatTurnSelection, WorkbenchContract, StatusResponse, ChatSubmissionError } from './lib/api';
import { createComposerDraftStore, draftScopeKey } from './lib/composerDrafts';
import type { ComposerSubmission } from './lib/useComposerDraft';
import { parseWorkbenchHash, hashForWorkbenchLocation } from './lib/workbenchNavigation';
import type { DeliverableReference } from './lib/deliverables';
import { deliverableFromUrl, mergeVerifiedArtifacts } from './lib/deliverables';
import type { VerifiedArtifact } from './lib/deliverables';
import { renderMarkdown } from './lib/sanitize';
import { linkifyOutputs } from './lib/linkifyOutputs';
import { snapshotChatModelSelection } from './lib/chatModels';
import { questionStatus } from './lib/api';
import { deleteSession as deleteRemoteSession } from './lib/api';
import {
  loadSessions,
  saveSessions,
  createSession,
  updateSessionTitle,
  loadActiveId,
  saveActiveId,
} from './lib/store';
import type { ChatSession, ChatMessage, StreamState } from './lib/store';

const ONBOARDING_SEEN_KEY = 'easel_onboarding_seen';

function onboardingSeen(): boolean {
  const current = localStorage.getItem(ONBOARDING_SEEN_KEY);
  if (current) return true;
  const previousKey = `${['post', 'craft'].join('')}_onboarding_seen`;
  const previous = localStorage.getItem(previousKey);
  if (previous) {
    localStorage.setItem(ONBOARDING_SEEN_KEY, previous);
    localStorage.removeItem(previousKey);
    return true;
  }
  return false;
}

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>(() => parseWorkbenchHash(window.location.hash)?.page || 'dashboard');
  const [workbench, setWorkbench] = useState<WorkbenchContract>();
  const [workbenchChecking, setWorkbenchChecking] = useState(true);
  const [workbenchError, setWorkbenchError] = useState(false);
  const [draftRefresh, setDraftRefresh] = useState<Record<string, number>>({});
  const [draftStore] = useState(() => {
    try { return createComposerDraftStore(localStorage); } catch { return createComposerDraftStore(); }
  });
  const [initialDraftScope] = useState(() => `pending:${Date.now()}:${Math.random().toString(36).slice(2)}`);
  const composerScope = workbench ? draftScopeKey(workbench.workspace_scope, workbench.source_scope) : initialDraftScope;
  const currentDraftScopeRef = useRef(composerScope);
  const statusSequence = useRef(0);
  const [dashboardSession, setDashboardSession] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem('easel_dashboard_session_v1') || 'null') as { id?: string; source?: string } | null;
      if (saved?.id && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(saved.id) && !loadSessions().some(session => session.id === saved.id))
        return { ...createSession(), id: saved.id, sourceScope: saved.source };
    } catch { /* create an independent draft */ }
    return createSession();
  });
  const [personas, setPersonas] = useState<PersonaItem[]>([]);
  const [selectedPersona, setSelectedPersona] = useState('');
  const [sessions, setSessions] = useState<ChatSession[]>(() => loadSessions());
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [gatewayStatus, setGatewayStatus] = useState('connecting');
  const [apiDirect, setApiDirect] = useState(false);
  const [agentRuntime, setAgentRuntime] = useState('openclaw');
  const [runtimeDetail, setRuntimeDetail] = useState('');
  const [showRecommend, setShowRecommend] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyToggleRef = useRef<HTMLButtonElement>(null);
  const closeHistory = useCallback(() => {
    if (historyOpen) {
      setHistoryOpen(false);
      historyToggleRef.current?.focus();
    }
  }, [historyOpen]);
  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 760px)');
    const handleResize = () => { if (!narrow.matches) setHistoryOpen(false); };
    narrow.addEventListener('change', handleResize);
    return () => narrow.removeEventListener('change', handleResize);
  }, []);
  const [settingsSectionRequest, setSettingsSectionRequest] = useState(0);
  const [settingsSection, setSettingsSection] = useState<'model' | 'theme' | 'account' | SettingsSection>('model');
  // 装完还没配模型时，首开要把用户直接带到模型设置页（安装脚本不再在终端问 Key）
  const [needModelSetup, setNeedModelSetup] = useState(false);

  const [outputsJump, setOutputsJump] = useState('');
  const navigationOrigin = useRef({ page: currentPage, sessionId: activeSessionId });
  navigationOrigin.current = { page: currentPage, sessionId: activeSessionId };
  useEffect(() => {
    const onHash = () => {
      const location = parseWorkbenchHash(window.location.hash);
      if (!location) return;
      setCurrentPage(location.page);
      if (location.sessionId && loadSessions().some(session => session.id === location.sessionId)) setActiveSessionId(location.sessionId);
      if (location.legacyOutputPath) {
        setOutputsJump(location.legacyOutputPath);
        const id = `file-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        try {
          sessionStorage.setItem(`easel_artifact_location:${id}`, JSON.stringify({ path: location.legacyOutputPath,
            originPage: navigationOrigin.current.page, originSessionId: navigationOrigin.current.sessionId }));
          window.history.replaceState(null, '', hashForWorkbenchLocation({ page: 'outputs', artifactId: id }));
        } catch { /* Keep the original compatible reference. */ }
      }
      if (location.artifactId) {
        try {
          const value = JSON.parse(sessionStorage.getItem(`easel_artifact_location:${location.artifactId}`) || 'null') as { path?: string } | null;
          if (value?.path && !value.path.startsWith('/') && !value.path.includes('\\') && !value.path.split('/').some(part => part.startsWith('.') || part.startsWith('_'))) setOutputsJump(value.path);
        } catch { /* invalid or expired reference */ }
      }
    };
    window.addEventListener('hashchange', onHash);
    onHash();
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const clearOutputsJump = useCallback(() => setOutputsJump(''), []);
  useEffect(() => {
    if (currentPage === 'chat' && !activeSessionId) return;
    const location = parseWorkbenchHash(window.location.hash);
    if (location?.page === currentPage && (currentPage !== 'chat' || location.sessionId === activeSessionId)) return;
    const hash = hashForWorkbenchLocation({ page: currentPage, ...(currentPage === 'chat' && activeSessionId ? { sessionId: activeSessionId } : {}) });
    if (window.location.hash !== hash) window.location.hash = hash;
  }, [currentPage, activeSessionId]);

  // 挂载时决定进哪个会话。规则：
  //  - 同一标签刷新（sessionStorage 记着本标签的会话）→ 直接续上（同标签不算冲突）。
  //  - 新开标签/窗口 → 若「上次活跃会话」正被另一个存活标签占用（跨标签 BroadcastChannel 探测），
  //    则开一个新会话，避免两个窗口撞同一会话 → openclaw 并发 takeover 崩溃（后端还有 flock 兜底）。
  //  - 否则续上上次会话（保留「关页重开续接」的体验）。
  const TAB_SESSION_KEY = 'easel_tab_session';
  useEffect(() => {
    const existing = loadSessions();
    let ch: BroadcastChannel | null = null;
    try { ch = new BroadcastChannel('easel-session'); } catch { ch = null; }

    const settle = (id: string, sess: ChatSession[]) => {
      setSessions(sess);
      setActiveSessionId(id);
      const s = sess.find((x) => x.id === id);
      if (s) setSelectedPersona(s.persona || '');
      try { sessionStorage.setItem(TAB_SESSION_KEY, id); } catch { /* ignore */ }
      ch?.postMessage({ type: 'claim', sessionId: id });
    };
    const openNew = (sess: ChatSession[]) => {
      const ns = createSession();
      const updated = [ns, ...sess];
      saveSessions(updated);
      settle(ns.id, updated);
    };

    // 持久监听：别的标签问「谁在用会话 X」时，若正是本标签当前会话就应答 owned
    const onMsg = (e: MessageEvent) => {
      const d = e.data as { type?: string; sessionId?: string } | null;
      if (d?.type === 'query' && d.sessionId && d.sessionId === activeIdRef.current) {
        ch?.postMessage({ type: 'owned', sessionId: d.sessionId });
      }
    };
    ch?.addEventListener('message', onMsg);

    // 1) 本标签刷新：续本标签原会话
    let tabOwn: string | null = null;
    try { tabOwn = sessionStorage.getItem(TAB_SESSION_KEY); } catch { tabOwn = null; }
    const requested = parseWorkbenchHash(window.location.hash)?.sessionId;
    if (tabOwn && (!requested || requested === tabOwn) && existing.find((s) => s.id === tabOwn)) {
      settle(tabOwn, existing);
      return () => { ch?.removeEventListener('message', onMsg); ch?.close(); };
    }

    // 2) 新标签：候选=上次活跃会话；先跨标签问有没有别的活标签占着它
    const lastId = requested || loadActiveId();
    const candidate = lastId && existing.find((s) => s.id === lastId) ? lastId : null;
    if (candidate && ch) {
      let taken = false;
      const probe = (e: MessageEvent) => {
        const d = e.data as { type?: string; sessionId?: string } | null;
        if (d?.type === 'owned' && d.sessionId === candidate) taken = true;
      };
      ch.addEventListener('message', probe);
      ch.postMessage({ type: 'query', sessionId: candidate });
      const t = setTimeout(() => {
        ch?.removeEventListener('message', probe);
        if (taken) openNew(existing);      // 另一个窗口在用 → 开新会话
        else settle(candidate, existing);  // 没人占 → 续上
      }, 250);
      return () => { clearTimeout(t); ch?.removeEventListener('message', probe); ch?.removeEventListener('message', onMsg); ch?.close(); };
    }

    // 3) 无候选 / 不支持 BroadcastChannel：退化为原逻辑（复用空会话或新建；后端 flock 兜底防崩）
    if (candidate) {
      settle(candidate, existing);
    } else {
      const empty = existing.find((s) => s.messages.length === 0);
      if (empty) settle(empty.id, existing);
      else openNew(existing);
    }
    return () => { ch?.removeEventListener('message', onMsg); ch?.close(); };
  }, []);

  // 持久化当前活跃会话 id，重开网页据此续接上次对话（修复"今天再问就忘了"）。
  // 仅在非空时写：避免挂载首刷 activeSessionId 尚为 null 时误清掉已存的 id。
  // 同时更新本标签的 sessionStorage 标记：手动切会话/新建后刷新本标签仍续在正确会话上。
  useEffect(() => {
    if (activeSessionId) {
      saveActiveId(activeSessionId);
      try { sessionStorage.setItem(TAB_SESSION_KEY, activeSessionId); } catch { /* ignore */ }
    }
  }, [activeSessionId]);

  const applyStatus = useCallback((data: StatusResponse) => {
    setPersonas(data.personas || []);
    setGatewayStatus(data.gateway ? 'connected' : 'disconnected');
    setApiDirect(data.transport === 'api');
    setAgentRuntime(data.runtime || 'openclaw');
    setRuntimeDetail(data.runtime_detail || '');
    if (data.workbench && typeof data.workbench.workspace_scope === 'string' && typeof data.workbench.source_scope === 'string') {
      const nextScope = draftScopeKey(data.workbench.workspace_scope, data.workbench.source_scope);
      if (currentDraftScopeRef.current === initialDraftScope) {
        for (const session of [...loadSessions(), dashboardSession]) {
          const pending = draftStore.load(initialDraftScope, session.id);
          const saved = draftStore.load(nextScope, session.id);
          if ((pending.text || pending.attachments.length) && !saved.text && !saved.attachments.length)
            draftStore.update(nextScope, session.id, { text: pending.text, attachments: pending.attachments });
        }
      }
      currentDraftScopeRef.current = nextScope;
      setWorkbench(data.workbench);
      setDashboardSession(previous => {
        const value = previous.sourceScope && previous.sourceScope !== data.workbench!.source_scope ? createSession() : previous;
        const next = { ...value, sourceScope: data.workbench!.source_scope };
        try { sessionStorage.setItem('easel_dashboard_session_v1', JSON.stringify({ id: next.id, source: next.sourceScope })); } catch { /* memory remains */ }
        return next;
      });
    } else setWorkbench(undefined);
    setWorkbenchChecking(false); setWorkbenchError(false);
  }, [dashboardSession, draftStore, initialDraftScope]);
  const applyStatusRef = useRef(applyStatus); applyStatusRef.current = applyStatus;
  const refreshWorkbench = useCallback((invalidate = true) => {
    const sequence = ++statusSequence.current;
    if (invalidate) setWorkbenchChecking(true);
    void fetchStatus().then(data => { if (sequence === statusSequence.current) applyStatusRef.current(data); })
      .catch(() => { if (sequence === statusSequence.current) { setWorkbenchChecking(false); setWorkbenchError(true); setGatewayStatus('disconnected'); } });
  }, []);
  useEffect(() => {
    const invalidate = () => refreshWorkbench(true);
    const focus = () => refreshWorkbench(false);
    const unsubscribe = subscribeCentralAccountBoundary(invalidate);
    window.addEventListener('focus', focus);
    return () => { unsubscribe(); window.removeEventListener('focus', focus); };
  }, [refreshWorkbench]);

  // Fetch status on mount — 真实反映 gateway 状态 + 首次引导检测
  useEffect(() => {
    const sequence = ++statusSequence.current;
    // 先问 bootstrap：模型没配好时，画像推荐弹窗要让位 —— 一次只问用户一件事，
    // 而且画像配得再好，没有模型也对话不了。
    const bootstrap = fetch('./api/settings/bootstrap')
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);

    Promise.all([fetchStatus(), bootstrap])
      .then(([data, boot]) => {
        if (sequence !== statusSequence.current) return;
        applyStatusRef.current(data);
        const unconfigured = !!boot && boot.modelConfigured === false;
        if (unconfigured) {
          // 直接打开设置面板（它默认就停在「模型配置 · 对话」页）
          setNeedModelSetup(true);
          setSettingsOpen(true);
          return;
        }
        // 首次使用：没有任何个性化画像 且 未看过引导 → 推荐配置
        if (data.gateway === true && (data.personas || []).length === 0 && !onboardingSeen()) {
          setShowRecommend(true);
        }
      })
      .catch(() => {
        if (sequence !== statusSequence.current) return;
        setGatewayStatus('disconnected');
        setRuntimeDetail('');
        setWorkbenchChecking(false); setWorkbenchError(true);
      });
  }, []);

  const activeSession = sessions.find((s) => s.id === activeSessionId) || null;

  // 最新 sessions 的 ref，供回调里读取而不必进依赖数组（避免闭包过期/频繁重建）
  const sessionsRef = useRef(sessions);
  useEffect(() => { sessionsRef.current = sessions; }, [sessions]);

  // 当前活跃会话 id 的 ref：供跨标签「谁在用会话 X」查询时即时应答（见挂载 effect）
  const activeIdRef = useRef<string | null>(activeSessionId);
  useEffect(() => { activeIdRef.current = activeSessionId; }, [activeSessionId]);

  // ---- 流式对话：状态与生命周期都放在 App（永不卸载），切页/切 ChatPage 都不中断/丢失 ----
  const [streams, setStreams] = useState<Record<string, StreamState>>({});
  const streamCtl = useRef<Record<string, AbortController>>({});
  const resendChecks = useRef<Set<string>>(new Set());
  // Per-session choices stay in memory; account, settings and saved history are untouched.
  const modelSelections = useRef<Record<string, { selection: ChatTurnSelection; sourceRevision: string }>>({});
  useEffect(() => { modelSelections.current = {}; }, [workbench?.source_scope]);
  const [resendNotice, setResendNotice] = useState<{ sessionId: string; message: string; blocked: boolean } | null>(null);
  const streamAcc = useRef<Record<string, { content: string; thinking: string; steps: string[]; questions: ChatQuestion[]; centralRecoveryTurnId?: string; turnId?: string; verifiedArtifacts?: VerifiedArtifact[] }>>({});
  const answeredRef = useRef<Set<string>>(new Set());   // 已提交答案的 question id：重放/恢复不再重现
  // ---- 打字机：分批到达的 token 按节奏吐给界面 ----
  const typingBuf = useRef<Record<string, string>>({});
  const typingTimer = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const TYPING_INTERVAL = 12;          // 每 tick 间隔 ms（短回复约 83 字/秒：快且有逐字感）

  const pumpTyping = (sessionId: string) => {
    const buf = typingBuf.current[sessionId] || '';
    if (!buf) { clearTyping(sessionId); return; }
    // 动态步长：<80 字逐字吐（83字/秒），每满 80 字每 tick 多吐 1 字，长文更快
    const step = Math.max(1, Math.floor(buf.length / 80));
    const take = buf.slice(0, step);
    typingBuf.current[sessionId] = buf.slice(step);
    const a = streamAcc.current[sessionId]; if (!a) { clearTyping(sessionId); return; }
    a.content += take;
    setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], content: a.content } } : p));
  };
  const startTypingPump = (sessionId: string) => {
    if (typingTimer.current[sessionId]) return;
    typingTimer.current[sessionId] = setInterval(() => pumpTyping(sessionId), TYPING_INTERVAL);
  };
  const ensureTypingPump = (sessionId: string) => {
    if (!typingTimer.current[sessionId]) startTypingPump(sessionId);
  };
  const clearTyping = (sessionId: string) => {
    const t = typingTimer.current[sessionId];
    if (t) { clearInterval(t); delete typingTimer.current[sessionId]; }
  };
  /** 收尾冲刷：把剩余队列立刻吐完（避免结束瞬间内容被截断）。 */
  const flushTyping = (sessionId: string) => {
    clearTyping(sessionId);
    const buf = typingBuf.current[sessionId] || '';
    if (!buf) return;
    typingBuf.current[sessionId] = '';
    const a = streamAcc.current[sessionId]; if (!a) return;
    a.content += buf;
    setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], content: a.content } } : p));
  };

  const appendAssistant = useCallback((sessionId: string, msg: ChatMessage, sessionKey?: string) => {
    setSessions((prev) => {
      const next = prev.map((s) =>
        s.id === sessionId
          ? { ...s, messages: [...s.messages, msg], sessionKey: sessionKey || s.sessionKey, pendingTurnId: undefined, pendingVerifiedArtifacts: undefined }
          : s);
      saveSessions(next);
      return next;
    });
  }, []);

  const clearStream = useCallback((sessionId: string) => {
    // 打字机收尾：剩余队列立刻吐出，避免结束瞬间内容被截断
    flushTyping(sessionId);
    delete streamCtl.current[sessionId];
    delete streamAcc.current[sessionId];
    setStreams((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
  }, []);

  // 启动一次流式（fetch + 累积 + 回调）——只管流，不动消息列表
  const startStream = useCallback((
    sessionId: string,
    text: string,
    persona: string | undefined,
    attachments: UploadedFile[] = [],
    selection?: ChatTurnSelection,
    submission?: ComposerSubmission,
  ) => {
    const turnSelection = selection ? snapshotChatModelSelection(selection) : undefined;
    const turnId = submission?.turnId || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try { sessionStorage.setItem(`easel_pending_turn:${sessionId}`, turnId); } catch { /* ignore */ }
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === sessionId ? { ...s, pendingTurnId: turnId, pendingVerifiedArtifacts: undefined } : s));
      saveSessions(next); return next;
    });
    streamAcc.current[sessionId] = { content: '', thinking: '', steps: [], questions: [], turnId, verifiedArtifacts: [] };
    setStreams((prev) => ({ ...prev, [sessionId]: { content: '', thinking: '', activity: '', questions: [], phase: 'submitting', turnId, verifiedArtifacts: [] } }));
    // 打字机队列：流式事件按批到达（OpenClaw 攒批），前端按字符节奏显示，体验逐字浮现。
    typingBuf.current[sessionId] = '';
    startTypingPump(sessionId);
    streamCtl.current[sessionId] = streamChat(
      text, persona, sessionId,
      (chunk) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        // 不直接追加 content——进打字机队列，pump 按节奏吐出（切会话不中断，队列归属 sessionId）
        typingBuf.current[sessionId] = (typingBuf.current[sessionId] || '') + chunk;
        ensureTypingPump(sessionId);
      },
      (sessionKey, completion) => {
        // done 不能立即 flush——token 和 done 几乎同时到达（OpenClaw 攒批），
        // 立即 flush 会把整包瞬间冲出，打字机白做。等队列吐完再落盘。
        const waitAndFinalize = () => {
          if (typingBuf.current[sessionId]) {
            setTimeout(waitAndFinalize, 60);
            return;
          }
          const a = streamAcc.current[sessionId];
          appendAssistant(sessionId, {
            role: 'assistant', content: a?.content || '',
            thinking: a?.thinking || undefined, activity: a?.steps.join('\n') || undefined,
            centralRecoveryTurnId: a?.centralRecoveryTurnId,
            taskState: completion?.state,
            turnId, verifiedArtifacts: a?.verifiedArtifacts,
          }, sessionKey);
          clearStream(sessionId);
          try { sessionStorage.removeItem(`easel_pending_turn:${sessionId}`); } catch { /* ignore */ }
        };
        waitAndFinalize();
      },
      (err) => {
        const rejection = (err as ChatSubmissionError).rejection;
        if (submission && rejection?.not_dispatched === true && rejection.session_id === sessionId && rejection.turn_id === turnId) {
          draftStore.abandonSubmission(submission.scopeKey, sessionId, turnId);
          setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
        }
        const waitAndFinalize = () => {
          if (typingBuf.current[sessionId]) {
            setTimeout(waitAndFinalize, 60);
            return;
          }
          const a = streamAcc.current[sessionId];
          appendAssistant(sessionId, {
            role: 'assistant',
            content: (a?.content ? a.content + '\n\n' : '') + requestFailureMessage(err.message),
            taskState: (err as ChatSubmissionError).completion?.state || (rejection ? 'failed' : 'unknown'),
            thinking: a?.thinking || undefined, activity: a?.steps.join('\n') || undefined,
            centralRecoveryTurnId: a?.centralRecoveryTurnId,
            turnId, verifiedArtifacts: a?.verifiedArtifacts,
          });
          clearStream(sessionId);
          try { sessionStorage.removeItem(`easel_pending_turn:${sessionId}`); } catch { /* ignore */ }
        };
        waitAndFinalize();
      },
      (thinkChunk) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        a.thinking = (a.thinking + thinkChunk).slice(-4000);
        // 有真实思考流 → 清掉防呆提示（不再显示「未卡住」）
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], thinking: a.thinking, stillWorking: undefined } } : p));
      },
      (status) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        // 有真实活动状态 → 清掉防呆提示，让真实状态占据活动行
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status, stillWorking: undefined } } : p));
      },
      // onInterrupted：SSE 被中断（长任务时代理掐断），但后端仍在跑并会落盘完整结果。
      // streamChat 会按 eventId 自动重连并补发遗漏事件；这里只更新用户可见状态。
      () => {
        setStreams((p) => (p[sessionId]
          ? { ...p, [sessionId]: { ...p[sessionId], activity: '⏳ 连接中断，正在自动续接…' } } : p));
      },
      turnId,
      false,
      undefined,
      attachments,
      (q) => {
        // ask_user 问答题：追加进流式状态（去重），ChatPage 渲染为选项卡片
        // 重放可能带已解决/已过期的旧问题，先查状态只留 pending（失败则保留）
        const a = streamAcc.current[sessionId]; if (!a) return;
        if (a.questions.some((x) => x.id === q.id)) return;
        if (answeredRef.current.has(q.id)) return;   // 本会话已答过：不再重现
        void questionStatus([q.id]).then((st) => {
          const s = st[q.id]?.status;
          // 只显示仍 pending 的：answered/expired/cancelled/not_found/unknown 一律过滤
          if (s && s !== 'pending' || answeredRef.current.has(q.id)) return;
          const a2 = streamAcc.current[sessionId]; if (!a2) return;
          if (a2.questions.some((x) => x.id === q.id)) return;
          a2.questions.push(q);
          setStreams((p) => (p[sessionId]
            ? { ...p, [sessionId]: { ...p[sessionId], questions: [...a2.questions] } } : p));
        });
      },
      // onHeartbeat：防呆心跳（30s 静默）。只设独立的「未卡住」提示，绝不写 activity/thinking → 不顶掉真实状态。
      (note) => setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], stillWorking: note } } : p)),
      turnSelection,
      (hint) => {
        const a = streamAcc.current[sessionId];
        if (a && hint.turn_id === turnId) a.centralRecoveryTurnId = turnId;
      },
      (receipt) => {
        if (receipt.version !== 1 || receipt.session_id !== sessionId || receipt.turn_id !== turnId) return;
        if (submission) {
          draftStore.acknowledge(submission.scopeKey, sessionId, turnId, submission.revision);
          setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
        }
        setStreams(previous => previous[sessionId] ? { ...previous, [sessionId]: { ...previous[sessionId], phase: 'running' } } : previous);
        setSessions(previous => {
          const next = previous.map(session => session.id === sessionId ? { ...session, sourceScope: workbench?.source_scope || session.sourceScope } : session);
          saveSessions(next); return next;
        });
      },
      (binding) => {
        if (submission && binding.session_id === sessionId && binding.turn_id === turnId) {
          draftStore.bindSubmission(submission.scopeKey, sessionId, turnId, binding.request_hash);
          setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
        }
      },
      (artifact) => {
        const a = streamAcc.current[sessionId]; if (!a || a.turnId !== turnId) return;
        const artifacts = mergeVerifiedArtifacts(a.verifiedArtifacts, [artifact], sessionId, turnId);
        a.verifiedArtifacts = artifacts;
        setStreams(previous => previous[sessionId] ? { ...previous, [sessionId]: { ...previous[sessionId], verifiedArtifacts: artifacts } } : previous);
        setSessions(previous => {
          const next = previous.map(session => session.id === sessionId && session.pendingTurnId === turnId
            ? { ...session, pendingVerifiedArtifacts: artifacts } : session);
          saveSessions(next); return next;
        });
      },
    );
  }, [appendAssistant, clearStream, draftStore, workbench]);

  // 刷新/重开页面后按 eventId=0 重放当前 job，再继续实时 tail；旧任务无事件日志时退回最终快照。
  const resumePendingTurn = useCallback((sessionId: string) => {
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId]) return;  // 本标签正在跑，不插手
    const s = sessionsRef.current.find((x) => x.id === sessionId);
    const last = s?.messages[s.messages.length - 1];
    if (!last || last.role !== 'user') return;   // 没有悬空的用户消息 = 无需恢复
    const recoveryScope = currentDraftScopeRef.current;
    let turnId = s.pendingTurnId;
    try { turnId = sessionStorage.getItem(`easel_pending_turn:${sessionId}`) || turnId; } catch { /* use persisted id */ }
    const recoveredArtifacts = mergeVerifiedArtifacts([], s?.pendingVerifiedArtifacts, sessionId, turnId);
    streamAcc.current[sessionId] = { content: '', thinking: '', steps: [], questions: [], turnId, verifiedArtifacts: recoveredArtifacts };
    setStreams((p) => ({ ...p, [sessionId]: { content: '', thinking: '', activity: '⏳ 正在接回上一轮结果…', questions: [], turnId, verifiedArtifacts: recoveredArtifacts } }));
    typingBuf.current[sessionId] = '';
    startTypingPump(sessionId);
    if (!turnId) {
      void fetchLastTurn(sessionId).then((r) => {
        if (r.status === 'done') appendAssistant(sessionId, { role: 'assistant', content: r.text || '（无输出）' });
        clearStream(sessionId);
      }).catch(() => clearStream(sessionId));
      return;
    }
    streamCtl.current[sessionId] = streamChat(
      '', undefined, sessionId,
      (chunk) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        typingBuf.current[sessionId] = (typingBuf.current[sessionId] || '') + chunk;
        ensureTypingPump(sessionId);
      },
      (sessionKey, completion) => {
        const waitAndFinalize = () => {
          if (typingBuf.current[sessionId]) {
            setTimeout(waitAndFinalize, 60);
            return;
          }
          const a = streamAcc.current[sessionId];
          appendAssistant(sessionId, {
            role: 'assistant', content: a?.content || '（无输出）',
            thinking: a?.thinking || undefined, activity: a?.steps.join('\n') || undefined,
            centralRecoveryTurnId: a?.centralRecoveryTurnId,
            taskState: completion?.state,
            turnId, verifiedArtifacts: a?.verifiedArtifacts,
          }, sessionKey);
          clearStream(sessionId);
          try { sessionStorage.removeItem(`easel_pending_turn:${sessionId}`); } catch { /* ignore */ }
        };
        waitAndFinalize();
      },
      (err) => {
        const waitAndFinalize = () => {
          if (typingBuf.current[sessionId]) {
            setTimeout(waitAndFinalize, 60);
            return;
          }
          const a = streamAcc.current[sessionId];
          appendAssistant(sessionId, { role: 'assistant', content: (a?.content || '') + `\n\n${requestFailureMessage(err.message)}`, taskState: (err as ChatSubmissionError).completion?.state || 'unknown',
            centralRecoveryTurnId: a?.centralRecoveryTurnId, turnId, verifiedArtifacts: a?.verifiedArtifacts });
          clearStream(sessionId);
        };
        waitAndFinalize();
      },
      (chunk) => { const a = streamAcc.current[sessionId]; if (a) a.thinking = (a.thinking + chunk).slice(-4000); },
      (status) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status } } : p));
      },
      () => setStreams((p) => (p[sessionId]
        ? { ...p, [sessionId]: { ...p[sessionId], activity: '⏳ 正在自动续接…' } } : p)),
      turnId,
      true,
      () => {
        // The event log may disappear after a backend restart. Prefer the
        // completed per-session snapshot; otherwise terminate stale recovery.
        void fetchLastTurn(sessionId, turnId).then((r) => {
          if (r.status === 'done') {
            appendAssistant(sessionId, { role: 'assistant', content: r.text || '（无输出）',
              turnId, verifiedArtifacts: mergeVerifiedArtifacts(streamAcc.current[sessionId]?.verifiedArtifacts,
                r.turn_id === turnId ? r.artifacts : [], sessionId, turnId),
              centralRecoveryTurnId: r.turn_id === turnId && r.central_recovery?.version === 1
                && r.central_recovery.turn_id === turnId && r.route?.service_source === 'subscription'
                && r.route?.subscription_backend === 'central' ? turnId : undefined });
          } else {
            appendAssistant(sessionId, {
              role: 'assistant',
              content: '上一轮任务记录暂不可确认，原消息已保留。请查询原请求状态；发起独立任务请新建对话。',
              turnId, verifiedArtifacts: streamAcc.current[sessionId]?.verifiedArtifacts,
            });
          }
          clearStream(sessionId);
          try { sessionStorage.removeItem(`easel_pending_turn:${sessionId}`); } catch { /* ignore */ }
        }).catch(() => {
          appendAssistant(sessionId, {
            role: 'assistant', content: '上一轮任务记录暂不可确认，原消息已保留。请稍后查询原请求状态。',
            turnId, verifiedArtifacts: streamAcc.current[sessionId]?.verifiedArtifacts,
          });
          clearStream(sessionId);
        });
      },
      undefined,   // attachments: 恢复轮次无新附件
      (q) => {
        const a = streamAcc.current[sessionId]; if (!a) return;
        if (a.questions.some((x) => x.id === q.id)) return;
        if (answeredRef.current.has(q.id)) return;   // 本会话已答过：不再重现
        // 重放可能带已解决/已过期的旧问题（gateway 15s 后即清理）——先查状态只留 pending；
        // 查询失败时保留原样（宁显示不丢题）。
        void questionStatus([q.id]).then((st) => {
          const s = st[q.id]?.status;
          // 只显示仍 pending 的：answered/expired/cancelled/not_found/unknown 一律过滤
          //（unknown 通常=问题已从 gateway 清理，即已答或已过期，重放旧事件时不该重现）
          if (s && s !== 'pending' || answeredRef.current.has(q.id)) return;
          const a2 = streamAcc.current[sessionId]; if (!a2) return;
          if (a2.questions.some((x) => x.id === q.id)) return;
          a2.questions.push(q);
          setStreams((p) => (p[sessionId]
            ? { ...p, [sessionId]: { ...p[sessionId], questions: [...a2.questions] } } : p));
        });
      },
      // onHeartbeat：同上，独立的「未卡住」提示，不覆盖 activity/thinking。
      (note) => setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], stillWorking: note } } : p)),
      undefined,
      (hint) => {
        const a = streamAcc.current[sessionId];
        if (a && hint.turn_id === turnId) a.centralRecoveryTurnId = turnId;
      },
      (receipt) => {
        if (receipt.session_id !== sessionId || receipt.turn_id !== turnId || currentDraftScopeRef.current !== recoveryScope) return;
        const submission = draftStore.load(recoveryScope, sessionId).submission;
        if (!submission || submission.clientTurnId !== turnId) return;
        draftStore.acknowledge(recoveryScope, sessionId, turnId!, submission.submittedRevision);
        setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
      },
      undefined, // onRequestPrepared: resuming never creates a new request binding
      (artifact) => {
        const a = streamAcc.current[sessionId]; if (!a || a.turnId !== turnId) return;
        const artifacts = mergeVerifiedArtifacts(a.verifiedArtifacts, [artifact], sessionId, turnId);
        a.verifiedArtifacts = artifacts;
        setStreams(previous => previous[sessionId] ? { ...previous, [sessionId]: { ...previous[sessionId], verifiedArtifacts: artifacts } } : previous);
        setSessions(previous => {
          const next = previous.map(session => session.id === sessionId && session.pendingTurnId === turnId
            ? { ...session, pendingVerifiedArtifacts: artifacts } : session);
          saveSessions(next); return next;
        });
      },
    );
  }, [appendAssistant, clearStream, draftStore]);

  // 活跃会话确定后（含挂载首刷）尝试恢复它悬空的一轮
  useEffect(() => {
    if (activeSessionId && !workbenchChecking && workbench?.version === 1) resumePendingTurn(activeSessionId);
  }, [activeSessionId, resumePendingTurn, workbenchChecking, workbench]);

  const queryOriginalTurn = useCallback(async (sessionId: string, turnId: string) => {
    const scope = currentDraftScopeRef.current;
    setResendNotice({ sessionId, blocked: true, message: '正在查询' });
    try {
      const value = await fetchChatTurn(sessionId, turnId);
      if (scope !== currentDraftScopeRef.current) return;
      const submitted = draftStore.load(scope, sessionId).submission;
      if (value.state === 'rejected' && value.not_dispatched === true && submitted?.clientTurnId === turnId
        && submitted.requestHash && submitted.requestHash === value.request_hash) {
        draftStore.abandonSubmission(scope, sessionId, turnId);
        setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
        setResendNotice({ sessionId, blocked: false, message: '未提交' }); return;
      }
      if (value.accepted && submitted?.clientTurnId === turnId) {
        if (!submitted.requestHash || submitted.requestHash !== value.request_hash) {
          setResendNotice({ sessionId, blocked: true, message: '请求状态待确认' });
          return;
        }
        draftStore.acknowledge(scope, sessionId, turnId, submitted.submittedRevision);
        setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
      }
      const session = sessionsRef.current.find(item => item.id === sessionId);
      if (value.state === 'completed' || value.state === 'stopped' || value.state === 'failed') {
        if (!streamCtl.current[sessionId] && session && (session.pendingTurnId === turnId || submitted?.clientTurnId === turnId)
          && (session.messages.at(-1)?.role === 'user' || session.messages.at(-1)?.taskState === 'unknown'))
          appendAssistant(sessionId, { role: 'assistant', content: value.text || '', taskState: value.state === 'completed' ? 'completed' : value.state });
        setResendNotice({ sessionId, blocked: value.state !== 'completed' || value.clean_end !== true, message: value.state === 'completed' ? '已完成' : value.state === 'stopped' ? '已停止' : '未完成' });
      } else {
        setResendNotice({ sessionId, blocked: true, message: value.accepted ? '正在处理' : '请求状态待确认' });
        if (value.accepted) resumePendingTurn(sessionId);
      }
    } catch { if (scope === currentDraftScopeRef.current) setResendNotice({ sessionId, blocked: true, message: '请求状态待确认' }); }
  }, [draftStore, appendAssistant, resumePendingTurn]);

  // 落用户消息（可选先把 messages 截断到 truncateAt）→ 启动流。retry/edit 都走这里。
  const sendUserAndStream = useCallback((
    sessionId: string,
    displayText: string,
    attachments: UploadedFile[] = [],
    legacyAgentText?: string,
    _truncateAt?: number,
    selection?: ChatTurnSelection,
    submission?: ComposerSubmission,
  ) => {
    const visible = displayText.trim();
    const agentMessage = (legacyAgentText || displayText).trim();
    if ((!agentMessage && attachments.length === 0) || streamCtl.current[sessionId]) return;
    const cur = sessionsRef.current.find((s) => s.id === sessionId);
    const persona = cur?.persona || selectedPersona || undefined;
    setSessions((prev) => {
      const next = prev.map((s) => {
        if (s.id !== sessionId) return s;
        const base = s.messages;
        const updated = {
          ...s,
          messages: [...base, {
            role: 'user',
            content: visible,
            ...(attachments.length ? { attachments } : {}),
            ...(legacyAgentText && legacyAgentText !== visible ? { agentContent: legacyAgentText } : {}),
          } as ChatMessage],
        };
        updateSessionTitle(updated);
        return updated;
      });
      saveSessions(next);
      return next;
    });
    startStream(sessionId, agentMessage, persona, attachments, selection, submission);
  }, [selectedPersona, startStream]);

  const handleSendMessage = useCallback((sessionId: string, displayText: string, attachments?: UploadedFile[], selection?: ChatTurnSelection, submission?: ComposerSubmission) => {
    if (submission && (submission.scopeKey !== currentDraftScopeRef.current || workbenchChecking || workbenchError || workbench?.version !== 1)) return;
    const original = sessionsRef.current.find(session => session.id === sessionId);
    if (original?.sourceScope && original.sourceScope !== workbench?.source_scope) {
      if (submission) { draftStore.abandonSubmission(submission.scopeKey, sessionId, submission.turnId); setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 })); }
      setResendNotice({ sessionId, blocked: true, message: '账号或渠道已变化' });
      return;
    }
    setResendNotice(null);
    sendUserAndStream(sessionId, displayText, attachments, undefined, undefined, selection, submission);
  }, [sendUserAndStream, workbenchChecking, workbenchError, workbench, draftStore]);

  // 重试/编辑重发：从该用户消息处截断（丢弃它及其之后），用 text 重新发起。
  const handleResend = useCallback(async (
    sessionId: string,
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
    selection?: ChatTurnSelection,
  ) => {
    const turnSelection = selection ? snapshotChatModelSelection(selection) : undefined;
    const originalScope = currentDraftScopeRef.current;
    if (streamCtl.current[sessionId] || resendChecks.current.has(sessionId)) return;
    const originalSession = sessionsRef.current.find(session => session.id === sessionId);
    if (!originalSession) return;
    if (originalSession.sourceScope && originalSession.sourceScope !== workbench?.source_scope) {
      setResendNotice({ sessionId, blocked: true, message: '账号或渠道已变化' }); return;
    }
    const originalMessages = originalSession.messages;
    const unchanged = () => {
      const current = sessionsRef.current.find(session => session.id === sessionId);
      return originalScope === currentDraftScopeRef.current && current === originalSession && current.messages === originalMessages;
    };
    const clearObsoleteNotice = () => setResendNotice(previous => previous?.sessionId === sessionId ? null : previous);
    resendChecks.current.add(sessionId);
    setResendNotice({ sessionId, blocked: true, message: '正在核对上一轮状态…' });
    try {
      const last = await fetchLastTurn(sessionId);
      if (!unchanged()) { clearObsoleteNotice(); return; }
      if (streamCtl.current[sessionId]) return;
      const source = last?.route?.service_source;
      const backend = last?.route?.subscription_backend;
      if (!last || last.status !== 'done' || (source !== 'custom' && source !== 'subscription')
        || (source === 'subscription' && backend !== 'central' && backend !== 'legacy')) {
        setResendNotice({ sessionId, blocked: true, message: '无法确认上一轮是否完成，已保留原消息。请先查询原请求状态，不要直接重发。' });
        return;
      }
      const central = source === 'subscription' && backend === 'central';
      if (central && last.clean_end !== true) {
        setResendNotice({ sessionId, blocked: true, message: '上一轮结果或费用尚未确认，已保留原消息。请到设置 → 余额 → 待核算请求查询；不要直接重发原任务。如需发起独立任务，请新建对话。' });
        return;
      }
      setResendNotice(central ? { sessionId, blocked: false, message: '本次重新生成会创建新任务，并按新请求另行计点。' } : null);
      sendUserAndStream(sessionId, displayText, attachments, legacyAgentText, userIndex, turnSelection);
    } catch {
      if (!unchanged()) { clearObsoleteNotice(); return; }
      setResendNotice({ sessionId, blocked: true, message: '无法确认上一轮状态，已保留原消息且没有重新发送。请稍后查询原请求状态。' });
    } finally {
      resendChecks.current.delete(sessionId);
    }
  }, [sendUserAndStream, workbench]);

  // 热点「一键做成内容」：新开会话，把选题作为指令发出去，跳到对话页。
  const handleUseTopic = useCallback((title: string) => {
    const prompt = `围绕当前热点「${title}」：先判断它适不适合我的账号赛道；若合适，给 2-3 个差异化的二创角度，并把你最推荐的那条写成可直接发布的文案初稿。`;
    const ns = createSession(selectedPersona || undefined);
    setSessions((prev) => { const u = [ns, ...prev]; saveSessions(u); return u; });
    setActiveSessionId(ns.id);
    setCurrentPage('chat');
    draftStore.update(composerScope, ns.id, { text: prompt });
  }, [selectedPersona, draftStore, composerScope]);

  const handleStartCreate = useCallback((brief: string) => {
    const ns = createSession(selectedPersona || undefined);
    setSessions(prev => { const next = [ns, ...prev]; saveSessions(next); return next; });
    setActiveSessionId(ns.id);
    setCurrentPage('chat');
    if (brief.trim()) draftStore.update(composerScope, ns.id, { text: brief });
  }, [selectedPersona, draftStore, composerScope]);

  const handleStopStream = useCallback(async (sessionId: string, requestedTurnId?: string) => {
    const turnId = requestedTurnId || sessionsRef.current.find(session => session.id === sessionId)?.pendingTurnId;
    setStreams(previous => previous[sessionId] ? { ...previous, [sessionId]: { ...previous[sessionId], phase: 'stopping' } } : previous);
    let result;
    try { result = await stopChat(sessionId, turnId); }
    catch {
      setStreams(previous => previous[sessionId] ? { ...previous, [sessionId]: { ...previous[sessionId], phase: 'unknown', activity: '停止状态待确认' } } : previous);
      return;
    }
    if (!result.stopped || result.state === 'stopping' || result.state === 'unknown' || result.state === 'completed') return;
    if (!streamAcc.current[sessionId]) return;
    streamCtl.current[sessionId]?.abort();
    flushTyping(sessionId);   // 停止时立刻把队列余字吐完，保证已到内容不丢
    const a = streamAcc.current[sessionId];
    if (a && (a.content || a.thinking || a.steps.length || a.centralRecoveryTurnId || a.verifiedArtifacts?.length)) {
      appendAssistant(sessionId, {
        role: 'assistant',
        content: a.content || '',
        taskState: 'stopped',
        thinking: a.thinking || undefined,
        activity: a.steps.join('\n') || undefined,
        centralRecoveryTurnId: a.centralRecoveryTurnId,
        turnId: a.turnId, verifiedArtifacts: a.verifiedArtifacts,
      });
    }
    clearStream(sessionId);
    // 关键：清掉「本轮进行中」标记，否则下一句被判为「上一条还没跑完」拦下
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === sessionId ? { ...s, pendingTurnId: undefined, pendingVerifiedArtifacts: undefined } : s));
      saveSessions(next);
      return next;
    });
    try { sessionStorage.removeItem(`easel_pending_turn:${sessionId}`); } catch { /* ignore */ }
  }, [appendAssistant, clearStream]);

  const withdrawOriginalTurn = useCallback(async (sessionId: string, turnId: string) => {
    const scope = currentDraftScopeRef.current;
    const submitted = draftStore.load(scope, sessionId).submission;
    if (submitted?.clientTurnId !== turnId || !submitted.requestHash) return;
    setResendNotice({ sessionId, blocked: true, message: '正在撤回' });
    try {
      const value = await withdrawChatTurn(sessionId, turnId, submitted.requestHash);
      if (scope !== currentDraftScopeRef.current || value.request_hash !== submitted.requestHash) return;
      if (value.state === 'rejected' && value.not_dispatched === true) {
        draftStore.abandonSubmission(scope, sessionId, turnId);
        setDraftRefresh(previous => ({ ...previous, [sessionId]: (previous[sessionId] || 0) + 1 }));
        streamCtl.current[sessionId]?.abort(); clearStream(sessionId);
        const session = sessionsRef.current.find(item => item.id === sessionId);
        if (session?.messages.at(-1)?.role === 'user') appendAssistant(sessionId, { role: 'assistant', content: '已撤回' });
        setResendNotice({ sessionId, blocked: false, message: '已撤回' });
      } else if (value.accepted) {
        await queryOriginalTurn(sessionId, turnId);
        if (value.state === 'running' || value.state === 'accepted') await handleStopStream(sessionId, turnId);
      }
    } catch { if (scope === currentDraftScopeRef.current) setResendNotice({ sessionId, blocked: true, message: '撤回状态待确认' }); }
  }, [draftStore, clearStream, appendAssistant, queryOriginalTurn, handleStopStream]);

  const handleSessionRename = useCallback((id: string, title: string) => {
    const t = title.trim();
    if (!t) return;
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, title: t, draftTitle: false } : s));
      saveSessions(next);
      return next;
    });
  }, []);

  const handleSessionArchive = useCallback((id: string, archived: boolean) => {
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, archived } : s));
      saveSessions(next);
      return next;
    });
    // 归档当前激活会话 → 切到另一个未归档会话或新建
    if (archived && id === activeSessionId) {
      const rest = sessionsRef.current.filter((s) => s.id !== id && !s.archived);
      if (rest.length) {
        setActiveSessionId(rest[0].id);
      } else {
        const ns = createSession(selectedPersona || undefined);
        setSessions((prev) => { const u = [ns, ...prev]; saveSessions(u); return u; });
        setActiveSessionId(ns.id);
      }
      setCurrentPage('chat');
    }
  }, [activeSessionId, selectedPersona]);

  const handleNewChat = useCallback(() => {
    const newSession = createSession(selectedPersona || undefined);
    setSessions((prev) => {
      const updated = [newSession, ...prev];
      saveSessions(updated);
      return updated;
    });
    setActiveSessionId(newSession.id);
    setCurrentPage('chat');
  }, [selectedPersona]);

  const handleSessionSelect = useCallback((id: string) => {
    setActiveSessionId(id);
    const target = sessions.find(s => s.id === id);
    if (target) {
      setSelectedPersona(target.persona || '');
    }
    setCurrentPage('chat');
  }, [sessions]);

  const recordDraftState = useCallback((sessionId: string, hasDraft: boolean) => {
    setSessions(previous => {
      const session = previous.find(item => item.id === sessionId);
      if (!session) return previous;
      const automatic = !session.messages.length && (session.draftTitle || session.title === 'New Chat' || session.title === '新对话');
      const text = draftStore.load(currentDraftScopeRef.current, sessionId).text.trim();
      const title = automatic ? (Array.from(text).slice(0, 24).join('') || '新对话') : session.title;
      if (!!session.hasDraft === hasDraft && session.title === title) return previous;
      const next = previous.map(item => item.id === sessionId ? { ...item, hasDraft,
        title, ...(automatic ? { draftTitle: true } : {}),
        sourceScope: item.sourceScope || (hasDraft ? workbench?.source_scope : undefined) } : item);
      saveSessions(next); return next;
    });
  }, [workbench, draftStore]);

  const sourceForOutput = useCallback((path: string) => {
    const sources = sessionsRef.current.filter(session => session.messages.some(message => {
      if (message.role !== 'assistant') return false;
      const document = new DOMParser().parseFromString(renderMarkdown(linkifyOutputs(message.content)), 'text/html');
      return Array.from(document.querySelectorAll('a[href]')).some(link => deliverableFromUrl(link.getAttribute('href') || '')?.path === path);
    }));
    return sources.length === 1 ? sources[0].id : undefined;
  }, []);
  const continueOutput = useCallback(async (reference: DeliverableReference) => {
    const scope = currentDraftScopeRef.current;
    let target = reference.sessionId ? sessionsRef.current.find(session => session.id === reference.sessionId) : undefined;
    if (target?.sourceScope && target.sourceScope !== workbench?.source_scope) target = undefined;
    const session = target || createSession(selectedPersona || undefined);
    try {
      const attachment = await fetchArtifactReference(reference.path, session.id);
      if (scope !== currentDraftScopeRef.current) return;
      const draft = draftStore.load(scope, session.id);
      draftStore.update(scope, session.id, { attachments: [...draft.attachments.filter(item => item.path !== attachment.path), { ...attachment, available: true }] });
      if (!target) setSessions(previous => { const next = [session, ...previous]; saveSessions(next); return next; });
      setDraftRefresh(previous => ({ ...previous, [session.id]: (previous[session.id] || 0) + 1 }));
      setActiveSessionId(session.id); setCurrentPage('chat');
    } catch { setResendNotice({ sessionId: activeIdRef.current || '', blocked: false, message: '文件暂不可用' }); }
  }, [draftStore, selectedPersona, workbench]);

  const handleSessionDelete = useCallback((id: string) => {
    if (!window.confirm('确定删除这条对话？')) return;

    const target = sessionsRef.current.find((s) => s.id === id);
    const wasRunning = Boolean(streamCtl.current[id]);
    const stopped = wasRunning
      ? stopChat(id).catch(() => ({ stopped: false }))
      : Promise.resolve({ stopped: false });
    streamCtl.current[id]?.abort();   // 删除正在流式的会话时中止其流
    clearStream(id);
    try { sessionStorage.removeItem(`easel_pending_turn:${id}`); } catch { /* ignore */ }

    if (target?.sessionKey) {
      // Do not delete OpenClaw's session record while its agent is still
      // writing to it; the stop endpoint waits for backend cleanup first.
      void stopped.then(() => deleteRemoteSession(target.sessionKey as string)).catch(() => {});
    }

    setSessions((prev) => {
      const updated = prev.filter((s) => s.id !== id);
      saveSessions(updated);

      if (id === activeSessionId) {
        if (updated.length > 0) {
          setActiveSessionId(updated[0].id);
        } else {
          const newSession = createSession();
          updated.unshift(newSession);
          saveSessions(updated);
          setActiveSessionId(newSession.id);
        }
      }
      return updated;
    });
  }, [activeSessionId, clearStream]);

  // 首次引导：跳过（用通用模式）
  const dismissRecommend = useCallback(() => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    setShowRecommend(false);
  }, []);

  // 打开引导向导
  const openWizard = useCallback(() => {
    setShowRecommend(false);
    setShowWizard(true);
  }, []);

  // 画像创建完成
  const handleProfileCreated = useCallback((name: string) => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    setShowWizard(false);
    fetchPersonas().then((list) => {
      setPersonas(list);
      setSelectedPersona(name);
      // 用新画像开一个新会话
      const newSession = createSession(name);
      setSessions((prev) => {
        const updated = [newSession, ...prev];
        saveSessions(updated);
        return updated;
      });
      setActiveSessionId(newSession.id);
      setCurrentPage('profile');
    }).catch(() => {});
  }, []);

  // 画像删除完成：刷新列表 + 若删的是当前选中的则清空选择
  const handleProfileDeleted = useCallback((name: string) => {
    fetchPersonas().then((list) => {
      setPersonas(list);
      setSelectedPersona((cur) => (cur === name ? '' : cur));
    }).catch(() => {});
  }, []);

  // 流式生命周期在 App，页面切换随意——ChatPage 可自由卸载/重挂，回来从 props 读流式态即可。
  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return (
          <DashboardPage
            persona={selectedPersona}
            gatewayStatus={gatewayStatus}
            apiDirect={apiDirect}
            agentRuntime={agentRuntime}
            runtimeDetail={runtimeDetail}
            onNavigate={setCurrentPage}
            onUseTopic={handleUseTopic}
            onStartCreate={handleStartCreate}
            composer={<ChatPage apiDirect={apiDirect} composerOnly session={dashboardSession}
              draftScopeKey={composerScope} draftStore={draftStore} draftRefreshKey={draftRefresh[dashboardSession.id] || 0}
              workbench={workbench} workbenchChecking={workbenchChecking} workbenchError={workbenchError}
              modelRefreshKey={settingsSectionRequest * 2 + Number(settingsOpen)}
              onReconnect={refreshWorkbench} onSettings={() => openSettings('model')} onNewChat={handleNewChat}
              initialModelSelection={modelSelections.current[dashboardSession.id]?.selection}
              initialModelSourceRevision={modelSelections.current[dashboardSession.id]?.sourceRevision}
              onRememberModel={(selection, sourceRevision) => {
                if (selection && sourceRevision) modelSelections.current[dashboardSession.id] = { selection, sourceRevision };
                else delete modelSelections.current[dashboardSession.id];
              }}
              onSend={(text, attachments, selection, submission) => {
                if (submission && submission.scopeKey !== currentDraftScopeRef.current) return;
                setSessions(previous => { const next = [dashboardSession, ...previous]; saveSessions(next); return next; });
                setActiveSessionId(dashboardSession.id); setCurrentPage('chat');
                handleSendMessage(dashboardSession.id, text, attachments, selection, submission);
                const next = { ...createSession(selectedPersona || undefined), sourceScope: workbench?.source_scope };
                try { sessionStorage.setItem('easel_dashboard_session_v1', JSON.stringify({ id: next.id, source: next.sourceScope })); } catch { /* memory remains */ }
                setDashboardSession(next);
              }} onStop={() => {}} onResend={() => {}} />}
          />
        );
      case 'chat':
        return activeSession ? (
          <ChatPage
            apiDirect={apiDirect}
            key={`${activeSession.id}:${composerScope}`}
            session={activeSession}
            draftScopeKey={composerScope}
            draftStore={draftStore}
            draftRefreshKey={draftRefresh[activeSession.id] || 0}
            workbench={workbench}
            expectedSourceScope={activeSession.sourceScope}
            workbenchChecking={workbenchChecking}
            workbenchError={workbenchError}
            onReconnect={refreshWorkbench}
            onSettings={() => openSettings('model')}
            onNewChat={handleNewChat}
            onQueryTurn={(turnId) => { void queryOriginalTurn(activeSession.id, turnId); }}
            onWithdrawTurn={turnId => { void withdrawOriginalTurn(activeSession.id, turnId); }}
            onContinue={(reference) => { void continueOutput(reference); }}
            onSource={handleSessionSelect}
            onDraftChange={hasDraft => recordDraftState(activeSession.id, hasDraft)}
            initialModelSelection={modelSelections.current[activeSession.id]?.selection}
            initialModelSourceRevision={modelSelections.current[activeSession.id]?.sourceRevision}
            modelRefreshKey={settingsSectionRequest * 2 + Number(settingsOpen)}
            onRememberModel={(selection, sourceRevision) => {
              if (selection && sourceRevision) modelSelections.current[activeSession.id] = { selection, sourceRevision };
              else delete modelSelections.current[activeSession.id];
            }}
            retryNotice={resendNotice?.sessionId === activeSession.id ? resendNotice : undefined}
            stream={streams[activeSession.id]}
            onSend={(displayText, attachments, selection, submission) => handleSendMessage(activeSession.id, displayText, attachments, selection, submission)}
            onStop={() => handleStopStream(activeSession.id)}
            onResend={(userIndex, displayText, attachments, legacyAgentText, selection) => handleResend(
              activeSession.id, userIndex, displayText, attachments, legacyAgentText, selection,
            )}
            onQuestionAnswered={(qid) => {
              answeredRef.current.add(qid);
              // 已答题从流式状态中移除——切走/切回会话都不再重现（组件内部 state 会在重挂时清零，只藏不移除没用）
              const a = streamAcc.current[activeSession.id];
              if (a) {
                const before = a.questions.length;
                const kept = a.questions.filter((q) => q.id !== qid);
                if (kept.length !== before) {
                  a.questions = kept;
                  setStreams((p) => {
                    const cur = p[activeSession.id];
                    if (!cur) return p;
                    return { ...p, [activeSession.id]: { ...cur, questions: [...kept] } };
                  });
                }
              }
            }}
          />
        ) : null;
      case 'trends':
        return <TrendsPage onUseTopic={handleUseTopic} />;
      case 'ideas':
        return <IdeasPage onUseTopic={handleUseTopic} />;
      case 'calendar':
        return <CalendarPage />;
      case 'publish':
        return <PublishPage persona={selectedPersona} />;
      case 'breakdown':
        return <BreakdownPage persona={selectedPersona} />;
      case 'skills':
        return <SkillPage persona={selectedPersona} />;
      case 'outputs':
        return <OutputsPage jumpPath={outputsJump} onJumpHandled={clearOutputsJump} resolveSource={sourceForOutput}
          onContinue={reference => { void continueOutput(reference); }} onSource={handleSessionSelect}
          onPreviewClose={() => {
            const artifactId = parseWorkbenchHash(window.location.hash)?.artifactId;
            if (!artifactId) return;
            try {
              const origin = JSON.parse(sessionStorage.getItem(`easel_artifact_location:${artifactId}`) || 'null') as { originPage?: Page; originSessionId?: string } | null;
              if (origin?.originPage === 'chat' && origin.originSessionId && sessionsRef.current.some(session => session.id === origin.originSessionId)) {
                setCurrentPage('chat'); setActiveSessionId(origin.originSessionId);
                window.location.hash = hashForWorkbenchLocation({ page: 'chat', sessionId: origin.originSessionId });
                const id = origin.originSessionId;
                requestAnimationFrame(() => document.getElementById(`composer-${id}`)?.focus());
              }
            } catch { /* no source navigation to restore */ }
          }} />;
      case 'accounts':
        return <AccountsPage />;
      case 'profile':
        return <ProfilePage persona={selectedPersona} onNewProfile={() => setShowWizard(true)} onDeleted={handleProfileDeleted} />;
      default:
        return null;
    }
  };

  const handlePersonaChange = useCallback((persona: string) => {
    setSelectedPersona(persona);
    setCurrentPage('chat');
    const cur = sessionsRef.current.find((s) => s.id === activeSessionId);
    if (cur && cur.messages.length === 0) {
      setSessions((prev) => {
        const updated = prev.map((s) =>
          s.id === activeSessionId ? { ...s, persona: persona || undefined } : s);
        saveSessions(updated);
        return updated;
      });
      return;
    }
    const newSession = createSession(persona || undefined);
    setSessions((prev) => {
      const updated = [newSession, ...prev];
      saveSessions(updated);
      return updated;
    });
    setActiveSessionId(newSession.id);
  }, [activeSessionId]);

  const openSettings = (section: 'model' | 'theme' | 'account' | SettingsSection) => {
    setHistoryOpen(false);
    setSettingsSection(section);
    setSettingsSectionRequest(request => request + 1);
    setSettingsOpen(true);
  };

  const closeSettings = () => {
    if (!settingsOpen) return;
    setSettingsOpen(false);
    setNeedModelSetup(false);
    refreshWorkbench();
  };

  const navigateToPage = (page: Page) => {
    setHistoryOpen(false);
    closeSettings();
    setCurrentPage(page);
  };

  // Global shortcuts (listed in 设置 → 快捷键). The ref keeps the handler pointed at the latest state.
  const shortcutActions = useRef({ settings: () => {}, newChat: () => {} });
  shortcutActions.current = { settings: () => openSettings('model'), newChat: () => { closeSettings(); handleNewChat(); } };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey || event.isComposing) return;
      const key = event.key.toLowerCase();
      if (key === ',') { event.preventDefault(); shortcutActions.current.settings(); }
      else if (key === 'n') { event.preventDefault(); shortcutActions.current.newChat(); }
      else if (key === 'k') {
        const input = document.querySelector<HTMLInputElement>('.topbar-search input');
        if (input) { event.preventDefault(); input.focus(); input.select(); }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="app-layout">
      <Sidebar
        currentPage={currentPage}
        onPageChange={navigateToPage}
        gatewayStatus={gatewayStatus}
        apiDirect={apiDirect}
        agentRuntime={agentRuntime}
        runtimeDetail={runtimeDetail}
        onOpenSettings={() => openSettings('model')}
        onOpenAccount={() => openSettings('account')}
        settingsOpen={settingsOpen}
        historyOpen={historyOpen}
        historyToggleRef={historyToggleRef}
        onToggleHistory={() => setHistoryOpen(open => !open)}
      />
      {currentPage === 'chat' && !settingsOpen && <>
        {historyOpen && <button type="button" className="conversation-backdrop"
          aria-label="关闭对话历史" onClick={closeHistory} />}
        <ConversationSidebar
          sessions={sessions} activeSessionId={activeSessionId}
          onSessionSelect={handleSessionSelect} onSessionDelete={handleSessionDelete}
          onSessionRename={handleSessionRename} onSessionArchive={handleSessionArchive}
          onNewChat={handleNewChat} mobileOpen={historyOpen} onClose={closeHistory}
        />
      </>}
      <main className="main-content">
        {/* Settings has its own header; the page top bar would only repeat navigation there. */}
        {!settingsOpen && <AppTopbar currentPage={currentPage} settingsOpen={settingsOpen}
          personas={personas} selectedPersona={selectedPersona}
          onNavigate={navigateToPage}
          onPersonaChange={handlePersonaChange} onNewProfile={() => setShowWizard(true)}
          onAppearance={() => openSettings('theme')} />}
        <div className="page-host">
          <div className="page-view" hidden={settingsOpen}>{renderPage()}</div>
          {settingsOpen && <SettingsPanel initialSection={settingsSection} sectionRequest={settingsSectionRequest}
            banner={needModelSetup ? '配置创作服务' : ''}
            onOpenPage={page => navigateToPage(page)}
            onClose={closeSettings} />}
        </div>
      </main>

      {/* 首次使用：推荐配置画像 */}
      {showRecommend && (
        <div className="overlay">
          <div className="modal" style={{ width: 420, maxWidth: '100%', textAlign: 'center' }}>
            <div style={{ fontSize: 40 }}>👋</div>
            <h2 style={{ margin: '12px 0 8px', fontSize: 20 }}>欢迎使用优创</h2>
            <div style={{ display: 'flex', gap: 10, marginTop: 20, justifyContent: 'center' }}>
              <button className="btn" onClick={dismissRecommend}>先用通用模式</button>
              <button className="btn btn-primary" onClick={openWizard}>开始配置</button>
            </div>
          </div>
        </div>
      )}

      {/* 画像配置向导 */}
      {showWizard && (
        <OnboardingWizard onClose={() => setShowWizard(false)} onCreated={handleProfileCreated} />
      )}

    </div>
  );
}
