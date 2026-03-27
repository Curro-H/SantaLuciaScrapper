# SantaLucia Scraper — Project Context

## What this is

A Flask web app that logs into the SantaLucia insurance portal, scrapes a paginated table of claims, and filters rows where **T. Trabajo = "Encargo"** AND **Fecha visita is empty**. Built as a web app (replacing a Windows .exe) so it can be used from a browser on admin-restricted machines.

**Live deployment:** Back4App Containers, auto-deployed from this GitHub repo (`main` branch).
**GitHub:** https://github.com/Curro-H/SantaLuciaScrapper

---

## File structure

```
scraper.py            Pure Python stdlib scraper — no external dependencies
web_app.py            Flask backend with SSE streaming
templates/index.html  Single-page web UI (vanilla JS, dark theme)
Dockerfile            Back4App Containers config (port 8080, gunicorn gthread)
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

## Deployment (Back4App Containers)

1. Push to `main` → Back4App auto-rebuilds from `Dockerfile`
2. Container listens on port 8080 (hardcoded in `Dockerfile` CMD)
3. `PORT` env var supported in `web_app.py` for local override

To deploy a change: commit + push to `main`. That's it.
