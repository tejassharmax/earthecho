import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Brand, LangSwitch, LevelSwitch } from "../components/Brand";
import { AcquisitionTimeline, RainBars, fmt } from "../components/Charts";
import Investigate from "../components/Investigate";
import RadarSwipe, { type GeoOverlay, type ImageOverlay } from "../components/RadarSwipe";
import { api, type Coverage } from "../lib/api";
import { nisarTiles } from "../lib/gibs";
import { LangContext, useT } from "../lib/i18n";
import { play, rhythmNotes } from "../lib/sound";

const DEFAULT = { lat: 30.555, lon: 79.56 }; // Joshimath, used only if location is unavailable
const LC: Record<number, string> = { 10: "#006400", 20: "#ffbb22", 30: "#ffff4c", 40: "#f096ff", 50: "#fa0000", 60: "#b4b4b4",
  70: "#f0f0f0", 80: "#0064c8", 90: "#0096a0", 95: "#00cf75", 100: "#fae6a0" };
type Tab = "overview" | "changes" | "ground" | "about";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "overview" }, { id: "changes", label: "changesTab" }, { id: "ground", label: "groundTab" }, { id: "about", label: "aboutTab" },
];

function lon2tile(lon: number, z: number) { return Math.floor(((lon + 180) / 360) * 2 ** z); }
function lat2tile(lat: number, z: number) {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * 2 ** z);
}
/** GIBS daily mosaics only contain data where NISAR imaged that day: check a tile. */
async function gibsHas(date: string, lat: number, lon: number) {
  const z = 10;
  const url = nisarTiles(date).replace(/^retry:\/\//, "").replace("{z}", String(z)).replace("{y}", String(lat2tile(lat, z))).replace("{x}", String(lon2tile(lon, z)));
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(url);
      if (r.status === 404) return false;
      if (r.ok) return (await r.blob()).size > 1500;
    } catch { /* retry */ }
    await new Promise(res => setTimeout(res, 700 * (i + 1)));
  }
  return false;
}

function square(lat: number, lon: number, km = 3): GeoJSON.FeatureCollection {
  const dlat = km / 111.32, dlon = km / (111.32 * Math.cos((lat * Math.PI) / 180));
  return { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon",
    coordinates: [[[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]] } }] };
}
const lines = (arr: [number, number][][] = []): GeoJSON.FeatureCollection => ({ type: "FeatureCollection",
  features: arr.map(c => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: c } })) });

let dimCache = "";
function dimUrl() {
  if (dimCache) return dimCache;
  const c = document.createElement("canvas"); c.width = c.height = 1;
  const x = c.getContext("2d")!; x.fillStyle = "rgba(13,21,38,0.62)"; x.fillRect(0, 0, 1, 1);
  return (dimCache = c.toDataURL());
}

type Layers = { motion: boolean; motion2d: boolean; events: boolean; terrain: boolean; landcover: boolean; osm: boolean; swaths: boolean };

/** Area monitoring priority: context for any place, even with no detected change. Not a hazard prediction. */
function areaPriority(site: any, osm: any, inv: any) {
  const comps: { label: string; weight: number; value: number | null; detail: string }[] = [];
  const add = (label: string, weight: number, value: number | null, detail: string) =>
    comps.push({ label, weight, value: value == null ? null : Math.max(0, Math.min(1, value)), detail });
  const steep = site?.terrain?.steep_fraction;
  add("steepGround", 0.25, steep == null ? null : steep / 0.5, steep == null ? "unknown" : `${Math.round(steep * 100)}% steeper than 30°`);
  const r30 = site?.rain?.last30_mm;
  add("recentRain", 0.15, r30 == null ? null : r30 / 150, r30 == null ? "unknown" : `${r30} mm in 30 days`);
  const tot = site?.rain?.total_mm;
  add("seasonRain", 0.10, tot == null ? null : tot / 2000, tot == null ? "unknown" : `${tot} mm since June`);
  add("roadsShort", 0.15, osm?.road_km == null ? null : osm.road_km / 40, osm?.road_km == null ? "not loaded" : `${osm.road_km} km nearby`);
  add("buildings", 0.10, osm?.buildings == null ? null : osm.buildings / 1000, osm?.buildings == null ? "not loaded" : `${osm.buildings} mapped`);
  add("groundChanged", 0.25, inv?.changed_fraction == null ? null : inv.changed_fraction / 0.03,
      inv?.changed_fraction == null ? "run the analysis to include this" : `${(inv.changed_fraction * 100).toFixed(1)}% of the area`);
  const used = comps.filter(c => c.value != null);
  const w = used.reduce((a, c) => a + c.weight, 0) || 1;
  const score = used.reduce((a, c) => a + c.weight * (c.value as number), 0) / w;
  return { score, level: score >= 0.55 ? "Elevated" : score >= 0.35 ? "Moderate" : "Routine", comps };
}

