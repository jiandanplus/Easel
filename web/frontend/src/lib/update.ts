export interface UpdateStatus {
  managed: boolean;
  version: number | null;
  state: 'idle' | 'latest' | 'available' | 'downloading' | 'ready' | 'failed' | 'switching';
  error: string;
  received: number;
  available: { version: number; notes: string; bytes: number } | null;
  busy: boolean;
  last: { status: string; from: number; to: number } | null;
}

export interface UpdateView {
  line: string;
  action: 'check' | 'download' | 'apply' | null;
  actionLabel: string;
  disabled: boolean;
  notes: string;
  notice: string;
}

const megabytes = (bytes: number) => `${Math.round(bytes / 1024 / 1024)} MB`;

export function updateView(status: UpdateStatus): UpdateView {
  const current = status.version ? `v${status.version}` : '未知版本';
  const next = status.available;
  const view: UpdateView = { line: `当前版本 ${current}`, action: null, actionLabel: '', disabled: false, notes: '', notice: '' };
  if (status.last?.status === 'rolled_back') view.notice = `v${status.last.to} 启动失败，已恢复到 v${status.last.from}`;
  if (!status.managed) return { ...view, line: `${view.line} · 当前安装不支持应用内更新` };
  switch (status.state) {
    case 'available':
    case 'failed':
      if (!next) break;
      return { ...view, line: `新版本 v${next.version} · ${megabytes(next.bytes)}`, action: 'download',
        actionLabel: status.state === 'failed' ? '重新下载' : '下载更新', notes: next.notes,
        notice: status.state === 'failed' ? status.error : view.notice };
    case 'downloading':
      if (!next) break;
      return { ...view, line: `正在下载 v${next.version} · ${Math.min(100, Math.floor(status.received * 100 / next.bytes))}%`, notes: next.notes };
    case 'ready':
      if (!next) break;
      return { ...view, line: `v${next.version} 已下载`, action: 'apply', actionLabel: '立即更新', notes: next.notes,
        disabled: status.busy, notice: status.busy ? '有任务进行中，完成后再更新' : view.notice };
    case 'switching':
      return { ...view, line: '正在更新，完成后页面会自动刷新' };
    case 'latest':
      return { ...view, line: `${view.line} · 已是最新版本`, action: 'check', actionLabel: '检查更新', notice: status.error || view.notice };
  }
  return { ...view, action: 'check', actionLabel: '检查更新', notice: status.error || view.notice };
}

export function restarted(before: number | null, now: UpdateStatus | null): boolean {
  return !!now && before !== null && now.version !== null && now.version > before;
}
