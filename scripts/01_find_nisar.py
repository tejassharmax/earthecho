"""
Step 1 - find the NISAR products for ANY study area and write a clean manifest.

No Earthdata login needed for searching. Output: data/manifest.json
  - one entry per acquisition date per track (duplicates removed)
  - prefers Production (PR) over Urgent Response (UR), then the newest CRID,
    then the highest product counter (_002 beats _001)
  - reports how much of the box each track really covers (a point can sit on a
    swath edge: for Joshimath, track 055 covers only ~47% of the box)

Usage:
    python -m pip install asf_search
    python scripts/01_find_nisar.py                                  # Joshimath defaults
    python scripts/01_find_nisar.py --bbox -63.0 -10.0 -62.8 -9.8    # anywhere: W S E N
"""
import argparse
import collections
import json
import os
import re
from datetime import date, timedelta

import asf_search as asf
from shapely.geometry import box, shape


def product_rank(name):
    """Higher is better: PR > UR, newer CRID, higher counter."""
    proc = 1 if "_PR_" in name else 0
    crid = re.search(r"_P(\d{5})_", name)
    ctr = re.search(r"_(\d{3})$", name)
    return (proc, int(crid.group(1)) if crid else 0, int(ctr.group(1)) if ctr else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"),
                    default=[79.45, 30.48, 79.70, 30.65])
    ap.add_argument("--start", default="2026-06-17")  # first calibrated (PROVISIONAL) data
    ap.add_argument("--out", default="data/manifest.json")
    args = ap.parse_args()

    aoi = box(*args.bbox)
    manifest = {"bbox": args.bbox, "tracks": {}}

    for level in ("GCOV", "GUNW"):
        results = asf.search(dataset="NISAR", processingLevel=level,
                             intersectsWith=aoi.wkt, start=args.start, maxResults=2000)
        best = {}
        for g in results:
            p = g.properties
            name = p["sceneName"]
            key = (p["pathNumber"], p["flightDirection"], p["frameNumber"], p["startTime"][:10])
            if key not in best or product_rank(name) > product_rank(best[key]["name"]):
                best[key] = {"name": name, "url": p["url"], "date": p["startTime"][:10],
                             "crid": p.get("crid"),
                             "mode": name.split("_")[9] if level == "GCOV" else None,
                             "coverage": round(shape(g.geometry).intersection(aoi).area / aoi.area, 2)}
        by_track = collections.defaultdict(list)
        for (path, direction, frame, _), item in best.items():
            by_track[f"{path:03d}_{direction[0]}_{frame:03d}"].append(item)
        for track, items in by_track.items():
            items.sort(key=lambda i: i["date"])
            manifest["tracks"].setdefault(track, {})[level] = items

    for track, levels in manifest["tracks"].items():
        gcov = levels.get("GCOV", [])
        levels["min_coverage"] = min((i["coverage"] for i in gcov), default=0)
        if gcov:
            # 12-day repeat projected forward past today (the newest acquisitions are often
            # not published yet). Estimate only: confirm against the NISAR observation plan.
            nxt = date.fromisoformat(gcov[-1]["date"]) + timedelta(days=12)
            while nxt <= date.today():
                nxt += timedelta(days=12)
            levels["next_expected_pass"] = str(nxt)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Wrote {args.out}  (bbox {args.bbox})")
    ranked = sorted(manifest["tracks"].items(),
                    key=lambda kv: (kv[1]["min_coverage"] >= 0.95, len(kv[1].get("GCOV", []))),
                    reverse=True)
    for track, levels in ranked:
        gcov, gunw = levels.get("GCOV", []), levels.get("GUNW", [])
        modes = dict(collections.Counter(i["mode"] for i in gcov))
        flag = "" if levels["min_coverage"] >= 0.95 else "  <-- PARTIAL COVERAGE, avoid"
        print(f"track {track}: covers {levels['min_coverage']:.0%} of box | {len(gcov)} GCOV dates {modes} | "
              f"{len(gunw)} GUNW pairs | next pass ~{levels.get('next_expected_pass')}{flag}")
        if gcov:
            print("   GCOV:", ", ".join(i["date"] for i in gcov))


if __name__ == "__main__":
    main()
