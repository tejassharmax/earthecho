"""
Physical-driver attribution (Feature 2): ranks hypotheses for one change event.

Not a classifier. Each hypothesis lists the evidence that supports it, contradicts
it, could not be tested (missing channel/data) and what would confirm it. The score
(supporting - contradicting) / tested is an evidence balance in [-1, 1], NOT a
probability. Rules follow L-band scattering physics; they were reviewed for a
Himalayan site and should be treated as hypotheses elsewhere.
"""
from __future__ import annotations


def _chk(cond, text):
    return (None if cond is None else bool(cond), text)


def _month(d: str | None) -> int | None:
    return int(d[5:7]) if d else None


def hypotheses(fp: dict) -> list[dict]:
    dhh, dhv = fp.get("dHH_db"), fp.get("dHV_db")
    slope, elev = fp.get("slope_deg"), fp.get("elev_m")
    lc = fp.get("landcover") or ""
    rain = fp.get("rain_30d_mm")
    river = (fp.get("nearest_river") or {}).get("distance_m")
    road = (fp.get("nearest_road") or {}).get("distance_m")
    transient = fp.get("transient")
    coh = fp.get("coherence_change")
    rate = fp.get("los_rate_mm_per_12d")
    consistent = fp.get("los_consistent_pairs")
    rel = fp.get("relative_to_surroundings_db")
    lat = fp.get("centroid", [0, 0])[1]
    m = _month(fp.get("date_first"))
    winter = None if m is None else (m in (10, 11, 12, 1, 2, 3, 4, 5) if lat >= 0 else m in (4, 5, 6, 7, 8, 9, 10))
    nohv = dhv is None
    latest_only = fp.get("latest_only")
    extreme = dhh is not None and abs(dhh) >= 8 and (dhv is None or abs(dhv) >= 8)
    unwrap_bad = (fp.get("los_unwrap_suspect_pairs") or 0) > 0
    veg = any(k in lc for k in ("Tree", "Shrub", "Mangrove", "wetland"))

    H = [
        ("Open water / flooding", "Smooth water reflects radar away, so both channels drop sharply.",
         [_chk(dhh is not None and dhh <= -3, "HH dropped by 3 dB or more"),
          _chk(None if nohv else dhv <= -3, "HV dropped by 3 dB or more"),
          _chk(None if slope is None else slope < 5, "terrain is nearly flat (< 5°)"),
          _chk(None if river is None else river < 1000, "a river or stream is within 1 km"),
          _chk(None if rain is None else rain > 100, "more than 100 mm of rain in the 30 days before")],
         [_chk(None if fp.get("shadow_fraction") is None else fp["shadow_fraction"] > 0.2,
               "the slope faces away from the radar (shadow risk): a drop here can be shadow, not water"),
          _chk(dhh is not None and dhh > 0, "HH increased"),
          _chk(None if river is None and not lc else (river is None or river > 1000) and not any(k in lc for k in ("wetland", "water", "Mangrove")),
               "no river, wetland or water body within 1 km to supply the water")],
         ["An optical image (e.g. NASA HLS) from the same week shows water",
          "Backscatter returns to its old level on a later pass as water recedes"]),
        ("Flooding under vegetation", "Water under a canopy creates a strong double bounce, so HH rises.",
         [_chk(dhh is not None and dhh >= 3, "HH rose by 3 dB or more"),
          _chk(veg, "the area is forest, shrub, wetland or mangrove"),
          _chk(None if slope is None else slope < 5, "terrain is nearly flat"),
          _chk(None if river is None else river < 1000, "a river is within 1 km"),
          _chk(transient, "the change reversed later (water receded)")],
         [_chk(None if slope is None else slope > 10, "slope too steep to hold standing water")],
         ["Field report or optical image of standing water under trees"]),
        ("Vegetation loss (clearing, burn or scar)", "Losing canopy removes volume scattering, so HV drops.",
         [_chk(None if nohv else dhv <= -2, "HV dropped by 2 dB or more"),
          _chk(veg or "Crop" in lc or "Grass" in lc, "the area was vegetated"),
          _chk(transient is not None and not transient, "the change persisted"),
          _chk(None if coh is None else coh > 0.1, "interferometric coherence increased (bare ground is more stable)")],
         [_chk(None if nohv else dhv > 0, "HV increased"),
          _chk("Built" in lc or "water" in lc, "the area is built-up or water")],
         ["Optical image shows bare soil or burn scar", "A field photo of the cleared area"]),
        ("Crop harvest or ploughing", "Harvest removes the crop canopy, a seasonal drop in HV.",
         [_chk("Crop" in lc, "the area is mapped as cropland"),
          _chk(None if nohv else dhv <= -1.5, "HV dropped"),
          _chk(None if m is None else m in (3, 4, 5, 9, 10, 11), "the date falls in a common harvest season")],
         [_chk("Crop" not in lc, "the area is not cropland"),
          _chk(None if slope is None else slope > 20, "slope too steep for fields")],
         ["Local crop calendar", "Optical image showing harvested fields"]),
        ("Landslide or debris flow", "Fresh debris replaces vegetation and changes surface roughness.",
         [_chk(None if slope is None else slope >= 20, "slope is 20° or steeper"),
          _chk(None if nohv else dhv <= -1.5, "HV dropped (vegetation removed)"),
          _chk(dhh is not None and abs(dhh) >= 1.5, "HH changed by 1.5 dB or more (roughness changed)"),
          _chk(None if coh is None else coh < -0.1, "coherence dropped (surface disturbed)"),
          _chk(None if rain is None else rain >= 150, "150 mm or more of rain in the 30 days before")],
         [_chk(None if slope is None else slope < 10, "terrain is too flat for a landslide")],
         ["Deposit downslope or in the river channel", "Road blockage or damage reports", "Field photos of the scarp"]),
        ("Slow ground movement", "Radar phase measures centimetre-scale motion along the line of sight.",
         [_chk(None if rate is None else abs(rate) >= 5, "moving 5 mm or more per 12 days along the line of sight"),
          _chk(None if consistent is None else consistent >= 3, "the motion has the same sign over 3+ pairs"),
          _chk(None if slope is None else slope >= 10, "on a slope"),
          _chk(None if fp.get("motion_mostly") is None else True,
               f"both orbit directions see it: mostly {fp.get('motion_mostly')} motion"
               + (" (settling or heave)" if fp.get("motion_mostly") == "vertical" else
                  " (slope creep or sliding)" if fp.get("motion_mostly") == "east-west" else ""))],
         [_chk(None if fp.get("motion_tracks_elevation") is None else fp["motion_tracks_elevation"],
               "motion follows elevation: likely atmospheric delay, not ground motion"),
          _chk(unwrap_bad if rate is not None else None,
               "some pairs jump by about one phase cycle (119 mm): unwrapping errors, removed from the rate")],
         ["The same motion seen from the other orbit direction", "GNSS station or crack monitoring on the ground"]),
        ("New structure or debris pile", "Walls and piles create a strong double bounce: HH and HV both rise.",
         [_chk(dhh is not None and dhh >= 2, "HH rose by 2 dB or more"),
          _chk(None if nohv else dhv >= 1, "HV rose"),
          _chk(transient is not None and not transient, "the change persisted"),
          _chk(None if road is None else road < 300, "a road is within 300 m")],
         [_chk(transient, "the change reversed later"),
          _chk(None if slope is None else slope > 25, "slope too steep for construction")],
         ["Optical image shows new buildings or material", "Local construction records"]),
        ("Soil moisture after rain", "Wet soil reflects more radar energy, so HH rises across the area.",
         [_chk(dhh is not None and 0.5 <= dhh <= 4, "HH rose moderately"),
          _chk(None if rain is None else rain > 50, "rain in the 30 days before"),
          _chk(None if rel is None else abs(rel) < 1.5, "the surroundings changed in the same way"),
          _chk(transient, "the change reversed later (soil dried)")],
         [_chk(None if rel is None else abs(rel) >= 3, "the change is local, unlike the surroundings"),
          _chk(None if (dhh is None or dhv is None) else dhv - dhh > 1.5,
               "HV rose more than HH: that is vegetation volume growing, not soil getting wetter"),
          _chk(transient is not None and not transient, "the change persisted")],
         ["Rain gauge record", "Backscatter falls back once soils dry"]),
        ("Wet snow", "Liquid water in snow absorbs radar: both channels drop.",
         [_chk(dhh is not None and dhh <= -2, "HH dropped"),
          _chk(None if nohv else dhv <= -2, "HV dropped"),
          _chk(None if elev is None else elev >= 3000 or "Snow" in lc, "high elevation or mapped snow/ice"),
          _chk(winter, "date is in the cold season")],
         [_chk(None if elev is None else elev < 1500, "too low for seasonal snow")],
         ["Optical snow cover image (e.g. MODIS snow)", "Weather station temperature"]),
        ("Ice or snow melt and refreeze", "Meltwater makes ice dark to radar; refreezing makes it bright again.",
         [_chk("Snow" in lc, "mapped as snow and ice"),
          _chk(dhh is not None and abs(dhh) >= 2, "HH changed by 2 dB or more"),
          _chk(None if m is None else (m in (5, 6, 7, 8, 9) if lat >= 0 else m in (11, 12, 1, 2, 3)), "date falls in the melt season"),
          _chk(transient, "the change reversed (refroze or melted back)")],
         [_chk("Snow" not in lc and (elev is None or elev < 2500), "no ice or snow mapped here")],
         ["MODIS or Landsat snow/ice image of the same week", "Air temperature records above freezing"]),
        ("Seasonal wetland inundation", "Wetlands fill and drain with the seasons; open water darkens, flooded vegetation brightens.",
         [_chk(any(k in lc for k in ("wetland", "Mangrove", "water")), "mapped as wetland, mangrove or water"),
          _chk(dhh is not None and abs(dhh) >= 2, "HH changed by 2 dB or more"),
          _chk(transient, "the change reversed with the season"),
          _chk(None if rain is None else rain > 80, "rain in the 30 days before")],
         [_chk(None if slope is None else slope > 8, "too steep to hold water"),
          _chk(not any(k in lc for k in ("wetland", "Mangrove", "water")), "not mapped as wetland or water")],
         ["Water level gauge or optical image", "The same rhythm in other years"]),
        ("Fresh lava or volcanic surface change", "New lava changes surface roughness: rough a'a flows brighten radar, smooth pahoehoe darkens it.",
         [_chk("Bare" in lc, "bare or sparsely vegetated ground"),
          _chk(dhh is not None and abs(dhh) >= 3, "HH changed by 3 dB or more"),
          _chk(transient is not None and not transient, "the change persisted (lava does not wash away)"),
          _chk(None if fp.get("los_rate_mm_per_12d") is None else abs(fp["los_rate_mm_per_12d"]) >= 5, "the ground is also moving 5 mm or more per 12 days")],
         [_chk(veg or "Crop" in lc or "Built" in lc, "the area is vegetated, farmed or built-up")],
         ["Volcano observatory eruption and lava-flow maps (e.g. USGS HVO)", "Thermal satellite imagery of the same dates"]),
        ("Crop growth (canopy filling in)", "As crops grow, leaves and stalks scatter radar inside the canopy, so HV rises faster than HH.",
         [_chk("Crop" in lc or "Grass" in lc, "mapped as cropland or grassland"),
          _chk(None if nohv else dhv >= 2, "HV rose by 2 dB or more"),
          _chk(None if (dhh is None or nohv) else dhv > dhh, "HV rose more than HH"),
          _chk(None if m is None else (m in (5, 6, 7, 8) if lat >= 0 else m in (11, 12, 1, 2)), "date falls in the growing season")],
         [_chk("Crop" not in lc and "Grass" not in lc, "not cropland or grassland"),
          _chk(None if nohv else dhv < 0, "HV fell")],
         ["Optical image showing the crop canopy (e.g. NASA HLS)", "Local planting and crop calendar"]),
        ("Processing artifact or one-off error", "A single image can be misaligned or processed differently; real change should repeat on later passes.",
         [_chk(latest_only, "seen only on the latest image, with no later pass to confirm it"),
          _chk(extreme, "an extreme drop or rise of 8 dB or more in every channel"),
          _chk(None if slope is None else slope >= 35, "very steep slope, where small misalignments look like change"),
          _chk(None if fp.get("layover_fraction") is None else fp["layover_fraction"] > 0.2,
               "the slope faces the radar steeply (layover), where geometry distorts brightness")],
         [_chk(transient is not None and not transient, "the change persisted over at least two later images")],
         ["The same change on the next NISAR pass (see the next expected pass date)",
          "The same change in the reprocessed, precise-orbit version of this image"]),
    ]
    out = []
    for name, why, sup, con, confirm in H:
        tested_sup = [t for v, t in sup if v is True]
        failed_sup = [t for v, t in sup if v is False]
        hit_con = [t for v, t in con if v is True]
        untested = [t for v, t in sup + con if v is None]
        n = sum(1 for v, _ in sup if v is not None) + sum(1 for v, _ in con if v is True)
        score = (len(tested_sup) - len(hit_con)) / n if n else 0.0
        out.append({"name": name, "why": why, "score": round(score, 2),
                    "supporting": tested_sup, "not_seen": failed_sup, "contradicting": hit_con,
                    "cannot_test": untested, "would_confirm": confirm})
    # ties go to the explanation backed by more observed evidence, not to list order
    out.sort(key=lambda h: (h["score"], len(h["supporting"])), reverse=True)
    return out


