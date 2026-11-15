---
title: EarthEcho
emoji: 🛰️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: NASA-ISRO NISAR radar change explorer for any place on Earth
---

# EarthEcho

**Ask the NASA–ISRO NISAR radar what changed at any place on Earth, and why.**
NASA Space Apps Challenge 2026: *Dancing with the SARs*.

No accounts and no login pages. Open the site, allow your location (or search or click the map), and every feature runs on that place.

## What it does

| Feature | Where | Works without a server token? |
|---|---|---|
| Live NISAR radar swipe between any two dates (F1) | Map | Yes (NASA GIBS mosaics) |
| Every NISAR pass over the place, data maturity, data gap, next expected pass (F8, F10) | Timeline | Yes (ASF search, live) |
| Sonification of NISAR's visits; keyboard + screen-reader text (F3) | Timeline | Yes |
| Terrain, slope, land cover, rainfall, roads, rivers | The ground here | Yes |
| What continuous radar monitoring makes possible here (F4) | Panel | Yes |
| Statistically tested change map, dated per pixel | Analysis | Needs token |
| Change events with fingerprints (F5) and ranked physical explanations: evidence for, against, can't test, would confirm (F2) | Analysis | Needs token |
| Line-of-sight ground motion from interferograms (GUNW) | Analysis | Needs token |
| Possible connections: slope → river → road (F7) | Event card | Needs token |
| Transparent monitoring priority (F10) and printable field-check sheet (F9) | Event card | Needs token |
| Guided tour, Simple/Expert, English/हिन्दी | Everywhere | Yes |

"Needs token" means the **server** reads NISAR source files with one NASA Earthdata token. Users never sign in.

## How accurate is it?

Change detection is a gamma likelihood-ratio change-point test gated by the Conradsen et al. (2016) omnibus test. It is validated on simulated NISAR-like speckle where the truth is known (`python -m app.validate_detector`). At 30 looks and α = 0.01:

| | Result |
|---|---|
| False alarms (unchanged pixels) | 0.4 % |
| 2 dB change found | 98 % (correct date 90 %) |
| 3 dB change found | 100 % (correct date 98 %) |

The end-to-end test (`tests/test_pipeline.py`) plants a 19.6 ha landslide-like change and 8 mm/12-day motion at Joshimath, using real terrain and land cover. The pipeline recovers 19.8 ha, the right date, −3.9 dB HV (true −4) and −8.0 mm/12 days.

**Before testing** (`app/physics.py`): radar layover and shadow are masked from the DEM's slope and aspect and the orbit's look direction; each land-cover class's regional seasonal swing (monsoon to dry, crop growth, melt) is removed; and the HH–HV correlation is measured in each scene and corrected for with a Satterthwaite adjustment. Without that correction, correlated speckle at HH–HV coherence 0.9 raised false alarms to 2.8%; with it they stay at 0.5% (`app/data/validation.json`, `tests/test_physics.py`). NISAR's dual-pol GCOV products contain no HH–HV off-diagonal term (only quad-pol products do), so the full complex-Wishart test cannot be applied to them.

**Ground motion** uses a non-overlapping chain of interferograms and each pixel's own valid days. **Connections** (slope → river → road) follow the steepest downhill path over the DEM, not straight-line distance.

**Showcase results ship in the repo** (`backend/demo/`) for Joshimath, Haridwar, Corcoran (California), Rondônia (Amazon) and Kangerlussuaq (Greenland), so the dashboard and the **Story** page (`#/story`) open instantly.

There is **no trained ML model**. No labelled NISAR change dataset exists, so its accuracy could not be measured. The attribution rules follow L-band scattering physics and were reviewed for a Himalayan site; elsewhere, treat them as hypotheses.

## Quick start (teammates)

The zip already contains the built website (`frontend/dist`) and `backend/.env` with the team's Earthdata token. Only the backend needs installing:

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows; use .venv/bin/python on macOS/Linux
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Then open http://localhost:8000. A new place takes about 3 to 4 minutes to analyse the first time; places analysed before open instantly. Keep `backend/.env` private: never post it or push it to GitHub.

## Run locally

```bash
# backend (Python 3.12+)
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows; use .venv/bin/python on macOS/Linux
cp .env.example .env        # optional: add EARTHDATA_TOKEN to enable deep analysis
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# frontend
cd frontend
npm install
npm run dev                  # http://localhost:5173 (proxies /api to :8000)
```

Tests: `cd backend && .venv/Scripts/python -m pytest -q -s`

## Deploy (one public link)

The `Dockerfile` builds the frontend and serves it from FastAPI on port 7860. That works for a Hugging Face Docker Space, Render or Railway. Set `EARTHDATA_TOKEN` as a secret in the host's settings, never in the repo.

## Data

- NISAR L2 GCOV and GUNW (NASA–ISRO), ASF DAAC: search API and Earthdata Cloud
- NISAR GCOV daily mosaics, Blue Marble, reference features and labels: NASA GIBS
- Daily precipitation: NASA POWER (PRECTOTCORR)
- Copernicus DEM GLO-30 and ESA WorldCover 10 m 2021 (ESA)
- Roads, rivers, buildings and place search: © OpenStreetMap contributors (Overpass, Nominatim)
