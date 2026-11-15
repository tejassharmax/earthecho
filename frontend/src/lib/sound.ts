// Sonification (Feature 3), built as an accessibility feature: every note is also
// announced as text through an aria-live region by the caller.
const PENTA = [0, 2, 4, 7, 9]; // major pentatonic, semitones

function noteFreq(step: number, base = 220): number {
  const oct = Math.floor(step / PENTA.length);
  const deg = PENTA[((step % PENTA.length) + PENTA.length) % PENTA.length];
  return base * Math.pow(2, oct + deg / 12);
}

let ctx: AudioContext | null = null;
function audio(): AudioContext {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

type Note = { at: number; step: number; gain?: number; wave?: OscillatorType; dur?: number; tick?: boolean };

export function play(notes: Note[], onNote?: (i: number) => void): () => void {
  const a = audio();
  const t0 = a.currentTime + 0.08;
  const timers: number[] = [];
  const nodes: OscillatorNode[] = [];
  notes.forEach((n, i) => {
    const osc = a.createOscillator();
    const g = a.createGain();
    osc.type = n.wave ?? "triangle";
    osc.frequency.value = noteFreq(n.step);
    const dur = n.dur ?? 0.28;
    const peak = (n.gain ?? 0.5) * 0.25;
    g.gain.setValueAtTime(0.0001, t0 + n.at);
    g.gain.exponentialRampToValueAtTime(peak, t0 + n.at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + n.at + dur);
    osc.connect(g).connect(a.destination);
    osc.start(t0 + n.at);
    osc.stop(t0 + n.at + dur + 0.05);
    nodes.push(osc);
    if (n.tick) {
      const o2 = a.createOscillator(); const g2 = a.createGain();
      o2.type = "square"; o2.frequency.value = 1400;
      g2.gain.setValueAtTime(0.06, t0 + n.at); g2.gain.exponentialRampToValueAtTime(0.0001, t0 + n.at + 0.05);
      o2.connect(g2).connect(a.destination); o2.start(t0 + n.at); o2.stop(t0 + n.at + 0.06);
      nodes.push(o2);
    }
    if (onNote) timers.push(window.setTimeout(() => onNote(i), (n.at + 0.08) * 1000));
  });
  return () => {
    timers.forEach(clearTimeout);
    nodes.forEach(o => { try { o.stop(); } catch { /* already stopped */ } });
  };
}

/** NISAR's visits to a place: one note per pass, pitch per track, time scaled from days. */
export function rhythmNotes(passes: { date: string; trackIndex: number }[]): Note[] {
  if (!passes.length) return [];
  const t0 = new Date(passes[0].date).getTime();
  return passes.map(p => ({
    at: ((new Date(p.date).getTime() - t0) / 864e5) * 0.09,
    step: 5 + p.trackIndex * 2, wave: (["triangle", "sine", "square", "sawtooth"] as OscillatorType[])[p.trackIndex % 4],
    gain: 0.45, dur: 0.22,
  }));
}

/** A radar time series: pitch follows dB, tick marks a detected change. */
export function seriesNotes(values: (number | null)[], flagged: number[]): Note[] {
  const v = values.filter((x): x is number => x != null);
  if (!v.length) return [];
  const lo = Math.min(...v), hi = Math.max(...v);
  return values.map((x, i) => ({
    at: i * 0.32,
    step: x == null ? 0 : Math.round(((x - lo) / Math.max(hi - lo, 0.5)) * 9) + 3,
    gain: x == null ? 0.001 : 0.55, tick: flagged.includes(i),
  }));
}
