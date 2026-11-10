import { useEffect, useRef, useState } from 'react';
import { ConsoleClient, Submission, validReason } from './core';
import type { RecordData } from './core';
import { Modal } from './views';

// Fixed by the server (decision 0011): the page cannot choose the amount or duration.
export function AllowanceDialog({ client, wallet, onClose, onFailure, onDone }: { client: ConsoleClient; wallet: RecordData; onClose: () => void; onFailure: (error: unknown) => string; onDone: () => void }) {
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [submission] = useState(() => new Submission());
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const submit = async () => {
    if (submission.busy || busy) return;
    setError('');
    try {
      const checked = validReason(reason);
      setBusy(true);
      await submission.send(client, `/api/wallets/${encodeURIComponent(String(wallet.id))}/allowance`, { reason: checked }, 'owner:allowance');
      if (active.current) onDone();
    } catch (cause) { if (active.current) setError(onFailure(cause)); }
    finally { if (active.current) setBusy(false); }
  };
  return <Modal title="发放所有者额度" onClose={onClose} busy={busy}><form onSubmit={e => { e.preventDefault(); void submit(); }}><div className="dialog-content">
    <p>钱包：<code>{String(wallet.id)}</code></p>
    <ul className="plain-list"><li><span>额度</span><span>100 点</span></li><li><span>有效期</span><span>365 天</span></li><li><span>可用功能</span><span>文本和图片</span></li></ul>
    <p className="muted">这不是付款，只是系统内额度，模型费用仍由供应商向你收取。同一个钱包的额度到期前不能再发，发放记录不能修改或删除。</p>
    <label className="reason-field">发放原因<textarea required maxLength={255} rows={2} value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：付款开放前给朋友试用" disabled={busy || submission.locked} /></label>
    {error && <p className="notice error" role="alert">{error}</p>}
    {submission.locked && error && <p className="caption">重试会沿用同一次操作，不会重复发放。</p>}
  </div><div className="dialog-actions"><button type="button" className="button" onClick={onClose} disabled={busy}>取消</button><button type="submit" className="button primary" disabled={busy || submission.conflict}>{busy ? '正在发放…' : '确认发放'}</button></div></form></Modal>;
}
