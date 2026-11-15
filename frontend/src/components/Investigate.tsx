import { useContext, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { LangContext, useT } from "../lib/i18n";
import { play, seriesNotes } from "../lib/sound";
import { LineChart, fmt } from "./Charts";
import WaitGame from "./WaitGame";

// A calm step indicator instead of a ticking countdown: where the analysis is, not how long is left.
const STEPS: [number, string][] = [
  [0.05, "Finding passes"], [0.62, "Reading images"], [0.76, "Testing for change"],
  [0.88, "Ground motion"], [1.01, "Explaining"],
];
function Steps({ progress }: { progress: number }) {
  const cur = Math.max(0, STEPS.findIndex(([end]) => progress < end));
  return (
    <span className="steps" aria-label={`Step ${cur + 1} of ${STEPS.length}: ${STEPS[cur][1]}`}>
      {STEPS.map(([, label], i) => (
        <i key={label} className={i < cur ? "done" : i === cur ? "now" : ""} title={label} />
      ))}
      <span className="steps__label">Step {cur + 1} of {STEPS.length} · {STEPS[cur][1]}</span>
    </span>
  );
}

export type Inv = any;

// Official NISAR learning resources from the challenge's Resources tab, matched to each explanation.
const SM = "https://storymaps.arcgis.com/stories/";
const WP = "https://assets.science.nasa.gov/content/dam/science/missions/nisar/nisar-jpl/pdf/";
const LEARN: Record<string, { label: string; url: string }[]> = {
  "Open water / flooding": [{ label: "NISAR StoryMap: Floods", url: SM + "c00d7691b8004b20a14f28df710d05fd" }, { label: "NISAR white paper: Floods", url: WP + "NISAR_Applications_Floods.pdf" }],
  "Flooding under vegetation": [{ label: "NISAR StoryMap: Floods", url: SM + "c00d7691b8004b20a14f28df710d05fd" }, { label: "NISAR white paper: Flood forecasting", url: WP + "NISAR_Applications_Flood_Forecasting.pdf" }],
  "Vegetation loss (clearing, burn or scar)": [{ label: "NISAR StoryMap: Wildfires", url: SM + "4724cd32c4064b9eb5ebe300c1385d07" }, { label: "NISAR white paper: Forest disturbance", url: WP + "NISAR_Applications_Timber_and_Forest_Disturbance.pdf" }],
  "Crop harvest or ploughing": [{ label: "NISAR white paper: Food security", url: WP + "NISAR_Applications_Food_Security1.pdf" }],
  "Landslide or debris flow": [{ label: "NISAR white paper: Landslides", url: WP + "NISAR_Applications_Landslides.pdf" }],
  "Slow ground movement": [{ label: "NISAR StoryMap: Land subsidence", url: SM + "109e602e748346f3be8dc89124619828" }, { label: "NISAR white paper: Subsidence", url: WP + "NISAR_Applications_Subsidence.pdf" }],
  "New structure or debris pile": [{ label: "NISAR white paper: Damage mapping", url: WP + "NISAR_DamageMapping.pdf" }],
  "Soil moisture after rain": [{ label: "NISAR white paper: Soil moisture", url: WP + "NISARApplications_SoilMoisture1.pdf" }],
  "Wet snow": [{ label: "NISAR white paper: Snow hydrology", url: WP + "SnowHydrology_Final.pdf" }, { label: "NISAR StoryMap: Glacial motion", url: SM + "7c674de4e9024a219249627500d2449b" }],
};

type Props = {
  lat: number; lon: number; enabled: boolean | null; gaps: [string, string][];
  result: Inv | null; setResult: (r: Inv | null) => void;
  selected: string | null; setSelected: (id: string | null) => void;
  onFocusEvent?: (ev: any, result?: any) => void;
  autoStart?: boolean;
};

export default function Investigate({ lat, lon, enabled, gaps, result, setResult, selected, setSelected, onFocusEvent, autoStart }: Props) {
  const t = useT();
  const { expert } = useContext(LangContext);
  const [job, setJob] = useState<{ progress: number; message: string; eta: number | null; at: number; started: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => { setJob(null); setErr(null); window.clearTimeout(timer.current); }, [lat, lon]);

  const start = async () => {
    setErr(null);
    const started = Date.now();
    setJob({ progress: 0, message: "Starting", eta: null, at: started, started });
    try {
      let id = (await api.investigate(lat, lon)).job;
      let failures = 0;
      const poll = async () => {
        try {
          let j: any;
          try {
            j = await api.job(id);
            failures = 0;
          } catch (e: any) {
            // Server restarted and forgot the job: start it again (downloaded data is cached).
            if (String(e.message).includes("Unknown analysis") || String(e.message) === "404") {
              id = (await api.investigate(lat, lon)).job;
              setJob(prev => prev && { ...prev, message: "The server restarted; resuming the analysis" });
              timer.current = window.setTimeout(poll, 1500);
              return;
            }
            // Server briefly unreachable: keep trying for about a minute before giving up.
            if (++failures <= 20) {
              setJob(prev => prev && { ...prev, message: "Reconnecting to the server…" });
              timer.current = window.setTimeout(poll, 3000);
              return;
            }
            throw new Error("The EarthEcho server stopped answering. Check it is running, then press the button again.");
          }
          if (j.status === "done") {
            setJob(null); setResult(j.result);
            const first = j.result.events[0];
            setSelected(first?.id ?? null);
            if (first) onFocusEvent?.(first, j.result);
            return;
          }
          if (j.status === "error") { setJob(null); setErr(j.message); return; }
          setJob(prev => ({ progress: j.progress, message: j.message, started: prev?.started ?? started,
            eta: j.eta_s ?? (prev?.eta != null ? Math.max(0, prev.eta - (Date.now() - prev.at) / 1000) : null),
            at: Date.now() }));
        } catch (e: any) { setErr(e.message); setJob(null); return; }
        timer.current = window.setTimeout(poll, 1500);
      };
      poll();
    } catch (e: any) { setJob(null); setErr(e.message); }
  };

  // showcase links open with ?analyse=1: results are shipped with the app, so this is instant
  useEffect(() => {
    if (autoStart && enabled !== null && !result && !job) start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, enabled, lat, lon]);

  const ev = result?.events?.find((e: any) => e.id === selected) ?? null;

  return (
    <div className="block">
      <div className="block__head">
        <h2>{t("changes")}</h2>
        {result && <span className="block__note">{result.dates.length} images, {fmt(result.dates[0])} to {fmt(result.dates[result.dates.length - 1])}</span>}
      </div>

      {enabled === false && !result && (
        <p className="notice">
          Deep analysis reads NISAR's source files, which needs a NASA Earthdata token on the EarthEcho server.
          This server doesn't have one yet, so this section is off. The map, timeline, terrain, rainfall and roads all work without it.
        </p>
      )}

      {!result && !job && enabled !== false && (
        <div style={{ display: "grid", gap: "0.6rem" }}>
          <p className="muted">Reads every NISAR image of the 6 × 6 km square around this place, tests each 20 m pixel for change and explains what it finds. The first time for a new place takes about 3 to 4 minutes; places analysed before open instantly.</p>
          <div><button className="btn btn--primary" onClick={start} disabled={enabled === null}>{t("analyse")}</button></div>
        </div>
      )}

      {job && (
        <div style={{ display: "grid", gap: "0.9rem" }}>
          <div style={{ display: "grid", gap: "0.4rem" }}>
            <div className="progress" role="progressbar" aria-label="Analysis progress" aria-valuenow={Math.round(job.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
              <i style={{ width: `${Math.max(3, job.progress * 100)}%` }} />
            </div>
            <div className="block__head">
              <span className="block__note" aria-live="polite">{job.message}</span>
              <Steps progress={job.progress} />
            </div>
            <span className="block__note">You can keep exploring the map and other tabs; the result appears here when it's ready.</span>
          </div>
          <WaitGame />
        </div>
      )}
      {err && <p className="notice error">{err}</p>}

      {result && (
        <>
          <p>
            {result.events.length === 0
              ? "No change large enough to rule out radar noise was found in this square. That is a result too: the ground looks stable at NISAR's resolution."
              : `${result.events.length} change event${result.events.length > 1 ? "s" : ""} found. ${(result.changed_fraction * 100).toFixed(1)}% of the square changed significantly at least once.`}
            {expert && ` Track ${result.track.track}, ${result.pixel_m} m pixels, ENL ${result.enl}, α = ${result.alpha}.`}
          </p>
          {result.excluded_dates?.length > 0 && (
            <p className="notice">
              Left out of the change test: {result.excluded_dates.map((x: any) => `${fmt(x.date)} (${x.reason})`).join("; ")}.
              A misaligned image creates false changes on steep slopes, so EarthEcho waits for the precise version.
            </p>
          )}
          {result.events.length > 0 && (
            <ul className="events" aria-label={t("events")}>
              {result.events.map((e: any) => (
                <li key={e.id}>
                  <button aria-pressed={e.id === selected} onClick={() => { setSelected(e.id); onFocusEvent?.(e); }}>
                    <span className="num">{e.id}</span>
                    <span>
                      <strong>{e.hypotheses[0].name}</strong>
                      <span className="muted"> · {fmt(e.date_first)} · {e.area_ha} ha</span>
                    </span>
                    <span className={`level level--${e.priority.level}`}>{e.priority.level}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {ev && <EventCard ev={ev} dates={result.dates} gaps={gaps} expert={expert} />}
          {result.seasonal?.length > 0 && <SeasonalDance result={result} gaps={gaps} />}
        </>
      )}
    </div>
  );
}

function EventCard({ ev, dates, gaps, expert }: { ev: any; dates: string[]; gaps: [string, string][]; expert: boolean }) {
  const t = useT();
  const [live, setLive] = useState("");
  const [playing, setPlaying] = useState(false);
  const stop = useRef<() => void>();
  const s = ev.series;
  const chan = s.HV ? "HV" : "HH";

  const listen = () => {
    if (playing) { stop.current?.(); setPlaying(false); return; }
    setPlaying(true);
    const vals: (number | null)[] = s[chan];
    stop.current = play(seriesNotes(vals, [ev.date_index]), i => {
      const v = vals[i];
      setLive(`${fmt(dates[i])}: ${chan} ${v == null ? "no data" : v.toFixed(1) + " dB"}${i === ev.date_index ? ", change detected" : ""}`);
      if (i === vals.length - 1) window.setTimeout(() => setPlaying(false), 400);
    });
  };

  const top = ev.hypotheses.slice(0, expert ? 6 : 3);
  return (
    <article className="block" aria-labelledby={`ev-${ev.id}`} style={{ borderTop: "1px solid var(--line)", paddingTop: "1rem" }}>
      <div className="block__head">
        <h3 id={`ev-${ev.id}`}>{ev.id}: {ev.hypotheses[0].name}</h3>
        <span className="block__note">first seen {fmt(ev.date_first)}{ev.date_next_change ? `, changed again ${fmt(ev.date_next_change)}` : ""}</span>
      </div>
      <dl className="facts">
        <div><dt>{t("area")}</dt><dd className="num">{ev.area_ha} ha</dd></div>
        <div><dt>{t("slope")}</dt><dd className="num">{ev.slope_deg ?? "?"}°</dd></div>
        <div><dt>{t("landcover")}</dt><dd style={{ fontSize: "var(--t-m)" }}>{ev.landcover}</dd></div>
        {ev.rain_30d_mm != null && <div><dt>Rain, 30 days before</dt><dd className="num">{ev.rain_30d_mm} mm</dd></div>}
        {expert && <div><dt>ΔHH / ΔHV</dt><dd className="num">{ev.dHH_db ?? "–"} / {ev.dHV_db ?? "n/a"} dB</dd></div>}
        {expert && <div><dt>Smallest p-value</dt><dd className="num">{ev.min_p}</dd></div>}
      </dl>

      <h3 style={{ fontSize: "var(--t-m)" }}>{t("explanations")}</h3>
      <p className="block__note">{t("scoreNote")}</p>
      <ol className="hyp">
        {top.map((h: any) => (
          <li key={h.name}>
            <div className="hyp__top"><strong>{h.name}</strong><span className="num">{h.score > 0 ? "+" : ""}{h.score.toFixed(2)}</span></div>
            <div className="hyp__bar" aria-hidden="true">
              <i style={{ left: h.score >= 0 ? "50%" : `${50 + h.score * 50}%`, width: `${Math.abs(h.score) * 50}%`, background: h.score >= 0 ? "var(--volume)" : "var(--double)" }} />
            </div>
            <p className="muted" style={{ fontSize: "var(--t-s)" }}>{h.why}</p>
            {h.supporting.length > 0 && <ul className="ev ev--for">{h.supporting.map((x: string) => <li key={x}>{x}</li>)}</ul>}
            {h.contradicting.length > 0 && <ul className="ev ev--against">{h.contradicting.map((x: string) => <li key={x}>{x}</li>)}</ul>}
            {expert && h.not_seen.length > 0 && <ul className="ev ev--against">{h.not_seen.map((x: string) => <li key={x}>not seen: {x}</li>)}</ul>}
            {expert && h.cannot_test.length > 0 && <ul className="ev ev--na">{h.cannot_test.map((x: string) => <li key={x}>can't test: {x}</li>)}</ul>}
            <p style={{ fontSize: "var(--t-s)" }}><span className="muted">Would confirm: </span>{h.would_confirm.join("; ")}</p>
            {LEARN[h.name] && (
              <p style={{ fontSize: "var(--t-s)" }}>
                <span className="muted">Learn how NISAR sees this: </span>
                {LEARN[h.name].map((l, i) => <span key={l.url}>{i > 0 && ", "}<a href={l.url} target="_blank" rel="noreferrer">{l.label}</a></span>)}
              </p>
            )}
          </li>
        ))}
      </ol>

      <LineChart
        label={`Radar brightness of ${ev.id} compared with its surroundings`}
        dates={dates} unit="dB" gaps={gaps} marker={ev.date_first}
        series={[
          ...(s.HH ? [{ name: "HH here", color: "#e46fb0", values: s.HH }] : []),
          ...(s.HV ? [{ name: "HV here", color: "#5bd18b", values: s.HV }] : []),
          ...(s.HH_surroundings ? [{ name: "HH around it", color: "#e46fb0", values: s.HH_surroundings, dashed: true }] : []),
          ...(s.HV_surroundings ? [{ name: "HV around it", color: "#5bd18b", values: s.HV_surroundings, dashed: true }] : []),
        ]}
      />
      {ev.motion_mostly && (
        <p className="notice" style={{ borderLeftColor: "var(--ink-2)", background: "var(--night-2)" }}>
          {t("bothOrbits")}: <strong>{t("mostly_" + ev.motion_mostly)}</strong> ({t("vertical")} {ev.motion_vertical_abs} mm, {t("eastWest")} {ev.motion_eastwest_abs} mm {t("per12")}).{" "}
          {t("signNote")}
        </p>
      )}
      {ev.los_series && (
        <LineChart
          label={`Line-of-sight ground motion at ${ev.id}`} dates={ev.los_series.to} unit="mm" gaps={gaps}
          series={[{ name: "Cumulative line-of-sight motion (relative to the area)", color: "#eaf0f7", values: ev.los_series.mm }]}
          height={160}
        />
      )}
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn btn--small" onClick={listen} aria-pressed={playing}>{playing ? t("stop") : t("listenChange")}</button>
        <span className="live" aria-live="polite">{live || `Pitch follows ${chan} brightness; a click marks the detected change.`}</span>
      </div>

      {ev.relationships.length > 0 && (
        <>
          <h3 style={{ fontSize: "var(--t-m)" }}>{t("relations")}</h3>
          <ul className="ev">
            {ev.relationships.map((r: any) => <li key={r.type}>{r.text} <span className="muted">Needs: {r.evidence_needed}</span></li>)}
          </ul>
        </>
      )}

      <h3 style={{ fontSize: "var(--t-m)" }}>{t("priority")}: <span className={`level level--${ev.priority.level}`}>{ev.priority.level}</span></h3>
      <div className="prio">
        {ev.priority.components.map((c: any) => (
          <div className="prio__row" key={c.key}>
            <span>{c.label} <span className="muted">×{c.weight}</span></span>
            <span className="prio__bar" aria-hidden="true"><i style={{ width: `${(c.value ?? 0) * 100}%`, opacity: c.value == null ? 0.2 : 1 }} /></span>
            <span className="num muted" title={c.detail}>{c.value == null ? "n/a" : c.value.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <p className="block__note">{ev.priority.note}</p>

      <div><button className="btn btn--small" onClick={() => window.print()}>{t("fieldCheck")}</button></div>
      <FieldSheet ev={ev} />
      {expert && (
        <details>
          <summary>Radar change fingerprint (JSON)</summary>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: "12px" }}>
            {JSON.stringify({ ...ev, series: undefined, geometry: undefined, hypotheses: undefined, priority: undefined }, null, 1)}
          </pre>
        </details>
      )}
    </article>
  );
}

function FieldSheet({ ev }: { ev: any }) {
  const [lon, lat] = ev.centroid;
  return (
    <section className="fieldsheet" aria-hidden="true">
      <h1>EarthEcho field check {ev.id}</h1>
      <p>Location: {lat.toFixed(5)}, {lon.toFixed(5)} · first seen by NISAR on {ev.date_first} · area {ev.area_ha} ha</p>
      <p>Leading explanation: {ev.hypotheses[0].name}</p>
      <h2>Please check and record</h2>
      <ol>
        {ev.hypotheses.slice(0, 3).flatMap((h: any) => h.would_confirm).map((c: string) => <li key={c}>{c}</li>)}
        <li>Photos facing north, east, south and west, with date and GPS position</li>
        <li>Visible cracks, soil movement, damage to road or vegetation</li>
      </ol>
      <p>Observer: ____________________ Date: ____________ Notes: ______________________________</p>
    </section>
  );
}


const LC_COL: Record<number, string> = { 10: "#5bd18b", 20: "#e0b04a", 30: "#d9d36a", 40: "#e46fb0", 50: "#f07a6a", 60: "#b9c4d4",
  70: "#eaf0f7", 80: "#4b8bea", 90: "#3fb8c4", 95: "#2fd29a", 100: "#e8d49a" };

/** The seasonal swing of each kind of ground: what EarthEcho removes before testing for change. */
function SeasonalDance({ result, gaps }: { result: any; gaps: [string, string][] }) {
  const chan = result.seasonal.some((c: any) => c.HV) ? "HV" : "HH";
  const top = [...result.seasonal].filter((c: any) => c[chan]).sort((a: any, b: any) => b.pixels - a.pixels).slice(0, 4);
  if (!top.length) return null;
  return (
    <section className="block" aria-labelledby="dance-title" style={{ borderTop: "1px solid var(--line)", paddingTop: "1rem" }}>
      <h3 id="dance-title">How each kind of ground moved through the season</h3>
      <p className="block__note">
        Median {chan} brightness per land type. These regional swings (rain, crops growing, drying) are removed
        before testing, so only local change is flagged. HH and HV here correlate at r = {result.chan_corr}, which the test corrects for.
      </p>
      <LineChart label={`Seasonal ${chan} brightness by land type`} dates={result.dates} unit="dB" gaps={gaps}
        series={top.map((c: any) => ({ name: c.name, color: LC_COL[c.code] ?? "#eaf0f7", values: c[chan] }))} height={170} />
    </section>
  );
}