export default function Dashboard() {
  const t = useT();
  const { expert } = useContext(LangContext);
  const [params, setParams] = useSearchParams();
  const lat = params.get("lat") ? Number(params.get("lat")) : null;
  const lon = params.get("lon") ? Number(params.get("lon")) : null;
  const [locMsg, setLocMsg] = useState<string | null>(null);
  const [health, setHealth] = useState<{ deep_analysis: boolean } | null>(null);
  const [cov, setCov] = useState<Coverage | null>(null);
  const [covErr, setCovErr] = useState<string | null>(null);
  const [site, setSite] = useState<any>(null);
  const [osm, setOsm] = useState<any>(null);
  const [place, setPlace] = useState<string | null>(null);
  const [dates, setDates] = useState<[string | null, string | null]>([null, null]);
  const [inv, setInv] = useState<any>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [view, setView] = useState<{ center: [number, number]; zoom: number; key: string } | null>(null);
  const [highlight, setHighlight] = useState(true);
  const [colorBy, setColorBy] = useState<"what" | "when">("what");
  const [grayscale, setGrayscale] = useState(false);
  const [source, setSource] = useState<"nasa" | "rgb" | "hh" | "hv">("nasa");
  const [showcase, setShowcase] = useState<any[]>([]);
  const autoAnalyse = params.get("analyse") === "1";
  const [layers, setLayers] = useState<Layers>({ motion: false, motion2d: false, events: true, terrain: false, landcover: false, osm: true, swaths: false });
  const [layersOpen, setLayersOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<{ name: string; lat: number; lon: number }[]>([]);
  const [tour, setTour] = useState<number | null>(null);
  const [rhythm, setRhythm] = useState(false);
  const [live, setLive] = useState("");
  const stopRhythm = useRef<() => void>();
  const invRef = useRef<any>(null);
  invRef.current = inv;

  const go = useCallback((la: number, lo: number) => setParams({ lat: la.toFixed(5), lon: lo.toFixed(5) }), [setParams]);

  const locate = useCallback(() => {
    if (!("geolocation" in navigator)) { setLocMsg("This browser can't share its location. Search for a place or click the map."); go(DEFAULT.lat, DEFAULT.lon); return; }
    setLocMsg("Asking your browser for your location…");
    navigator.geolocation.getCurrentPosition(
      p => { setLocMsg(null); go(p.coords.latitude, p.coords.longitude); },
      () => { setLocMsg("Location wasn't shared, so this shows Joshimath. Search for a place or click the map."); if (lat == null) go(DEFAULT.lat, DEFAULT.lon); },
      { timeout: 10000, maximumAge: 600000 },
    );
  }, [go, lat]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ deep_analysis: false }));
    if (lat == null || lon == null) locate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // everything follows the location
  useEffect(() => {
    if (lat == null || lon == null) return;
    setCov(null); setCovErr(null); setSite(null); setOsm(null); setInv(null); setSelected(null); setPlace(null); setDates([null, null]);
    setView({ center: [lon, lat], zoom: 12.3, key: `${lat},${lon}` });
    let alive = true;
    api.reverse(lat, lon).then(r => alive && setPlace(r.name ?? null)).catch(() => {});
    api.coverage(lat, lon).then(async c => {
      if (!alive) return;
      setCov(c);
      const best = c.tracks.find(tr => tr.min_coverage >= 0.95) ?? c.tracks[0];
      if (!best) return;
      const ds = best.acquisitions.map(a => a.date);
      // three at a time: a burst of parallel requests makes GIBS answer with 500s
      const has: boolean[] = [];
      for (let i = 0; i < ds.length; i += 3) {
        has.push(...await Promise.all(ds.slice(i, i + 3).map(d => gibsHas(d, lat, lon))));
        if (!alive) return;
      }
      const ok = ds.filter((_, i) => has[i]);
      if (alive) setDates([ok[0] ?? null, ok[ok.length - 1] ?? ok[0] ?? null]);
    }).catch(e => alive && setCovErr(e.message));
    api.site(lat, lon).then(s => alive && setSite(s)).catch(() => alive && setSite({ error: true }));
    api.osm(lat, lon).then(o => alive && setOsm(o)).catch(() => alive && setOsm({ error: "unavailable" }));
    return () => { alive = false; };
  }, [lat, lon]);

  useEffect(() => {
    if (q.trim().length < 3) { setResults([]); return; }
    const h = window.setTimeout(() => api.geocode(q.trim()).then(setResults).catch(() => setResults([])), 450);
    return () => window.clearTimeout(h);
  }, [q]);

  useEffect(() => { api.showcase().then(setShowcase).catch(() => {}); }, []);
  // once EarthEcho's own 20 m layers exist, show them (sharper and calibrated, vs. NASA's daily mosaic)
  useEffect(() => { setSource(inv?.overlays?.layers ? "rgb" : "nasa"); }, [inv]);

  /** Zoom to an event and set the swipe to the passes just before and after it changed. */
  const focusEvent = useCallback((ev: any, result?: any) => {
    const r = result ?? invRef.current;
    if (!r) return;
    const i = ev.date_index as number;
    setDates([r.dates[Math.max(0, i - 1)], r.dates[i]]);
    setView({ center: ev.centroid, zoom: 14.3, key: `${ev.id}-${Date.now()}` });
    setHighlight(true);
  }, []);

  const images = useMemo<ImageOverlay[]>(() => {
    const out: ImageOverlay[] = [];
    if (site?.overlays && layers.terrain) out.push({ id: "terrain", url: site.overlays.hillshade, corners: site.overlays.corners, opacity: 0.7 });
    if (site?.overlays && layers.landcover) out.push({ id: "landcover", url: site.overlays.landcover, corners: site.overlays.corners, opacity: 0.75 });
    if (inv?.overlays && highlight) {
      out.push({ id: "dim", url: dimUrl(), corners: inv.overlays.corners, opacity: 1 });
      out.push({ id: "chg", url: colorBy === "what" ? inv.overlays.change_direction : inv.overlays.change_date, corners: inv.overlays.corners, opacity: 1 });
    }
    if (inv?.motion?.velocity_png && layers.motion) out.push({ id: "motion", url: inv.motion.velocity_png, corners: inv.motion.corners, opacity: 0.85 });
    if (inv?.motion2d?.magnitude_png && layers.motion2d) out.push({ id: "motion2d", url: inv.motion2d.magnitude_png, corners: inv.motion2d.corners, opacity: 0.9 });
    return out;
  }, [site, inv, layers, highlight, colorBy]);

  // per-side EarthEcho radar layers for the two compared dates
  const side = (d: string | null): ImageOverlay[] => {
    const L = inv?.overlays?.layers;
    if (source === "nasa" || !L || !d || !L.dates.includes(d)) return [];
    const kind = L.kinds.includes(source) ? source : "rgb";
    return [{ id: `ee-${d}-${kind}`, url: api.layerUrl(L.key, d, kind), corners: inv.overlays.corners, opacity: 1 }];
  };
  const imagesA = useMemo(() => side(dates[0]), [inv, source, dates[0]]);  // eslint-disable-line react-hooks/exhaustive-deps
  const imagesB = useMemo(() => side(dates[1]), [inv, source, dates[1]]);  // eslint-disable-line react-hooks/exhaustive-deps

  const geo = useMemo<GeoOverlay[]>(() => {
    if (lat == null || lon == null) return [];
    // the analysis centre snaps to a ~1 km grid; once analysed, draw the square where it really is
    const g: GeoOverlay[] = [{ id: "window", data: inv ? square(inv.lat, inv.lon) : square(lat, lon), kind: "line", color: "#eaf0f7", width: 1.5, dash: [2, 2] }];
    if (cov && layers.swaths) {
      const fc = (dir: string): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features: cov.tracks
        .filter(tr => tr.footprint && tr.direction === dir)
        .map(tr => ({ type: "Feature", properties: { track: tr.track }, geometry: tr.footprint! })) });
      g.push({ id: "swath-asc", data: fc("ascending"), kind: "line", color: "#eaf0f7", width: 2, dash: [3, 2] });
      g.push({ id: "swath-desc", data: fc("descending"), kind: "line", color: "#8e9bb0", width: 2 });
    }
    if (osm && layers.osm) {
      g.push({ id: "rivers", data: lines(osm.river_lines), kind: "line", color: "#4b8bea", width: 2 });
      g.push({ id: "roads", data: lines(osm.roads), kind: "line", color: "#f2b84b", width: 1.2, opacity: 0.7 });
    }
    if (inv?.events && layers.events) {
      g.push({ id: "ev-poly", kind: "fill", color: "#fde68a", opacity: 0.08, width: 2, data: { type: "FeatureCollection",
        features: inv.events.map((e: any) => ({ type: "Feature", properties: { id: e.id }, geometry: e.geometry })) } });
      g.push({ id: "ev-pts", kind: "circle", color: "#fde68a", width: 5, data: { type: "FeatureCollection",
        features: inv.events.map((e: any) => ({ type: "Feature", properties: { id: e.id }, geometry: { type: "Point", coordinates: e.centroid } })) } });
      const sel = inv.events.find((e: any) => e.id === selected);
      const path = sel?.downhill?.path;
      if (path?.length > 1) g.push({ id: "ev-path", kind: "line", color: "#ffffff", width: 2.5, dash: [1.5, 1.2], data: { type: "FeatureCollection",
        features: [{ type: "Feature", properties: { id: sel.id }, geometry: { type: "LineString", coordinates: path } }] } });
      if (sel) g.push({ id: "ev-sel", kind: "circle", color: "#ffffff", width: 9, data: { type: "FeatureCollection",
        features: [{ type: "Feature", properties: { id: sel.id }, geometry: { type: "Point", coordinates: sel.centroid } }] } });
    }
    g.push({ id: "here", kind: "circle", color: "#eaf0f7", width: 4, data: { type: "FeatureCollection",
      features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lon, lat] } }] } });
    return g;
  }, [lat, lon, osm, inv, layers, selected, cov]);

  const playRhythm = () => {
    if (rhythm) { stopRhythm.current?.(); setRhythm(false); return; }
    if (!cov) return;
    const passes = cov.tracks.flatMap((tr, i) => tr.acquisitions.map(a => ({ date: a.date, trackIndex: i, track: tr.track }))).sort((a, b) => a.date.localeCompare(b.date));
    setRhythm(true);
    stopRhythm.current = play(rhythmNotes(passes), i => {
      setLive(`${fmt(passes[i].date)}: track ${passes[i].track}`);
      if (i === passes.length - 1) window.setTimeout(() => setRhythm(false), 400);
    });
  };

  const TOUR: { text: string; tab?: Tab; target: string }[] = [
    { text: "This is real NISAR radar. Drag the divider to compare two passes. The dashed square is the 6 × 6 km area EarthEcho analyses.", target: "sec-map" },
    { text: "Click anywhere on the map, search, or use your location: every panel switches to that place.", target: "sec-place" },
    { text: "Overview: each dot is one NISAR pass over this place. Click a dot to put that date on the right of the map, or listen to the passes.", tab: "overview", target: "sec-tabs" },
    { text: "Changes: run the analysis. Changed ground lights up on the map, and each change gets ranked explanations.", tab: "changes", target: "sec-tabs" },
    { text: "Ground: slope, land cover, rainfall, rivers and roads, the context the explanations rely on.", tab: "ground", target: "sec-tabs" },
    { text: "About the data: how accurate this is, and what NISAR's data can and can't tell you yet.", tab: "about", target: "sec-tabs" },
  ];
  useEffect(() => {
    document.querySelectorAll(".highlight").forEach(el => el.classList.remove("highlight"));
    if (tour == null) return;
    const step = TOUR[tour];
    if (step.tab) setTab(step.tab);
    const el = document.getElementById(step.target);
    el?.classList.add("highlight");
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tour]);

  const hasAscDesc = cov ? new Set(cov.tracks.map(tr => tr.direction)).size > 1 : false;
  const allDates = cov ? [...new Set(cov.tracks.flatMap(tr => tr.acquisitions.map(a => a.date)))].sort() : [];
  const nEvents = inv?.events?.length ?? 0;

  return (
    <div className="dash">
      <header className="topbar">
        <Brand />
        <div className="search">
          <label className="sr-only" htmlFor="place-search">{t("searchPlace")}</label>
          <input id="place-search" type="search" placeholder={t("searchPlace")} value={q} onChange={e => setQ(e.target.value)} autoComplete="off" />
          {results.length > 0 && (
            <ul className="search__results">
              {results.map(r => (
                <li key={`${r.lat},${r.lon}`}><button onClick={() => { setQ(""); setResults([]); go(r.lat, r.lon); }}>{r.name}</button></li>
              ))}
            </ul>
          )}
        </div>
        <button className="btn btn--small" onClick={locate}>{t("useMyLocation")}</button>
        <span className="topbar__spacer" />
        <button className="btn btn--small" onClick={() => setTour(0)}>{t("guide")}</button>
        <LevelSwitch />
        <LangSwitch />
      </header>

      <div className="dash__body">
        <div className="mapcol" id="sec-map">
          {lat != null && lon != null && view ? (
            <RadarSwipe
              center={view.center} zoom={view.zoom} flyKey={view.key} dateA={dates[0]} dateB={dates[1]} grayscale={grayscale}
              labelA={dates[0] ? `${t("before")}: ${fmt(dates[0])}` : undefined}
              labelB={dates[1] ? `${t("after")}: ${fmt(dates[1])}` : undefined}
              images={images} imagesA={imagesA} imagesB={imagesB} geo={geo}
              onMapClick={(lo, la) => go(la, lo)}
              onFeatureClick={(_, pr) => { const ev = invRef.current?.events?.find((e: any) => e.id === pr.id); if (ev) { setSelected(ev.id); setTab("changes"); focusEvent(ev); } }}
              ariaLabel="Map comparing two NISAR radar dates. Click anywhere to analyse that place."
            >
              <MapLegend inv={inv} highlight={highlight} colorBy={colorBy} grayscale={grayscale} layers={layers} source={source} selected={inv?.events?.find((e: any) => e.id === selected)} />
            </RadarSwipe>
          ) : <div className="swipe" />}

          <div className="layerbar glass" role="group" aria-label="Map controls">
            {inv?.overlays?.layers && (
              <span className="layerbar__group">
                <span className="layerbar__label">Radar</span>
                <span className="toggle toggle--tiny" role="group" aria-label="Radar image source">
                  <button aria-pressed={source === "nasa"} onClick={() => setSource("nasa")} title="NASA's daily NISAR mosaic (GIBS)">NASA mosaic</button>
                  <button aria-pressed={source === "rgb"} onClick={() => { setSource("rgb"); if (!inv.dates.includes(dates[0]) || !inv.dates.includes(dates[1])) setDates([inv.dates[0], inv.dates[inv.dates.length - 1]]); }} title="EarthEcho's calibrated 20 m composite: red HH, green HV, blue HH/HV">20 m colour</button>
                  <button aria-pressed={source === "hh"} onClick={() => setSource("hh")} title="Co-polarised HH backscatter">HH</button>
                  {inv.overlays.layers.kinds.includes("hv") && <button aria-pressed={source === "hv"} onClick={() => setSource("hv")} title="Cross-polarised HV backscatter (vegetation volume)">HV</button>}
                </span>
              </span>
            )}
            {allDates.length > 0 && (
              <span className="layerbar__group">
                <label htmlFor="date-a">{t("compare")}</label>
                <select id="date-a" value={dates[0] ?? ""} onChange={e => setDates([e.target.value, dates[1]])}>
                  {(source !== "nasa" && inv ? inv.dates as string[] : allDates).map(d => <option key={d} value={d}>{fmt(d)}</option>)}
                </select>
                <label htmlFor="date-b">{t("with")}</label>
                <select id="date-b" value={dates[1] ?? ""} onChange={e => setDates([dates[0], e.target.value])}>
                  {(source !== "nasa" && inv ? inv.dates as string[] : allDates).map(d => <option key={d} value={d}>{fmt(d)}</option>)}
                </select>
              </span>
            )}
            {inv && (
              <span className="layerbar__group">
                <label><input type="checkbox" checked={highlight} onChange={e => setHighlight(e.target.checked)} /> {t("showChanges")}</label>
                {highlight && (
                  <span className="toggle toggle--tiny" role="group" aria-label="Colour changes by">
                    <button aria-pressed={colorBy === "what"} onClick={() => setColorBy("what")}>{t("brighterDarker")}</button>
                    <button aria-pressed={colorBy === "when"} onClick={() => setColorBy("when")}>{t("when")}</button>
                  </span>
                )}
              </span>
            )}
            <span className="topbar__spacer" />
            <div className="layers">
              <button className="btn btn--small" aria-expanded={layersOpen} onClick={() => setLayersOpen(o => !o)}>{t("moreLayers")}</button>
              {layersOpen && (
                <div className="layers__menu" role="group" aria-label="More map layers">
                  {nEvents > 0 && <label><input type="checkbox" checked={layers.events} onChange={e => setLayers({ ...layers, events: e.target.checked })} /> {t("events")}</label>}
                  {inv?.motion?.velocity_png && <label><input type="checkbox" checked={layers.motion} onChange={e => setLayers({ ...layers, motion: e.target.checked })} /> {t("groundMotion")}</label>}
                  {inv?.motion2d?.magnitude_png && <label><input type="checkbox" checked={layers.motion2d} onChange={e => setLayers({ ...layers, motion2d: e.target.checked })} /> {t("motionBoth")}</label>}
                  {cov && cov.tracks.some(tr => tr.footprint) && <label><input type="checkbox" checked={layers.swaths} onChange={e => {
                    setLayers({ ...layers, swaths: e.target.checked });
                    if (e.target.checked && lat != null && lon != null) setView({ center: [lon, lat], zoom: 6.8, key: `sw-${Date.now()}` });
                  }} /> {t("swaths")}</label>}
                  <label><input type="checkbox" checked={layers.osm} onChange={e => setLayers({ ...layers, osm: e.target.checked })} /> {t("roadsRivers")}</label>
                  <label><input type="checkbox" checked={layers.terrain} onChange={e => setLayers({ ...layers, terrain: e.target.checked })} /> {t("terrainShading")}</label>
                  <label><input type="checkbox" checked={layers.landcover} onChange={e => setLayers({ ...layers, landcover: e.target.checked })} /> {t("landcover")}</label>
                  <label><input type="checkbox" checked={grayscale} onChange={e => setGrayscale(e.target.checked)} /> {t("grayscale")}</label>
                </div>
              )}
            </div>
          </div>
        </div>

        <main className="panel">
          <section className="place" id="sec-place">
            <h1>{place ?? (lat != null ? "This place" : "Finding your place…")}</h1>
            {lat != null && lon != null && <p className="muted num place__coords">{lat.toFixed(4)}°, {lon.toFixed(4)}° · {t("clickToMove")}</p>}
            {locMsg && <p className="notice">{locMsg}</p>}
            {cov && cov.tracks.length > 0 && (
              <dl className="facts facts--compact">
                <div><dt>{t("passes")}</dt><dd className="num">{cov.n_acquisitions}</dd></div>
                {cov.revisit_days && <div><dt>{t("looksEvery")}</dt><dd className="num">{cov.revisit_days} {t("days")}</dd></div>}
                {cov.next_expected_pass && <div><dt>{t("nextPass")}</dt><dd className="num">~{fmt(cov.next_expected_pass)}</dd></div>}
                <div><dt>{t("changesFound")}</dt><dd className="num">{inv ? nEvents : "–"}</dd></div>
              </dl>
            )}
            {cov && cov.tracks.length === 0 && <p className="notice">NISAR hasn't published calibrated images of this spot yet. Open ocean and some polar regions are imaged less often. Try a place on land.</p>}
            {covErr && <p className="notice error">Couldn't reach NASA's archive: {covErr}</p>}
            {showcase.length > 0 && (
              <nav className="showcase" aria-label="Pre-analysed showcase places">
                <span className="block__note">Instant results:</span>
                {showcase.map(sc => (
                  <button key={sc.id} className="chip" aria-pressed={inv?.lat === sc.lat && inv?.lon === sc.lon}
                          onClick={() => { setTab("changes"); setParams({ lat: sc.lat.toFixed(5), lon: sc.lon.toFixed(5), analyse: "1" }); }}>
                    {sc.short}
                  </button>
                ))}
              </nav>
            )}
          </section>

          <div className="tabs" id="sec-tabs">
            <div className="tabs__list" role="tablist" aria-label="Dashboard sections">
              {TABS.map(x => (
                <button key={x.id} role="tab" id={`tab-${x.id}`} aria-selected={tab === x.id} aria-controls={`panel-${x.id}`}
                        tabIndex={tab === x.id ? 0 : -1} onClick={() => setTab(x.id)}
                        onKeyDown={e => {
                          const i = TABS.findIndex(y => y.id === tab);
                          if (e.key === "ArrowRight") setTab(TABS[(i + 1) % TABS.length].id);
                          if (e.key === "ArrowLeft") setTab(TABS[(i + TABS.length - 1) % TABS.length].id);
                        }}>
                  {t(x.label)}{x.id === "changes" && inv ? <span className="tabs__count">{nEvents}</span> : null}
                </button>
              ))}
            </div>

            <section role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" hidden={tab !== "overview"} className="tabs__panel">
              <div className="block">
                <h2>{t("timeline")}</h2>
                {!cov && !covErr && <p className="muted">Asking NASA's archive…</p>}
                {cov && cov.tracks.length > 0 && <>
                  <AcquisitionTimeline tracks={cov.tracks} gaps={cov.data_gaps} selected={dates.filter(Boolean) as string[]} onPick={d => setDates([dates[0], d])} />
                  <p className="block__note">
                    {cov.tracks.map(tr => `${tr.direction === "ascending" ? "Northbound" : "Southbound"} track ${expert ? tr.track : tr.track.split("_")[0]}: ${tr.n_dates} images, ${tr.pols_always.join("+") || "HH"}${tr.min_coverage < 0.95 ? " (covers part of the square)" : ""}`).join(". ")}.
                  </p>
                  <div className="row">
                    {(() => {
                      const acq = cov.tracks.flatMap(tr => tr.acquisitions.map(a => ({ ...a, track: tr.track }))).find(a => a.date === dates[1] && a.browse);
                      return acq ? (
                        <a href={acq.browse!.replace("_thumbnail", "")} target="_blank" rel="noreferrer" className="thumb">
                          <img src={acq.browse!} alt={`NISAR preview of the whole ${fmt(acq.date)} pass on track ${acq.track}`}
                               onError={e => { const im = e.currentTarget; if (!im.dataset.retry) { im.dataset.retry = "1"; im.src = `${acq.browse}?retry=${Date.now()}`; } }} />
                          <span className="block__note">Whole {fmt(acq.date)} pass</span>
                        </a>
                      ) : null;
                    })()}
                    <div style={{ display: "grid", gap: "0.5rem", alignContent: "start" }}>
                      <button className="btn btn--small" onClick={playRhythm} aria-pressed={rhythm}>{rhythm ? t("stop") : t("listen")}</button>
                      <span className="live" aria-live="polite">{live}</span>
                    </div>
                  </div>
                </>}
              </div>
              {site?.terrain && (() => {
                const ap = areaPriority(site, osm, inv);
                return (
                  <div className="block">
                    <div className="block__head">
                      <h2>{t("whyWatch")}</h2>
                      <span className={`level level--${ap.level}`}>{t(ap.level)}</span>
                    </div>
                    <div className="prio">
                      {ap.comps.map(c => (
                        <div className="prio__row" key={c.label}>
                          <span>{t(c.label)} <span className="muted">×{c.weight}</span></span>
                          <span className="prio__bar" aria-hidden="true"><i style={{ width: `${(c.value ?? 0) * 100}%`, opacity: c.value == null ? 0.2 : 1 }} /></span>
                          <span className="num muted" title={c.detail}>{c.value == null ? "n/a" : c.value.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                    <p className="block__note">{t("areaNote")}</p>
                  </div>
                );
              })()}
              <div className="block">
                <h2>{t("future")}</h2>
                {cov && cov.tracks.length > 0 ? (
                  <ul className="ev">
                    <li>NISAR looks here about every {cov.revisit_days} days from {cov.tracks.length} orbit{cov.tracks.length > 1 ? "s" : ""}, day or night, through cloud. A change after heavy rain can be checked within days.</li>
                    <li>Next expected passes: {cov.tracks.map(tr => `${fmt(tr.next_expected_pass)} (track ${tr.track.split("_")[0]})`).join(", ")}. Estimates from the 12-day repeat.</li>
                    {hasAscDesc && <li>Northbound and southbound orbits both see this place, so slow ground motion could be split into up-down and east-west parts, telling subsidence apart from slope creep.</li>}
                    <li>Each new pass can be tested automatically against this baseline, turning the priority score into an early-attention list for field teams.</li>
                    <li>NISAR also takes urgent-response images after disasters. EarthEcho does not request them; it shows where they would help most.</li>
                  </ul>
                ) : <p className="muted">Pick a place on land to see what NISAR's repeat coverage makes possible there.</p>}
              </div>
            </section>

            <section role="tabpanel" id="panel-changes" aria-labelledby="tab-changes" hidden={tab !== "changes"} className="tabs__panel">
              {lat != null && lon != null && (
                <Investigate lat={lat} lon={lon} enabled={health ? health.deep_analysis : null} gaps={cov?.data_gaps ?? []}
                  result={inv} setResult={r => { setInv(r); if (r) setHighlight(true); }} selected={selected} setSelected={setSelected}
                  onFocusEvent={(ev, r) => focusEvent(ev, r)} autoStart={autoAnalyse} />
              )}
            </section>

            <section role="tabpanel" id="panel-ground" aria-labelledby="tab-ground" hidden={tab !== "ground"} className="tabs__panel">
              <div className="block">
                <div className="block__head"><h2>{t("ground")}</h2><span className="block__note">6 × 6 km around the point</span></div>
                {!site && <p className="muted">Loading terrain, land cover and rainfall…</p>}
                {site?.error && <p className="notice error">The terrain and rainfall service didn't answer. Try again in a moment.</p>}
                {site?.terrain && <>
                  <dl className="facts">
                    <div><dt>{t("elevation")}</dt><dd className="num">{site.point.elevation_m ?? "?"} m</dd></div>
                    <div><dt>{t("slope")} here</dt><dd className="num">{site.point.slope_deg ?? "?"}°</dd></div>
                    <div><dt>Steeper than 30°</dt><dd className="num">{site.terrain.steep_fraction != null ? Math.round(site.terrain.steep_fraction * 100) : "?"}%</dd></div>
                    <div><dt>Ground here</dt><dd style={{ fontSize: "var(--t-m)" }}>{site.point.landcover}</dd></div>
                  </dl>
                  {site.landcover.classes.length > 0 && <>
                    <div className="cover-bar" role="img" aria-label={site.landcover.classes.map((c: any) => `${c.name} ${Math.round(c.fraction * 100)}%`).join(", ")}>
                      {site.landcover.classes.map((c: any) => <span key={c.code} style={{ width: `${c.fraction * 100}%`, background: LC[c.code] }} />)}
                    </div>
                    <ul className="cover-list">
                      {site.landcover.classes.filter((c: any) => c.fraction >= 0.01).map((c: any) => <li key={c.code}><i style={{ background: LC[c.code] }} />{c.name} {Math.round(c.fraction * 100)}%</li>)}
                    </ul>
                  </>}
                </>}
              </div>
              {site?.rain?.dates && (
                <div className="block">
                  <h3>{t("rain")}: {site.rain.total_mm} mm <span className="muted">(last 30 days: {site.rain.last30_mm ?? "?"} mm)</span></h3>
                  <RainBars dates={site.rain.dates} mm={site.rain.mm} marks={inv?.events?.slice(0, 6).map((e: any) => e.date_first) ?? []} />
                  {nEvents > 0 && <p className="block__note">Yellow lines mark when changes were first seen.</p>}
                </div>
              )}
              <div className="block">
                <h3>{t("roadsRivers")}</h3>
                {!osm && <p className="muted">Loading from OpenStreetMap… this can take up to a minute the first time.</p>}
                {osm && !osm.error && (
                  <p>{t("roads")}: <strong className="num">{osm.road_km} km</strong>. Mapped buildings: <strong className="num">{osm.buildings ?? "?"}</strong>.
                    {osm.nearest_river && <> Nearest river or stream: {osm.nearest_river.name ?? osm.nearest_river.kind}, {Math.round(osm.nearest_river.distance_m)} m away.</>}
                  </p>
                )}
                {osm?.error && <p className="block__note">Roads and rivers didn't load this time. Reload the page to try again.</p>}
                {expert && site?.terrain && <p className="block__note">Sources: {site.terrain.source}; {site.landcover.source}; {site.rain?.source}; {osm?.source}.</p>}
              </div>
            </section>

            <section role="tabpanel" id="panel-about" aria-labelledby="tab-about" hidden={tab !== "about"} className="tabs__panel">
              <Accuracy expert={expert} />
              <div className="block">
                <h2>{t("aboutData")}</h2>
                <ul className="ev">
                  <li>NISAR's calibrated images start on 17 June 2026; images from 21 October 2026 are fully validated.</li>
                  {cov?.data_gaps?.length ? <li>NISAR recorded no passes here {cov.data_gaps.map(([a, b]) => `from ${fmt(a)} to ${fmt(b)}`).join(" and ")} (shaded on every chart), found from this place's own timeline.</li> : null}
                  <li>Images made with forecast orbits are left out of change tests until their precise version is published.</li>
                  <li>The map shows NASA's daily NISAR mosaic, which only covers places imaged that day.</li>
                  <li>Ground motion is relative to the surrounding area and not corrected for atmospheric delay.</li>
                  <li>No account or login is needed. Your location is only used to choose the place and is not stored.</li>
                </ul>
              </div>
              <div className="block">
                <h3>{t("learnMore")}</h3>
                <ul className="links">
                  <li><a href="https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/sar" target="_blank" rel="noreferrer">What is SAR? (NASA Earthdata)</a></li>
                  <li><a href="https://nisar-docs.asf.alaska.edu/" target="_blank" rel="noreferrer">NISAR Data User Guide (ASF)</a></li>
                  <li><a href="https://science.nasa.gov/mission/nisar/arcgis-storymaps/" target="_blank" rel="noreferrer">NISAR StoryMaps</a></li>
                  <li><a href="https://appliedsciences.nasa.gov/get-involved/training" target="_blank" rel="noreferrer">NASA ARSET trainings</a></li>
                  <li><a href="https://uavsar.jpl.nasa.gov/education/postcards.html" target="_blank" rel="noreferrer">UAVSAR postcards</a></li>
                </ul>
              </div>
            </section>
          </div>
        </main>
      </div>

      {tour != null && (
        <div className="tour" role="dialog" aria-label="Guided tour">
          <p>{TOUR[tour].text}</p>
          <div className="tour__nav">
            <span className="block__note">{tour + 1} of {TOUR.length}</span>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button className="btn btn--small" onClick={() => setTour(null)}>Close</button>
              {tour > 0 && <button className="btn btn--small" onClick={() => setTour(tour - 1)}>Back</button>}
              {tour < TOUR.length - 1
                ? <button className="btn btn--small btn--primary" onClick={() => setTour(tour + 1)}>Next</button>
                : <button className="btn btn--small btn--primary" onClick={() => setTour(null)}>Done</button>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MapLegend({ inv, highlight, colorBy, grayscale, layers, source, selected }: { inv: any; highlight: boolean; colorBy: "what" | "when"; grayscale: boolean; layers: Layers; source: string; selected?: any }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="maplegend">
      <button className="maplegend__toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? "Hide legend" : "Legend"}</button>
      {open && (
        <div className="maplegend__body glass">
          {source === "rgb" && <><strong>EarthEcho 20 m radar</strong>
            <span><i style={{ background: "#e46fb0" }} />Red: HH, bright where walls and trunks bounce twice</span>
            <span><i style={{ background: "#5bd18b" }} />Green: HV, bright inside vegetation</span>
            <span><i style={{ background: "#4b8bea" }} />Blue: HH over HV, smooth or bare surfaces</span></>}
          {(source === "hh" || source === "hv") && <><strong>{source.toUpperCase()} backscatter, 20 m</strong>
            <span>One brightness scale for every date, so differences are real.</span></>}
          {selected?.downhill?.path && <span><i style={{ background: "none", borderTop: "2px dashed #fff", height: 0 }} />Steepest downhill path from the selected change</span>}
          {inv && highlight ? (
            colorBy === "what" ? <>
              <strong>Changed ground</strong>
              <span><i style={{ background: "#e46fb0" }} />Got brighter: structures, debris, water under trees</span>
              <span><i style={{ background: "#4b7be5" }} />Got darker: flooding, cleared or wet ground</span>
              <span className="muted">Unchanged ground is dimmed</span>
            </> : <>
              <strong>When it first changed</strong>
              <span className="maplegend__ramp" aria-hidden="true">{(inv.overlays.date_palette as string[]).map(c => <i key={c} style={{ background: c }} />)}</span>
              <span className="muted">{fmt(inv.dates[0])} to {fmt(inv.dates[inv.dates.length - 1])}</span>
            </>
          ) : grayscale ? <>
            <strong>Radar brightness</strong>
            <span>Bright: buildings, rock, rough ground. Dark: calm water, smooth ground.</span>
          </> : <>
            <strong>Radar colours</strong>
            <span><i style={{ background: "#5bd18b" }} />Vegetation</span>
            <span><i style={{ background: "#f2b84b" }} />Buildings or bare ground</span>
            <span><i style={{ background: "#4b8bea" }} />Calm water, dry sand, frozen ground</span>
          </>}
          {inv?.events?.length > 0 && layers.events && <span><i style={{ background: "#fde68a", borderRadius: "50%" }} />Change event: click it</span>}
          {layers.motion2d && inv?.motion2d?.magnitude_png && <span><i style={{ background: "linear-gradient(90deg,#fde725,#fd6f61)" }} />Motion size from both orbits, up to {inv.motion2d.magnitude_limit_mm} mm per 12 days</span>}
          {layers.swaths && <><span><i style={{ border: "2px dashed #eaf0f7", background: "none" }} />Northbound NISAR swath</span><span><i style={{ border: "2px solid #8e9bb0", background: "none" }} />Southbound NISAR swath</span></>}
          {layers.motion && inv?.motion && <span><i style={{ background: "linear-gradient(90deg,#4b7be5,#e46fb0)" }} />Ground motion, ±{inv.motion.velocity_limit_mm} mm per 12 days</span>}
        </div>
      )}
    </div>
  );
}

function Accuracy({ expert }: { expert: boolean }) {
  const t = useT();
  const [v, setV] = useState<any>(null);
  useEffect(() => { api.validation().then(setV).catch(() => {}); }, []);
  if (!v) return null;
  const at30 = v.cases.filter((c: any) => c.looks === 30);
  const fa = at30.find((c: any) => c.step_db === 0)?.false_alarm_rate;
  return (
    <div className="block">
      <h2>{t("accuracy")}</h2>
      <p>
        Change detection uses a published statistical test for radar speckle (Conradsen and others, 2016) at significance level {v.alpha}.
        On simulated NISAR-like images where the true answer is known, unchanged ground was wrongly flagged {(fa * 100).toFixed(1)}% of the time.
      </p>
      <table className="acc">
        <thead><tr><th>Brightness change</th><th>Found</th><th>Correct date</th></tr></thead>
        <tbody>
          {at30.filter((c: any) => c.step_db > 0).map((c: any) => (
            <tr key={c.step_db}><td>{c.step_db} dB</td><td className="num">{(c.detection_rate * 100).toFixed(0)}%</td><td className="num">{(c.correct_date_rate * 100).toFixed(0)}%</td></tr>
          ))}
        </tbody>
      </table>
      <p className="block__note">
        {v.note} Pixels are averaged to about 30 looks before testing.{expert && ` At 9 looks the 3 dB detection rate drops to ${(v.cases.find((c: any) => c.looks === 9 && c.step_db === 3).detection_rate * 100).toFixed(0)}%.`}
        {" "}Before testing, each land type's regional seasonal swing is removed and radar layover and shadow are masked.
      </p>
      {v.correlation_check?.length > 0 && <>
        <h3 style={{ fontSize: "var(--t-m)" }}>When HH and HV move together</h3>
        <p className="block__note">
          In forests and cities the two polarisations are correlated. Treating them as independent would inflate false
          alarms, so EarthEcho measures the correlation in every scene and corrects the test for it.
        </p>
        <table className="acc">
          <thead><tr><th>HH–HV coherence</th><th>False alarms, uncorrected</th><th>With EarthEcho's correction</th></tr></thead>
          <tbody>
            {v.correlation_check.map((c: any) => (
              <tr key={c.hh_hv_coherence}><td className="num">{c.hh_hv_coherence}</td>
                <td className="num">{(c.false_alarms_naive * 100).toFixed(1)}%</td>
                <td className="num">{(c.false_alarms_corrected * 100).toFixed(1)}%</td></tr>
            ))}
          </tbody>
        </table>
      </>}
    </div>
  );
}
