import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import RadarSwipe from "../components/RadarSwipe";
import { api } from "../lib/api";
import { gibsHas } from "../lib/gibs";
import { fmt } from "../components/Charts";
import { useT } from "../lib/i18n";
import { play, rhythmNotes } from "../lib/sound";
import { Brand, LangSwitch } from "../components/Brand";

const JOSHIMATH: [number, number] = [79.56, 30.555];

function Surface() {
  return (
    <svg viewBox="0 0 220 120" aria-hidden="true">
      <path d="M20 108 H200" stroke="#4b8bea" strokeWidth="3" />
      <path d="M40 20 L110 104 L180 20" fill="none" stroke="#eaf0f7" strokeWidth="2" strokeDasharray="5 5" />
      <path d="M170 28 l10 -8 l-2 12" fill="none" stroke="#eaf0f7" strokeWidth="2" />
      <path d="M30 116 q10 -6 20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0" fill="none" stroke="#4b8bea" strokeOpacity=".5" strokeWidth="2" />
    </svg>
  );
}
function Volume() {
  return (
    <svg viewBox="0 0 220 120" aria-hidden="true">
      {[60, 110, 160].map(x => (
        <g key={x}>
          <path d={`M${x} 112 V70`} stroke="#5bd18b" strokeOpacity=".6" strokeWidth="3" />
          <circle cx={x} cy="58" r="22" fill="#5bd18b" fillOpacity=".22" stroke="#5bd18b" strokeWidth="2" />
        </g>
      ))}
      <path d="M30 14 L100 56 L82 66 L124 52 L108 70 L150 60 L190 16" fill="none" stroke="#eaf0f7" strokeWidth="2" strokeDasharray="5 5" />
      <path d="M180 22 l10 -6 l-1 12" fill="none" stroke="#eaf0f7" strokeWidth="2" />
    </svg>
  );
}
function Double() {
  return (
    <svg viewBox="0 0 220 120" aria-hidden="true">
      <path d="M20 108 H200" stroke="#e46fb0" strokeOpacity=".6" strokeWidth="3" />
      <rect x="130" y="34" width="34" height="74" fill="#e46fb0" fillOpacity=".22" stroke="#e46fb0" strokeWidth="2" />
      <path d="M30 22 L104 106 L130 78 L56 18" fill="none" stroke="#eaf0f7" strokeWidth="2" strokeDasharray="5 5" />
      <path d="M62 26 l-8 -9 l12 -1" fill="none" stroke="#eaf0f7" strokeWidth="2" />
    </svg>
  );
}

