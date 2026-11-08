import { useEffect, useState } from 'react';
import { centralAccountBoundaryVersion, centralAccountSignature, centralMediaModels, centralStatus, subscribeCentralAccountBoundary } from '../lib/api';
import type { CentralMediaModel } from '../lib/api';

const CAPABILITIES: Record<string, string> = { 'image.generate': '生图', 'image.edit': '改图', 'image.variation': '图片变体', 'video.generate': '视频', 'music.generate': '音乐', 'audio.speech': '语音合成', 'audio.transcribe': '语音转文字', 'voice.enroll': '声音复刻', 'voice.synthesize': '复刻配音' };
const UNITS: Record<string, [bigint, string]> = { image: [1n, '张'], video_ms: [1000n, '秒'], audio_clip: [1n, '首'], unicode_scalar: [10000n, '万字'], audio_ms: [60000n, '分钟'], registration: [1n, '次'], input_token: [1000000n, '百万输入 token'], output_token: [1000000n, '百万输出 token'] };

function points(numerator: bigint, denominator: bigint): string {
  // Four decimals is display only; the amount charged is computed on the server.
  const scaled = numerator * 10000n / denominator / 1000000n;
  const fraction = (scaled % 10000n).toString().padStart(4, '0').replace(/0+$/, '');
  return `${scaled / 10000n}${fraction ? `.${fraction}` : ''}`;
}

export function mediaPrice(model: CentralMediaModel): string {
  const meters = model.rate?.meters;
  if (!Array.isArray(meters) || meters.length === 0) return '费率待确认';
  const parts: string[] = [];
  for (const meter of meters) {
    const unit = UNITS[meter.unit];
    if (!unit || !/^\d{1,96}$/.test(meter.numerator) || !/^[1-9]\d{0,95}$/.test(meter.denominator)) return '费率待确认';
    parts.push(`${points(BigInt(meter.numerator) * unit[0], BigInt(meter.denominator))} 点/${unit[1]}`);
  }
  return parts.join(' + ');
}

export default function CentralMediaSettings({ accountSignature, revision }: { accountSignature: string | null; revision: number }) {
  const [refresh, setRefresh] = useState(0);
  const [stored, setStored] = useState<{ scope: string | null; models?: CentralMediaModel[]; error?: string; busy: boolean }>({ scope: accountSignature, busy: false });
  const view = stored.scope === accountSignature && accountSignature ? stored : { scope: accountSignature, busy: false };
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    const boundary = centralAccountBoundaryVersion();
    const current = () => alive && !controller.signal.aborted && boundary === centralAccountBoundaryVersion();
    const unsubscribe = subscribeCentralAccountBoundary(() => {
      controller.abort();
      setStored({ scope: accountSignature, busy: false, error: '账号状态已变化' });
    });
    setStored({ scope: accountSignature, busy: !!accountSignature });
    if (accountSignature) void (async () => {
      try {
        const before = await centralStatus(controller.signal);
        if (!current()) return;
        if (centralAccountSignature(before) !== accountSignature) throw new Error('account_changed');
        const data = await centralMediaModels(controller.signal);
        if (!current()) return;
        const after = await centralStatus(controller.signal);
        if (!current()) return;
        if (centralAccountSignature(after) !== accountSignature) throw new Error('account_changed');
        if (!Array.isArray(data.models)) throw new Error('invalid_catalog');
        setStored({ scope: accountSignature, busy: false, models: data.models });
      } catch (error) {
        if (current()) setStored({ scope: accountSignature, busy: false,
          error: error instanceof Error && error.message === 'account_changed' ? '账号状态已变化' : '媒体模型暂不可用' });
      }
    })();
    return () => { alive = false; controller.abort(); unsubscribe(); };
  }, [accountSignature, revision, refresh]);

  return <section className="central-account-section" aria-label="媒体模型" aria-busy={view.busy}>
    <div className="central-account-header"><h4>媒体模型</h4>
      <button type="button" className="btn btn-sm" disabled={!accountSignature || view.busy} onClick={() => setRefresh(value => value + 1)}>{view.busy ? '读取中…' : '刷新'}</button>
    </div>
    {!accountSignature && <p>登录后查看</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.models?.length === 0 && <p>暂无可用媒体模型</p>}
    {view.models?.map(model => <article className="central-account-card" key={model.id}>
      <strong>{model.name || model.id}</strong>
      <p>{model.id}</p>
      <p>{CAPABILITIES[model.capability] ?? model.capability} · {mediaPrice(model)} · {model.readiness === 'live' || model.readiness === 'test' ? '可用' : '暂不可用'}{model.test_only ? ' · 测试' : ''}</p>
    </article>)}
  </section>;
}
