import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { centralStatus, centralStartLogin, centralCancelLogin, centralLogout } from '../lib/api';
import type { CentralStatus } from '../lib/api';
import { AppearanceToggle } from './AppearanceControls';
import './SidebarAccount.css';

const STATUS_READ_DEADLINE_MS = 30_000;

interface Props {
  onOpenAccount: () => void;
  onOpenSettings: () => void;
  settingsOpen: boolean;
  runtime?: { name: string; connected: boolean; detail: string };
}

function AccountGlyph({ kind = 'person' }: { kind?: 'person' | 'settings' | 'usage' | 'logout' }) {
  return <svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'person' ? <><circle cx="12" cy="8" r="3.5" /><path d="M5 21v-2a7 7 0 0 1 14 0v2" /></>
      : kind === 'usage' ? <><path d="M4 17a9 9 0 1 1 16 0M12 13l5-5" /><circle cx="12" cy="13" r="1" /></>
        : kind === 'logout' ? <><path d="M10 4H5v16h5M17 8l4 4-4 4M9 12h12" /></>
          : <><path d="m9 3-1 3-3 1-2 4 2 2 1 4 4 1 2 3 4-2 1-3 3-2-1-4-3-1-2-3Z" /><circle cx="12" cy="12" r="3" /></>}
  </svg>;
}

function loginDestination(value: { authorization_url: string; expires_at: number }, current: CentralStatus): string {
  const url = new URL(value.authorization_url);
  const secure = url.protocol === 'https:' || (url.protocol === 'http:' && ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname));
  if (!secure || url.username || url.password || url.hash || url.origin !== new URL(current.url).origin
    || !Number.isFinite(value.expires_at) || value.expires_at <= Date.now() / 1000) throw new Error('invalid_login_destination');
  return url.href;
}

