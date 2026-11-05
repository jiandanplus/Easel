import { useEffect, useState } from 'react';
import { centralAccountBoundaryVersion, centralAccountSignature, centralMediaModels, centralStatus, subscribeCentralAccountBoundary } from '../lib/api';
import type { CentralMediaModel } from '../lib/api';

function imagePrice(model: CentralMediaModel): string {
  const meters = model.rate?.meters;
  if (!Array.isArray(meters) || meters.length !== 1) return '费率待确认';
  const meter = meters[0];
  if (meter.unit !== 'image' || meter.basis !== 'verified_delivery_v1'
      || !/^\d{1,96}$/.test(meter.numerator) || !/^[1-9]\d{0,95}$/.test(meter.denominator)) return '费率待确认';
  const numerator = BigInt(meter.numerator), denominator = BigInt(meter.denominator);
  if (numerator % denominator !== 0n) return `${meter.numerator}/${meter.denominator} 微点/张`;
  const microcredits = numerator / denominator;
  const fraction = (microcredits % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${microcredits / 1000000n}${fraction ? `.${fraction}` : ''} 点/张`;
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
        setStored({ scope: accountSignature, busy: false, models: data.models.filter(model => model.capability === 'image.generate') });
      } catch (error) {
        if (current()) setStored({ scope: accountSignature, busy: false,
          error: error instanceof Error && error.message === 'account_changed' ? '账号状态已变化' : '生图模型暂不可用' });
      }
    })();
    return () => { alive = false; controller.abort(); unsubscribe(); };
  }, [accountSignature, revision, refresh]);

  return <section className="central-account-section" aria-label="生图模型" aria-busy={view.busy}>
    <div className="central-account-header"><h4>生图模型</h4>
      <button type="button" className="btn btn-sm" disabled={!accountSignature || view.busy} onClick={() => setRefresh(value => value + 1)}>{view.busy ? '读取中…' : '刷新生图模型'}</button>
    </div>
    {!accountSignature && <p>登录后查看</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.models?.length === 0 && <p>暂无可用生图模型</p>}
    {view.models?.map(model => <article className="central-account-card" key={model.id}>
      <strong>{model.name || model.id}</strong>
      <p>{model.id}</p>
      <p>{imagePrice(model)} · {model.readiness === 'live' || model.readiness === 'test' ? '可用' : '暂不可用'}{model.test_only ? ' · 测试' : ''}</p>
    </article>)}
  </section>;
}
