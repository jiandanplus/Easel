import { useEffect, useRef, useState } from 'react';
import { fetchHarnessModels, fetchHarnessSettings, saveHarnessSettings, platformModels, centralModels } from '../lib/api';
import type { HarnessModel, HarnessSettingsResponse, SubscriptionBackend } from '../lib/api';
import SettingsSelect from './SettingsSelect';
import BundledProviderSettings from './BundledProviderSettings';

export default function HarnessSettings({ onChange, onSelect, onSettingsChange }: {
  onChange: (runtime: string) => void;
  onSelect: (runtime: string) => void;
  onSettingsChange?: (settings: HarnessSettingsResponse) => void;
}) {
  const [settings, setSettings] = useState<HarnessSettingsResponse | null>(null);
  const [runtime, setRuntime] = useState('pi');
  const [model, setModel] = useState('');
  const [source, setSource] = useState<'custom' | 'subscription'>('custom');
  const [backend, setBackend] = useState<SubscriptionBackend>('central');
  const [subscriptionModel, setSubscriptionModel] = useState('');
  const [subscriptionLoading, setSubscriptionLoading] = useState(false);
  const [subscriptionModels, setSubscriptionModels] = useState<{ id: string; name?: string; test_only?: boolean }[]>([]);
  const [sandbox, setSandbox] = useState('workspace-write');
  const [autoApprove, setAutoApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [catalogs, setCatalogs] = useState<Record<string, HarnessModel[]>>({});
  const [modelLoading, setModelLoading] = useState(false);
  const [modelNote, setModelNote] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const modelRequest = useRef(0);

  useEffect(() => {
    let alive = true;
    setSettingsLoading(true);
    setNote('');
    fetchHarnessSettings().then((value) => {
      if (!alive) return;
      setSettings(value);
      setRuntime(value.runtime);
      setSource(value.service_source || 'custom');
      setBackend(value.subscription_backend || (value.service_source === 'subscription' ? 'legacy' : 'central'));
      setSubscriptionModel(value.subscription_model || '');
      setModel(value.models[value.runtime] || '');
      setSandbox(value.sandbox);
      setAutoApprove(value.codex_auto_approve ?? false);
      onChange(value.runtime);
      onSelect(value.runtime);
      onSettingsChange?.(value);
    }).catch((error) => {
      if (alive) setNote(`读取失败：${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => {
      if (alive) setSettingsLoading(false);
    });
    return () => { alive = false; modelRequest.current += 1; };
  }, [onChange, onSelect, onSettingsChange, refresh]);

  useEffect(() => {
    if (source !== 'subscription') { setSubscriptionLoading(false); return; }
    let alive = true;
    setSubscriptionLoading(true); setModelNote(''); setSubscriptionModels([]);
    (backend === 'central' ? centralModels() : platformModels()).then(value => {
      if (!alive) return;
      setSubscriptionModels(value.models);
      if (value.ready === false || !value.models.length) setModelNote(value.reason === 'gateway_not_configured' ? '平台模型网关尚未配置，请联系运营者。' : '暂无可用订阅模型，请检查登录状态和权益');
    }).catch(error => { if (alive) setModelNote(error instanceof Error ? error.message : '读取订阅模型失败'); })
      .finally(() => { if (alive) setSubscriptionLoading(false); });
    return () => { alive = false; };
  }, [source, backend, refresh]);

  const selected = settings?.agents.find((agent) => agent.id === runtime);
  const bundled = runtime === 'pi' || (runtime === 'codex' && selected?.origin === 'bundled');
  const supportsSubscription = (agent: HarnessSettingsResponse['agents'][number] | undefined) =>
    (agent?.subscription_backends ?? (agent?.id === 'codex' ? ['central', 'legacy'] : [])).includes(backend);
  const nativeModels = catalogs[runtime] || [];
  const pullModels = async () => {
    const requested = runtime;
    const sequence = ++modelRequest.current;
    setModelLoading(true);
    setModelNote('');
    try {
      const result = await fetchHarnessModels(requested);
      if (modelRequest.current !== sequence) return;
      setCatalogs((previous) => ({ ...previous, [requested]: result.models }));
      if (!result.models.length) setModelNote('原生 CLI 没有返回模型，可使用默认模型或手动填写。');
    } catch (error) {
      if (modelRequest.current === sequence) {
        setModelNote(`读取失败：${error instanceof Error ? error.message : String(error)}`);
      }
    } finally {
      if (modelRequest.current === sequence) setModelLoading(false);
    }
  };
  const save = async () => {
    setBusy(true);
    setNote('');
    try {
      const value = await saveHarnessSettings(runtime, source === 'subscription' ? subscriptionModel : model, sandbox, runtime === 'codex' ? autoApprove : undefined, { service_source: source, execution_target: 'local', ...(source === 'subscription' ? { subscription_model: subscriptionModel, subscription_backend: backend } : {}) });
      setSettings(value);
      onChange(value.runtime);
      onSettingsChange?.(value);
      setNote('已保存 · 新建对话后生效');
    } catch (error) {
      setNote(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="st-panel active harness-settings" aria-label="Agent 底座设置">
      <div className="panel-top">
        <strong>Agent 底座</strong>
        <span className="spacer" />
        <button className="btn btn-sm" disabled={!settings || settingsLoading || busy || settings.locked || !selected?.installed || (source === 'subscription' && (!supportsSubscription(selected) || !subscriptionModels.some(item => item.id === subscriptionModel)))}
          onClick={() => void save()}>{busy ? '保存中…' : '保存底座'}</button>
        <button className="btn btn-sm" disabled={settingsLoading || busy}
          onClick={() => setRefresh((previous) => previous + 1)}>{settingsLoading ? '读取中…' : '重新读取'}</button>
      </div>
      <div className="settings-field">
        <span className="settings-field-label">执行引擎</span>
        <div className="settings-field-body">
          <div className="settings-engine-control">
            <SettingsSelect label="执行引擎" value={runtime} disabled={!settings || settingsLoading || busy || settings.locked}
              options={(settings?.agents || [{ id: 'pi', label: '优创内置智能体 · Pi', installed: false }])
                .map((agent) => ({
                value: agent.id,
                label: `${agent.label}${agent.installed ? '' : '（未安装 / 未配置）'}`,
              }))}
              onChange={(value) => {
                setRuntime(value);
                setModel(settings?.models[value] || '');
                modelRequest.current += 1;
                setModelLoading(false);
                setModelNote('');
                setNote('');
                onSelect(value);
              }} />

          </div>
          {selected && !selected.installed && (
            <p className="desc" role="status">{runtime === 'pi'
              ? '内置 Pi 不可用，请重新安装优创'
              : `未检测到 ${selected.label}，请安装后重启优创`}</p>
          )}
          {runtime !== settings?.runtime && <p className="desc">切换待保存</p>}
        </div>
      </div>
      {source === 'subscription' && <div className="settings-field">
        <span className="settings-field-label">订阅模型</span>
        <div className="settings-field-body">
          <SettingsSelect label="订阅模型" value={subscriptionModel} disabled={subscriptionLoading || busy || settings?.locked}
            options={[{ value: '', label: '请选择服务端授权模型' }, ...subscriptionModels.map(item => ({ value: item.id, label: `${item.name || item.id}${item.test_only ? '（测试点数）' : ''}` }))]} onChange={setSubscriptionModel} />
          <p className="desc">已开通订阅，对话默认用这个模型；发消息时也可以在输入框里换模型。</p>
          {!supportsSubscription(selected) && <p role="status">当前执行引擎不能使用订阅，会改用自定义接口。</p>}
          {modelNote && <p role="status">{modelNote}</p>}
        </div>
      </div>}
      {runtime !== 'openclaw' && runtime !== 'api' && (
        <>
          {source === 'subscription' && bundled && <p className="desc settings-fallback-note">下面是自定义接口，订阅到期或未开通时使用。</p>}
          {(bundled || source === 'custom') && (bundled ? <BundledProviderSettings disabled={busy || settingsLoading || !selected?.installed}
            runtime={runtime} version={selected?.version || ''} onSaved={(value) => {
              setModel(value);
              setCatalogs((previous) => ({ ...previous, [runtime]: [] }));
              setSettings((previous) => previous ? { ...previous, models: { ...previous.models, [runtime]: value } } : previous);
            }} /> : <div className="settings-field">
            <label className="settings-field-label" htmlFor="harness-model">模型覆盖</label>
            <div className="settings-field-body">
              {(runtime === 'codex' || runtime === 'opencode') && (
                <div className="settings-native-models">
                  <SettingsSelect label="原生模型" value={model} disabled={busy || settings?.locked}
                    options={[
                      { value: '', label: '使用原生默认模型' },
                      ...nativeModels.map((item) => ({ value: item.id,
                        label: `${item.label}${item.default ? '（原生默认）' : ''}` })),
                      ...(model && !nativeModels.some((item) => item.id === model)
                        ? [{ value: model, label: `${model}（当前 / 自定义）` }] : []),
                    ]} onChange={setModel} />
                  <button className="btn btn-sm" disabled={modelLoading || busy || !selected?.installed}
                    onClick={() => void pullModels()}>{modelLoading ? '读取中…' : '拉取模型'}</button>
                </div>
              )}
              <input id="harness-model" className="mock" value={model} disabled={busy || settings?.locked} maxLength={240}
              onChange={(event) => setModel(event.target.value)}
              placeholder={runtime === 'codex' ? '留空使用 Codex 原生配置' : 'provider/model；留空使用原生配置'} />
              {modelNote && <p className="desc" role="status">{modelNote}</p>}
            </div>
          </div>)}
          {(runtime === 'pi' || runtime === 'codex') && (
            <>
            <div className="settings-field">
              <span className="settings-field-label">{runtime === 'pi' ? '工具权限' : 'Codex 沙箱'}</span>
              <div className="settings-field-body">
                <SettingsSelect label={runtime === 'pi' ? '工具权限' : 'Codex 沙箱'} value={sandbox} disabled={busy || settings?.locked}
                  options={[
                    { value: 'workspace-write', label: '项目工作区写入' },
                    { value: 'read-only', label: '只读' },
                  ]}
                  onChange={setSandbox} />
                <p className="desc">{runtime === 'pi' ? '文件限当前工作区 · 命令逐次确认' : '限所选工作区权限'}</p>
              </div>
            </div>
            {runtime === 'codex' && <div className="settings-field">
              <label className="settings-field-label" htmlFor="codex-auto-approve">永久通过授权</label>
              <div className="settings-field-body">
                <button id="codex-auto-approve" type="button" role="switch"
                  aria-checked={autoApprove} aria-label="永久通过授权"
                  aria-describedby="codex-auto-approve-help" className="settings-permission-switch"
                  disabled={busy || settingsLoading || !settings || settings.locked}
                  onClick={() => setAutoApprove((previous) => !previous)}>
                  <span className="settings-permission-switch__thumb" />
                </button>
                <p id="codex-auto-approve-help" className="desc">自动批准文件读取、搜索、Git 查看／拉取和工作区内修改。
                  删除、系统修改及执行脚本仍需确认。保存后持续生效，可随时关闭。</p>
              </div>
            </div>}
            </>
          )}
        </>
      )}
      {settings?.locked && <p className="desc">执行引擎已锁定</p>}
      {note && <div className={`save-note${note.includes('失败') ? ' err' : ''}`} role="status">{note}</div>}
    </section>
  );
}
