import { useCallback, useEffect, useRef, useState } from 'react';
import { applyUpdate, checkUpdate, downloadUpdate, updateStatus } from '../lib/api';
import { restarted, updateView } from '../lib/update';
import type { UpdateStatus } from '../lib/update';

export default function UpdateSettings() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const versionBefore = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await updateStatus();
      if (restarted(versionBefore.current, next)) window.location.reload();
      setStatus(next);
      setError('');
    } catch (failure) {
      // While switching, the server is briefly down; keep polling until the new version answers.
      if (versionBefore.current === null) setError(failure instanceof Error ? failure.message : '读取更新状态失败');
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  const polling = versionBefore.current !== null || status?.state === 'downloading';
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [polling, refresh]);

  const run = useCallback(async (action: 'check' | 'download' | 'apply') => {
    setWorking(true);
    setError('');
    try {
      if (action === 'check') setStatus(await checkUpdate());
      else if (action === 'download') setStatus(await downloadUpdate());
      else {
        versionBefore.current = status?.version ?? null;
        await applyUpdate();
        setStatus(current => current && { ...current, state: 'switching' });
      }
    } catch (failure) {
      versionBefore.current = null;
      setError(failure instanceof Error ? failure.message : '操作失败');
    } finally {
      setWorking(false);
    }
  }, [status]);

  const view = status && updateView(status);
  return (
    <section className="settings-theme settings-update" aria-labelledby="update-settings-title">
      <div className="settings-theme-heading">
        <span className="settings-theme-eyebrow">UPDATE</span>
        <h3 id="update-settings-title">版本更新</h3>
      </div>
      {!view && !error && <p className="settings-update-line">读取中…</p>}
      {view && <p className="settings-update-line">{view.line}</p>}
      {view?.notes && <pre className="settings-update-notes">{view.notes}</pre>}
      {(error || view?.notice) && <p className="settings-update-notice" role="status">{error || view?.notice}</p>}
      {view?.action && (
        <button type="button" className="btn btn-sm btn-primary" disabled={working || view.disabled}
          onClick={() => void run(view.action!)}>{working ? '处理中…' : view.actionLabel}</button>
      )}
    </section>
  );
}
