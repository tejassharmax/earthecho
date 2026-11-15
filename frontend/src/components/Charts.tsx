// Small SVG charts: one axis system, direct labels, gaps shown honestly.
import type { Track } from "../lib/api";

const W = 560;
const day = (d: string) => new Date(d + "T00:00:00Z").getTime() / 864e5;
const fmt = (d: string) => new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

function months(d0: number, d1: number) {
  const out: { x: number; label: string }[] = [];
  const s = new Date(d0 * 864e5);
  let m = new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth() + 1, 1));
  while (m.getTime() / 864e5 <= d1) {
    out.push({ x: m.getTime() / 864e5, label: m.toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" }) });
    m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1));
  }
  return out;
}

export type Series = { name: string; color: string; values: (number | null)[]; dashed?: boolean };

export function LineChart({ dates, series, unit, gaps = [], marker, height = 200, label }: {
  dates: string[]; series: Series[]; unit: string; gaps?: [string, string][]; marker?: string; height?: number; label: string;
}) {
  const pad = { l: 44, r: 12, t: 12, b: 26 };
  const xs = dates.map(day);
  const d0 = Math.min(...xs), d1 = Math.max(...xs);
  const vals = series.flatMap(s => s.values).filter((v): v is number => v != null && isFinite(v));
  if (!vals.length) return <p className="muted">No values to plot.</p>;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1) { lo -= 0.5; hi += 0.5; }
  const span = hi - lo; lo -= span * 0.08; hi += span * 0.08;
  const X = (d: number) => pad.l + ((d - d0) / Math.max(d1 - d0, 1)) * (W - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (height - pad.t - pad.b);
  const ticks = [lo + (hi - lo) * 0.15, (lo + hi) / 2, hi - (hi - lo) * 0.15];
  return (
    <figure className="chart" style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
        {gaps.map(([a, b]) => day(b) > d0 && day(a) < d1 && (
          <rect key={a} x={X(Math.max(day(a), d0))} y={pad.t} width={X(Math.min(day(b), d1)) - X(Math.max(day(a), d0))}
                height={height - pad.t - pad.b} fill="rgba(234,240,247,0.06)" />
        ))}
        {ticks.map(t => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={Y(t)} y2={Y(t)} stroke="rgba(234,240,247,0.08)" />
            <text x={pad.l - 6} y={Y(t) + 4} textAnchor="end">{t.toFixed(Math.abs(hi - lo) < 6 ? 1 : 0)}</text>
          </g>
        ))}
        <text x={4} y={pad.t + 8}>{unit}</text>
        {months(d0, d1).map(m => (
          <text key={m.x} x={X(m.x)} y={height - 8} textAnchor="middle">{m.label}</text>
        ))}
        {marker && (
          <g>
            <line x1={X(day(marker))} x2={X(day(marker))} y1={pad.t} y2={height - pad.b} stroke="#fde68a" strokeDasharray="3 3" />
            <text x={X(day(marker)) + 4} y={pad.t + 10} style={{ fill: "#fde68a" }}>change</text>
          </g>
        )}
        {series.map(s => {
          const pts = s.values.map((v, i) => (v == null || !isFinite(v) ? null : [X(xs[i]), Y(v)] as const));
          let d = ""; let pen = false;
          pts.forEach(pt => { if (!pt) { pen = false; return; } d += `${pen ? "L" : "M"}${pt[0].toFixed(1)},${pt[1].toFixed(1)}`; pen = true; });
          return (
            <g key={s.name}>
              <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} />
              {pts.map((pt, i) => pt && <circle key={i} cx={pt[0]} cy={pt[1]} r={2.6} fill={s.color} />)}
            </g>
          );
        })}
      </svg>
      <figcaption className="chart__legend">
        {series.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}
        {gaps.length > 0 && <span className="muted">shaded: no NISAR data</span>}
      </figcaption>
    </figure>
  );
}

