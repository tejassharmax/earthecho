// Story mode: "Dancing with the SARs" across many landscapes. The radar map stays pinned while
// chapters scroll over it. Every chapter's text is written from that place's analysis result each
// time it loads; only one background sentence per place is fixed.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Brand, LangSwitch } from "../components/Brand";
import { LineChart, fmt } from "../components/Charts";
import RadarSwipe, { type GeoOverlay, type ImageOverlay } from "../components/RadarSwipe";
import { api } from "../lib/api";

type Site = {
  id: string; name: string; short: string; biome: string; region: string; lat: number; lon: number;
  key: string; context: string; status: string; updated: string | null;
};

const LC_COL: Record<number, string> = { 10: "#5bd18b", 20: "#e0b04a", 30: "#d9d36a", 40: "#e46fb0", 50: "#f07a6a", 60: "#b9c4d4",
  70: "#eaf0f7", 80: "#4b8bea", 90: "#3fb8c4", 95: "#2fd29a", 100: "#e8d49a" };

const PHRASE: Record<string, string> = {
  "Open water / flooding": "water spreads", "Flooding under vegetation": "water beneath the canopy",
  "Vegetation loss (clearing, burn or scar)": "the canopy thins", "Crop harvest or ploughing": "harvest season",
  "Landslide or debris flow": "a slope gives way", "Slow ground movement": "the ground creeps",
  "New structure or debris pile": "something new rises", "Soil moisture after rain": "rain soaks in",
  "Wet snow": "snow arrives", "Ice or snow melt and refreeze": "melt and refreeze",
  "Seasonal wetland inundation": "wetlands fill and drain",
  "Crop growth (canopy filling in)": "crops fill in", "Fresh lava or volcanic surface change": "the volcano resurfaces", "Processing artifact or one-off error": "a signal worth double-checking",
};

async function loadResult(lat: number, lon: number) {
  const { job } = await api.investigate(lat, lon);
  for (let i = 0; i < 60; i++) {
    const j = await api.job(job);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.message);
    await new Promise(r => setTimeout(r, 2000));
  }
  throw new Error("timed out");
}

const largest = (r: any) => r?.events?.length ? [...r.events].sort((a: any, b: any) => b.area_ha - a.area_ha)[0] : null;
const pct = (x: number) => (x * 100 < 1 ? (x * 100).toFixed(1) : Math.round(x * 100).toString());
const db = (x: number | null) => (x == null ? "" : `${x > 0 ? "rose" : "fell"} by ${Math.abs(x).toFixed(1)} dB`);

/** The chapter, written from the result. */
function narrate(s: Site, r: any) {
  const e = largest(r);
  const headline = `${s.short}: ${e ? PHRASE[e.hypotheses[0].name] ?? e.hypotheses[0].name.toLowerCase() : "the ground held still"}`;
  const paras: string[] = [s.context];
  const pols = r.track.pols_always.join(" and ");
  paras.push(`From ${fmt(r.dates[0])} to ${fmt(r.dates[r.dates.length - 1])}, NISAR imaged this 6 × 6 km square ${r.dates.length} times`
    + ` in ${pols}${r.track.pols_always.length === 1 ? " only, as it does near the poles" : ""}.`
    + (r.masked_fraction > 0.01 ? ` ${pct(r.masked_fraction)}% of it lies in radar layover or shadow and was left out.` : ""));
  const chan = r.seasonal?.some((c: any) => c.HV) ? "HV" : "HH";
  const swing = (r.seasonal ?? []).filter((c: any) => c[chan] && c.pixels > 500).map((c: any) => {
    const v = c[chan].filter((x: number | null) => x != null);
    return { name: c.name.toLowerCase(), amp: Math.max(...v) - Math.min(...v) };
  }).sort((a: any, b: any) => b.amp - a.amp)[0];
  if (swing && swing.amp >= 0.5) paras.push(`Through the season, ${swing.name} swung by up to ${swing.amp.toFixed(1)} dB in ${chan}. EarthEcho removes that regional rhythm before looking for local change.`);
  if (!e) {
    paras.push(`No patch changed beyond radar speckle: ${pct(r.changed_fraction)}% of pixels flagged, about what chance alone produces. Quiet is a result too.`);
  } else {
    const parts = [e.dHH_db != null ? `HH ${db(e.dHH_db)}` : "", e.dHV_db != null ? `HV ${db(e.dHV_db)}` : ""].filter(Boolean).join(" and ");
    paras.push(`${pct(r.changed_fraction)}% of the square changed, in ${r.events.length} event${r.events.length > 1 ? "s" : ""}.`
      + ` The largest, ${e.area_ha} ha first seen on ${fmt(e.date_first)}, ${parts ? parts + ": " : ""}most consistent with ${e.hypotheses[0].name.toLowerCase()}.`);
  }
  const m2 = r.motion2d?.magnitude_limit_mm, m1 = r.motion?.velocity_limit_mm;
  if (m2) paras.push(`Seen from both orbit directions, ground motion stays within about ${m2} mm per 12 days relative to the surrounding square.`);
  else if (m1) paras.push(`Ground motion along the radar's line of sight stays within about ${m1} mm per 12 days relative to the surrounding square.`);
  return { headline, paras, event: e };
}

