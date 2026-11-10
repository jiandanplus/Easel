import { useEffect, useState } from 'react';
import { fetchBundledProvider, saveBundledProvider } from '../lib/api';
import type { BundledProviderState } from '../lib/api';

export default function BundledProviderSettings({ disabled, version, onSaved, runtime = 'codex' }: {
  disabled: boolean; version: string; onSaved: (model: string) => void; runtime?: string;
}) {
  const engine = runtime === 'pi' ? '内置 Pi' : '内置 Codex';
  const [saved, setSaved] = useState<BundledProviderState | null>(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const read = async (alive: () => boolean = () => true) => {
    setBusy(true); setNote('');
    try {
      const value = await fetchBundledProvider();
      if (!alive()) return;
      setSaved(value); setBaseUrl(value.base_url); setModel(value.model); setKey('');
    } catch (error) {
      if (alive()) setNote(`读取失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { if (alive()) setBusy(false); }
  };
  useEffect(() => {
    let alive = true;
    void read(() => alive);
    return () => { alive = false; };
  }, []);
  const save = async (clear = false) => {
    if (!clear && saved?.key_present && baseUrl !== saved.base_url && !key) {
      setNote('API 地址已更改，请重新输入 Key');
      return;
    }
    setBusy(true); setNote('');
    try {
      const value = await saveBundledProvider({ base_url: baseUrl, model,
        api_key: clear ? '' : key, secret_action: clear ? 'clear' : key ? 'replace' : 'preserve' });
      setSaved(value); setKey('');
      onSaved(value.configured ? value.model : '');
      setNote(clear ? `Key 已清除 · ${engine}待配置` : '供应商已保存 · 新建对话后生效');
    } catch (error) {
      setKey('');
      setNote(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { setBusy(false); }
  };
  return (
    <div aria-label={`${engine}供应商`}>
      <p className="desc">{engine} · {version || '版本不可用'}</p>
      <div className="settings-field">
        <label className="settings-field-label" htmlFor="bundled-base-url">API 地址</label>
        <div className="settings-field-body"><input className="mock" id="bundled-base-url" type="url"
          value={baseUrl} maxLength={2048} disabled={disabled || busy || !saved}
          onChange={event => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" /></div>
      </div>
      <div className="settings-field">
        <label className="settings-field-label" htmlFor="bundled-model">模型</label>
        <div className="settings-field-body"><input className="mock" id="bundled-model" value={model}
          maxLength={240} disabled={disabled || busy || !saved} onChange={event => setModel(event.target.value)} />
        </div>
      </div>
      <div className="settings-field">
        <label className="settings-field-label" htmlFor="bundled-api-key">API Key</label>
        <div className="settings-field-body"><input className="mock" id="bundled-api-key" type="password"
          autoComplete="new-password" value={key} maxLength={16384} disabled={disabled || busy || !saved}
          placeholder={saved?.key_present ? '已保存；留空保留当前 Key' : '请输入 Key'}
          onChange={event => setKey(event.target.value)} />
        </div>
      </div>
      <button className="btn btn-sm" disabled={disabled || busy || !saved} onClick={() => void save()}>保存供应商</button>
      <button className="btn btn-sm" disabled={disabled || busy || !saved?.key_present} onClick={() => void save(true)}>清除 Key</button>
      <button className="btn btn-sm" disabled={disabled || busy} onClick={() => void read()}>重新读取供应商</button>
      {note && <p className="desc" role="status">{note}</p>}
    </div>
  );
}
