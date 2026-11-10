import { roundCredits } from './core';
import type { RecordData } from './core';

export type Point = { label: string; value: number; title: string };

// Credits are integer strings in millionths of a point; charts only need an approximate size.
export function creditNumber(value: unknown): number {
  return typeof value === 'string' && /^-?\d+$/.test(value) ? Number(value) / 1000000 : 0;
}
export function dayLabel(value: unknown): string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? `${Number(value.slice(5, 7))}/${Number(value.slice(8, 10))}` : '';
}

export function BarChart({ points, label, empty = '这段时间没有数据' }: { points: Point[]; label: string; empty?: string }) {
  const max = Math.max(0, ...points.map(p => p.value));
  if (!points.length || max === 0) return <div className="chart-empty">{empty}</div>;
  const width = 600, height = 160, gap = points.length > 40 ? 1 : 3, bar = (width - gap * (points.length - 1)) / points.length;
  const step = Math.ceil(points.length / 8);
  return <figure className="chart" aria-label={label}>
    <svg viewBox={`0 0 ${width} ${height + 22}`} role="img" preserveAspectRatio="none">
      <title>{label}</title>
      <line x1="0" x2={width} y1={height} y2={height} className="chart-axis" />
      {points.map((p, i) => {
        const h = p.value > 0 ? Math.max(2, (p.value / max) * (height - 8)) : 0, x = i * (bar + gap);
        return <g key={i}><rect x={x} y={height - h} width={bar} height={h} rx={Math.min(3, bar / 3)} className="chart-bar"><title>{p.title}</title></rect>
          {i % step === 0 && <text x={x + bar / 2} y={height + 16} textAnchor="middle" className="chart-label">{p.label}</text>}</g>;
      })}
    </svg>
  </figure>;
}

export function ModelBars({ rows }: { rows: RecordData[] }) {
  const values = rows.map(r => creditNumber(r.spent)), max = Math.max(0, ...values);
  if (!rows.length) return <div className="chart-empty">这段时间没有调用</div>;
  return <ul className="model-bars">{rows.map((row, i) => <li key={String(row.model)}>
    <span className="model-name">{String(row.model ?? '未知')}</span>
    <span className="model-track"><span style={{ width: `${max ? Math.max(2, (values[i] / max) * 100) : 0}%` }} /></span>
    <span className="model-value">{roundCredits(row.spent)} 点 · {String(row.calls ?? 0)} 次</span>
  </li>)}</ul>;
}
