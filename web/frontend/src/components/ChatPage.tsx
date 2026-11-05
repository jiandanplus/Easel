import { useState, useRef, useEffect } from 'react';
import MessageBubble from './MessageBubble';
import QuestionCards from './QuestionCards';
import BrushEntry from './BrushEntry';
import ComposerModelPicker from './ComposerModelPicker';
import { snapshotChatModelSelection } from '../lib/chatModels';
import { useComposerDraft } from '../lib/useComposerDraft';
import type { ComposerDraftStore, ComposerSubmission } from '../lib/useComposerDraft';
import type { WorkbenchReadiness } from '../lib/workbenchReadiness';
import type { DeliverableReference } from '../lib/deliverables';
import type { ChatSession, ChatMessage, StreamState } from '../lib/store';
import { uploadFiles, adoptOversize, validateDraftAttachments } from '../lib/api';
import type { UploadedFile, ChatTurnSelection, WorkbenchContract } from '../lib/api';
import { IconArrowUp, IconStop, IconPlus, IconFile } from './icons';

interface ChatPageProps {
  apiDirect: boolean;
  composerOnly?: boolean;
  draftScopeKey: string;
  draftStore: ComposerDraftStore;
  draftRefreshKey?: number;
  workbench?: WorkbenchContract;
  expectedSourceScope?: string;
  workbenchChecking?: boolean;
  workbenchError?: boolean;
  onReconnect?: () => void;
  onSettings?: () => void;
  onNewChat?: () => void;
  onQueryTurn?: (turnId: string) => void;
  onWithdrawTurn?: (turnId: string) => void;
  onContinue?: (reference: DeliverableReference) => void;
  onSource?: (id: string) => void;
  onDraftChange?: (hasDraft: boolean) => void;
  initialModelSelection?: ChatTurnSelection;
  initialModelSourceRevision?: string;
  modelRefreshKey?: number;
  onRememberModel?: (selection: ChatTurnSelection | undefined, sourceRevision?: string) => void;
  session: ChatSession;
  retryNotice?: { message: string; blocked: boolean };
  stream?: StreamState;          // 进行中的流式态（来自 App，切页也不丢）
  onSend: (displayText: string, attachments?: UploadedFile[], selection?: ChatTurnSelection, submission?: ComposerSubmission) => void;
  onStop: () => void;
  onResend: (
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
    selection?: ChatTurnSelection,
  ) => void; // 重试：仅对最后一轮
  onQuestionAnswered?: (questionId: string) => void;   // 某道问答题提交成功（App 记录答过，重放不再出现）
}

// 空态推荐（贴合 Easel 社媒创作场景）
const SUGGESTIONS = [
  { icon: '🔥', title: '蹭个热点', prompt: '看看现在微博和抖音有什么热搜，挑几个适合我做二创的选题' },
  { icon: '✍️', title: '写小红书文案', prompt: '帮我写一条小红书种草文案，主题先问我' },
  { icon: '🎴', title: '做金句卡片', prompt: '把一句走心的话做成一张适合发朋友圈的金句卡片' },
  { icon: '🎬', title: '口播脚本', prompt: '帮我写一条 60 秒的口播短视频脚本，主题先问我' },
];

function greeting(): string {
  const h = new Date().getHours();
  const g = h < 6 ? '夜深了' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  return `${g}，想创作点什么？`;
}

