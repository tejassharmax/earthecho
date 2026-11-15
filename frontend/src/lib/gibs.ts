// NASA GIBS layers (public, CORS-enabled). All base imagery in EarthEcho is NASA's.
import maplibregl, { type StyleSpecification } from "maplibre-gl";

// GIBS sometimes answers a tile with a transient 500; retry quietly before giving up.
let registered = false;
export function registerRetryProtocol() {
  if (registered) return;
  registered = true;
  // GIBS answers bursts of simultaneous requests with 500s: keep the burst small.
  maplibregl.setMaxParallelImageRequests(6);
  maplibregl.addProtocol("retry", async (params, abort) => {
    const url = params.url.replace(/^retry:\/\//, "");
    return { data: await fetchTile(url, abort.signal) };
  });
}

const DIRECT = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const API: string = (import.meta as any).env?.VITE_API_BASE ?? "";

/**
 * Tiles come through the EarthEcho server, which retries GIBS, limits load, falls back to a
 * coarser tile and caches. Only if the server itself is unreachable do we go to GIBS directly.
 */
async function fetchTile(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  try {
    const r = await fetch(url, { signal });
    if (r.ok) return await r.arrayBuffer();
    if (r.status === 404) throw new Error("no data for this tile");
  } catch (e) {
    if (signal.aborted || String(e).includes("no data")) throw e;
  }
  const direct = url.replace(/^.*\/api\/gibs/, DIRECT);
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(direct, { signal });
      if (r.ok) return await r.arrayBuffer();
      if (r.status === 404) break;
    } catch (e) {
      if (signal.aborted) throw e;
    }
    await new Promise(res => setTimeout(res, 900 * 2 ** i * (0.75 + Math.random() * 0.5)));
  }
  throw new Error("tile unavailable");
}

const WMTS = `retry://${API}/api/gibs`;

export const NISAR_LAYER = "NISAR_L2_Geocoded_Polarimetric_Covariance";

export function nisarTiles(date: string): string {
  return `${WMTS}/${NISAR_LAYER}/default/${date}/GoogleMapsCompatible_Level13/{z}/{y}/{x}.png`;
}

export function baseStyle(nisarDate?: string): StyleSpecification {
  const sources: StyleSpecification["sources"] = {
    relief: {
      type: "raster",
      tiles: [`${WMTS}/BlueMarble_ShadedRelief_Bathymetry/default/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`],
      tileSize: 256, maxzoom: 8,
      attribution: "Imagery: NASA GIBS / Blue Marble",
    },
    labels: {
      type: "raster",
      tiles: [`${WMTS}/Reference_Labels_15m/default/default/GoogleMapsCompatible_Level13/{z}/{y}/{x}.png`],
      tileSize: 256, maxzoom: 13,
      attribution: "Labels © OpenStreetMap contributors via NASA GIBS",
    },
  };
  const layers: StyleSpecification["layers"] = [
    { id: "bg", type: "background", paint: { "background-color": "#0d1526" } },
    { id: "relief", type: "raster", source: "relief", paint: { "raster-saturation": -0.55, "raster-brightness-max": 0.55 } },
  ];
  // The NISAR layer always exists (hidden without a date) so date changes never reload the style.
  sources.nisar = {
    type: "raster", tiles: [nisarTiles(nisarDate ?? "2026-11-01")], tileSize: 256, maxzoom: 13,
    attribution: "NISAR GCOV (NASA–ISRO) via NASA GIBS / ASF DAAC",
  };
  layers.push({ id: "nisar", type: "raster", source: "nisar", paint: { "raster-opacity": 1 },
    layout: { visibility: nisarDate ? "visible" : "none" } });
  layers.push({ id: "labels", type: "raster", source: "labels" });
  return { version: 8, sources, layers };
}


/** Does NASA's daily NISAR mosaic have data at this point on this date? (Checks one z10 tile.) */
export async function gibsHas(date: string, lat: number, lon: number): Promise<boolean> {
  const z = 10, n = 2 ** z, r = (lat * Math.PI) / 180;
  const x = Math.floor(((lon + 180) / 360) * n);
  const y = Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n);
  const url = nisarTiles(date).replace(/^retry:\/\//, "").replace("{z}", String(z)).replace("{y}", String(y)).replace("{x}", String(x));
  try {
    const res = await fetch(url);
    return res.ok && (await res.blob()).size > 1500;
  } catch { return false; }
}
