// "Speckle or change?" — a small game to play while the analysis runs.
// Two simulated radar patches of the same ground: is there a real change, or only speckle?
// It teaches the exact problem EarthEcho's statistical test solves.
import { useEffect, useRef, useState } from "react";

const N = 40;      // pixels per side
const SCALE = 4;   // drawn size multiplier

function gamma(k: number): number {
  // Marsaglia–Tsang for shape k >= 1
  const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
  for (;;) {
    let x: number, v: number;
    do {
      const u1 = Math.random(), u2 = Math.random();
      x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = Math.random();
    if (u < 1 - 0.0331 * x ** 4 || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
}

type Round = { changed: boolean; db: number; looks: number; cx: number; cy: number; r: number };

function draw(canvas: HTMLCanvasElement, round: Round, after: boolean) {
  const ctx = canvas.getContext("2d")!;
  const img = ctx.createImageData(N, N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    let mean = 0.12 + 0.05 * Math.sin(x / 7) * Math.cos(y / 9); // gentle terrain texture
    if (after && round.changed && Math.hypot(x - round.cx, y - round.cy) < round.r) mean *= 10 ** (round.db / 10);
    const v = (gamma(round.looks) / round.looks) * mean;
    const g = Math.max(0, Math.min(255, (10 * Math.log10(v) + 18) * 14)); // dB stretch
    const i = (y * N + x) * 4;
    img.data[i] = g * 0.55; img.data[i + 1] = g; img.data[i + 2] = g * 0.6; img.data[i + 3] = 255;
  }
  const off = document.createElement("canvas");
  off.width = N; off.height = N;
  off.getContext("2d")!.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, N * SCALE, N * SCALE);
}

function newRound(level: number): Round {
  const looks = level < 3 ? 4 : level < 6 ? 2 : 1; // gets noisier as you improve
  const db = (Math.random() < 0.5 ? -1 : 1) * (level < 3 ? 6 : level < 6 ? 4 : 3);
  return { changed: Math.random() < 0.5, db, looks, cx: 10 + Math.random() * 20, cy: 10 + Math.random() * 20, r: 6 + Math.random() * 5 };
}

export default function WaitGame() {
  const a = useRef<HTMLCanvasElement>(null);
  const b = useRef<HTMLCanvasElement>(null);
  const [level, setLevel] = useState(0);
  const [round, setRound] = useState<Round>(() => newRound(0));
  const [score, setScore] = useState({ right: 0, played: 0 });
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (a.current && b.current) { draw(a.current, round, false); draw(b.current, round, true); }
  }, [round]);

  const answer = (saysChanged: boolean) => {
    if (feedback) return;
    const ok = saysChanged === round.changed;
    setScore(s => ({ right: s.right + (ok ? 1 : 0), played: s.played + 1 }));
    setFeedback(
      (ok ? "Right. " : "Not quite. ") +
      (round.changed
        ? `A patch got ${round.db > 0 ? "brighter" : "darker"} by ${Math.abs(round.db)} dB${round.db > 0 ? " (like a new structure)" : " (like flooding)"}.`
        : "Nothing changed: every difference you saw was speckle, radar's built-in graininess.") +
      (round.looks === 1 ? " With this much speckle, a single pair of images is close to a coin toss, which is why EarthEcho tests 10+ images at once." : ""),
    );
    if (ok) setLevel(l => l + 1);
  };

  const next = () => { setFeedback(null); setRound(newRound(level)); };

  return (
    <div className="game" aria-labelledby="game-title">
      <div className="game__head">
        <strong id="game-title">While you wait: speckle or change?</strong>
        <span className="block__note num">{score.right} of {score.played} right</span>
      </div>
      <p className="block__note">Same ground, two radar passes. Did something really change on the right, or is it only speckle?</p>
      <div className="game__pair">
        <figure><canvas ref={a} width={N * SCALE} height={N * SCALE} aria-label="Radar patch, first pass" role="img" /><figcaption>First pass</figcaption></figure>
        <figure><canvas ref={b} width={N * SCALE} height={N * SCALE} aria-label="Radar patch, second pass" role="img" /><figcaption>Second pass</figcaption></figure>
      </div>
      {!feedback ? (
        <div className="game__actions">
          <button className="btn btn--small" onClick={() => answer(true)}>It changed</button>
          <button className="btn btn--small" onClick={() => answer(false)}>Just speckle</button>
        </div>
      ) : (
        <div className="game__actions" aria-live="polite">
          <p style={{ fontSize: "var(--t-s)" }}>{feedback}</p>
          <button className="btn btn--small btn--primary" onClick={next}>Next pair</button>
        </div>
      )}
    </div>
  );
}