export default function ChatPage({ apiDirect, composerOnly = false, draftScopeKey, draftStore, draftRefreshKey = 0,
  workbench, expectedSourceScope, workbenchChecking, workbenchError, onReconnect, onSettings, onNewChat, onQueryTurn, onWithdrawTurn, onContinue, onSource, onDraftChange,
  initialModelSelection, initialModelSourceRevision, modelRefreshKey = 0, onRememberModel, session, retryNotice, stream, onSend, onStop, onResend, onQuestionAnswered }: ChatPageProps) {
  const composer = useComposerDraft(draftScopeKey, session.id, draftStore, draftRefreshKey);
  const draft = composer.draft.scopeKey === draftScopeKey && composer.draft.sessionId === session.id
    ? composer.draft : draftStore.load(draftScopeKey, session.id);
  const input = draft.text;
  const attachments = draft.attachments;
  const draftChangeRef = useRef(onDraftChange); draftChangeRef.current = onDraftChange;
  useEffect(() => { draftChangeRef.current?.(!!draft.text || draft.attachments.length > 0 || !!draft.submission); }, [draft.text, draft.attachments.length, draft.submission]);
  const flushDraft = composer.flush;
  const setInput = (text: string) => composer.update({ text });
  const modelTarget = JSON.stringify([session.id, draftScopeKey, workbench?.workspace_scope, workbench?.source_scope]);
  const [modelState, setModelState] = useState<{ target: string; selection?: ChatTurnSelection; ready: boolean }>({ target: modelTarget, selection: initialModelSelection, ready: false });
  const modelReady = modelState.target === modelTarget && modelState.ready;
  const modelSelection = modelState.target === modelTarget ? modelState.selection : undefined;
  const modelSource = useRef(initialModelSourceRevision);
  const [catalogReconnect, setCatalogReconnect] = useState(0);
  const catalogRefreshKey = JSON.stringify([modelRefreshKey, catalogReconnect]);
  const [storedReadiness, setReadiness] = useState<WorkbenchReadiness>({ state: 'checking', canSubmit: false, label: '正在连接', action: 'none' });
  const readiness: WorkbenchReadiness = modelState.target === modelTarget ? storedReadiness : { state: 'checking', canSubmit: false, label: '正在连接', action: 'none' };
  const handleModelChange = (selection: ChatTurnSelection | undefined, ready: boolean, sourceRevision?: string) => {
    setModelState({ target: modelTarget, selection, ready });
    if (sourceRevision && modelSource.current && sourceRevision !== modelSource.current) onRememberModel?.(undefined);
    if (ready && selection && sourceRevision) {
      modelSource.current = sourceRevision;
      onRememberModel?.(selection, sourceRevision);
    }
  };
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(false);
  const [validatedAttachments, setValidatedAttachments] = useState('');
  const [attachmentError, setAttachmentError] = useState(false);
  const [validationRevision, setValidationRevision] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [maxMb, setMaxMb] = useState(50);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const target = `${draftScopeKey}:${session.id}`;
  const attachmentSignature = JSON.stringify(attachments.map(item => [item.id, item.path]));
  const attachmentTarget = `${target}:${attachmentSignature}`;
  const currentTarget = useRef(target); currentTarget.current = target;
  const currentAttachmentTarget = useRef(attachmentTarget); currentAttachmentTarget.current = attachmentTarget;
  const currentDraft = useRef(draft); currentDraft.current = draft;

  const isStreaming = !!stream;
  const isEmpty = session.messages.length === 0 && !isStreaming;

  useEffect(() => {
    fetch('/api/upload/limits').then((r) => r.json())
      .then((d) => { if (d?.max_mb) setMaxMb(d.max_mb); }).catch(() => {});
  }, []);

  useEffect(() => { setUploading(false); setUploadError(false); }, [target]);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const validationTarget = `${draftScopeKey}:${session.id}:${attachmentSignature}`;
    const restored = draftStore.load(draftScopeKey, session.id).attachments;
    setAttachmentError(false);
    if (!restored.length) { setValidatedAttachments(validationTarget); return () => { alive = false; controller.abort(); }; }
    void validateDraftAttachments(session.id, restored, controller.signal).then(value => {
      if (!alive || controller.signal.aborted || currentAttachmentTarget.current !== validationTarget) return;
      if (value.version !== 1 || value.session_id !== session.id || !Array.isArray(value.attachments)) throw Error('invalid_attachment_status');
      const available: Record<string, boolean> = {};
      for (const item of restored) {
        const checked = value.attachments.find(row => row.id === item.id && row.path === item.path);
        available[item.id] = checked?.state === 'available';
      }
      draftStore.setAttachmentAvailability(draftScopeKey, session.id, available);
      flushDraft();
      setValidatedAttachments(validationTarget);
    }).catch(() => {
      if (alive && !controller.signal.aborted && currentAttachmentTarget.current === validationTarget) {
        setAttachmentError(true); setValidatedAttachments('');
      }
    });
    return () => { alive = false; controller.abort(); };
  }, [draftScopeKey, session.id, attachmentSignature, validationRevision, draftStore, flushDraft]);

  const doUpload = async (fs: FileList | File[]) => {
    const arr = Array.from(fs);
    if (!arr.length) return;
    const cap = maxMb * 1024 * 1024;
    const big = arr.filter((f) => f.size > cap);
    const small = arr.filter((f) => f.size <= cap);

    const uploadTarget = target;
    setUploading(true);
    setUploadError(false);
    try {
      const saved = [];
      if (big.length) saved.push(...await adoptOversize(big, session.id));
      if (small.length) saved.push(...await uploadFiles(small, session.id));
      if (currentTarget.current === uploadTarget)
        composer.update({ attachments: [...currentDraft.current.attachments, ...saved] });
    } catch {
      if (currentTarget.current === uploadTarget) setUploadError(true);
    } finally {
      if (currentTarget.current === uploadTarget) setUploading(false);
    }
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files?.length) doUpload(e.dataTransfer.files);
  };
  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) { e.preventDefault(); doUpload(e.clipboardData.files); }
  };
  const removeAttachment = (path: string) => composer.update({ attachments: attachments.filter(item => item.path !== path) });

  useEffect(() => {
    if (!isEmpty) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [session.messages, stream?.content, stream?.thinking, stream?.activity, stream?.stillWorking, isEmpty]);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 180) + 'px';
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if ((!trimmed && attachments.length === 0) || isStreaming || uploading || !modelReady || !readiness.canSubmit
      || draftStore.load(draftScopeKey, session.id).submission || (attachments.length > 0 && (validatedAttachments !== attachmentTarget || attachments.some(item => item.available !== true)))) return;
    const turnId = crypto.randomUUID();
    const snapshot = composer.prepare(turnId);
    const revision = snapshot.submission!.submittedRevision;
    onSend(trimmed, attachments, snapshotChatModelSelection(modelSelection), { scopeKey: draftScopeKey, turnId, revision });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法确认候选词/英文的回车交给输入法处理，不能触发发送。
    // 部分浏览器先触发 compositionend，此时仍需用 229 识别输入法按键。
    if (isComposingRef.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const inputBox = (hero: boolean) => (
    <div className={`composer ${hero ? 'composer-hero' : ''} ${dragOver ? 'composer-drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
      onDrop={onDrop}>
      {attachments.length > 0 && (
        <div className="composer-attachments">
          {attachments.map((a) => (
            <span key={a.path} className="attach-chip">
              <IconFile size={12} /> <span className="attach-name">{a.name}</span>
              {a.available === false && <span role="status">需重新添加</span>}
              <button className="attach-x" onClick={() => removeAttachment(a.path)} title="移除" aria-label={`移除${a.name}`}>×</button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-top">
        {!apiDirect && <BrushEntry onPick={(t) => { setInput(t); requestAnimationFrame(() => textareaRef.current?.focus()); }} />}
        <label htmlFor={`composer-${session.id}`} className="composer-label">{session.messages.length ? '继续修改' : '创作要求'}</label>
        <textarea
          id={`composer-${session.id}`}
          ref={textareaRef}
          className="chat-input"
          placeholder={dragOver ? '添加素材' : '写下创作要求…'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; }}
          onBlur={() => { isComposingRef.current = false; composer.flush(); }}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          rows={1}
          autoFocus={hero}
        />
      </div>
      <input ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ''; }} />
      <div className="composer-bar">
        <button className="composer-attach-btn" onClick={() => fileInputRef.current?.click()}
          disabled={uploading} title="添加素材" aria-label="添加素材">
          <IconPlus size={15} /> {uploading ? '上传中…' : '素材'}
        </button>
        <span className="composer-hint" role="status">{stream?.phase === 'stopping' ? '正在停止' : isStreaming ? '正在处理' : draft.submission ? '正在确认' : uploadError ? '上传失败' : attachmentError ? '素材校验失败' : attachments.some(item => item.available === false) ? '需重新添加素材' : attachments.length && validatedAttachments !== attachmentTarget ? '正在检查素材' : !draft.persisted && (input || attachments.length) ? '草稿未保存' : readiness.canSubmit ? '' : readiness.label}</span>
        {draft.submission && <button type="button" className="composer-attach-btn" onClick={() => onQueryTurn?.(draft.submission!.clientTurnId)}>查询状态</button>}
        {draft.submission?.requestHash && onWithdrawTurn && <button type="button" className="composer-attach-btn" onClick={() => onWithdrawTurn(draft.submission!.clientTurnId)}>撤回提交</button>}
        {attachmentError && <button type="button" className="composer-attach-btn" onClick={() => setValidationRevision(value => value + 1)}>重试</button>}
        {!readiness.canSubmit && readiness.action !== 'none' && <button type="button" className="composer-attach-btn" onClick={() => {
          if (readiness.action === 'settings') onSettings?.();
          else if (readiness.action === 'new-chat') onNewChat?.();
          else if (readiness.action === 'confirm-model') document.getElementById(`model-picker-${session.id}`)?.click();
          else { setCatalogReconnect(value => value + 1); onReconnect?.(); }
        }}>{readiness.action === 'settings' ? '去设置' : readiness.action === 'new-chat' ? '新建对话' : readiness.action === 'confirm-model' ? '选择模型' : '重新连接'}</button>}
        <ComposerModelPicker sessionId={session.id} initialSelection={initialModelSelection} initialSelectionSourceRevision={initialModelSourceRevision} disabled={isStreaming}
          refreshKey={catalogRefreshKey} workbench={workbench} workbenchChecking={workbenchChecking} workbenchError={workbenchError}
          expectedSourceScope={expectedSourceScope}
          onReadiness={setReadiness} onChange={handleModelChange} />
        {isStreaming ? (
          <button className="send-btn" onClick={onStop} disabled={stream?.phase === 'stopping'} title="停止生成" aria-label="停止生成"><IconStop size={15} /></button>
        ) : (
          <button className="send-btn" onClick={handleSend} disabled={(!input.trim() && !attachments.length) || uploading || !modelReady || !readiness.canSubmit || !!draft.submission || (attachments.length > 0 && (validatedAttachments !== attachmentTarget || attachments.some(item => item.available !== true)))} title="发送" aria-label="发送"><IconArrowUp size={17} /></button>
        )}
      </div>
    </div>
  );

  if (composerOnly) return inputBox(false);

  // ---- 空态：居中欢迎页 ----
  if (isEmpty) {
    return (
      <div className="chat-page">
        <div className="chat-hero">
          <div className="chat-hero-brand">
            <img src="./static/easel-icon-transparent.png" alt="" />
            <span>Easel</span>
          </div>
          <h1 className="chat-hero-title">{greeting()}</h1>
          {inputBox(true)}
          <div className="suggestions">
            {SUGGESTIONS.filter((s) => !apiDirect || ['写小红书文案', '口播脚本'].includes(s.title)).map((s) => (
              <button key={s.title} className="card card-hover suggestion-card"
                onClick={() => { setInput(s.prompt); requestAnimationFrame(() => textareaRef.current?.focus()); }}>
                <span className="suggestion-icon">{s.icon}</span>
                <span className="suggestion-body">
                  <span className="suggestion-title">{s.title}</span>
                  <span className="suggestion-text">{s.prompt}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ---- 对话态 ----
  const displayMessages: ChatMessage[] = [...session.messages];
  if (isStreaming) displayMessages.push({ role: 'assistant', content: stream!.content || '',
    turnId: stream!.turnId, verifiedArtifacts: stream!.verifiedArtifacts });

  return (
    <div className="chat-page">
      <div className="chat-messages">
        <div className="chat-thread">
          {retryNotice && <p className="central-account-notice" role="status">{retryNotice.message}</p>}
          {displayMessages.map((msg, i) => {
            const isLast = i === displayMessages.length - 1;
            const live = isStreaming && isLast && msg.role === 'assistant';
            const isFinal = !live && i < session.messages.length;
            let actions;
            if (isFinal) {
              const copy = async () => {
                if (!navigator.clipboard?.writeText) throw new Error('copy_unavailable');
                await navigator.clipboard.writeText(msg.content);
              };
              // 只允许对「最后一轮」重试，契合 OpenClaw append-only 模型（不改写历史）；已移除编辑
              const isLastFinal = i === session.messages.length - 1;
              if (msg.role === 'user') {
                actions = {
                  onCopy: copy,
                  onRetry: isLastFinal && modelReady && !retryNotice?.blocked
                    ? () => onResend(i, msg.content, msg.attachments, msg.agentContent, snapshotChatModelSelection(modelSelection))
                    : undefined,
                  canModify: !isStreaming,
                };
              } else {
                const pi = i - 1;
                const prevUser = pi >= 0 && session.messages[pi]?.role === 'user' ? session.messages[pi] : null;
                actions = {
                  onCopy: copy,
                  onRetry: (isLastFinal && prevUser && modelReady && !retryNotice?.blocked)
                    ? () => onResend(pi, prevUser.content, prevUser.attachments, prevUser.agentContent, snapshotChatModelSelection(modelSelection))
                    : undefined,
                  canModify: !isStreaming,
                };
              }
            }
            return (
              <MessageBubble
                key={`${i}-${msg.role}`}
                message={msg}
                sessionId={session.id}
                isStreaming={live}
                thinking={live ? stream!.thinking : ''}
                activity={live ? stream!.activity : ''}
                stillWorking={live ? stream!.stillWorking : ''}
                actions={actions}
                onContinue={onContinue}
                onSource={onSource}
              />
            );
          })}
          {isStreaming && (stream!.questions?.length ?? 0) > 0 && (
            <QuestionCards questions={stream!.questions || []} onDone={(qid) => onQuestionAnswered?.(qid)} />
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="chat-input-area">
        <div className="chat-input-inner">{inputBox(false)}</div>
      </div>
    </div>
  );
}
