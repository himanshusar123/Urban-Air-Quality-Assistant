# AirWise – Urban Air Quality Assistant

AirWise turns live city air-quality data into clear, personal advice: **should you go out, for how long, when is the air cleaner, and do you need a mask?** It adjusts to who you are (a child, an older adult, someone with asthma or a heart condition) and what you plan to do (commute, walk, run, cycle, outdoor work, kids playing).

**Chosen vertical:** Challenge 6 – Urban Air Quality Assistant

**Stack:** Python 3.12 · FastAPI · Google Gemini (`google-genai`) · Open-Meteo Air Quality API · vanilla HTML/CSS/JS · Docker · Google Cloud Run

---

## Persona and problem

City dwellers in places like Delhi, Mumbai or Kolkata see an AQI number every day but rarely know what it means for *them*. A healthy adult walking for 20 minutes and an asthmatic child playing outside for two hours face very different risks at the same AQI. AirWise answers the real question: *"Given the air right now, what should I do?"*

## How it works

```
Browser (accessible form)
   │  POST /api/advice  {city | lat/lon, profile, activity, duration, question, language}
   ▼
FastAPI ──► Validation (Pydantic) ──► Rate limit + security headers
   │
   ├─► Open-Meteo Geocoding  ─┐   (cached, 10 min TTL)
   ├─► Open-Meteo Air Quality ┘   current AQI, 6 pollutants, 24 h hourly forecast
   │
   ├─► Rules engine (advisor.py)  ── deterministic decision: verdict, risk score,
   │                                  mask, actions, best time window, indoor tips
   │
   └─► Gemini (explainer.py) ── explains that decision in plain English or Hindi
                                and answers the user's follow-up question.
                                Falls back to a template if Gemini is unavailable.
```

### Decision logic (context-aware)

1. **AQI category** – US EPA bands (Good → Hazardous).
2. **Personal risk score (0–100)** = AQI × *sensitivity* × *exertion* × *duration*
   - Sensitivity: healthy adult 1.0; child, older adult, pregnant 1.3; respiratory or heart condition 1.5
   - Exertion (breathing rate): commute 0.8 → run 1.4
   - Duration: ≤30 min 0.85 → over 2 h 1.3
3. **Verdict:** `go` (< 25), `caution` (< 45), `limit` (< 70), `avoid` (≥ 70). Hazardous air (AQI > 300) is always `avoid`.
4. **Best time window:** scans the 24-hour forecast for the cleanest contiguous daytime slot (06:00–21:00) that fits the planned duration, and suggests it only if it is at least 10 AQI points better than now.
5. **Mask advice:** N95/FFP2 when AQI > 150, or > 100 for sensitive groups; strongly recommended above 200.
6. **Dominant pollutant:** the pollutant furthest above its WHO/EPA reference level (for example PM2.5 in winter smog, ozone on hot afternoons).
7. **Indoor tips** scale with the AQI level (closing windows, HEPA purifier, avoiding incense or frying smoke).

**Why rules and Gemini together?** Safety-relevant decisions stay deterministic, testable and explainable. Gemini adds what rules do badly: a natural explanation, Hindi output, and answers to free-form questions such as *"Can I open the windows tonight?"*. Gemini is told never to contradict the rules engine's verdict.

## Google services used

| Service | Where | Purpose |
|---|---|---|
| **Gemini API** (`gemini-2.5-flash`, configurable) via `google-genai` | `app/services/explainer.py` | Personalised plain-language explanation (English or Hindi) and answers to the user's question, grounded in a FACTS JSON |
| **Google Cloud Run** | `Dockerfile` | Serverless, autoscaling HTTPS hosting |
| **Secret Manager** (recommended) | deployment | Supplies `GEMINI_API_KEY` at runtime; no secrets in the repo |

## Security

- Strict input validation (ranges, lengths, enums, control-character stripping) with Pydantic
- Per-IP sliding-window rate limiting on POST API calls (default 30/min)
- Security headers: strict CSP (no inline scripts), HSTS, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy; `Cache-Control: no-store` on the API
- Prompt-injection hardening: the user question is wrapped in delimiters with `<`/`>` escaped, the system instruction treats it as untrusted data, Gemini output is length-capped, and the UI renders it with `textContent` only (no `innerHTML`)
- API key read only from the environment; never logged or returned. Upstream error details are not leaked to clients
- Container runs as a non-root user; CORS is off unless `ALLOWED_ORIGINS` is set

