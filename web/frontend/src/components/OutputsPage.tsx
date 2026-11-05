import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { CSSProperties } from 'react';
import { fetchOutputs, fetchOutputContent, mediaUrl, deleteOutput } from '../lib/api';
import type { OutputNode, OutputMeta } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { filterLibraryNodes } from '../lib/outputSearch';
import DeliverableActions from './DeliverableActions';
import { deliverableReference, mountPreviewDialog, trapPreviewKey } from '../lib/deliverables';
import type { DeliverableReference } from '../lib/deliverables';
import { IconOutputs, IconImage, IconVideo, IconMusic, IconFile, IconFolder, IconRefresh, IconChevron, IconTrash } from './icons';

const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
  { key: 'text', label: '文档' },
];

const KIND_LABEL: Record<string, string> = {
  article: '文章', 'xhs-note': '小红书', video: '视频', cards: '卡片',
  poster: '海报', audio: '音频', other: '其他',
};
const STATUS_LABEL: Record<string, string> = { draft: '草稿', ready: '待发', published: '已发' };
const STATUS_COLOR: Record<string, string> = { draft: 'var(--text-tertiary)', ready: 'var(--amber)', published: 'var(--green)' };

const badge: CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 999,
  background: 'var(--surface-hover)', color: 'var(--text-secondary)', whiteSpace: 'nowrap',
};
const statusBadge = (s: string): CSSProperties => ({
  ...badge,
  background: `color-mix(in srgb, ${STATUS_COLOR[s] || 'var(--text-tertiary)'} 13%, transparent)`,
  color: STATUS_COLOR[s] || 'var(--text-secondary)',
});

function kindIcon(kind: string | undefined, size = 30) {
  if (kind === 'video') return <IconVideo size={size} />;
  if (kind === 'audio') return <IconMusic size={size} />;
  if (kind === 'image') return <IconImage size={size} />;
  return <IconFile size={size} />;
}
const isHtml = (name: string) => /\.html?$/i.test(name);
const kindLabel = (f: OutputNode) =>
  f.kind === 'text' ? (isHtml(f.name) ? '卡片' : '文档')
    : f.kind === 'image' ? '图片' : f.kind === 'video' ? '视频' : f.kind === 'audio' ? '音频' : '文件';

/** 递归找目录下第一张图/视频作封面缩略图。 */
function firstMedia(node: OutputNode): OutputNode | null {
  if (node.type === 'file') return (node.kind === 'image' || node.kind === 'video') ? node : null;
  for (const c of node.children || []) {
    const m = firstMedia(c);
    if (m) return m;
  }
  return null;
}

/** 展示头声明的封面 → 伪 file 节点（供 Thumb 渲染）。 */
function coverNode(m?: OutputMeta): OutputNode | null {
  if (!m?.cover) return null;
  const kind = /\.(mp4|mov|webm|mkv)$/i.test(m.cover) ? 'video' : 'image';
  return { name: 'cover', type: 'file', path: m.cover, kind } as OutputNode;
}

/** 按名称路径解析到当前目录的 children（stackNames 稳定，刷新后仍有效）。 */
function resolvePath(roots: OutputNode[], names: string[]): OutputNode[] {
  let nodes = roots;
  for (const nm of names) {
    const found = nodes.find((n) => n.type === 'dir' && n.name === nm);
    if (!found) return nodes;   // 路径失效（被删/改）→ 停在能解析到的层
    nodes = found.children || [];
  }
  return nodes;
}

function Thumb({ f, big }: { f: OutputNode | null; big?: boolean }) {
  if (f && f.kind === 'image') return <img src={mediaUrl(f.path)} alt="" loading="lazy" />;
  if (f && f.kind === 'video') return <video src={mediaUrl(f.path)} preload="metadata" muted />;
  return <div className="gcard-ph">{kindIcon(f?.kind, big ? 34 : 30)}</div>;
}

interface OutputsPageProps {
  /** 从对话跳转进来的目录（outputs 相对路径，不含 outputs/ 前缀）；消费后由父层清空 */
  jumpPath?: string;
  onJumpHandled?: () => void;
  resolveSource?: (path: string) => string | undefined;
  onContinue?: (reference: DeliverableReference) => void;
  onSource?: (sessionId: string) => void;
  onPreviewClose?: () => void;
}

