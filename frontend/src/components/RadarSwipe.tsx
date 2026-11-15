import maplibregl, { type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { baseStyle, nisarTiles, registerRetryProtocol } from "../lib/gibs";

registerRetryProtocol();

export type ImageOverlay = { id: string; url: string; corners: [number, number][]; opacity?: number };
export type GeoOverlay = {
  id: string; data: GeoJSON.FeatureCollection; kind: "line" | "fill" | "circle";
  color: string; width?: number; opacity?: number; dash?: number[];
};

type Props = {
  center: [number, number];
  zoom: number;
  dateA: string | null;
  dateB: string | null;
  labelA?: string;
  labelB?: string;
  interactive?: boolean;
  images?: ImageOverlay[];
  /** per-side layers (e.g. EarthEcho 20 m radar for the left date and the right date) */
  imagesA?: ImageOverlay[];
  imagesB?: ImageOverlay[];
  geo?: GeoOverlay[];
  onMapClick?: (lon: number, lat: number) => void;
  onFeatureClick?: (layerId: string, props: Record<string, any>) => void;
  flyKey?: string;
  ariaLabel: string;
  initialPos?: number;
  grayscale?: boolean;
  children?: React.ReactNode;
};

function applyOverlays(map: MLMap, images: ImageOverlay[], geo: GeoOverlay[], owned: Set<string>) {
  for (const id of owned) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }
  owned.clear();
  const before = map.getLayer("labels") ? "labels" : undefined;
  for (const im of images) {
    map.addSource(im.id, { type: "image", url: im.url, coordinates: im.corners as any });
    map.addLayer({ id: im.id, type: "raster", source: im.id, paint: { "raster-opacity": im.opacity ?? 0.85, "raster-resampling": "nearest" } }, before);
    owned.add(im.id);
  }
  for (const g of geo) {
    map.addSource(g.id, { type: "geojson", data: g.data });
    if (g.kind === "fill") {
      map.addLayer({ id: g.id, type: "fill", source: g.id, paint: { "fill-color": g.color, "fill-opacity": g.opacity ?? 0.25 } });
      map.addLayer({ id: g.id + "-outline", type: "line", source: g.id, paint: { "line-color": g.color, "line-width": g.width ?? 2 } });
      owned.add(g.id + "-outline");
    } else if (g.kind === "line") {
      map.addLayer({ id: g.id, type: "line", source: g.id, paint: {
        "line-color": g.color, "line-width": g.width ?? 1.5, "line-opacity": g.opacity ?? 0.9,
        ...(g.dash ? { "line-dasharray": g.dash } : {}) } });
    } else {
      map.addLayer({ id: g.id, type: "circle", source: g.id, paint: {
        "circle-color": g.color, "circle-radius": g.width ?? 6, "circle-stroke-color": "#0d1526", "circle-stroke-width": 2 } });
    }
    owned.add(g.id);
  }
}

