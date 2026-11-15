/**
 * Read a typed coordinate pair. Accepts the forms people paste from maps and papers:
 *   "18.52, 73.86"   "18.52 73.86"   "-9.9,-62.9"   "18.52°N 73.86°E"   "9.9 S, 62.9 W"
 *   "lat 18.52 lon 73.86"   "18°31'12\"N 73°51'36\"E"
 * Returns null when the text is not a coordinate pair (then it is a place name).
 */
export function parseCoords(text: string): { lat: number; lon: number } | null {
  const s = text.trim().toUpperCase().replace(/LAT(ITUDE)?|LO?NG?(ITUDE)?|[=:]/g, " ");
  // one number in degrees, minutes, seconds or decimal degrees, with an optional hemisphere letter
  const part = String.raw`([-+]?\d+(?:\.\d+)?)\s*(?:°|D|DEG)?\s*(?:(\d+(?:\.\d+)?)\s*['′M]\s*)?(?:(\d+(?:\.\d+)?)\s*(?:"|″|''|S(?=\s|$|,))\s*)?([NSEW])?`;
  const m = s.match(new RegExp(String.raw`^\s*${part}\s*[,;/\s]\s*${part}\s*$`));
  if (!m) return null;
  const val = (d: string, mi?: string, se?: string, h?: string) => {
    let v = Math.abs(parseFloat(d)) + (mi ? parseFloat(mi) / 60 : 0) + (se ? parseFloat(se) / 3600 : 0);
    if (d.startsWith("-") || h === "S" || h === "W") v = -v;
    return v;
  };
  let a = { v: val(m[1], m[2], m[3], m[4]), h: m[4] };
  let b = { v: val(m[5], m[6], m[7], m[8]), h: m[8] };
  if (a.h === "E" || a.h === "W" || b.h === "N" || b.h === "S") [a, b] = [b, a]; // "73.86E 18.52N"
  const lat = a.v, lon = b.v;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

export const fmtCoords = (lat: number, lon: number) =>
  `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"}`;
