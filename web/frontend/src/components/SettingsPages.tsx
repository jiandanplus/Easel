import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  centralStatus, centralUsage, emptyTrash, fetchAccounts, fetchIdeas, fetchOutputs, fetchSchedule, fetchSkills,
  fetchSystem, fetchTrash, openSystemPlace, removeTrash, restoreTrash,
} from '../lib/api';
import type { CentralUsage, OutputNode, SkillItem, SystemInfo, TrashItem } from '../lib/api';
import CentralAccountSettings from './CentralAccountSettings';
import UpdateSettings from './UpdateSettings';
import './SettingsPages.css';

const message = (failure: unknown) => failure instanceof Error ? failure.message : '读取失败，请稍后重试';

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  const amount = value / 1024 ** index;
  return `${amount >= 100 || index === 0 ? Math.round(amount) : amount.toFixed(1)} ${units[index]}`;
}

/** One titled page in the settings area: a heading row, an optional action and content cards. */
export function SettingsPage({ title, desc, action, children }: { title: string; desc?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="st-sec active settings-page-body" aria-label={title}>
    <div className="settings-page-head"><div><h3>{title}</h3>{desc && <p className="desc">{desc}</p>}</div>{action}</div>
    {children}
  </section>;
}

function Card({ title, children }: { title?: string; children: ReactNode }) {
  return <div className="settings-card">{title && <h4>{title}</h4>}{children}</div>;
}

const mod = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
export const SHORTCUTS: { group: string; items: [string, string][] }[] = [
  { group: '全局', items: [[`${mod} ,`, '打开设置'], [`${mod} N`, '新建对话'], [`${mod} K`, '搜索页面'], ['Esc', '关闭设置、弹窗或下拉菜单']] },
  { group: '对话', items: [['Enter', '发送消息'], ['Shift Enter', '换行'], ['Enter / Esc', '重命名对话时确认 / 取消']] },
  { group: '浏览成品', items: [['← →', '切换上一张 / 下一张']] },
];

export function ShortcutSettings() {
  return <SettingsPage title="快捷键" desc="目前不能自定义，下面是可以使用的快捷键。">
    {SHORTCUTS.map(group => <Card key={group.group} title={group.group}>
      <dl className="settings-rows">{group.items.map(([keys, label]) => <div key={label}><dt>{label}</dt><dd>{keys.split(' ').map(key => <kbd key={key}>{key}</kbd>)}</dd></div>)}</dl>
    </Card>)}
  </SettingsPage>;
}

function countFiles(nodes: OutputNode[]): { files: number; byKind: Record<string, number> } {
  const byKind: Record<string, number> = {};
  let files = 0;
  const walk = (list: OutputNode[]) => list.forEach(node => {
    if (node.type === 'file') { files += 1; byKind[node.kind || 'binary'] = (byKind[node.kind || 'binary'] || 0) + 1; }
    else if (node.children) walk(node.children);
  });
  walk(nodes);
  return { files, byKind };
}

export function StatsSettings() {
  const [local, setLocal] = useState<{ projects: number; files: number; byKind: Record<string, number>; ideas: number; schedule: number; published: number; skills: number; accounts: number } | null>(null);
  const [usage, setUsage] = useState<CentralUsage[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    void Promise.all([fetchOutputs(), fetchIdeas(), fetchSchedule(), fetchSkills(), fetchAccounts()]).then(([outputs, ideas, schedule, skills, accounts]) => {
      if (!alive) return;
      const { files, byKind } = countFiles(outputs);
      setLocal({ projects: outputs.length, files, byKind, ideas: ideas.length, schedule: schedule.filter(item => item.kind !== 'event').length,
        published: schedule.filter(item => item.status === 'published' || Boolean(item.url)).length, skills: skills.length, accounts: accounts.filter(item => item.loggedIn).length });
    }).catch(failure => { if (alive) setError(message(failure)); });
    void centralStatus().then(status => status.authenticated ? centralUsage() : null).then(value => { if (alive) setUsage(value?.usage ?? null); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  const spent = usage?.reduce((total, item) => total + (item.actual && /^\d+$/.test(item.actual) ? BigInt(item.actual) : 0n), 0n);
  const kinds: [string, string][] = [['text', '文案'], ['image', '图片'], ['video', '视频'], ['audio', '音频']];
  return <SettingsPage title="数据统计" desc="这台电脑上的创作数据，以及账号的模型调用情况。">
    {error && <p role="alert" className="env-error">{error}</p>}
    {!local && !error && <p role="status">正在统计…</p>}
    {local && <Card title="创作">
      <div className="settings-stats">
        <div><strong>{local.projects}</strong><span>内容项目</span></div><div><strong>{local.files}</strong><span>成品文件</span></div>
        <div><strong>{local.ideas}</strong><span>选题</span></div><div><strong>{local.schedule}</strong><span>日历排期</span></div>
        <div><strong>{local.published}</strong><span>已发布</span></div><div><strong>{local.accounts}</strong><span>已登录的运营账号</span></div>
      </div>
      <p className="desc">成品类型：{kinds.map(([kind, label]) => `${label} ${local.byKind[kind] || 0}`).join(' · ')}</p>
    </Card>}
    <Card title="模型调用">
      {usage === null ? <p className="desc">登录统一账号后显示调用次数和消耗的点数。</p> : <div className="settings-stats">
        <div><strong>{usage.length}</strong><span>最近调用</span></div>
        <div><strong>{spent === undefined ? '—' : (Number(spent / 10000n) / 100).toLocaleString()}</strong><span>消耗点数</span></div>
        <div><strong>{usage.filter(item => item.operation === 'media').length}</strong><span>媒体任务</span></div>
      </div>}
    </Card>
  </SettingsPage>;
}

export function SkillSettings({ onOpenLibrary }: { onOpenLibrary?: () => void }) {
  const [skills, setSkills] = useState<SkillItem[] | null>(null), [error, setError] = useState('');
  useEffect(() => { let alive = true; void fetchSkills().then(value => { if (alive) setSkills(value); }).catch(failure => { if (alive) setError(message(failure)); }); return () => { alive = false; }; }, []);
  const layers: Record<string, string> = { builtin: '内置', shared: '通用', private: '自建', user: '自建' };
  return <SettingsPage title="技能" desc="智能体在对话里可以调用的技能。" action={onOpenLibrary && <button type="button" className="btn btn-sm" onClick={onOpenLibrary}>打开技能库</button>}>
    {error && <p role="alert" className="env-error">{error}</p>}
    {!skills && !error && <p role="status">正在读取技能…</p>}
    {skills && <Card title={`已安装 ${skills.length} 个`}>
      <ul className="settings-list">{skills.map(skill => <li key={skill.name}><div><strong>{skill.name}</strong><p className="desc">{skill.description}</p></div>
        <span className={`settings-tag${skill.needsApi && !skill.apiConfigured ? ' warn' : ''}`}>{skill.needsApi ? (skill.apiConfigured ? '已配置 Key' : '需要配置 Key') : (layers[skill.layer] || skill.layer)}</span></li>)}</ul>
    </Card>}
  </SettingsPage>;
}

/** Shown for pages whose feature is planned but not built yet; it says so instead of offering dead buttons. */
export function ComingSoon({ title, desc, items }: { title: string; desc: string; items: [string, string][] }) {
  return <SettingsPage title={title} desc={desc}>
    <Card>
      <ul className="settings-list">{items.map(([name, note]) => <li key={name}><div><strong>{name}</strong><p className="desc">{note}</p></div><span className="settings-tag">即将推出</span></li>)}</ul>
    </Card>
  </SettingsPage>;
}

export function DeviceSettings() {
  const [info, setInfo] = useState<SystemInfo | null>(null), [error, setError] = useState('');
  useEffect(() => { let alive = true; void fetchSystem().then(value => { if (alive) setInfo(value); }).catch(failure => { if (alive) setError(message(failure)); }); return () => { alive = false; }; }, []);
  return <SettingsPage title="设备" desc="这台电脑的信息、应用版本，以及登录了你账号的设备。">
    <Card title="这台电脑">
      {error && <p role="alert" className="env-error">{error}</p>}
      {info ? <dl className="settings-rows">
        <div><dt>名称</dt><dd>{info.device.name || '未知'}</dd></div><div><dt>系统</dt><dd>{info.device.os}</dd></div>
        <div><dt>芯片架构</dt><dd>{info.device.arch}</dd></div><div><dt>运行环境</dt><dd>Python {info.device.python}</dd></div>
      </dl> : !error && <p role="status">正在读取…</p>}
    </Card>
    <div className="settings-card settings-embed"><UpdateSettings /></div>
    <div className="settings-card settings-embed"><CentralAccountSettings view="devices" /></div>
  </SettingsPage>;
}

export function StorageSettings() {
  const [info, setInfo] = useState<SystemInfo | null>(null), [error, setError] = useState(''), [note, setNote] = useState('');
  const load = useCallback(() => { setError(''); void fetchSystem().then(setInfo).catch(failure => setError(message(failure))); }, []);
  useEffect(load, [load]);
  const open = (key: string) => { setNote(''); void openSystemPlace(key).catch(failure => setNote(message(failure))); };
  const total = info?.storage.reduce((sum, place) => sum + place.bytes, 0) ?? 0;
  return <SettingsPage title="数据存储" desc="所有数据都保存在这台电脑上。" action={<button type="button" className="btn btn-sm" onClick={load}>刷新</button>}>
    {error && <p role="alert" className="env-error">{error}</p>}
    {note && <p role="alert" className="env-error">{note}</p>}
    {!info && !error && <p role="status">正在计算占用空间…</p>}
    {info && <>
      <Card title={`共占用 ${formatBytes(total)}`}>
        <div className="settings-bar" aria-hidden="true">{info.storage.filter(place => place.bytes > 0).map(place => <span key={place.key} className={`seg-${place.key}`} style={{ flexGrow: place.bytes }} title={`${place.label} ${formatBytes(place.bytes)}`} />)}</div>
        <ul className="settings-list">{info.storage.map(place => <li key={place.key}><div><strong><i className={`dot seg-${place.key}`} />{place.label}</strong><p className="desc">{place.desc} · {place.files} 个文件</p><p className="desc mono">{place.path}</p></div>
          <div className="settings-list-side"><span>{formatBytes(place.bytes)}</span><button type="button" className="btn btn-sm" disabled={!place.exists} onClick={() => open(place.key)}>在访达中显示</button></div></li>)}</ul>
      </Card>
      <p className="desc">工作目录：<span className="mono">{info.workspace}</span>。删除的内容先进回收站，可以在「回收站」里彻底清除来释放空间。</p>
    </>}
  </SettingsPage>;
}

export function TrashSettings() {
  const [items, setItems] = useState<TrashItem[] | null>(null), [keep, setKeep] = useState(30), [error, setError] = useState(''), [busy, setBusy] = useState(''), [note, setNote] = useState('');
  const load = useCallback(() => { setError(''); void fetchTrash().then(value => { setItems(value.items); setKeep(value.keep_days); }).catch(failure => setError(message(failure))); }, []);
  useEffect(load, [load]);
  const act = async (id: string, work: () => Promise<unknown>, done: string) => {
    if (busy) return; setBusy(id); setNote(''); setError('');
    try { await work(); setNote(done); load(); } catch (failure) { setError(message(failure)); } finally { setBusy(''); }
  };
  const date = (seconds: number) => new Date(seconds * 1000).toLocaleString();
  const daysLeft = (seconds: number) => Math.max(0, Math.ceil((seconds * 1000 - Date.now()) / 86400000));
  return <SettingsPage title="回收站" desc={`内容库里删除的内容会在这里保留 ${keep} 天，到期自动彻底删除。`}
    action={items && items.length > 0 && <button type="button" className="btn btn-sm btn-danger" disabled={Boolean(busy)} onClick={() => { if (window.confirm(`彻底删除回收站里的 ${items.length} 项？删除后不能恢复。`)) void act('*', emptyTrash, '回收站已清空。'); }}>清空回收站</button>}>
    {error && <p role="alert" className="env-error">{error}</p>}
    {note && <p role="status" className="central-account-notice">{note}</p>}
    {!items && !error && <p role="status">正在读取…</p>}
    {items?.length === 0 && <div className="settings-card settings-empty"><strong>回收站是空的</strong><p className="desc">在内容库里删除的项目和文件会出现在这里。</p></div>}
    {items && items.length > 0 && <Card>
      <ul className="settings-list">{items.map(item => <li key={item.id}><div><strong>{item.kind === 'dir' ? '📁' : '📄'} {item.name}</strong>
        <p className="desc">原位置：内容库/{item.original} · {formatBytes(item.bytes)}{item.kind === 'dir' ? ` · ${item.files} 个文件` : ''}</p>
        <p className="desc">删除于 {date(item.deleted_at)} · {daysLeft(item.expires_at)} 天后自动清除</p></div>
        <div className="settings-list-side"><button type="button" className="btn btn-sm" disabled={Boolean(busy)} onClick={() => void act(item.id, () => restoreTrash(item.id), `已恢复「${item.name}」到内容库。`)}>{busy === item.id ? '处理中…' : '恢复'}</button>
          <button type="button" className="btn btn-sm btn-danger" disabled={Boolean(busy)} onClick={() => { if (window.confirm(`彻底删除「${item.name}」？删除后不能恢复。`)) void act(item.id, () => removeTrash(item.id), `已彻底删除「${item.name}」。`); }}>彻底删除</button></div></li>)}</ul>
    </Card>}
  </SettingsPage>;
}