export default function SidebarAccount({ onOpenAccount, onOpenSettings, settingsOpen, runtime }: Props) {
  const [status, setStatus] = useState<CentralStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [position, setPosition] = useState<{ left: number; bottom: number; width: number; maxHeight?: number }>({ left: 88, bottom: 16, width: 320 });
  const mounted = useRef(true);
  const statusRef = useRef<CentralStatus | null>(null);
  const loadingRef = useRef(true);
  const failedRef = useRef(false);
  const actionLock = useRef(false);
  const actionGeneration = useRef(0);
  const readGeneration = useRef(0);
  const currentRead = useRef<AbortController | null>(null);
  const readDeadline = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const urlRef = useRef('');
  const loginWindow = useRef<Window | null>(null);
  const blankWindow = useRef<Window | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cardRef = useRef<HTMLElement>(null);

  function invalidateReads() {
    readGeneration.current++;
    const controller = currentRead.current;
    currentRead.current = null;
    if (readDeadline.current !== null) clearTimeout(readDeadline.current);
    readDeadline.current = null;
    if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    pollTimer.current = null;
    controller?.abort();
  }
  function pollLater() {
    if (!mounted.current || actionLock.current || !statusRef.current?.login?.pending) return;
    if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    const generation = readGeneration.current;
    const timer = setTimeout(() => {
      if (!mounted.current || actionLock.current || generation !== readGeneration.current || pollTimer.current !== timer) return;
      pollTimer.current = null;
      void refresh(true);
    }, 1000);
    pollTimer.current = timer;
  }
  function accept(value: CentralStatus) {
    statusRef.current = value;
    setStatus(value);
    const unconfigured = value.configured === false && value.error?.code === 'not_configured';
    failedRef.current = Boolean(value.error && !unconfigured);
    setFailed(failedRef.current);
    loadingRef.current = false;
    setLoading(false);
    if (!value.login?.pending) {
      urlRef.current = '';
      setAuthorizationUrl('');
      loginWindow.current = null;
    }
  }
  async function refresh(quiet = false) {
    if (!mounted.current || actionLock.current) return;
    invalidateReads();
    const generation = readGeneration.current;
    const controller = new AbortController();
    currentRead.current = controller;
    if (!quiet) {
      loadingRef.current = true;
      setLoading(true);
      failedRef.current = false;
      setFailed(false);
    }
    const live = () => mounted.current && !controller.signal.aborted && generation === readGeneration.current;
    readDeadline.current = setTimeout(() => {
      if (!live() || currentRead.current !== controller) return;
      // Revoke this read before aborting, including fetches that ignore abort.
      readGeneration.current++;
      currentRead.current = null;
      readDeadline.current = null;
      loadingRef.current = false;
      setLoading(false);
      failedRef.current = true;
      setFailed(true);
      controller.abort();
    }, STATUS_READ_DEADLINE_MS);
    try {
      const value = await centralStatus(controller.signal);
      if (live()) accept(value);
    } catch {
      if (live()) { failedRef.current = true; setFailed(true); }
    } finally {
      if (live()) {
        if (readDeadline.current !== null) clearTimeout(readDeadline.current);
        readDeadline.current = null;
        currentRead.current = null;
        loadingRef.current = false;
        setLoading(false);
        pollLater();
      }
    }
  }
  function closeCard(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }
  function closeBlank(window: Window | null) {
    try { if (window && !window.closed) window.close(); } catch { /* Only an owned placeholder is eligible. */ }
    if (blankWindow.current === window) blankWindow.current = null;
  }
  function closePreparedWindow() {
    // This ref owns a mutable window handle, rather than a rendered DOM node.
    closeBlank(blankWindow.current);
  }
  function prepareWindow() {
    let handle: Window | null = null;
    try {
      handle = window.open('about:blank', '_blank');
      if (handle) handle.opener = null;
      blankWindow.current = handle;
      return handle;
    } catch { closeBlank(handle); return null; }
  }
  function continueLogin() {
    if (!urlRef.current) return;
    try {
      if (loginWindow.current && !loginWindow.current.closed) { loginWindow.current.focus(); return; }
      const handle = prepareWindow();
      if (handle) {
        handle.location.replace(urlRef.current);
        blankWindow.current = null;
        loginWindow.current = handle;
      } else setActionError('请手动打开登录页面');
    } catch { closeBlank(blankWindow.current); setActionError('请手动打开登录页面'); }
  }
  function startLogin() {
    const current = statusRef.current;
    if (actionLock.current || loadingRef.current || failedRef.current || !current?.configured
      || current.authenticated || current.error || current.scope || current.login?.pending) return;
    actionLock.current = true;
    const generation = ++actionGeneration.current;
    invalidateReads(); setBusy(true); setActionError(''); setOpen(true);
    // Must be created synchronously in the click, before the asynchronous request.
    const placeholder = prepareWindow();
    const live = () => mounted.current && generation === actionGeneration.current;
    void centralStartLogin().then(result => {
      const destination = loginDestination(result, current);
      if (!live()) { closeBlank(placeholder); return; }
      urlRef.current = destination;
      setAuthorizationUrl(destination);
      accept({ ...current, login: { pending: true, expires_at: result.expires_at } });
      if (!placeholder || placeholder.closed) {
        setActionError('请手动打开登录页面');
        return;
      }
      try {
        placeholder.location.replace(destination);
        blankWindow.current = null;
        loginWindow.current = placeholder;
      } catch {
        closeBlank(placeholder);
        setActionError('请手动打开登录页面');
      }
    }).catch(() => {
      closeBlank(placeholder);
      if (live()) {
        setActionError('登录暂不可用，请重试');
        failedRef.current = true;
        setFailed(true);
      }
    }).finally(() => {
      if (!live()) return;
      actionLock.current = false; setBusy(false); pollLater();
    });
  }
  function accountAction(kind: 'cancel' | 'logout') {
    if (actionLock.current || loadingRef.current) return;
    actionLock.current = true;
    const generation = ++actionGeneration.current;
    const previousWindow = loginWindow.current;
    invalidateReads(); setBusy(true); setActionError('');
    const live = () => mounted.current && generation === actionGeneration.current;
    const operation = kind === 'cancel' ? centralCancelLogin() : centralLogout();
    void operation.then(value => {
      if (!live()) return;
      accept(value);
      if (kind === 'cancel') {
        try { if (previousWindow && !previousWindow.closed) previousWindow.close(); } catch { /* Cancellation already succeeded. */ }
      } else closeCard();
    }).catch(() => {
      if (!live()) return;
      setActionError(kind === 'cancel' ? '取消未完成，正在查询' : '退出未完成，请重试');
      if (kind === 'logout') { failedRef.current = true; setFailed(true); }
    }).finally(() => {
      if (!live()) return;
      actionLock.current = false; setBusy(false); pollLater();
    });
  }

  function disposeEntry() {
    mounted.current = false;
    actionGeneration.current++;
    invalidateReads();
    closePreparedWindow();
  }
  useEffect(() => {
    mounted.current = true;
    return disposeEntry;
  }, []);
  useEffect(() => {
    setOpen(false);
    void refresh();
    const onFocus = () => { void refresh(); };
    window.addEventListener('focus', onFocus);
    return () => { invalidateReads(); window.removeEventListener('focus', onFocus); };
  }, [settingsOpen]);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.max(200, Math.min(320, window.innerWidth - 24));
      const bottom = Math.max(12, Math.min(window.innerHeight - rect.bottom, window.innerHeight - 100));
      setPosition({ width, left: Math.max(12, Math.min(rect.right + 12, window.innerWidth - width - 12)), bottom, maxHeight: Math.max(0, window.innerHeight - bottom - 12) });
    };
    place();
    const first = cardRef.current?.querySelector<HTMLButtonElement>('button');
    first?.focus();
    const outside = (event: PointerEvent) => {
      if (!cardRef.current?.contains(event.target as Node) && !triggerRef.current?.contains(event.target as Node)) closeCard();
    };
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeCard(); }
    };
    const focusOutside = (event: FocusEvent) => {
      if (!cardRef.current?.contains(event.target as Node) && !triggerRef.current?.contains(event.target as Node)) closeCard(false);
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard);
    document.addEventListener('focusin', focusOutside);
    window.addEventListener('resize', place);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard); document.removeEventListener('focusin', focusOutside); window.removeEventListener('resize', place); };
  }, [open]);

  const authenticated = !loading && !failed && status?.configured === true && status.authenticated === true && !status.error;
  const loginPending = Boolean(status?.login?.pending);
  const verifiedPending = !loading && !failed && loginPending;
  const signedOut = !loading && !failed && status?.configured === true && !status.authenticated && !status.error && !status.scope && !loginPending;
  const label = loading ? '正在读取账号状态' : authenticated ? '用户菜单' : verifiedPending ? '登录进行中' : signedOut ? '登录 / 注册' : '账号状态待确认';
  const identity = authenticated ? status.account?.email || status.account?.id || '统一账号' : verifiedPending ? '正在登录' : status?.configured === false ? '平台入口待配置' : '账号状态待确认';
  const shortIdentity = identity.length > 32 ? `${identity.slice(0, 16)}…${identity.slice(-8)}` : identity;
  const clickAvatar = () => {
    if (loadingRef.current || actionLock.current) return;
    if (signedOut) { startLogin(); return; }
    if (verifiedPending && urlRef.current) continueLogin();
    setOpen(value => !value);
  };
  const navigate = (action: () => void) => { closeCard(false); action(); };

  return <>
    <button ref={triggerRef} type="button" className={`sidebar-account${authenticated ? ' is-authenticated' : ''}${open ? ' is-open' : ''}`}
      disabled={loading || busy} onClick={clickAvatar} aria-label={label} aria-busy={loading || busy}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? 'sidebar-user-card' : undefined} title={authenticated ? `${identity} · 已登录` : label}>
      <span className="sidebar-account-avatar" aria-hidden="true"><AccountGlyph /></span>
      <span className="sidebar-account-copy"><strong>{authenticated ? shortIdentity : label}</strong><small>{authenticated ? '已登录' : label}</small></span>
    </button>
    {open && createPortal(<section ref={cardRef} id="sidebar-user-card" className="sidebar-user-card" role="dialog" aria-label="用户卡片" aria-busy={busy}
      style={{ left: position.left, bottom: position.bottom, width: position.width, maxHeight: position.maxHeight }}>
      <header className="account-card-header">
        <span className="account-card-avatar" aria-hidden="true"><AccountGlyph /></span>
        <div className="account-card-identity"><strong title={identity}>{shortIdentity}</strong><span className={`account-card-status${authenticated ? ' is-signed-in' : ''}`}>{authenticated ? '已登录' : verifiedPending ? '等待完成' : loading ? '正在读取…' : '状态待确认'}</span></div>
        <button type="button" className="account-card-close" aria-label="关闭用户卡片" onClick={() => closeCard()}>×</button>
      </header>
      {actionError && <p className="account-card-feedback" role="alert">{actionError}</p>}
      {authenticated ? <>
        <div className="account-card-actions">
          <button type="button" onClick={() => navigate(onOpenAccount)}><AccountGlyph kind="usage" /><span>账号与订阅</span></button>
          <button type="button" onClick={() => navigate(onOpenSettings)}><AccountGlyph kind="settings" /><span>设置</span></button>
        </div>
      </> : <>
        <p className="account-card-description" role="status">{verifiedPending ? '等待登录' : loading ? '正在确认账号…' : status?.configured === false ? '平台入口待配置' : '账号状态待确认'}</p>
        {authorizationUrl && verifiedPending && <a className="account-card-login-link" href={authorizationUrl} target="_blank" rel="noopener noreferrer">打开登录页面</a>}
        <div className="account-card-actions">
          {verifiedPending ? <button type="button" disabled={busy || loading} onClick={() => accountAction('cancel')}><AccountGlyph kind="logout" /><span>取消登录</span></button>
            : <button type="button" disabled={busy || loading} onClick={() => void refresh()}><AccountGlyph kind="usage" /><span>重试账号状态</span></button>}
          <button type="button" onClick={() => navigate(onOpenSettings)}><AccountGlyph kind="settings" /><span>设置</span></button>
        </div>
      </>}
      <div className="account-card-preferences" aria-label="工作台偏好">
        <div className="account-card-preference-row" title={runtime?.detail}>
          <span className="account-card-runtime-label"><span className={`status-dot${runtime?.connected ? '' : ' offline'}`} aria-hidden="true" />本地运行</span>
          <span className="account-card-runtime-name">{runtime?.name || '—'}</span>
        </div>
        <div className="account-card-preference-row">
          <span className="account-card-preference-label">显示模式</span>
          <AppearanceToggle />
        </div>
      </div>
      {authenticated && <div className="account-card-actions account-card-signout"><button type="button" disabled={busy} onClick={() => accountAction('logout')}><AccountGlyph kind="logout" /><span>退出登录</span></button></div>}
    </section>, document.body)}
  </>;
}
