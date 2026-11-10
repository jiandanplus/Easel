import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import EnvBoard from './EnvBoard';
import ThemeSettings from './ThemeSettings';
import HarnessSettings from './HarnessSettings';
import PlatformAccountSettings from './PlatformAccountSettings';
import MediaGatewaySettings from './MediaGatewaySettings';
import { OptionsDropdown } from './SettingsSelect';
import type { JobView } from './EnvBoard';
import {
  fetchEnvTools, startEnvInstall, fetchEnvJob,
  fetchModelChannels, runChannelSelftest, saveModelConfig,
  fetchLocalAgents, enableLocalAgent, fetchAvailableModels,
} from '../lib/api';
import type { EnvTool, ModelRow, SelftestResult, LocalAgentInfo, MediaCapability, MediaGatewaySettings as MediaGatewayState, HarnessSettingsResponse } from '../lib/api';
import AccountsPage from './AccountsPage';
import ProfileSettings from './ProfileSettings';
import PlanSettings from './PlanSettings';
import { ComingSoon, DeviceSettings, ShortcutSettings, SkillSettings, StatsSettings, StorageSettings, TrashSettings } from './SettingsPages';
import { NAV_ICONS } from './settingsNavIcons';

interface Props {
  onClose: () => void;
  initialSection?: 'model' | 'theme' | 'account' | Sec;
  /** 从「技能」页跳到主界面的技能库。 */
  onOpenPage?: (page: 'skills' | 'profile') => void;
  sectionRequest?: number;
  /** 非空时在面板顶部显示一条提示条。首开未配置模型时由 App 传入。 */
  banner?: string;
}

export type Sec = 'profile' | 'theme' | 'shortcuts' | 'stats' | 'plan' | 'usage' | 'balance' | 'billing'
  | 'model' | 'skills' | 'connectors' | 'chat' | 'accounts' | 'devices' | 'env' | 'trash' | 'storage';
/** 设置侧栏：六组，跟着用户的任务分。 */
export const SETTINGS_GROUPS: { label: string; items: { id: Sec; label: string }[] }[] = [
  { label: '账号', items: [{ id: 'profile', label: '个人中心' }, { id: 'theme', label: '外观' }, { id: 'shortcuts', label: '快捷键' }, { id: 'stats', label: '数据统计' }] },
  { label: '订阅与账单', items: [{ id: 'plan', label: '套餐' }, { id: 'usage', label: '用量' }, { id: 'balance', label: '余额' }, { id: 'billing', label: '账单' }] },
  { label: '智能体', items: [{ id: 'model', label: 'AI 服务商' }, { id: 'skills', label: '技能' }, { id: 'connectors', label: '连接器' }] },
  { label: '连接', items: [{ id: 'chat', label: '聊天平台' }, { id: 'accounts', label: '账号管理' }] },
  { label: '运行资源', items: [{ id: 'devices', label: '设备' }, { id: 'env', label: '运行环境' }] },
  { label: '其他', items: [{ id: 'trash', label: '回收站' }, { id: 'storage', label: '数据存储' }] },
];
type Chan = 'chat' | 'transcribe' | 'speech' | 'image' | 'video' | 'music';

const CHANNELS: { id: Chan; label: string }[] = [
  { id: 'chat', label: '对话与脚本' },
  { id: 'transcribe', label: '语音转写' },
  { id: 'speech', label: '配音' },
  { id: 'image', label: '生图' },
  { id: 'video', label: '视频' },
  { id: 'music', label: '音乐' },
];

/** 后台繁忙（整机高负载）时的抗抖动取数：单次超时即重试，撑过多秒级接口延迟。 */
async function fetchWithRetry<T>(fn: () => Promise<T>, tries = 4, timeoutMs = 18000): Promise<T> {
  let lastErr: unknown = new Error('取数失败');
  for (let i = 0; i < tries; i += 1) {
    try {
      // eslint-disable-next-line no-await-in-loop
      return await new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('请求超时')), timeoutMs);
        fn().then(
          (v) => { clearTimeout(timer); resolve(v); },
          (e) => { clearTimeout(timer); reject(e); },
        );
      });
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

/** 后端放在 model/baseUrl 里的展示占位串——提交前要清掉，它们不是真实配置值。 */
const PLACEHOLDERS = new Set(['—', '官方', '（未配置）', '本机', '内建默认']);

