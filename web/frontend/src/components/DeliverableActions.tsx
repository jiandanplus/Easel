import { useEffect, useRef, useState } from 'react';
import { fetchOutputContent } from '../lib/api';
import { copyDeliverable, deliverableCopyLabel, deliverableDownloadUrl } from '../lib/deliverables';
import type { DeliverableReference } from '../lib/deliverables';
import './DeliverableActions.css';

interface Props {
  reference: DeliverableReference;
  onContinue?: (reference: DeliverableReference) => void;
  onSource?: (sessionId: string) => void;
}

export default function DeliverableActions({ reference, onContinue, onSource }: Props) {
  const [state, setState] = useState<'idle' | 'copying' | 'copied' | 'error'>('idle');
  const sequence = useRef(0);
  const pending = useRef<number | null>(null);
  useEffect(() => {
    const generation = sequence, inFlight = pending;
    generation.current++; inFlight.current = null; setState('idle');
    return () => { generation.current++; inFlight.current = null; };
  }, [reference.path]);
  const copyLabel = deliverableCopyLabel(reference.name);
  const download = deliverableDownloadUrl(reference);
  const copy = async () => {
    if (pending.current !== null) return;
    const request = ++sequence.current;
    pending.current = request;
    setState('copying');
    try {
      await copyDeliverable(reference, fetchOutputContent, async text => {
        if (request !== sequence.current || !navigator.clipboard?.writeText) throw new Error('copy_unavailable');
        await navigator.clipboard.writeText(text);
      });
      if (request === sequence.current) setState('copied');
    } catch { if (request === sequence.current) setState('error'); }
    finally { if (pending.current === request) pending.current = null; }
  };
  if (!download) return null;
  return <div className="deliverable-actions">
    {copyLabel && <button type="button" className="btn btn-sm" disabled={state === 'copying'} onClick={() => void copy()}>{state === 'copying' ? '复制中…' : state === 'copied' ? '已复制' : copyLabel}</button>}
    <a className="btn btn-sm" href={download} download={reference.name}>下载文件</a>
    {onContinue && <button type="button" className="btn btn-sm" onClick={() => onContinue(reference)}>{reference.sessionId ? '继续修改' : '新建对话'}</button>}
    {onSource && reference.sessionId && <button type="button" className="btn btn-ghost btn-sm" onClick={() => onSource(reference.sessionId!)}>来源对话</button>}
    <span className="deliverable-feedback" role="status">{state === 'error' ? '复制失败' : ''}</span>
  </div>;
}
