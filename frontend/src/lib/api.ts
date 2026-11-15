// Backend client. Coverage falls back to calling ASF directly from the browser
// (ASF's search API allows cross-origin requests), so the timeline works even
// when the EarthEcho server is unreachable.
const BASE: string = (import.meta as any).env?.VITE_API_BASE ?? "";

export type Acquisition = {
  date: string; time: string; mode: string | null; maturity: string; coverage: number;
  name: string; browse: string | null; pols: string[];
};
export type Track = {
  track: string; direction: string; acquisitions: Acquisition[]; gunw_pairs: number;
  min_coverage: number; n_dates: number; pols_always: string[]; next_expected_pass: string;
  footprint?: GeoJSON.Geometry;
};
export type Coverage = {
  lat: number; lon: number; tracks: Track[]; n_acquisitions: number; first: string | null; last: string | null;
  data_gaps: [string, string][]; next_expected_pass: string | null; revisit_days: number | null; offline?: boolean;
};

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* keep status */ }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  health: () => get<{ ok: boolean; deep_analysis: boolean; max_dates: number }>("/api/health"),
  geocode: (q: string) => get<{ name: string; lat: number; lon: number }[]>(`/api/geocode?q=${encodeURIComponent(q)}`),
  reverse: (lat: number, lon: number) => get<{ name: string | null; full?: string; country?: string }>(`/api/reverse?lat=${lat}&lon=${lon}`),
  site: (lat: number, lon: number) => get<any>(`/api/site?lat=${lat}&lon=${lon}`),
  osm: (lat: number, lon: number) => get<any>(`/api/osm?lat=${lat}&lon=${lon}`),
  validation: () => get<any>("/api/validation"),
  showcase: () => get<any[]>("/api/showcase"),
  layerUrl: (key: string, date: string, kind: string) => `${BASE}/api/layers/${key}/${date}_${kind}.png`,
  job: (id: string) => get<any>(`/api/jobs/${id}`),
  async investigate(lat: number, lon: number): Promise<{ job: string }> {
    const r = await fetch(BASE + "/api/investigate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lat, lon }),
    });
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail ?? msg; } catch { /* keep */ }
      throw new Error(msg);
    }
    return r.json();
  },
  async coverage(lat: number, lon: number): Promise<Coverage> {
    try {
      return await get<Coverage>(`/api/coverage?lat=${lat}&lon=${lon}`);
    } catch {
      return coverageDirect(lat, lon);
    }
  },
};

const POLS: Record<string, string[]> = { QP: ["HH", "HV", "VH", "VV"], DH: ["HH", "HV"], DV: ["VV", "VH"], SH: ["HH"], SV: ["VV"] };

async function coverageDirect(lat: number, lon: number): Promise<Coverage> {
  const url = `https://api.daac.asf.alaska.edu/services/search/param?dataset=NISAR&processingLevel=GCOV` +
    `&intersectsWith=${encodeURIComponent(`POINT(${lon} ${lat})`)}&start=2026-06-17&output=geojson&maxResults=500`;
  const data = await (await fetch(url)).json();
  const tracks: Record<string, Track> = {};
  for (const f of data.features ?? []) {
    const p = f.properties;
    const key = `${String(p.pathNumber).padStart(3, "0")}_${p.flightDirection[0]}_${String(p.frameNumber).padStart(3, "0")}`;
    const t = (tracks[key] ??= { track: key, direction: p.flightDirection.toLowerCase(), acquisitions: [], gunw_pairs: 0,
      min_coverage: 1, n_dates: 0, pols_always: [], next_expected_pass: "" });
    const date = p.startTime.slice(0, 10);
    if (t.acquisitions.some(a => a.date === date)) continue;
    const mode = p.sceneName.split("_")[9] ?? null;
    t.acquisitions.push({ date, time: p.startTime, mode, maturity: p.url.includes("PROVISIONAL") ? "PROVISIONAL" : "VALIDATED",
      coverage: 1, name: p.sceneName, browse: null, pols: POLS[(mode ?? "").slice(0, 2)] ?? ["HH"] });
  }
  const list = Object.values(tracks);
  const today = new Date();
  for (const t of list) {
    t.acquisitions.sort((a, b) => a.date.localeCompare(b.date));
    t.n_dates = t.acquisitions.length;
    let d = new Date(t.acquisitions[t.n_dates - 1].date);
    do { d = new Date(d.getTime() + 12 * 864e5); } while (d <= today);
    t.next_expected_pass = d.toISOString().slice(0, 10);
    t.pols_always = ["HH", "HV"].filter(p => t.acquisitions.every(a => a.pols.includes(p)));
  }
  list.sort((a, b) => b.n_dates - a.n_dates);
  const all = list.flatMap(t => t.acquisitions.map(a => a.date)).sort();
  return {
    lat, lon, tracks: list, n_acquisitions: new Set(all).size, first: all[0] ?? null, last: all[all.length - 1] ?? null,
    data_gaps: [["2026-07-27", "2026-08-10"]], next_expected_pass: list.map(t => t.next_expected_pass).sort()[0] ?? null,
    revisit_days: list.length ? Math.round((12 / list.length) * 10) / 10 : null, offline: true,
  };
}
