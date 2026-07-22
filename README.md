# DotaReplayCoach

Send a **match ID** → get one **interactive full-page report**: every death on the
real minimap, with who was where, hp/mana/level, items, wards, tower status, and
data-derived insight bullets.

Data comes from **STRATZ** (position/health/level/inventory tracks, death details)
+ **OpenDota** (wards, building-kill timeline, hero/item constants).
No replay (`.dem`) parsing needed — see "Why no .dem" below.

```
match_id ──► stratz_client ─┐
             opendota  ─────┴► MatchData ──► detect_deaths ──► render_report
                                                               (reports/match_<id>.html)
```

## Setup
```bash
cd DotaReplayCoach
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env        # then paste your STRATZ token
```
Get a free STRATZ token at https://stratz.com/api (Bearer token, GraphQL).

**CLI** (one report to a file):
```bash
python main.py 8905742060 --account 1855477712 --open
```

**Web app** (input page → report, the deploy target):
```bash
python webapp.py                 # http://127.0.0.1:8000
```
Open the page, enter a match id + Steam id (32- or 64-bit), get the report.
Generation runs in a background thread and the page polls until ready; finished
reports are cached under `reports/` and served instantly next time.

## Deploying the web app
The whole thing is one Flask app (`webapp:app`) that calls `pipeline.generate`.
The STRATZ token stays server-side — the browser never sees it.

- **Local / same network:** `waitress-serve --listen=0.0.0.0:8000 webapp:app`
  (Windows-friendly production server), then hit `http://<your-ip>:8000`.
- **A host (Render / Railway / Fly / any VPS):** the included `Procfile` runs
  `gunicorn webapp:app`. Set the `STRATZ_TOKEN` env var in the host's dashboard
  (do **not** commit `.env`). `PORT` is read from the environment automatically.
- **Assets & caches:** everything the report needs is embedded at render time,
  so no static-file serving is required. The `*.json` constant caches and
  `assets/` ship with the repo (or re-download from OpenDota on first run).

**Before making it public**, note: all traffic shares your one STRATZ token, so
mind its rate limits; add a reverse-proxy/auth or a per-IP limit if it's exposed.
This is built for personal / small-group use.

## Status
| Module | State | Purpose |
|---|---|---|
| `webapp.py` | ✅ | Flask app: input page → background job → served report |
| `pipeline.py` | ✅ | shared fetch→analyze→render (used by CLI **and** web) |
| `main.py` | ✅ | CLI wrapper over `pipeline.generate` |
| `stratz_client.py` | ✅ | positions, health, levels, inventory, death details |
| `opendota.py` | ✅ | wards, building-kill timeline, economy, parse requests |
| `detect_deaths.py` | ✅ | mistake detector + ranking |
| `render_report.py` | ✅ | single interactive HTML report (map toggle, playback) |
| `heroes.py` / `items.py` / `abilities.py` / `status.py` | ✅ | id → name/icon + inferred statuses |
| `config.py` | ✅ | grid 54..202 calibration + thresholds |
| `coach.py` | 🔲 stub | future AI narrative layer |

Map + building sprites/coords in `assets/` come from
[MangoByte](https://github.com/mdiller/MangoByte) (calibrated to the STRATZ grid).

## Why no `.dem`
STRATZ gives god-view hero positions (~1 s sampling) for parsed matches, permanently.
Replays expire (~2 weeks) and only add creeps + true fog-of-war. Death forensics uses a
**proximity / last-known-position proxy** for "no enemies visible", which is enough.
Defer `.dem` parsing to an optional deep-dive mode.

## Roadmap
1. **M1 — verify the pipe**: run against one of your matches, confirm STRATZ field names,
   calibrate `config.game_to_pixel` with two landmarks. Print raw death features.
2. **M2 — detector**: tune thresholds so labels match your intuition on ~10 matches.
3. **M3 — card**: data-driven minimap HTML (reuse the toggle mockup).
4. **M4 — coach**: feed top-ranked deaths' features to Claude for narrative + green path.
5. **M5 — deep dive (optional)**: `.dem` via `manta` for creeps / fog-exact vision.