export default function RadarSwipe(p: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const elA = useRef<HTMLDivElement>(null);
  const elB = useRef<HTMLDivElement>(null);
  const maps = useRef<{ a?: MLMap; b?: MLMap; owned: [Set<string>, Set<string>]; ready: [boolean, boolean] }>({ owned: [new Set(), new Set()], ready: [false, false] });
  const [pos, setPos] = useState(p.initialPos ?? 50);
  const [ready, setReady] = useState(0);
  const cb = useRef(p);
  cb.current = p;

  useEffect(() => {
    const opts = (el: HTMLDivElement, date: string | null, attrib: boolean) => new maplibregl.Map({
      container: el, style: baseStyle(date ?? undefined), center: p.center, zoom: p.zoom, maxZoom: 15,
      interactive: p.interactive !== false, attributionControl: attrib ? { compact: true } : false,
      dragRotate: false, pitchWithRotate: false,
    });
    const a = opts(elA.current!, p.dateA, false);
    const b = opts(elB.current!, p.dateB, true);
    maps.current.a = a; maps.current.b = b;
    let syncing = false;
    const sync = (from: MLMap, to: MLMap) => () => {
      if (syncing) return;
      syncing = true;
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() });
      syncing = false;
    };
    a.on("move", sync(a, b));
    b.on("move", sync(b, a));
    if (p.interactive !== false) b.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    [a, b].forEach((m, i) => {
      m.on("load", () => {
        maps.current.ready[i] = true;
        setReady(r => r + 1);
        // start with the attribution collapsed to its (i) button
        m.getContainer().querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
      });
      m.on("click", e => {
        const hits = m.queryRenderedFeatures(e.point).filter(f => f.layer.id.startsWith("ev-"));
        if (hits.length && cb.current.onFeatureClick) cb.current.onFeatureClick(hits[0].layer.id, hits[0].properties ?? {});
        else cb.current.onMapClick?.(e.lngLat.lng, e.lngLat.lat);
      });
      // tile failures after retries are already shown as empty tiles; keep the console quiet
      m.on("error", e => { if (!(e as any).tile && !(e as any).sourceId) console.warn(e.error); });
      m.on("mousemove", e => {
        const hit = m.queryRenderedFeatures(e.point).some(f => f.layer.id.startsWith("ev-"));
        m.getCanvas().style.cursor = hit ? "pointer" : "";
      });
    });
    return () => { a.remove(); b.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // dates
  useEffect(() => {
    const { a, b, ready: rd } = maps.current;
    const set = (m: MLMap | undefined, ok: boolean, d: string | null) => {
      if (!m || !ok) return;
      const src = m.getSource("nisar") as maplibregl.RasterTileSource | undefined;
      if (!src) return;
      if (d) src.setTiles([nisarTiles(d)]);
      m.setLayoutProperty("nisar", "visibility", d ? "visible" : "none");
    };
    set(a, rd[0], p.dateA);
    set(b, rd[1], p.dateB);
  }, [p.dateA, p.dateB, ready]);

  // colour-blind-safe grayscale radar
  useEffect(() => {
    const { a, b, ready: rd } = maps.current;
    [a, b].forEach((m, i) => { if (m && rd[i] && m.getLayer("nisar")) m.setPaintProperty("nisar", "raster-saturation", p.grayscale ? -1 : 0); });
  }, [p.grayscale, ready]);

  // overlays
  useEffect(() => {
    const { a, b, ready: rd, owned } = maps.current;
    if (a && rd[0]) applyOverlays(a, [...(p.imagesA ?? []), ...(p.images ?? [])], p.geo ?? [], owned[0]);
    if (b && rd[1]) applyOverlays(b, [...(p.imagesB ?? []), ...(p.images ?? [])], p.geo ?? [], owned[1]);
  }, [p.images, p.imagesA, p.imagesB, p.geo, ready]);

  // fly to new place
  useEffect(() => {
    maps.current.a?.flyTo({ center: p.center, zoom: p.zoom, essential: false, duration: 1600 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.flyKey]);

  const drag = (clientX: number) => {
    const r = wrap.current!.getBoundingClientRect();
    setPos(Math.min(98, Math.max(2, ((clientX - r.left) / r.width) * 100)));
  };

  return (
    <div className="swipe" ref={wrap} role="region" aria-label={p.ariaLabel}>
      <div className="swipe__map" ref={elA} />
      <div className="swipe__map" ref={elB} style={{ clipPath: `inset(0 0 0 ${pos}%)` }} />
      {p.labelA && <span className="swipe__label swipe__label--a">{p.labelA}</span>}
      {p.labelB && <span className="swipe__label swipe__label--b">{p.labelB}</span>}
      <button
        className="swipe__handle" style={{ left: `${pos}%` }}
        aria-label={`Compare dates: divider at ${Math.round(pos)} percent. Use left and right arrow keys to move it.`}
        onPointerDown={e => {
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          const move = (ev: PointerEvent) => drag(ev.clientX);
          const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
        onKeyDown={e => {
          if (e.key === "ArrowLeft") setPos(v => Math.max(2, v - 5));
          if (e.key === "ArrowRight") setPos(v => Math.min(98, v + 5));
        }}
      >
        <span className="swipe__knob" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 6l-6 6 6 6" /><path d="M15 6l6 6-6 6" />
          </svg>
        </span>
      </button>
      {p.children}
    </div>
  );
}