def priority(fp: dict) -> dict:
    """Monitoring priority (F10). Transparent weighted sum; missing parts are re-weighted."""
    comps = []

    def add(key, label, weight, value, detail):
        comps.append({"key": key, "label": label, "weight": weight,
                      "value": None if value is None else round(max(0.0, min(1.0, value)), 2), "detail": detail})

    rate = fp.get("los_rate_mm_per_12d")
    add("rate", "Recent movement", 0.25, None if rate is None else abs(rate) / 10,
        "not measured" if rate is None else f"{rate:+.1f} mm per 12 days")
    acc = fp.get("los_acceleration")
    add("accel", "Speeding up", 0.15, None if acc is None else acc / 5,
        "not measured" if acc is None else f"{acc:+.1f} mm per 12 days faster than earlier")
    s = fp.get("slope_deg")
    add("slope", "Slope steepness", 0.15, None if s is None else s / 40, "unknown" if s is None else f"{s:.0f}°")
    r = fp.get("rain_30d_mm")
    add("rain", "Recent rain", 0.10, None if r is None else r / 300, "unknown" if r is None else f"{r:.0f} mm in 30 days")
    road = (fp.get("nearest_road") or {}).get("distance_m")
    add("exposure", "Roads nearby", 0.15, None if road is None else 1 - road / 1000,
        "no mapped road" if road is None else f"road {road:.0f} m away")
    add("recurrence", "Changed more than once", 0.10, 1.0 if (fp.get("n_changes") or 1) > 1 else 0.2,
        f"{fp.get('n_changes') or 1} change date(s)")
    q = fp.get("quality", {})
    add("quality", "Data quality", 0.10, q.get("score"), q.get("summary", ""))
    used = [c for c in comps if c["value"] is not None]
    wsum = sum(c["weight"] for c in used) or 1
    val = sum(c["weight"] * c["value"] for c in used) / wsum
    level = "Elevated" if val >= 0.55 else "Moderate" if val >= 0.35 else "Routine"
    return {"score": round(val, 2), "level": level, "components": comps,
            "note": "Monitoring priority, not a hazard prediction. Weights are shown so anyone can question them."}