export default function OutputsPage({ jumpPath, onJumpHandled, resolveSource, onContinue, onSource, onPreviewClose }: OutputsPageProps) {
  const [roots, setRoots] = useState<OutputNode[]>([]);
  const [treeError, setTreeError] = useState('');
  const [treeLoading, setTreeLoading] = useState(true);
  const treeSeq = useRef(0);
  const [stack, setStack] = useState<string[]>([]);   // 当前所在的文件夹名称路径
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<OutputNode | null>(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewDialog = useRef<HTMLDialogElement>(null);
  const previewOpener = useRef<HTMLElement | null>(null);
  const previewTarget = useRef('');
  const reqSeq = useRef(0);

  const load = useCallback(() => {
    const request = ++treeSeq.current;
    setTreeError(''); setTreeLoading(true);
    fetchOutputs().then(nodes => { if (request === treeSeq.current) setRoots(nodes); })
      .catch(() => { if (request === treeSeq.current) setTreeError('内容库加载失败'); })
      .finally(() => { if (request === treeSeq.current) setTreeLoading(false); });
  }, []);
  useEffect(() => {
    const treeRequests = treeSeq, previewRequests = reqSeq;
    load(); return () => { treeRequests.current++; previewRequests.current++; };
  }, [load]);
  const selectedPath = selected?.path;
  useEffect(() => {
    if (!selectedPath || !previewDialog.current) return;
    return mountPreviewDialog(previewDialog.current, previewOpener.current);
  }, [selectedPath]);
  const closePreview = useCallback(() => { reqSeq.current++; setSelected(null); setLoading(false); onPreviewClose?.(); }, [onPreviewClose]);

  const currentNodes = useMemo(() => resolvePath(roots, stack), [roots, stack]);
  const visibleNodes = useMemo(() => filterLibraryNodes(currentNodes, query), [currentNodes, query]);
  const dirs = useMemo(
    () => visibleNodes.filter((n) => n.type === 'dir').sort((a, b) => (b.mtime || 0) - (a.mtime || 0)),
    [visibleNodes]);
  const files = useMemo(() => {
    const fs = visibleNodes.filter((n) => n.type === 'file').sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    return filter === 'all' ? fs : fs.filter((f) => f.kind === filter);
  }, [visibleNodes, filter]);

  const atTop = stack.length === 0;
  const atProjectRoot = stack.length === 1;
  // 当前项目的展示头（进入项目后才有），用于「成品/素材」分区
  const projectMeta = useMemo(
    () => (stack.length >= 1 ? roots.find((r) => r.name === stack[0])?.meta : undefined),
    [roots, stack]);
  const deliverableSet = useMemo(
    () => new Set(atProjectRoot ? (projectMeta?.deliverablePaths || []) : []),
    [projectMeta, atProjectRoot]);
  const hasSplit = atProjectRoot && deliverableSet.size > 0;
  const deliverableFiles = useMemo(
    () => (hasSplit ? files.filter((f) => deliverableSet.has(f.path)) : []),
    [files, deliverableSet, hasSplit]);
  const restFiles = useMemo(
    () => (hasSplit ? files.filter((f) => !deliverableSet.has(f.path)) : files),
    [files, deliverableSet, hasSplit]);

  const enterDir = useCallback((name: string) => { setStack((s) => [...s, name]); setFilter('all'); setQuery(''); }, []);
  const goTo = useCallback((depth: number) => { setStack((s) => s.slice(0, depth)); setFilter('all'); setQuery(''); }, []);

  const remove = useCallback(async (node: OutputNode, e: React.MouseEvent) => {
    e.stopPropagation();
    const isDir = node.type === 'dir';
    const label = isDir ? `项目/文件夹「${node.meta?.title || node.name}」及其全部内容` : `文件「${node.name}」`;
    if (!window.confirm(`确定删除${label}？\n此操作不可恢复。`)) return;
    try {
      await deleteOutput(node.path);
      setSelected((cur) => (cur?.path === node.path ? null : cur));
      load();
    } catch (err) {
      alert((err as Error).message || '删除失败');
    }
  }, [load]);

  const open = useCallback(async (f: OutputNode, opener?: HTMLElement | null) => {
    const seq = ++reqSeq.current;
    previewTarget.current = f.path;
    if (opener !== undefined) previewOpener.current = opener;
    else if (!previewDialog.current?.open) previewOpener.current = document.activeElement as HTMLElement | null;
    setSelected(f); setContent(''); setPreviewError(''); setLoading(false);
    if (f.kind === 'text') {
      setLoading(true);
      try {
        const res = await fetchOutputContent(f.path);
        if (seq === reqSeq.current) {
          if (res.isBinary) setPreviewError('无法预览');
          else setContent(res.content);
        }
      } catch (error) {
        if (seq === reqSeq.current) {
          const reason = error instanceof Error ? error.message : '';
          setPreviewError(/不存在|404/.test(reason) ? '文件不存在' : /权限|403/.test(reason) ? '无权访问' : '预览加载失败');
        }
      } finally { if (seq === reqSeq.current) setLoading(false); }
    }
  }, []);

  // 等内容树加载后区分文件与目录；文件链接直接打开预览。
  useEffect(() => {
    if (!jumpPath || roots.length === 0) return;
    const parts = jumpPath.split('/').filter(Boolean);
    const parent: string[] = [];
    let nodes = roots;
    let node: OutputNode | undefined;
    for (const part of parts) {
      node = nodes.find(candidate => candidate.name === part);
      if (!node || node.type !== 'dir') break;
      parent.push(part);
      nodes = node.children || [];
    }
    if (node?.path !== parts.join('/')) node = undefined;
    setStack(parent);
    setFilter('all');
    if (node?.type === 'file') void open(node);
    else setSelected(null);
    onJumpHandled?.();
  }, [jumpPath, roots, open, onJumpHandled]);

  const preview = () => {
    if (!selected) return null;
    const url = mediaUrl(selected.path);
    const mediaFailed = () => { if (previewTarget.current === selected.path) setPreviewError('预览加载失败'); };
    if (loading) return <div className="loading" role="status"><div className="spinner" />加载中…</div>;
    if (previewError) return <div role="status">{previewError} <button type="button" className="btn btn-sm" onClick={() => void open(selected, previewOpener.current)}>重新加载</button></div>;
    if (selected.kind === 'image') return <img src={url} alt={selected.name} onError={mediaFailed} style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'video') return <video src={url} controls onError={mediaFailed} style={{ maxWidth: '100%', borderRadius: 'var(--radius)' }} />;
    if (selected.kind === 'audio') return <audio src={url} controls onError={mediaFailed} style={{ width: '100%' }} />;
    if (selected.kind === 'text' && isHtml(selected.name)) return (
      <>
        {/* allow-scripts：让预览页自带的「复制到公众号」按钮(execCommand('copy'))能运行；
            allow="clipboard-write"：授予剪贴板写权限。不给 allow-same-origin —— iframe 保持
            opaque origin，脚本跑得起来但访问不到本站，安全。(修复：内嵌预览里复制按钮点了没反应) */}
        <iframe src={`${url}?v=${selected.mtime ?? 0}`} title={selected.name} onError={mediaFailed} sandbox="allow-scripts" allow="clipboard-write"
          style={{ width: '100%', height: '68vh', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: '#fff' }} />
        <div style={{ marginTop: 8 }}><a href={url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-start)', fontSize: 13 }}>在新标签打开 ↗</a></div>
      </>
    );
    if (selected.kind === 'text') {
      return <div className="outputs-viewer-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />;
    }
    return <div role="status" style={{ color: 'var(--text-secondary)', fontSize: 14 }}>无法预览</div>;
  };

  /** 项目/文件夹卡片：顶层项目用展示头（标题/平台/状态/封面），嵌套子文件夹回退朴素样式。 */
  const renderDir = (d: OutputNode) => {
    const m = d.meta;
    const cover = coverNode(m) || firstMedia(d);
    return (
      <div key={d.path} className="card card-hover gcard output-card">
        <button type="button" className="gcard-open" aria-label={`打开文件夹 ${m?.title || d.name}`} onClick={() => enterDir(d.name)}>
          <div className="gcard-thumb">
            <span className="gcard-kind">{m?.kind ? (KIND_LABEL[m.kind] || m.kind) : '文件夹'}</span>
            {cover ? <Thumb f={cover} big /> : <div className="gcard-ph"><IconFolder size={38} /></div>}
          </div>
          <div className="gcard-meta">
            <div className="gcard-name" title={m?.title || d.name}>{!m && <IconFolder size={13} />} {m?.title || d.name}</div>
            <div className="gcard-sub" style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              {m?.platform && <span style={badge}>{m.platform}</span>}
              {m?.status && <span style={statusBadge(m.status)}>{STATUS_LABEL[m.status] || m.status}</span>}
              <span>{d.fileCount ?? 0} 个文件</span>
            </div>
          </div>
        </button>
        <button type="button" className="gcard-del" aria-label={`删除文件夹 ${m?.title || d.name}`} onClick={(e) => remove(d, e)}><IconTrash size={14} /></button>
      </div>
    );
  };

  const renderFile = (f: OutputNode) => (
    <div key={f.path} className="card card-hover gcard output-card">
      <button type="button" className="gcard-open" aria-label={`打开 ${f.name}`} onClick={(event) => open(f, event.currentTarget)}>
        <div className="gcard-thumb"><span className="gcard-kind">{kindLabel(f)}</span><Thumb f={f} /></div>
        <div className="gcard-meta"><div className="gcard-name" title={f.name}>{f.name}</div></div>
      </button>
      <button type="button" className="gcard-del" aria-label={`删除 ${f.name}`} onClick={(e) => remove(f, e)}><IconTrash size={14} /></button>
    </div>
  );

  const empty = dirs.length === 0 && files.length === 0;

  return (
    <div className="gallery-page">
      <div className="gallery-head">
        <div>
          <h1 className="page-title">
            <IconOutputs size={21} />
            <button type="button" className="crumb" onClick={() => goTo(0)}>内容库</button>
            {stack.map((name, i) => (
              <span key={i}>
                <span className="crumb-sep">/</span>
                {i === stack.length - 1
                  ? (projectMeta?.title && i === 0 ? projectMeta.title : name)
                  : <button type="button" className="crumb" onClick={() => goTo(i + 1)}>{name}</button>}
              </span>
            ))}
          </h1>
          <p className="page-subtitle">
            {atTop ? `${roots.length} 个项目` : `${dirs.length} 个文件夹 · ${files.length} 个文件`}
          </p>
        </div>
        <button className="btn btn-sm" onClick={load}><IconRefresh size={14} /> 刷新</button>
      </div>

      {treeError && <div className="notice-error" role="status">{treeError}</div>}
      <div className="library-search">
        <input className="field" type="search" aria-label="搜索项目或文件" placeholder="搜索项目或文件" value={query} onChange={event => setQuery(event.target.value)} />
        {query && <button className="btn btn-ghost btn-sm" onClick={() => setQuery('')}>清除</button>}
      </div>

      {/* 项目主题标签（进入项目根时展示） */}
      {atProjectRoot && projectMeta?.tags && projectMeta.tags.length > 0 && (
        <div className="gallery-filters" style={{ marginBottom: 4 }}>
          {projectMeta.tags.map((t) => <span key={t} style={badge}>#{t}</span>)}
        </div>
      )}

      {/* 面包屑返回 + 文件过滤（进入任意层后显示） */}
      {stack.length > 0 && (
        <div className="gallery-filters">
          <button className="btn btn-sm" onClick={() => goTo(stack.length - 1)}>
            <span style={{ transform: 'rotate(180deg)', display: 'inline-flex' }}><IconChevron size={13} /></span> 返回上级
          </button>
          {files.length > 0 && FILTERS.map((f) => (
            <button key={f.key} className={`chip ${filter === f.key ? 'active' : ''}`} onClick={() => setFilter(f.key)}>{f.label}</button>
          ))}
        </div>
      )}

      {treeLoading ? <div className="loading" role="status"><div className="spinner" />加载中…</div> : empty && !treeError ? (
        <div className="empty-state" style={{ height: 300 }}>
          <div className="empty-icon"><IconOutputs size={44} /></div>
          <p>{query.trim() ? '没有找到匹配的项目或文件' : atTop ? '暂无成品' : '这个文件夹是空的'}</p>
        </div>
      ) : hasSplit ? (
        <>
          {/* 成品区 */}
          {deliverableFiles.length > 0 && (
            <>
              <div className="section-label" style={{ margin: '6px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
                成品 · {deliverableFiles.length}
              </div>
              <div className="gallery-grid">{deliverableFiles.map(renderFile)}</div>
            </>
          )}
          {/* 素材 / 过程文件区 */}
          {(dirs.length > 0 || restFiles.length > 0) && (
            <>
              <div className="section-label" style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                素材 / 过程文件
              </div>
              <div className="gallery-grid">
                {dirs.map(renderDir)}
                {restFiles.map(renderFile)}
              </div>
            </>
          )}
        </>
      ) : (
        <div className="gallery-grid">
          {dirs.map(renderDir)}
          {files.map(renderFile)}
        </div>
      )}

      {selected && (
        <dialog ref={previewDialog} className="drawer outputs-preview-dialog" aria-labelledby="output-preview-title"
          onCancel={event => { event.preventDefault(); closePreview(); }}
          onKeyDown={event => { if (previewDialog.current) trapPreviewKey(event, previewDialog.current); }}
          onClick={event => { if (event.target === event.currentTarget) closePreview(); }}>
          <div className="drawer-header">
            <div style={{ minWidth: 0 }}>
              <h2 id="output-preview-title" className="skill-detail-title" style={{ fontSize: 16, margin: 0 }}>{selected.name}</h2>
              {selected.mtime && <time dateTime={new Date(selected.mtime * 1000).toISOString()} style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{new Date(selected.mtime * 1000).toLocaleString()}</time>}
            </div>
            <button type="button" className="icon-btn" aria-label="关闭预览" onClick={closePreview}>×</button>
          </div>
          <div className="drawer-body">{preview()}</div>
          <div className="outputs-preview-footer">
            {(() => {
              const reference = deliverableReference(selected.path, selected.name, resolveSource?.(selected.path));
              return reference && <DeliverableActions reference={reference} onContinue={onContinue} onSource={onSource} />;
            })()}
            <button type="button" className="btn btn-sm btn-danger" onClick={(e) => remove(selected, e)}><IconTrash size={13} /> 删除</button>
          </div>
        </dialog>
      )}
    </div>
  );
}