## Efficiency

- Async I/O end to end (`httpx.AsyncClient` with connection pooling, shared across requests)
- Bounded LRU + TTL caches for geocoding, air-quality data (coordinates rounded to ~100 m to increase hits) and Gemini responses
- Only one Gemini call per request, with a small token budget; skipped entirely when no key is set
- GZip compression; dependency-free front end (~10 KB)

## Accessibility (WCAG 2.1 AA minded)

- Semantic landmarks, skip link, every input has a visible `<label>`
- Results announced via `aria-live`; focus moves to the result heading; errors use `role="alert"`
- AQI is never conveyed by colour alone: always shown with the number and category text, and the badge has an `aria-label`
- Colour contrast checked for badge colours; visible focus ring; 44 px minimum touch targets
- Supports dark mode and `prefers-reduced-motion`; data tables with captions and `scope` headers
- Hindi output option for wider reach

## Testing

66 automated tests, about 97 % line coverage:

| File | What it covers |
|---|---|
| `tests/test_aqi.py` | Every AQI band boundary, dominant-pollutant logic |
| `tests/test_advisor.py` | Risk scoring, verdict thresholds, sensitivity and exertion effects, mask rules, best-window search (duration, night hours, contiguity) |
| `tests/test_air_quality.py` | Payload parsing, geocoding, caching, HTTP 500 / bad JSON / timeouts (mocked with `respx`) |
| `tests/test_explainer.py` | Gemini path and SDK config (v1beta, thinking budget), caching, failure fallback, truncation, prompt-injection escaping |
| `tests/test_api.py` | Endpoints, validation (422), unknown city (404), upstream failure (502), AI on/off |
| `tests/test_security_and_cache.py` | Security headers, rate limiting (429), TTL/LRU cache, sanitisation, no key leakage |

```bash
pip install -r requirements-dev.txt
ruff check .
pytest --cov=app
```

CI runs lint and tests on every push (`.github/workflows/ci.yml`).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GEMINI_API_KEY=your_key                       # optional; Windows: set GEMINI_API_KEY=...
uvicorn app.main:app --reload
# open http://localhost:8000  (API docs at /api/docs)
```

## Deploy to Google Cloud Run

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
printf "%s" "YOUR_GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=-
gcloud run deploy airwise --source . --region asia-south1 --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest --memory 512Mi --max-instances 3
```

(Grant the Cloud Run service account the *Secret Manager Secret Accessor* role if prompted.)

## API

`POST /api/advice`

```json
{ "city": "Delhi", "profile": "respiratory", "activity": "run",
  "duration_minutes": 45, "language": "en", "question": "Is evening better?" }
```

Returns `air` (AQI, pollutants, 24 h forecast), `category`, `decision` (verdict, risk score, mask, actions, best window, indoor tips), `explanation` and `ai_generated`. `GET /api/health` reports status and whether AI is enabled.

## Project structure

```
app/
  main.py              FastAPI app factory, routes, error handling
  config.py            Environment-based settings
  schemas.py           Validated request/response models
  security.py          Security headers and rate limiting middleware
  cache.py             Bounded TTL cache
  services/
    air_quality.py     Open-Meteo geocoding and air-quality client
    aqi.py             AQI categories and dominant pollutant
    advisor.py         Context-aware decision engine
    explainer.py       Gemini explanation with fallback
  static/              Accessible front end (HTML/CSS/JS)
tests/                 Pytest suite
```

## Assumptions

- AQI uses the **US EPA scale** from Open-Meteo (CAMS model data, about 11 km resolution, hourly). India's NAQI uses different breakpoints, so values may differ from CPCB stations.
- Multipliers for sensitivity, exertion and duration are reasoned heuristics based on public EPA/WHO guidance, not clinically validated values.
- The "best window" considers waking hours (06:00–21:00) in the location's local time.
- Advice is general guidance, **not medical advice**; the app says so in the UI and Gemini is instructed not to diagnose.
- Without `GEMINI_API_KEY` the app still works fully, using template explanations (English).
- Rate limiting and caches are in-memory per instance, which is fine for the Cloud Run demo scale; Redis or Memorystore would be used for multi-instance production.