export function RainBars({ dates, mm, marks = [] }: { dates: string[]; mm: (number | null)[]; marks?: string[] }) {
  const H = 110, pad = { l: 34, r: 8, t: 8, b: 22 };
  const n = dates.length;
  if (!n) return null;
  const max = Math.max(5, ...mm.map(v => v ?? 0));
  const bw = (W - pad.l - pad.r) / n;
  const d0 = day(dates[0]), d1 = day(dates[n - 1]);
  const X = (d: number) => pad.l + ((d - d0) / Math.max(d1 - d0, 1)) * (W - pad.l - pad.r);
  return (
    <figure className="chart" style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Daily rainfall from ${fmt(dates[0])} to ${fmt(dates[n - 1])}, maximum ${max.toFixed(0)} mm in a day`}>
        <text x={4} y={14}>mm/day</text>
        <text x={pad.l - 4} y={pad.t + 10} textAnchor="end">{max.toFixed(0)}</text>
        {mm.map((v, i) => v != null && v > 0 && (
          <rect key={i} x={pad.l + i * bw} y={H - pad.b - (v / max) * (H - pad.t - pad.b)} width={Math.max(bw - 0.5, 0.8)}
                height={(v / max) * (H - pad.t - pad.b)} fill="#4b8bea" />
        ))}
        {marks.map(m => <line key={m} x1={X(day(m))} x2={X(day(m))} y1={pad.t} y2={H - pad.b} stroke="#fde68a" strokeDasharray="3 3" />)}
        {months(d0, d1).map(m => <text key={m.x} x={X(m.x)} y={H - 6} textAnchor="middle">{m.label}</text>)}
      </svg>
    </figure>
  );
}

export function AcquisitionTimeline({ tracks, gaps, selected, onPick }: {
  tracks: Track[]; gaps: [string, string][]; selected: string[]; onPick: (date: string) => void;
}) {
  const all = tracks.flatMap(t => t.acquisitions.map(a => day(a.date)));
  if (!all.length) return null;
  const d0 = Math.min(...all) - 3, d1 = Math.max(Math.max(...all), ...tracks.map(t => day(t.next_expected_pass))) + 3;
  const pad = { l: 92, r: 10, t: 6, b: 24 };
  const rowH = 26;
  const H = pad.t + pad.b + rowH * tracks.length;
  const X = (d: number) => pad.l + ((d - d0) / (d1 - d0)) * (W - pad.l - pad.r);
  const col = (m: string) => (m === "VALIDATED" ? "#eaf0f7" : m === "PROVISIONAL" ? "#8e9bb0" : "#5b6578");
  return (
    <figure className="chart timeline" style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label="NISAR acquisitions by orbit track">
        {gaps.map(([a, b]) => (
          <rect key={a} x={X(day(a))} y={pad.t} width={X(day(b)) - X(day(a))} height={H - pad.t - pad.b} fill="rgba(234,240,247,0.06)" />
        ))}
        {tracks.map((t, r) => {
          const y = pad.t + r * rowH + rowH / 2;
          return (
            <g key={t.track}>
              <text x={4} y={y + 4}>{t.track.replace(/_/g, " ")}</text>
              <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="rgba(234,240,247,0.08)" />
              {t.acquisitions.map(a => (
                <circle key={a.date} cx={X(day(a.date))} cy={y} r={selected.includes(a.date) ? 7 : 5}
                        fill={col(a.maturity)} stroke={selected.includes(a.date) ? "#fde68a" : "#0d1526"} strokeWidth={2}
                        tabIndex={0} role="button" style={{ cursor: "pointer" }}
                        aria-label={`${fmt(a.date)}, track ${t.track}, ${a.mode ?? ""} ${a.pols.join("+")}, ${a.maturity.toLowerCase()}. Press to show on the map.`}
                        onClick={() => onPick(a.date)} onKeyDown={e => (e.key === "Enter" || e.key === " ") && onPick(a.date)}>
                  <title>{`${fmt(a.date)} · ${a.pols.join("+")} · ${a.maturity}`}</title>
                </circle>
              ))}
              <circle cx={X(day(t.next_expected_pass))} cy={y} r={5} fill="none" stroke="#eaf0f7" strokeDasharray="2 2">
                <title>{`Next expected pass ${fmt(t.next_expected_pass)} (estimate)`}</title>
              </circle>
            </g>
          );
        })}
        {months(d0, d1).map(m => <text key={m.x} x={X(m.x)} y={H - 6} textAnchor="middle">{m.label}</text>)}
      </svg>
      <figcaption className="chart__legend">
        <span><i style={{ background: "#eaf0f7", height: 8, width: 8, borderRadius: 4 }} />Validated</span>
        <span><i style={{ background: "#8e9bb0", height: 8, width: 8, borderRadius: 4 }} />Provisional</span>
        <span><i style={{ border: "1px dashed #eaf0f7", height: 8, width: 8, borderRadius: 4 }} />Next pass (estimate)</span>
        <span className="muted">shaded: instrument data gap</span>
      </figcaption>
    </figure>
  );
}

export { fmt };