let dim = "";
function dimUrl() {
  if (dim) return dim;
  const c = document.createElement("canvas"); c.width = c.height = 1;
  const x = c.getContext("2d")!; x.fillStyle = "rgba(13,21,38,0.6)"; x.fillRect(0, 0, 1, 1);
  return (dim = c.toDataURL());
}

export default function Story() {
  const [sites, setSites] = useState<Site[]>([]);
  const [results, setResults] = useState<Record<string, any>>({});
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLElement | null)[]>([]);
  const loading = useRef(new Set<string>());

  // the server builds and refreshes showcase results itself; poll so new ones appear without a reload
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const list: Site[] = await api.showcase();
        if (!alive) return;
        const order = ["World", "United States"];
        setSites([...list].sort((a, b) => order.indexOf(a.region) - order.indexOf(b.region)));
        for (const s of list) {
          if (s.status === "ready" && !loading.current.has(`${s.id}@${s.updated}`)) {
            loading.current.add(`${s.id}@${s.updated}`);
            loadResult(s.lat, s.lon).then(r => alive && setResults(prev => ({ ...prev, [s.id]: r }))).catch(() => {});
          }
        }
      } catch { /* server unreachable: keep what we have */ }
    };
    tick();
    const h = window.setInterval(tick, 30000);
    return () => { alive = false; window.clearInterval(h); };
  }, []);

  useEffect(() => {
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => { if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.idx)); });
    }, { rootMargin: "-45% 0px -45% 0px" });
    refs.current.forEach(el => el && io.observe(el));
    return () => io.disconnect();
  }, [sites.length]);

  const site = active >= 1 && active <= sites.length ? sites[active - 1] : null;
  const res = site ? results[site.id] : null;
  const ev = largest(res);

  const view = useMemo(() => {
    if (!site) return { center: [20, 25] as [number, number], zoom: 1.4, key: `v-${active}` };
    return { center: (ev?.centroid ?? [site.lon, site.lat]) as [number, number], zoom: ev ? 13.6 : 12.4, key: `v-${active}-${!!ev}` };
  }, [active, site, ev]);

  const dates: [string | null, string | null] = res
    ? ev ? [res.dates[Math.max(0, ev.date_index - 1)], res.dates[ev.date_index]] : [res.dates[0], res.dates[res.dates.length - 1]]
    : [null, null];
  const L = res?.overlays?.layers;
  const side = (d: string | null): ImageOverlay[] =>
    L && d ? [{ id: `st-${d}`, url: api.layerUrl(L.key, d, "rgb"), corners: res.overlays.corners, opacity: 1 }] : [];
  const images: ImageOverlay[] = res ? [
    { id: "st-dim", url: dimUrl(), corners: res.overlays.corners, opacity: 0.5 },
    { id: "st-chg", url: res.overlays.change_direction, corners: res.overlays.corners, opacity: 1 },
  ] : [];
  const geo: GeoOverlay[] = ev ? [
    { id: "ev-st", kind: "fill", color: "#fde68a", opacity: 0.1, width: 2, data: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: ev.geometry }] } },
    ...(ev.downhill?.path?.length > 1 ? [{ id: "st-path", kind: "line" as const, color: "#ffffff", width: 2.5, dash: [1.5, 1.2],
      data: { type: "FeatureCollection" as const, features: [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: ev.downhill.path } }] } }] : []),
  ] : [];
  // the globe view at the start shows NISAR's most recent daily mosaic
  const latest = useMemo(() => Object.values(results).flatMap((r: any) => r.dates).sort().pop() ?? null, [results]);

  const chapters = sites.length + 2;
  return (
    <div className="story">
      <header className="story__nav">
        <Brand />
        <nav className="story__rail" aria-label="Chapters">
          {Array.from({ length: chapters }, (_, i) => (
            <button key={i} aria-current={i === active ? "step" : undefined}
                    onClick={() => refs.current[i]?.scrollIntoView({ behavior: "smooth", block: "center" })}>
              {i === 0 ? "Start" : i === chapters - 1 ? "You" : sites[i - 1]?.short}
            </button>
          ))}
        </nav>
        <LangSwitch />
      </header>

      <div className="story__stage">
        <RadarSwipe
          center={view.center} zoom={view.zoom} flyKey={view.key} interactive={false} initialPos={50}
          dateA={site ? dates[0] : latest} dateB={site ? dates[1] : latest}
          labelA={site && dates[0] ? `Before: ${fmt(dates[0])}` : undefined}
          labelB={site && dates[1] ? `After: ${fmt(dates[1])}` : undefined}
          imagesA={side(dates[0])} imagesB={side(dates[1])} images={images} geo={geo}
          ariaLabel={site ? `NISAR radar of ${site.name}, before and after the change` : "NISAR radar of Earth"}
        />
      </div>

      <main className="story__scroll">
        <section className="chapter chapter--intro" data-idx={0} ref={el => { refs.current[0] = el; }}>
          <div className="chapter__card glass">
            <h1>Dancing with the SARs</h1>
            <p className="chapter__lede">
              The ground is never still. Forests breathe with the rains, fields are cut, rivers wander, ice melts and
              refreezes, volcanoes swell, cities sink as water is pumped from below. NASA and ISRO's NISAR radar feels
              each of these moves, through cloud and darkness, every 12 days.
            </p>
            <p>Scroll to follow its beat across {sites.length || "several"} places, each a different dance partner. Every chapter is written from NISAR's latest data for that place.</p>
            <div className="hero__actions">
              <button className="btn btn--primary" onClick={() => refs.current[1]?.scrollIntoView({ behavior: "smooth", block: "center" })}>
                Start the story <span aria-hidden="true">↓</span>
              </button>
              <Link className="btn" to="/dashboard">Explore your own place</Link>
            </div>
          </div>
        </section>

        {sites.map((s, i) => {
          const r = results[s.id];
          const story = r ? narrate(s, r) : null;
          const chan = r?.seasonal?.some((c: any) => c.HV) ? "HV" : "HH";
          const top = r?.seasonal?.filter((c: any) => c[chan]).sort((a: any, b: any) => b.pixels - a.pixels).slice(0, 3) ?? [];
          const firstUS = s.region === "United States" && sites[i - 1]?.region !== "United States";
          return (
            <section key={s.id} className="chapter" data-idx={i + 1} ref={el => { refs.current[i + 1] = el; }}>
              <article className="chapter__card glass" aria-labelledby={`ch-${s.id}`}>
                {firstUS && <p className="chapter__region">Across the United States</p>}
                <p className="chapter__place">{s.biome}</p>
                <h2 id={`ch-${s.id}`}>{story?.headline ?? s.name}</h2>
                {story ? story.paras.map((para, k) => <p key={k}>{para}</p>) : (
                  <>
                    <p>{s.context}</p>
                    <p className="block__note">
                      {s.status === "building" ? "The server is analysing NISAR's data for this place now; this chapter fills in by itself in a few minutes."
                        : s.status === "queued" ? "Queued for analysis on the server; this chapter fills in by itself once it's done."
                        : s.status === "waiting" ? "This chapter fills in once NISAR's data for this place has been analysed."
                        : s.status.startsWith("error") ? "This place's analysis failed on the server; it will retry on the next refresh."
                        : "Loading the analysis…"}
                    </p>
                  </>
                )}
                {top.length > 0 && (
                  <figure className="chapter__chart">
                    <LineChart label={`Seasonal ${chan} rhythm by land type at ${s.name}`} dates={r.dates} unit="dB" height={130}
                      series={top.map((c: any) => ({ name: c.name, color: LC_COL[c.code] ?? "#eaf0f7", values: c[chan] }))} />
                    <figcaption className="block__note">The season's rhythm in {chan}, per land type.</figcaption>
                  </figure>
                )}
                {story?.event && (
                  <p className="chapter__finding">
                    <strong>{story.event.hypotheses[0].name}</strong>: {story.event.hypotheses[0].supporting.slice(0, 2).join("; ")}.
                  </p>
                )}
                <div className="chapter__foot">
                  <Link className="btn btn--small" to={`/dashboard?lat=${s.lat}&lon=${s.lon}&analyse=1`}>Open {s.short} in the dashboard</Link>
                  {s.updated && <span className="block__note">{s.status === "refreshing" ? "Updating with a newer NISAR pass…" : `Analysed ${fmt(s.updated)}`}</span>}
                </div>
              </article>
            </section>
          );
        })}

        <section className="chapter chapter--outro" data-idx={chapters - 1} ref={el => { refs.current[chapters - 1] = el; }}>
          <div className="chapter__card glass">
            <h2>Now, your place</h2>
            <p>Every place on land has its own rhythm. Search for yours, or use your location, and EarthEcho will listen to it the same way.</p>
            <div className="hero__actions">
              <Link className="btn btn--primary" to="/dashboard">Open the dashboard</Link>
              <Link className="btn" to="/dashboard?geo=1">Use my location</Link>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