export default function Home() {
  const t = useT();
  const nav = useNavigate();
  // hero dates come from NISAR's own record: the earliest and the latest pass NASA's mosaic shows here
  const [hero, setHero] = useState<[string | null, string | null]>([null, null]);
  useEffect(() => {
    let alive = true;
    api.coverage(JOSHIMATH[1], JOSHIMATH[0]).then(async c => {
      const tr = c.tracks.filter(t => t.min_coverage >= 0.95).sort((a, b) => b.n_dates - a.n_dates)[0] ?? c.tracks[0];
      if (!tr) return;
      const ds = tr.acquisitions.map(a => a.date);
      let a: string | null = null, b: string | null = null;
      for (const d of ds) { if (await gibsHas(d, JOSHIMATH[1], JOSHIMATH[0])) { a = d; break; } }
      for (const d of [...ds].reverse()) { if (d !== a && await gibsHas(d, JOSHIMATH[1], JOSHIMATH[0])) { b = d; break; } }
      if (alive) setHero([a, b ?? a]);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const [playing, setPlaying] = useState(false);
  const [live, setLive] = useState("");
  const stopRef = useRef<() => void>();

  const listen = async () => {
    if (playing) { stopRef.current?.(); setPlaying(false); setLive("Stopped."); return; }
    setPlaying(true);
    setLive("Loading NISAR passes over Joshimath…");
    try {
      const cov = await api.coverage(JOSHIMATH[1], JOSHIMATH[0]);
      const passes = cov.tracks.flatMap((tr, i) => tr.acquisitions.map(a => ({ date: a.date, trackIndex: i, track: tr.track })))
        .sort((a, b) => a.date.localeCompare(b.date));
      const notes = rhythmNotes(passes);
      stopRef.current = play(notes, i => {
        setLive(`${passes[i].date}: pass on orbit track ${passes[i].track}`);
        if (i === notes.length - 1) window.setTimeout(() => setPlaying(false), 400);
      });
    } catch {
      setLive("Could not load the passes. Check your connection and try again.");
      setPlaying(false);
    }
  };

  return (
    <>
      <header className="home-nav">
        <Brand />
        <LangSwitch />
      </header>
      <main>
        <section className="hero" aria-labelledby="hero-title">
          <RadarSwipe
            center={JOSHIMATH} zoom={11.4} dateA={hero[0]} dateB={hero[1]} interactive={false} initialPos={64}
            labelA={hero[0] ? `First pass: ${fmt(hero[0])}` : undefined} labelB={hero[1] ? `Latest pass: ${fmt(hero[1])}` : undefined}
            ariaLabel="NISAR radar images of Joshimath, Uttarakhand, compared between 22 June and 1 November 2026"
          />
          <div className="hero__fade" />
          <div className="hero__text">
            <h1 id="hero-title" className="hero__title">{t("heroTitle")}</h1>
            <p className="hero__lede">{t("heroLede")}</p>
            <div className="hero__actions">
              <Link className="btn btn--primary" to="/dashboard">{t("openDashboard")}</Link>
              <button className="btn" onClick={() => nav("/dashboard?geo=1")}>{t("useMyLocation")}</button>
              <Link className="btn" to="/story">Watch nine places dance</Link>
            </div>
            <p className="hero__caption">{t("heroCaption")}</p>
          </div>
        </section>

        <section className="section" aria-labelledby="read-title">
          <div className="section__head">
            <h2 id="read-title">{t("readTitle")}</h2>
            <p>NISAR sends its own microwave pulse and listens for the echo. How the echo comes back tells you what the ground is made of.</p>
          </div>
          <div className="mech">
            <div className="mech__item" style={{ ["--c" as any]: "var(--surface)" }}>
              <Surface />
              <span className="mech__name">It bounces away</span>
              <p>Calm water and smooth ground reflect the pulse away like a mirror. Little comes back, so they look dark. A sudden dark patch can mean flooding.</p>
            </div>
            <div className="mech__item" style={{ ["--c" as any]: "var(--volume)" }}>
              <Volume />
              <span className="mech__name">It scatters in leaves</span>
              <p>A canopy bounces the pulse around inside and twists its polarization. That twisted echo (called HV) measures vegetation, so losing it can mean forest loss or a harvest.</p>
            </div>
            <div className="mech__item" style={{ ["--c" as any]: "var(--double)" }}>
              <Double />
              <span className="mech__name">It bounces twice</span>
              <p>A wall and the ground, or tree trunks standing in floodwater, send the pulse straight back. A strong straight echo (HH) can mean new buildings or water under trees.</p>
            </div>
          </div>
        </section>

        <section className="section" aria-labelledby="how-title">
          <div className="section__head">
            <h2 id="how-title">{t("howTitle")}</h2>
            <p>Choose any place. The dashboard runs these four steps on it.</p>
          </div>
          <ol className="steps">
            <li>
              <strong>Find every pass</strong>
              <p>Every NISAR image of the place is listed, by orbit, date and data quality. Joshimath is seen from four orbits, about once every three days.</p>
              <span className="proof">Live from NASA's Alaska Satellite Facility archive</span>
            </li>
            <li>
              <strong>Test every pixel</strong>
              <p>A statistical test built for radar speckle flags only changes too large to be noise, and dates when they happened.</p>
              <span className="proof">Measured on simulated speckle: 0.4% false alarms, 98% of 2 dB changes found</span>
            </li>
            <li>
              <strong>Rank the likely causes</strong>
              <p>Each change is checked against slope, land cover, rainfall, rivers and roads. Every explanation shows the evidence for it and against it.</p>
              <span className="proof">Explanations, never a single unexplained label</span>
            </li>
            <li>
              <strong>Decide what to watch</strong>
              <p>A priority score with its working shown, and the date of the next pass, say where to look again first.</p>
              <span className="proof">Monitoring priority, not a disaster prediction</span>
            </li>
          </ol>
        </section>

        <section className="section" aria-labelledby="listen-title" style={{ paddingTop: 0 }}>
          <div className="listen">
            <div style={{ display: "grid", gap: "0.5rem" }}>
              <h2 id="listen-title" style={{ fontSize: "var(--t-xl)" }}>{t("listenTitle")}</h2>
              <p>Each note is one NISAR pass over Joshimath since June 2026; each orbit has its own voice. The silence in August is a real gap in the data. Every note is also written out below for screen readers.</p>
              <p className="live" aria-live="polite">{live}</p>
            </div>
            <button className="btn" onClick={listen} aria-pressed={playing}>{playing ? t("stop") : t("playPasses")}</button>
          </div>
        </section>

        <section className="section" aria-labelledby="limits-title">
          <div className="section__head">
            <h2 id="limits-title">{t("limitsTitle")}</h2>
          </div>
          <div className="limits">
            <div>
              <h3>It can</h3>
              <ul>
                <li>Show real NISAR radar of almost any land on Earth, and compare any two dates.</li>
                <li>List every pass over a place and estimate the next one.</li>
                <li>Find statistically significant changes, measure slow ground motion, and rank possible causes with their evidence.</li>
              </ul>
            </div>
            <div>
              <h3>It can't</h3>
              <ul>
                <li>Predict disasters. It tells you where to look, not what will happen.</li>
                <li>Prove a cause. Each explanation lists what would confirm it on the ground.</li>
                <li>Look back before June 2026, when NISAR's calibrated record starts. Any pauses in its record are found from each place's timeline and shaded.</li>
              </ul>
            </div>
          </div>
        </section>
      </main>
      <footer className="footer">
        <div className="footer__inner">
          <p>Built for NASA Space Apps Challenge 2026, "Dancing with the SARs". Open source.</p>
          <p>Data: NISAR L-band GCOV and GUNW (NASA–ISRO) from the ASF DAAC; NISAR mosaics, Blue Marble and reference layers from NASA GIBS; rainfall from NASA POWER; Copernicus DEM GLO-30 and ESA WorldCover (ESA); roads and rivers © OpenStreetMap contributors.</p>
        </div>
      </footer>
    </>
  );
}
