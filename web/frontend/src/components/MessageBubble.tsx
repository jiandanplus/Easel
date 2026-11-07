import { useMemo, useState } from 'react';
import type { ChatMessage } from '../lib/store';
import { renderMarkdown } from '../lib/sanitize';
import { linkifyOutputs, externalizeMediaLinks, disableRedactedOutputLinks } from '../lib/linkifyOutputs';
import { IconCopy, IconCheck, IconRetry } from './icons';
import { extractArtifacts } from '../lib/artifacts';
import ArtifactGallery from './ArtifactGallery';
import ConversationRecovery from './ConversationRecovery';
import DeliverableActions from './DeliverableActions';
import { deliverableFromUrl, mergeVerifiedArtifacts, verifiedArtifactReference } from '../lib/deliverables';
import type { DeliverableReference } from '../lib/deliverables';

export interface BubbleActions {
  onCopy: () => void | Promise<void>;
  onRetry?: () => void;    // 仅最后一轮可用（append-only：不改写历史）
  canModify: boolean;      // 流式中禁用 retry
}

interface MessageBubbleProps {
  message: ChatMessage;
  sessionId?: string;
  isStreaming?: boolean;
  thinking?: string;
  activity?: string;
  stillWorking?: string;   // 防呆心跳提示（未卡住）；仅流式时的独立提示，不替代思考/活动
  actions?: BubbleActions;
  onContinue?: (reference: DeliverableReference) => void;
  onSource?: (id: string) => void;
}

function ActionBar({ actions }: { actions: BubbleActions }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const copy = async () => {
    try { await actions.onCopy(); setCopied(true); setCopyError(false); setTimeout(() => setCopied(false), 1200); }
    catch { setCopied(false); setCopyError(true); }
  };
  return (
    <div className="msg-actions">
      <button className="msg-action" onClick={() => void copy()} title="复制回复">
        {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}<span>{copied ? '已复制' : '复制'}</span>
      </button>
      {copyError && <span role="status">复制失败</span>}
      {actions.onRetry && actions.canModify && (
        <button className="msg-action" onClick={actions.onRetry} title="重新生成"><IconRetry size={14} /><span>重试</span></button>
      )}
    </div>
  );
}

export default function MessageBubble({ message, sessionId, isStreaming, thinking, activity, stillWorking, actions, onContinue, onSource }: MessageBubbleProps) {
  const rendered = useMemo(() => {
    if (message.role === 'user') return { html: '', artifacts: [], archives: [] };
    // 助手正文里裸露的产物路径先转成前端可用链接（文件直开、图片内联、目录跳内容库）。
    // 流式中不转换：路径/围栏代码块可能被 SSE token 从中间切开（扩展名没收全会被
    // 误判成目录、未闭合的 ``` 会误伤命令示例里的 outputs/），转换只在流式结束后
    // 对完整正文做一次，避免闪烁成错误链接再跳变。
    const body = isStreaming ? disableRedactedOutputLinks(message.content) : linkifyOutputs(message.content);
    const html = externalizeMediaLinks(renderMarkdown(body));
    return isStreaming ? { html, artifacts: [], archives: [] } : extractArtifacts(html);
  }, [message.content, message.role, isStreaming]);
  const documents = useMemo(() => {
    if (message.role !== 'assistant') return [];
    const found = new Map<string, DeliverableReference>();
    for (const item of mergeVerifiedArtifacts([], message.verifiedArtifacts, sessionId, message.turnId)) {
      const reference = verifiedArtifactReference(item, sessionId, message.turnId);
      if (reference) found.set(reference.path, reference);
    }
    if (isStreaming) return [...found.values()];
    const document = new DOMParser().parseFromString(rendered.html, 'text/html');
    for (const link of document.querySelectorAll('a[href]')) {
      const reference = deliverableFromUrl(link.getAttribute('href') || '', sessionId);
      if (reference && !/\.(png|jpe?g|gif|webp|svg|avif|bmp|ico|html?|mp4|webm|mov|m4v|mp3|wav|ogg|m4a|flac|pdf|zip)$/i.test(reference.name)) found.set(reference.path, reference);
    }
    return [...found.values()];
  }, [rendered.html, message.role, message.verifiedArtifacts, message.turnId, isStreaming, sessionId]);

  // ---- 用户消息 ----
  if (message.role === 'user') {
    // Attachment-only turns are intentionally invisible; the structured refs
    // remain in session state for retry but never leak paths into the chat UI.
    if (!message.content.trim()) return null;
    return (
      <div className="message-row user">
        <div className="msg-col user">
          <div className="message-bubble user">{message.content}</div>
          {actions && <ActionBar actions={actions} />}
        </div>
      </div>
    );
  }

  // ---- 助手消息 ----
  // 思考 / 活动：流式时用实时值；结束后用消息里持久化的值 —— 一直保留，不隐藏
  const effThinking = isStreaming ? (thinking || '') : (message.thinking || '');
  const liveActivity = isStreaming ? (activity || '') : '';
  const liveHint = isStreaming ? (stillWorking || '') : '';   // 防呆「未卡住」提示，附着显示、不顶掉真实状态
  const doneSteps = !isStreaming ? (message.activity || '') : '';

  const livePanel = (effThinking || liveActivity || liveHint || doneSteps) ? (
    <div className="live-panel">
      {liveActivity ? (
        <div className="live-activity">
          <span className="live-pulse" />{liveActivity}
          {liveHint && <span className="live-still"> · {liveHint}</span>}
        </div>
      ) : liveHint ? (
        <div className="live-activity"><span className="live-pulse" />{liveHint}</div>
      ) : null}
      {doneSteps && (
        <details className="thinking-block">
          <summary>🧠 执行过程（{doneSteps.split('\n').length} 步）</summary>
          <div className="thinking-text">{doneSteps}</div>
        </details>
      )}
      {effThinking && (
        <details className="thinking-block" open={isStreaming && !message.content}>
          <summary>💭 思考过程</summary>
          <div className="thinking-text">{effThinking}</div>
        </details>
      )}
    </div>
  ) : null;

  // 等待回复中（还没有正文、思考、活动）
  if (isStreaming && !message.content && !effThinking && !liveActivity && !liveHint && !documents.length) {
    return (
      <div className="message-row assistant">
        <div className="message-bubble assistant">
          <div className="typing-indicator">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="message-row assistant">
      <div className="msg-col assistant">
        <div className="message-bubble assistant">
          {livePanel}
          {message.content && <div dangerouslySetInnerHTML={{ __html: rendered.html }} />}
          {!isStreaming && (rendered.artifacts.length > 0 || rendered.archives.length > 0) && (
            <ArtifactGallery artifacts={rendered.artifacts} archives={rendered.archives} sourceSessionId={sessionId}
              onContinue={onContinue} onSource={onSource} />
          )}
          {documents.map(reference => <section className="message-document" key={reference.path} aria-label={reference.name}>
            <strong>{reference.name}</strong><DeliverableActions reference={reference} onContinue={onContinue} onSource={onSource} />
          </section>)}
          {isStreaming && <span className="streaming-cursor" />}
        </div>
        {actions && !isStreaming && <ActionBar actions={actions} />}
        {!isStreaming && message.taskState && <span className="message-task-state" role="status">{message.taskState === 'stopped' ? '已停止' : message.taskState === 'failed' ? '未完成' : message.taskState === 'unknown' ? '状态待确认' : '已完成'}</span>}
        {!isStreaming && sessionId && message.centralRecoveryTurnId && (
          <ConversationRecovery sessionId={sessionId} turnId={message.centralRecoveryTurnId} />
        )}
      </div>
    </div>
  );
}