const hhmm = (ts: number) => {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** 设置（统一入口）：竖＝功能分类（模型配置 / 环境安装 / 更多设置），横＝模型六通道。 */
export default function SettingsPanel({ onClose, banner = '', initialSection = 'model', sectionRequest = 0, onOpenPage }: Props) {
  const sectionOf = (value: Props['initialSection']): Sec => value === 'account' ? 'profile' : value || 'model';
  const [sec, setSec] = useState<Sec>(sectionOf(initialSection));
  const [collapsed, setCollapsed] = useState<string[]>([]);
  useEffect(() => { setSec(sectionOf(initialSection)); }, [initialSection, sectionRequest]);
  const [chan, setChan] = useState<Chan>('chat');
  const [harness, setHarness] = useState('');
  const [selectedHarness, setSelectedHarness] = useState('');
  const [harnessSettings, setHarnessSettings] = useState<HarnessSettingsResponse | null>(null);
  const usesSubscription = harnessSettings?.service_source === 'subscription';
  const subscriptionLabel = harnessSettings?.subscription_backend === 'central' ? '中央订阅' : '旧版订阅';
  const setupBanner = banner && usesSubscription
    ? `${subscriptionLabel} · ${harnessSettings.subscription_model ? '模型已保存，请新建对话' : '请选择模型并保存底座'}`
    : banner;
  const [mediaGateway, setMediaGateway] = useState<MediaGatewayState | null>(null);
  const [mediaGatewayRevision, setMediaGatewayRevision] = useState(0);
  const onMediaGatewayChange = useCallback((value: MediaGatewayState) => {
    setMediaGateway(value);
    setMediaGatewayRevision(revision => revision + 1);
  }, []);
  const mediaCapability: MediaCapability | null = chan === 'speech' ? 'voice' : ['image', 'video', 'music'].includes(chan) ? chan as MediaCapability : null;
  const usesMediaGateway = !!mediaCapability && mediaGateway?.managed[mediaCapability] === true;

  // ── 环境安装（引擎真实数据） ──────────────────────────────
  const [tools, setTools] = useState<EnvTool[]>([]);
  const [python, setPython] = useState('');
  const [envLoading, setEnvLoading] = useState(true);
  const [envError, setEnvError] = useState('');
  const [jobs, setJobs] = useState<Record<string, JobView>>({});
  const runningRef = useRef(false);

  const refreshEnv = useCallback(async (force = false) => {
    setEnvLoading(true);
    setEnvError('');
    try {
      const d = await fetchWithRetry(() => fetchEnvTools(force));
      setTools(d.tools || []);
      setPython(d.python || '');
    } catch (e) {
      setEnvError(e instanceof Error ? `环境体检失败：${e.message}` : '环境体检失败');
    } finally {
      setEnvLoading(false);
    }
  }, []);

  useEffect(() => {
    if (sec === 'env') void refreshEnv();
  }, [sec, refreshEnv]);

  const batchMode = useRef(false);

  // 装完统一收尾：刷新体检（期间卡片显示「校验中」），然后清掉成功的 job 记录、保留失败（带原因）
  const settleJobs = useCallback(async () => {
    await refreshEnv(true);
    setJobs((j) => {
      const n: Record<string, JobView> = {};
      Object.entries(j).forEach(([k, v]) => { if (v.state === 'fail') n[k] = v; });
      return n;
    });
  }, [refreshEnv]);

  const pollJob = useCallback((id: string, jobId: string) => new Promise<void>((resolve) => {
    let fails = 0;
    const timer = setInterval(async () => {
      try {
        const st = await fetchEnvJob(jobId);
        const last = (st.lines || [])[st.lines.length - 1] || '';
        setJobs((j) => ({
          ...j,
          [id]: { state: st.state, line: last.trim().slice(0, 140), detail: st.result?.detail || null },
        }));
        if (st.state !== 'running') { clearInterval(timer); resolve(); }
      } catch {
        fails += 1;
        if (fails >= 3) { clearInterval(timer); resolve(); }   // 服务重启/任务丢失：放弃轮询
      }
    }, 1500);
  }), []);

  const installOne = useCallback(async (id: string) => {
    setJobs((j) => ({ ...j, [id]: { state: 'running', line: '启动中…' } }));
    try {
      const { jobId } = await startEnvInstall(id);
      await pollJob(id, jobId);
    } catch (e) {
      setJobs((j) => ({ ...j, [id]: { state: 'fail', line: '', detail: e instanceof Error ? e.message : '启动失败' } }));
    }
    if (!batchMode.current) await settleJobs();   // 单卡装完立即收尾刷新
  }, [pollJob, settleJobs]);

  const installMany = useCallback(async (ids: string[]) => {
    runningRef.current = true;
    batchMode.current = true;
    setJobs((j) => {
      const n = { ...j };
      ids.forEach((id) => { n[id] = { state: 'running', line: '排队中…' }; });
      return n;
    });
    for (const id of ids) {
      // eslint-disable-next-line no-await-in-loop
      await installOne(id);
    }
    batchMode.current = false;
    runningRef.current = false;
    await settleJobs();
  }, [installOne, settleJobs]);

  const anyRunning = runningRef.current || Object.values(jobs).some((j) => j.state === 'running');
  const okCount = tools.filter((t) => t.state === 'ok').length;
  const total = tools.length;

  // ── 模型配置（真值只读 + 真自测） ────────────────────────
  const [chatRows, setChatRows] = useState<ModelRow[]>([]);
  const [transRows, setTransRows] = useState<ModelRow[]>([]);
  const [mediaRows, setMediaRows] = useState<Record<string, ModelRow[]>>({});
  const [modelLoading, setModelLoading] = useState(true);
  const [modelErr, setModelErr] = useState('');
  const [selftest, setSelftest] = useState<{ testedAt: number; byBase: Record<string, SelftestResult> } | null>(null);
  const [testing, setTesting] = useState(false);
  const [selftestNote, setSelftestNote] = useState('');
  // 本机 agent CLI（Claude Code / Gemini CLI…）：装了并登录过就免 API key
  const [localAgents, setLocalAgents] = useState<LocalAgentInfo[]>([]);
  const [localAgentsErr, setLocalAgentsErr] = useState('');
  const [enabling, setEnabling] = useState('');
  // 每个本机 agent 行上选中的模型（'' = 用该家默认）。
  const [laModels, setLaModels] = useState<Record<string, string>>({});
  const [localAgentNote, setLocalAgentNote] = useState('');

  // 拉取模型列表：{ 行号 → { loading, models, err } }。结果按 baseUrl 落 localStorage（7 天过期），
  // 刷新页面/重开设置不用重新拉。
  const [modelLists, setModelLists] = useState<Record<number, { loading: boolean; models: string[]; err: string; fetchedAt?: number }>>({});
  // localStorage 里按 baseUrl 存的拉取结果（7 天过期），初始化时一次性读入。
  const [modelCache, setModelCache] = useState<Record<string, { models: string[]; fetchedAt: number }>>(() => {
    try {
      const raw = localStorage.getItem('easel_model_lists');
      return raw ? JSON.parse(raw) : {};
    } catch { return {}; }
  });
  // 自定义下拉面板的展开状态与收起检测
  const [openDd, setOpenDd] = useState<Record<number, boolean>>({});
  const [protoDd, setProtoDd] = useState<Record<number, boolean>>({});
  const protoRefs = useRef<Record<number, HTMLSpanElement | null>>({});
  const modelCellRefs = useRef<Record<number, HTMLSpanElement | null>>({});

  useEffect(() => {
    if (harness !== 'openclaw') return;
    let alive = true;
    fetchWithRetry(() => fetchLocalAgents(), 3, 10000)
      .then((d) => { if (alive) setLocalAgents(d.agents || []); })
      .catch((e) => { if (alive) setLocalAgentsErr(e instanceof Error ? e.message : '探测失败'); });
    return () => { alive = false; };
  }, [harness]);

  const doEnableAgent = useCallback(async (id: string, model = '') => {
    setEnabling(id);
    setLocalAgentNote('');
    try {
      const d = await enableLocalAgent(id, model);
      setLocalAgentNote(d.note || '已接入');
      setLocalAgents((as) => as.map((a) => (a.id === id ? { ...a, configured: true } : a)));
    } catch (e) {
      setLocalAgentNote(e instanceof Error ? e.message : '接入失败');
    } finally {
      setEnabling('');
    }
  }, []);

  const pullModels = useCallback(async (i: number, r: ModelRow) => {
    setModelLists((m) => ({ ...m, [i]: { loading: true, models: m[i]?.models || [], err: '' } }));
    try {
      const proto = r.slot === 'custom' ? (r.protocol || 'openai') : (r.type === 'anthropic' ? 'anthropic' : 'openai');
      const d = await fetchAvailableModels(r.baseUrl || '', r.keyNew || '', proto, r.slot || '', r.name || '');
      const fetchedAt = Date.now();
      setModelLists((m) => ({ ...m, [i]: { loading: false, models: d.models, err: d.models.length ? '' : '该端点没有返回模型', fetchedAt } }));
      if (d.models.length) {
        setModelCache((c) => {
          const next = { ...c, [cacheKeyFor(r)]: { models: d.models, fetchedAt } };
          try { localStorage.setItem('easel_model_lists', JSON.stringify(next)); } catch { /* 忽略 */ }
          return next;
        });
      }
    } catch (e) {
      setModelLists((m) => ({ ...m, [i]: { loading: false, models: m[i]?.models || [], err: e instanceof Error ? e.message : '拉取失败' } }));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    if (!harness) return;
    setModelLoading(true);
    const configuration = selectedHarness === 'api' || selectedHarness === 'openclaw' ? selectedHarness : undefined;
    fetchWithRetry(() => fetchModelChannels(configuration), 5, 15000)
      .then((d) => {
        if (!alive) return;
        setChatRows(d.channels.chat.rows || []);
        setTransRows(d.channels.transcribe.rows || []);
        setMediaRows({
          image: (d.channels.image?.rows || []).filter(row => row.slot !== 'new-api'),
          video: (d.channels.video?.rows || []).filter(row => row.slot !== 'new-api'),
          music: (d.channels.music?.rows || []).filter(row => row.slot !== 'new-api'),
          speech: (d.channels.speech?.rows || []).filter(row => row.slot !== 'new-api'),
        });
        setModelErr('');
      })
      .catch((e) => { if (alive) setModelErr(e instanceof Error ? e.message : '模型配置读取失败'); })
      .finally(() => { if (alive) setModelLoading(false); });
    return () => { alive = false; };
  }, [harness, selectedHarness, mediaGatewayRevision]);

  const doSelftest = useCallback(async (channel: string) => {
    if (usesMediaGateway) return;
    setTesting(true);
    setSelftestNote('');
    try {
      const r = await runChannelSelftest(channel);
      const byBase: Record<string, SelftestResult> = {};
      r.results.forEach((x) => { byBase[x.baseUrl] = x; });
      setSelftest({ testedAt: r.testedAt, byBase });
      if (!r.results.length) setSelftestNote('没有可自测的通道（未配置 key）');
      void refreshEnv();
    } catch (e) {
      setSelftestNote(e instanceof Error ? `自测失败：${e.message}` : '自测失败');
    } finally {
      setTesting(false);
    }
  }, [refreshEnv, usesMediaGateway]);

  // ── 模型配置可编辑（v2）：保存到 .env / openclaw ──────────
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState('');

  const saveCurrent = useCallback(async () => {
    const rows = chan === 'chat' ? chatRows : chan === 'transcribe' ? transRows : (mediaRows[chan] || []);
    const payload = rows
      .filter((r) => r.slot && r.slot !== 'new-api')
      .map((r) => ({
        slot: r.slot as string,
        name: r.slot === 'custom' ? r.name.trim().toLowerCase() : '',
        // 后端在这些字段里塞的是展示占位（'官方'/'（未配置）'/'本机'/'—'），不是真值：
        // 原样回传会被后端的 Base URL 校验打成 400，导致该行永远保存不了。
        model: PLACEHOLDERS.has(r.model) ? '' : r.model,
        baseUrl: PLACEHOLDERS.has(r.baseUrl) ? '' : r.baseUrl,
        key: r.keyNew || '',
        key2: r.keyNew2 || '',
        primary: r.role === '主',
        // 协议只对自定义供应商有意义；其余行后端不读这个字段
        protocol: r.slot === 'custom' ? (r.protocol || 'openai') : undefined,
        thinking: r.slot === 'custom' ? r.thinking : undefined,
        thinkingFormat: r.slot === 'custom' && r.thinking && r.protocol !== 'anthropic'
          ? (r.thinkingFormat || undefined) : undefined,
      }));
    if (!payload.length) {
      setSavedNote('当前通道没有可保存的配置');
      return;
    }
    setSaving(true);
    setSavedNote('');
    try {
      const d = await fetchWithRetry(() => saveModelConfig(chan, payload), 3, 20000);
      setChatRows(d.channels.chat.rows || []);
      setTransRows(d.channels.transcribe.rows || []);
      setMediaRows({
        image: (d.channels.image?.rows || []).filter(row => row.slot !== 'new-api'),
        video: (d.channels.video?.rows || []).filter(row => row.slot !== 'new-api'),
        music: (d.channels.music?.rows || []).filter(row => row.slot !== 'new-api'),
        speech: (d.channels.speech?.rows || []).filter(row => row.slot !== 'new-api'),
      });
      setSavedNote(d.note ? `✓ 已保存（${d.note}）` : '✓ 已保存');
      void refreshEnv();
    } catch (e) {
      setSavedNote(e instanceof Error ? `保存失败：${e.message}` : '保存失败');
    } finally {
      setSaving(false);
      setTimeout(() => setSavedNote(''), 6000);
    }
  }, [chan, chatRows, transRows, mediaRows, refreshEnv]);

  // Esc 关闭
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 本地兜底 whisper：从环境工具状态推出来（fw + 模型都在才算就绪）
  const fw = tools.find((t) => t.id === 'fw');
  const modelTool = tools.find((t) => t.id === 'model');
  const localReady = fw?.state === 'ok' && modelTool?.state === 'ok';
  const localRow: ModelRow = {
    order: 2, name: 'local-whisper', sub: '本机兜底 · 免 key', type: 'local',
    model: 'faster-whisper', baseUrl: '本机', keyMasked: '—', role: '备',
    result: localReady ? '✓ 已就绪' : (tools.length ? '未装（去环境安装）' : '检测中…'),
  };

  const resultText = (row: ModelRow): { text: string; cls: string } => {
    const st = row.baseUrl && row.baseUrl !== '—' ? selftest?.byBase[row.baseUrl] : undefined;
    if (st) return st.ok ? { text: `✓ ${st.ms}ms`, cls: 'good' } : { text: `✗ ${(st.detail || '失败').slice(0, 42)}`, cls: 'bad' };
    if (row.result.includes('已就绪') || row.result.includes('已配置')) return { text: row.result, cls: 'good' };
    if (row.result.includes('缺') || row.result.includes('未装')) return { text: row.result, cls: 'warn-text' };
    return { text: row.result, cls: '' };
  };

  const SLOT_EDIT: Record<string, { model: boolean; base: boolean }> = {
    'direct-api': { model: true, base: true },
    openai: { model: true, base: true },
    relay: { model: true, base: true },
    // anthropic 也要能改 Base URL：官方直连之外，中转站/自建网关/兼容代理都靠它
    // （后端 _SLOT_ENV_KEYS 已含 anthropic，改地址必须重填 Key 那道闸同样生效）。
    anthropic: { model: true, base: true },
    siliconflow: { model: false, base: true },
    custom: { model: true, base: true },
  };

  const addProvider = () => {
    setChatRows((rs) => [...rs, {
      slot: 'custom', order: 0, name: '', sub: '自定义', type: 'openai',
      model: '', baseUrl: '', keyMasked: '', role: '备', result: '待保存',
      thinking: false, thinkingFormat: 'openai',
    }]);
  };

  const setPrimaryRow = (i: number) =>
    setChatRows((rs) => rs.map((r, j) => (r.slot ? { ...r, role: j === i ? '主' : '备' } : r)));

  const removeRow = (i: number) =>
    setChatRows((rs) => {
      const gone = rs[i];
      const left = rs.filter((_, j) => j !== i);
      if (gone && gone.role === '主') {
        const first = left.findIndex((r) => r.slot);
        if (first >= 0) left[first] = { ...left[first], role: '主' };
      }
      return left;
    });

  const updateRow = (
    setRows: Dispatch<SetStateAction<ModelRow[]>>,
    i: number,
    patch: Partial<ModelRow>,
  ) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const updateMediaRow = (ch: string, i: number, patch: Partial<ModelRow>) =>
    setMediaRows((m) => ({ ...m, [ch]: (m[ch] || []).map((r, j) => (j === i ? { ...r, ...patch } : r)) }));

  const setMediaPrimary = (ch: string, i: number) =>
    setMediaRows((m) => ({ ...m, [ch]: (m[ch] || []).map((r, j) => ({ ...r, role: j === i ? '主' : '备' })) }));

  const mediaOk = (ch: string) => (mediaRows[ch] || []).some((r) => r.result === '已配置');

  const cacheKeyFor = (r: ModelRow) => {
    const base = (r.baseUrl || '').trim().replace(/\/+$/, '');
    if (!base) return '';
    return `${base}::${r.slot === 'custom' ? (r.protocol || 'openai') : (r.type === 'anthropic' ? 'anthropic' : 'openai')}`;
  };

  const cachedListFor = (r: ModelRow) => {
    const key = cacheKeyFor(r);
    if (!key) return undefined;
    const hit = modelCache[key];
    const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
    return hit && hit.fetchedAt > weekAgo && hit.models.length ? hit : undefined;
  };

  const renderBoard = (
    rows: ModelRow[],
    ops?: { onRow?: (i: number, patch: Partial<ModelRow>) => void; onPrimary?: (i: number) => void; onRemove?: (i: number) => void; media?: boolean },
  ) => (
    modelLoading && rows.length === 0 ? (
      <div className="board"><div className="empty"><span className="spin" /> 正在读取配置…</div></div>
    ) : rows.length === 0 ? (
      <div className="board"><div className="empty">还没有配置。</div></div>
    ) : (
      <div className="board">
        <div className="prow head">
          <span>顺序</span><span>供应商</span><span>类型</span><span>模型</span>
          <span>Base URL</span><span>API Key</span><span>角色</span><span>上次结果</span>
          <span />
        </div>
        {rows.map((r, i) => {
          const rt = resultText(r);
          const ed = SLOT_EDIT[r.slot || '']
            || (ops?.media && r.slot
              ? { model: r.modelEditable !== false, base: r.baseEditable !== false }
              : undefined);
          const isCustom = r.slot === 'custom';
          return (
            <div className="prow" key={i}>
              <span className={`step${r.order === 0 ? ' ghost' : ''}`}>{isCustom ? i + 1 : r.order}</span>
              {isCustom ? (
                <span className="pname">
                  <input
                    className="mock"
                    value={r.name}
                    placeholder="名称"
                    onChange={(e) => ops?.onRow?.(i, { name: e.target.value.toLowerCase() })}
                  />
                </span>
              ) : (
                <span className="pname">{r.name}<small>{r.sub}</small></span>
              )}
              {isCustom && ed && ed.base ? (
                <span
                  className="proto-trigger"
                  role="button"
                  tabIndex={0}
                  aria-haspopup="listbox"
                  aria-expanded={!!protoDd[i]}
                  ref={(el) => { protoRefs.current[i] = el; }}
                  title="接口协议"
                  onClick={() => setProtoDd((s) => ({ ...s, [i]: !s[i] }))}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setProtoDd((state) => ({ ...state, [i]: !state[i] }));
                    }
                  }}
                >
                  <span className="proto-label">{r.protocol === 'anthropic' ? 'anthropic' : 'openai'}</span>
                  {protoDd[i] && (
                    <OptionsDropdown
                      anchor={protoRefs.current[i]}
                      items={[{ value: 'openai', label: 'openai（OpenAI 兼容）' }, { value: 'anthropic', label: 'anthropic（原生 /v1/messages）' }]}
                      current={r.protocol || 'openai'}
                      onPick={(v) => {
                        ops?.onRow?.(i, { protocol: v });
                        // 协议变了，旧协议拉来的模型列表不再适用：清内存列表 + 收起
                        setModelLists((m) => {
                          const next = { ...m };
                          delete next[i];
                          return next;
                        });
                        setProtoDd((s) => ({ ...s, [i]: false }));
                        setOpenDd((state) => ({ ...state, [i]: false }));
                      }}
                      onClose={() => setProtoDd((s) => ({ ...s, [i]: false }))}
                    />
                  )}
                </span>
              ) : isCustom ? (
                <span title="保存后可切换协议">{r.protocol === 'anthropic' ? 'anthropic' : 'openai'}</span>
              ) : (
                <span>{r.type}</span>
              )}
              {ed && ed.model && (!ops?.media || r.adv) ? (
                <span className={`model-cell${isCustom ? ' custom-model-cell' : ''}`} ref={(el) => { modelCellRefs.current[i] = el; }}>
                  <input
                    className="mock"
                    value={r.model}
                    placeholder={isCustom ? '模型名' : ''}
                    onChange={(e) => ops?.onRow?.(i, { model: e.target.value })}
                    onFocus={() => setOpenDd((s) => ({ ...s, [i]: true }))}
                  />
                  {openDd[i] ? (
                    <FixedDropdown
                      anchor={modelCellRefs.current[i]}
                      models={modelLists[i]?.models.length ? modelLists[i].models : (cachedListFor(r)?.models || [])}
                      current={r.model}
                      emptyHint={modelLists[i]?.err || undefined}
                      onPull={() => void pullModels(i, r)}
                      pulling={modelLists[i]?.loading}
                      onPick={(mid) => { ops?.onRow?.(i, { model: mid }); setOpenDd((s) => ({ ...s, [i]: false })); }}
                      onClose={() => setOpenDd((s) => ({ ...s, [i]: false }))}
                    />
                  ) : null}
                  {(() => {
                    const mem = modelLists[i];
                    const cached = mem?.fetchedAt ? undefined : cachedListFor(r);
                    const has = !!(mem?.fetchedAt || cached);
                    return (
                      <button
                        className={`fetch-btn${has ? ' ok' : ''}`}
                        title={has ? `已拉取 ${(mem?.models.length || cached!.models.length)} 个模型，点击重新拉取` : '从该端点拉取可用模型列表'}
                        onClick={() => {
                          setOpenDd((s) => ({ ...s, [i]: true }));
                          void pullModels(i, r);
                        }}
                        disabled={mem?.loading}
                      >
                        {mem?.loading
                          ? <><span className="spin" />拉取中</>
                          : has
                            ? <span title={`已拉取 ${(mem?.models.length || cached!.models.length)} 个模型，点输入框选择`}>✓</span>
                            : '拉取模型'}
                      </button>
                    );
                  })()}
                  {modelLists[i]?.err && (
                    <span className="fetch-err" title={modelLists[i].err}>⚠</span>
                  )}
                  {isCustom ? (
                    <span className="thinking-controls">
                      <label title="仅在上游模型支持推理时开启；格式须与供应商接口一致">
                        <input
                          type="checkbox"
                          checked={r.thinking === true}
                          onChange={(e) => ops?.onRow?.(i, { thinking: e.target.checked })}
                        />
                        该模型会思考
                      </label>
                      {r.thinking && r.protocol !== 'anthropic' ? (
                        <select
                          aria-label={`${r.name || '自定义供应商'}思考格式`}
                          value={r.thinkingFormat || ''}
                          onChange={(e) => ops?.onRow?.(i, { thinkingFormat: e.target.value })}
                        >
                          <option value="">保留现有 / 自动</option>
                          <option value="openai">OpenAI</option>
                          <option value="deepseek">DeepSeek</option>
                          <option value="openrouter">OpenRouter</option>
                          <option value="together">Together</option>
                          <option value="qwen">Qwen</option>
                          <option value="qwen-chat-template">Qwen chat template</option>
                          <option value="zai">ZAI</option>
                        </select>
                      ) : null}
                    </span>
                  ) : null}
                </span>
              ) : (
                <span className={`cell-text${ops?.media && !r.model ? ' dim' : ''}`} title={r.model || '内建默认'}>
                  {r.model || (ops?.media ? '默认（内建）' : '')}
                </span>
              )}
              {ed && ed.base && (!ops?.media || r.adv) ? (
                <input className="mock" value={r.baseUrl} placeholder="https://…" onChange={(e) => ops?.onRow?.(i, { baseUrl: e.target.value })} />
              ) : (
                <span className={`cell-text${ops?.media && !r.baseUrl ? ' dim' : ''}`} title={r.baseUrl || '内建默认'}>
                  {r.baseUrl
                    || (ops?.media
                      ? r.baseOptional === false
                        ? '需填写（点「高级」）'
                        : '默认（内建）'
                      : '')}
                </span>
              )}
              {ed ? (
                r.key2Label ? (
                  <span className="key-stack">
                    <input
                      className="mock key-input"
                      type="password"
                      value={r.keyNew || ''}
                      placeholder={r.keyMasked || 'Key'}
                      onChange={(e) => ops?.onRow?.(i, { keyNew: e.target.value })}
                    />
                    <input
                      className="mock key-input"
                      type="password"
                      value={r.keyNew2 || ''}
                      placeholder={r.key2Masked || r.key2Label}
                      onChange={(e) => ops?.onRow?.(i, { keyNew2: e.target.value })}
                    />
                  </span>
                ) : (
                  <input
                    className="mock key-input"
                    type="password"
                    value={r.keyNew || ''}
                    placeholder={r.keyMasked || '粘贴 Key'}
                    onChange={(e) => ops?.onRow?.(i, { keyNew: e.target.value })}
                  />
                )
              ) : (
                <span className="cell-text key-mask" title="密钥不显示明文">{r.keyMasked || '—'}</span>
              )}
              {r.slot && ops?.onPrimary ? (
                <button
                  className={`tag ${r.role === '主' ? 'main' : 'backup'}`}
                  onClick={() => ops.onPrimary?.(i)}
                  title="设为主通道"
                >
                  {r.role}
                </button>
              ) : (
                <span className={`tag ${r.role === '主' ? 'main' : 'backup'}`}>{r.role}</span>
              )}
              <span className={`stt ${rt.cls}`}>{rt.text}</span>
              {isCustom && ops?.onRemove ? (
                <button className="row-del" onClick={() => ops.onRemove?.(i)} title="删除该供应商">✕</button>
              ) : ops?.media && r.slot ? (
                <button className="adv-btn" onClick={() => ops?.onRow?.(i, { adv: !r.adv })}>
                  {r.adv ? '收起' : '高级'}
                </button>
              ) : (
                <span />
              )}
            </div>
          );
        })}
      </div>
    )
  );

  const chatOk = chatRows.length > 0 && !chatRows[0].result.includes('缺');
  const transOk = transRows.length > 1 && transRows[1].result.includes('已配置');

  return (
    <div className="settings-page settings-overlay">
      <div className="settings-panel" role="region" aria-label="设置">
        <div className="settings-head">
          <div>
            <h2 className="settings-title">设置</h2>
          </div>
          <div className="settings-actions">
            {sec === 'model' && <>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => void saveCurrent()}
              disabled={saving || modelLoading || sec !== 'model' || (chan === 'chat' && (selectedHarness !== harness || !['api', 'openclaw'].includes(harness)))}
              title={usesMediaGateway ? '保存供应商配置' : chan === 'chat' && usesSubscription ? `${subscriptionLabel} · 请使用「保存底座」` : chan === 'chat' && !['api', 'openclaw'].includes(harness) ? '请使用「保存底座」或「保存供应商」' : undefined}
            >
              {saving ? '保存中…' : '保存配置'}
            </button>
            <button
              className="btn btn-sm"
              onClick={() => void doSelftest(sec === 'model' ? chan : 'all')}
              disabled={usesMediaGateway || testing || modelLoading || selectedHarness !== harness || (!['api', 'openclaw'].includes(harness) && (sec !== 'model' || chan === 'chat'))}
              title={usesMediaGateway ? '统一网关自测暂不可用' : chan === 'chat' && usesSubscription ? `${subscriptionLabel} · 自测暂不可用` : !['api', 'openclaw'].includes(harness) ? '当前引擎自测暂不可用' : undefined}
            >
              {testing ? '自测中…' : '全部自测'}
            </button>
            </>}
            <button className="settings-close" onClick={onClose} title="关闭（Esc）">✕</button>
          </div>
        </div>

        {setupBanner && (
          <div
            role="status"
            style={{
              margin: '0 16px 12px', padding: '10px 12px', borderRadius: 8,
              background: 'var(--warn-bg, #fff7ed)', color: 'var(--warn-fg, #9a3412)',
              border: '1px solid var(--warn-border, #fed7aa)', fontSize: 13, lineHeight: 1.5,
            }}
          >
            {setupBanner}
          </div>
        )}

        <div className="settings-body">
          <nav className="settings-nav settings-nav-grouped" aria-label="设置分类">
            {SETTINGS_GROUPS.map(group => {
              const open = !collapsed.includes(group.label);
              return <div className="snav-group" key={group.label}>
                <button type="button" className="snav-group-title" aria-expanded={open} onClick={() => setCollapsed(list => open ? [...list, group.label] : list.filter(item => item !== group.label))}>{group.label}<span aria-hidden="true">{open ? '▾' : '▸'}</span></button>
                {open && group.items.map(item => <button type="button" key={item.id} className={`snav${sec === item.id ? ' active' : ''}`} aria-current={sec === item.id ? 'page' : undefined} onClick={() => setSec(item.id)}>
                  {NAV_ICONS[item.id]}<span>{item.label}</span>{item.id === 'env' && total > 0 && <small>{okCount === total ? '全就绪' : `${okCount}/${total}`}</small>}
                </button>)}
              </div>;
            })}
          </nav>

          <div className="settings-main">
            {sec === 'profile' && <ProfileSettings onOpenPersonas={onOpenPage ? () => onOpenPage('profile') : undefined} onOpenAccounts={() => setSec('accounts')} />}
            {sec === 'plan' && <PlanSettings onOpenBilling={() => setSec('billing')} />}
            {(sec === 'usage' || sec === 'balance' || sec === 'billing') && <PlatformAccountSettings view={sec} />}
            {sec === 'theme' && <ThemeSettings />}
            {sec === 'shortcuts' && <ShortcutSettings />}
            {sec === 'stats' && <StatsSettings />}
            {sec === 'skills' && <SkillSettings onOpenLibrary={onOpenPage ? () => onOpenPage('skills') : undefined} />}
            {sec === 'connectors' && <ComingSoon title="连接器" desc="让智能体读写你常用的工具和资料，比如网盘、文档和数据库。" items={[['MCP 服务器', '接入支持 MCP 协议的工具，智能体可以直接调用'], ['云文档与网盘', '飞书文档、Notion、Google Drive 等'], ['浏览器', '让智能体打开网页、读取内容']]} />}
            {sec === 'chat' && <ComingSoon title="聊天平台" desc="在常用的聊天软件里直接和智能体对话、接收任务结果。" items={[['飞书', '在飞书群或私聊里使用智能体'], ['企业微信', '在企业微信里使用智能体'], ['钉钉', '在钉钉里使用智能体'], ['Telegram', '在 Telegram 里使用智能体']]} />}
            {sec === 'accounts' && <div className="settings-embed-page"><AccountsPage /></div>}
            {sec === 'devices' && <DeviceSettings />}
            {sec === 'trash' && <TrashSettings />}
            {sec === 'storage' && <StorageSettings />}
            {sec === 'model' && (
              <section className="st-sec active">
                <MediaGatewaySettings onChange={onMediaGatewayChange} />
                <div className="tabbar">
                  {CHANNELS.map((c) => (
                    <button key={c.id} className={`tab${chan === c.id ? ' active' : ''}`} onClick={() => setChan(c.id)}>
                      <span className="cdot" />{c.label}
                    </button>
                  ))}
                </div>
                {savedNote ? <div className={`save-note${savedNote.startsWith('保存失败') || savedNote.startsWith('没有') ? ' err' : ''}`}>{savedNote}</div> : null}

                {chan === 'chat' && <HarnessSettings onChange={setHarness} onSelect={setSelectedHarness} onSettingsChange={setHarnessSettings} />}
                {chan === 'chat' && (selectedHarness === 'api' || selectedHarness === 'openclaw') && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <strong>{selectedHarness === 'api' ? '仅聊天模式 API 配置' : 'OpenClaw API 配置'}</strong>
                      <span className={`pill ${chatOk ? 'ok' : 'off'}`}><span className="dot" />{chatOk ? '已配置' : '未配置'}</span>
                      {selftest && <span className="desc">上次自测 {hhmm(selftest.testedAt)}</span>}
                      <span className="spacer" />
                      <button className="btn btn-sm" onClick={() => void doSelftest('chat')} disabled={testing || selectedHarness !== harness}>自测本通道</button>
                    </div>
                    {renderBoard(chatRows, { onRow: (i, p) => updateRow(setChatRows, i, p), onPrimary: setPrimaryRow, onRemove: removeRow })}
                    {selectedHarness === 'openclaw' && <div className="add-row" onClick={addProvider}>＋ 添加供应商</div>}
                    {harness === 'openclaw' && (() => {
                      // 本机 agent 区块：装了 Claude Code / Gemini CLI 并登录过的用户不需要填 API Key。
                      // 只展示「已装」的行 —— 没装的用户看一眼全是灰的只会困惑。
                      const shown = localAgents.filter((a) => a.installed);
                      if (localAgentsErr) return <div className="foot-note">本机 agent 探测失败：{localAgentsErr}</div>;
                      if (!shown.length) return null;
                      const usable = shown.filter((a) => a.supported);
                      return (
                        <div className="local-agents">
                          <div className="la-head">
                            本机 Agent
                            <span className="desc">
                              {usable.length
                                ? `${usable.length} 个可接入`
                                : '暂无可接入引擎'}
                            </span>
                          </div>
                          {shown.map((a) => (
                            <div className="la-row" key={a.id}>
                              <span className="la-name">{a.label}<small>{a.path || a.command}</small></span>
                              <span className={`la-state ${a.configured ? 'ok' : a.supported ? 'todo' : 'na'}`}>
                                {a.configured ? '已接入' : a.supported ? '可接入' : '暂不支持'}
                              </span>
                              <span className="la-act">
                                {a.supported && (a.models?.length ?? 0) > 0 && (
                                  <select
                                    className="la-model"
                                    value={laModels[a.id] ?? ''}
                                    title="选择该 agent 使用的模型"
                                    onChange={(e) => setLaModels((m) => ({ ...m, [a.id]: e.target.value }))}
                                  >
                                    <option value="">默认模型</option>
                                    {(a.models ?? []).map((m) => (
                                      <option key={m.id} value={m.id}>{m.name}</option>
                                    ))}
                                  </select>
                                )}
                                {a.supported && (
                                  <button
                                    className="btn btn-sm"
                                    disabled={enabling === a.id}
                                    title={a.configured ? '应用所选模型' : '接入本机 Agent'}
                                    onClick={() => void doEnableAgent(a.id, laModels[a.id] ?? '')}
                                  >{enabling === a.id ? '接入中…' : a.configured ? '应用模型' : '一键接入'}</button>
                                )}
                              </span>
                              <span className="la-hint">{a.loginHint}</span>
                            </div>
                          ))}
                          {localAgentNote && <div className="foot-note">{localAgentNote}</div>}
                        </div>
                      );
                    })()}
                  </section>
                )}

                {chan === 'transcribe' && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <span className={`pill ${transOk ? 'ok' : 'warn'}`}><span className="dot" />{transOk ? '主通道已配置' : (localReady ? '本地兜底生效' : '备用待安装')}</span>
                      <span className="spacer" />
                      <button className="btn btn-sm" onClick={() => void doSelftest('transcribe')} disabled={testing}>自测本通道</button>
                    </div>
                    {renderBoard([...transRows, localRow], { onRow: (i, p) => updateRow(setTransRows, i, p) })}
                  </section>
                )}

                {usesMediaGateway && mediaCapability && mediaGateway && <section className="st-panel active media-gateway-channel" aria-label="当前媒体通道">
                  <strong>当前通道使用统一网关 · {mediaGateway.configured[mediaCapability] ? '配置完整' : '配置待完善'} · 模型可用性待验证</strong>
                  <p>模型：{mediaGateway[mediaCapability].model || '供应商默认模型'} · 网关：{mediaGateway.baseUrl || '尚未填写'}</p>
                </section>}

                {chan === 'speech' && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <span className={`pill ${mediaOk('speech') ? 'ok' : 'off'}`}><span className="dot" />{mediaOk('speech') ? '已配置' : '未配置'}</span>
                      <span className="spacer" />
                    </div>
                    {renderBoard(mediaRows.speech || [], { onRow: (i, p) => updateMediaRow('speech', i, p), onPrimary: (i) => setMediaPrimary('speech', i), media: true })}
                  </section>
                )}

                {chan === 'image' && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <span className={`pill ${mediaOk('image') ? 'ok' : 'off'}`}><span className="dot" />{mediaOk('image') ? '已配置' : '未配置'}</span>
                      <span className="spacer" />
                    </div>
                    {renderBoard(mediaRows.image || [], { onRow: (i, p) => updateMediaRow('image', i, p), media: true })}
                  </section>
                )}

                {chan === 'video' && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <span className={`pill ${mediaOk('video') ? 'ok' : 'off'}`}><span className="dot" />{mediaOk('video') ? '已配置' : '未配置'}</span>
                      <span className="spacer" />
                    </div>
                    {renderBoard(mediaRows.video || [], { onRow: (i, p) => updateMediaRow('video', i, p), onPrimary: (i) => setMediaPrimary('video', i), media: true })}
                  </section>
                )}

                {chan === 'music' && (
                  <section className="st-panel active">
                    <div className="panel-top">
                      <span className={`pill ${mediaOk('music') ? 'ok' : 'off'}`}><span className="dot" />{mediaOk('music') ? '已配置' : '未配置'}</span>
                      <span className="spacer" />
                    </div>
                    {renderBoard(mediaRows.music || [], { onRow: (i, p) => updateMediaRow('music', i, p), onPrimary: (i) => setMediaPrimary('music', i), media: true })}
                  </section>
                )}

                {modelErr && <div className="env-error">{modelErr}</div>}
                {selftestNote && <div className="foot-note">{selftestNote}</div>}
              </section>
            )}

            {sec === 'env' && (
              <section className="st-sec active">
                <EnvBoard
                  tools={tools}
                  python={python}
                  loading={envLoading}
                  error={envError}
                  jobs={jobs}
                  anyRunning={anyRunning}
                  onRefresh={() => void refreshEnv(true)}
                  onInstall={installOne}
                  onInstallMany={installMany}
                />
              </section>
            )}

          </div>
        </div>

        {sec === 'model' && usesSubscription && <div className="settings-foot">
          {subscriptionLabel} · {harnessSettings?.subscription_model || '未选择模型'}
        </div>}
      </div>
    </div>
  );
}


/** 模型下拉面板：portal 到 body + fixed 定位，避免被 board 的 overflow:hidden 裁切。 */
function FixedDropdown({ anchor, models, current, emptyHint, onPull, pulling, onPick, onClose }: {
  anchor: HTMLElement | null;
  models: string[];
  current: string;
  emptyHint?: string;
  onPull?: () => void;
  pulling?: boolean;
  onPick: (mid: string) => void;
  onClose: () => void;
}) {
  return (
    <OptionsDropdown
      anchor={anchor}
      items={models.map((m) => ({ value: m, label: m }))}
      current={current}
      emptyHint={emptyHint}
      onPull={onPull}
      pulling={pulling}
      onPick={onPick}
      onClose={onClose}
    />
  );
}
