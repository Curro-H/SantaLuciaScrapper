# SantaLucia Scraper — Project Context

## What this is

A Flask web app that logs into the SantaLucia insurance portal, scrapes a paginated table of claims, and filters rows where **T. Trabajo = "Encargo"** AND **Fecha visita is empty**. Built as a web app (replacing a Windows .exe) so it can be used from a browser on admin-restricted machines.

**Live deployment:** Render (free tier), auto-deployed from this GitHub repo (`main` branch).
**GitHub:** https://github.com/Curro-H/SantaLuciaScrapper

---

## File structure

```
scraper.py            Pure Python stdlib scraper — no external dependencies
web_app.py            Flask backend with SSE streaming
templates/index.html  Single-page web UI (vanilla JS, dark theme)
Dockerfile            Render config (port from $PORT env var, gunicorn gthread)
render.yaml           Render service definition (free tier, Docker)
requirements.txt      flask + gunicorn only
```

---

## Architecture

```
Browser
  POST /api/scrape/start  { username, password }
       → returns { job_id }
  GET  /api/scrape/stream/<job_id>
       → Server-Sent Events: {type:"log"|"row"|"error"|"done"}
  POST /api/scrape/stop/<job_id>
       → signals stop_event to background thread
```

The scraper runs in a background thread. A `queue.Queue` bridges scraper callbacks to the SSE generator. Gunicorn is configured `--workers=1 --threads=4` so all requests share the same process and in-memory `jobs` dict.

---

## Scraper internals (hard-won details — do not change lightly)

**Login flow (3 steps):**
1. `GET /pyp/Profesionales` — extract CSRF tokens (`CNC_codigoToken`, `CNC_identificacionPN`) from hidden inputs
2. `POST /pyp/CNCEntrada` — submit credentials; server returns `<mensaje-motor-ajax>` JSON
3. Parse JSON for `ControladorURLVuelta` task → `GET /pyp/<urlVuelta>` to get the actual HTML page

**Password field encoding quirk:**
The HTML attribute is literally `name="deContrase%F1a"`. Passing `'deContrase%F1a'` as a dict key to `urllib.parse.urlencode()` encodes `%` → `%25`, producing `deContrase%25F1a=...` — which matches what a real browser sends. Do NOT use `deContraseña` (wrong encoding) or manual `quote()`.

**Table IDs:**
- `*_cabecera` — header table (`<th>` cells)
- `*_datos` — data table (`<td>` cells)

**Pagination:** POST to `/pyp/CNCEntrada` with `CNC_codigoEvento=recuperarPagina[0]`. Response may be:
- Full `ControladorURLVuelta` redirect → follow with GET
- Zone HTML fragment inside `<mensaje-motor-ajax>` → parse the fragment

**SSL:** Certificate verification disabled (`ssl.CERT_NONE`) — the site uses a self-signed cert.

---

## Target site

- **URL:** `https://wwwssl.santalucia.es:3415/pyp/Profesionales`
- **POST endpoint:** `https://wwwssl.santalucia.es:3415/pyp/CNCEntrada`
- **Framework:** MotorAJAX (custom Java/JSP) — all POST responses are wrapped in `<mensaje-motor-ajax>{json}</mensaje-motor-ajax>`
- **Default credentials (pre-filled in UI):** user `B82215179`, pass `DmP2*UmMzp`

---

## Local development

```bash
pip install flask
python web_app.py
# open http://localhost:5000
```

No other dependencies. `scraper.py` uses only Python stdlib.

---

## Deployment (Render)

**First-time setup (once):**
1. Go to https://render.com → sign up / log in
2. **New → Web Service** → connect GitHub repo `Curro-H/SantaLuciaScrapper`
3. Select environment: **Docker**, Plan: **Free**
4. Click **Deploy** — Render builds and assigns a permanent URL (e.g. `https://santalucia-scraper.onrender.com`)

**Ongoing deploys:**
Push to `main` → Render auto-rebuilds from `Dockerfile`. That's it.

**Notes:**
- Container port is set via `PORT` env var (Render default: `10000`)
- Free tier spins down after 15 min of inactivity; ~30s cold start on first visit
