# Memora — Architecture & Design

## What it does

Memora converts meeting recordings into structured Confluence pages. You give it a file or a URL; it transcribes the audio with OpenAI Whisper, extracts decisions/action items/attendees with Gemini, and publishes a formatted page to Confluence.

---

## System overview

```
Browser (React SPA)
        │  HTTP (via nginx proxy)
        ▼
┌─────────────────────┐
│  nginx (port 3000)  │  Serves the React bundle + proxies /api/* → backend
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐         ┌────────────────────────┐
│  FastAPI backend    │──task──▶│  Celery worker          │
│  (port 8000)        │         │  (same Docker image,    │
│                     │◀──DB────│   start.sh runs both)   │
└──────────┬──────────┘         └───────────┬────────────┘
           │                                │
           ▼                                ▼
┌──────────────────────┐        ┌────────────────────────┐
│  PostgreSQL (jobs DB)│        │  Redis (task queue +   │
│  Railway managed     │        │  result backend)       │
└──────────────────────┘        └────────────────────────┘
```

### Railway deployment (production)

Five Railway services:

| Service | Purpose |
|---|---|
| `backend` | FastAPI API server + Celery worker (single service via `start.sh`) |
| `frontend` | Nginx serving the React SPA; proxies `/api/*` to backend |
| `redis` | Railway managed Redis plugin — Celery broker and result store |
| `postgres` | Railway managed PostgreSQL plugin — job database |

`start.sh` launches `celery worker` in the background then `exec uvicorn` as PID 1. Both processes share the same container filesystem, so uploaded files and job artifacts are accessible to both without a shared volume.

The backend service has one persistent volume mounted at `/app/data/uploads` for uploaded recordings. Job artifacts (audio, transcripts) are written to `/app/data/jobs/`.

### Local development (docker-compose)

Four Docker services: `redis`, `backend`, `worker` (separate container, same image), `frontend`. A single named volume `app_data` is mounted into both `backend` and `worker` so they share files and the SQLite database.

---

## Data flow for a single job

```
1. User submits file or URL + options (output type, Confluence destination, etc.)
   → POST /upload  or  POST /upload-url
   → Job row created in PostgreSQL (status: uploaded)
   → process_recording.delay(job_id) queued in Redis

2. Celery worker picks up the task
   Step 0 (URL jobs only):
     RecordingDownloader.download() via yt-dlp → status: downloading
   Step 1:
     ffmpeg converts to 16 kHz mono MP3 (64 kbps) → status: extracting_audio
   Step 2:
     OpenAI Whisper API (whisper-1) transcribes → writes transcript.json → status: transcribing
   Step 3 (agent):
     Gemini Phase 1: structured JSON extraction (title, decisions, action items, …)
     Gemini Phase 2: Confluence tool loop (search → create/update → flag incomplete items)
     → writes result.json → status: done (or failed at any step)

3. Frontend polls GET /status/{job_id} every 2 s
   → switches to ResultView when status == "done"
   → shows error when status == "failed"
   → user can download a .docx via GET /jobs/{job_id}/download
```

---

## Backend files

### `config.py` — Settings

**Class: `Settings(BaseSettings)`**

Single source of truth for every environment variable. All modules import from `config.settings`.

```
gemini_api_key      → GEMINI_API_KEY
openai_api_key      → OPENAI_API_KEY       (Whisper API)
confluence_url      → CONFLUENCE_URL
confluence_email    → CONFLUENCE_EMAIL     (Atlassian account email for Basic auth)
confluence_token    → CONFLUENCE_TOKEN     (Atlassian API token)
org_api_key         → ORG_API_KEY          (shared secret for X-API-Key auth)
redis_url           → REDIS_URL
database_url        → DATABASE_URL         (PostgreSQL in prod; SQLite locally)
upload_dir          → UPLOAD_DIR
jobs_dir            → JOBS_DIR
cors_origins        → CORS_ORIGINS         (JSON array or comma-separated)
mock_transcription  → MOCK_TRANSCRIPTION   (dev: skip OpenAI, return stub)
mock_agent          → MOCK_AGENT           (dev: skip Gemini + Confluence)
```

**`db_url` property:** Normalises Railway's `postgres://` scheme to `postgresql://` which SQLAlchemy 2.x requires. Always use `settings.db_url` (not `settings.database_url`) when constructing the engine.

**`cors_origins` property:** Accepts either a JSON array (`["url1","url2"]`) or comma-separated string (`url1,url2`).

---

### `database.py` — Data model & CRUD

**Enum: `JobStatus`**

```
uploaded → downloading → extracting_audio → transcribing → processing → publishing → done
                                                                                   ↘ failed
```

`downloading` is only active for URL-submitted jobs. `processing` and `publishing` are set inside `agent.py`.

**Class: `Job` (SQLAlchemy model)**

One row per submitted recording. Key columns:

| Column | Purpose |
|---|---|
| `id` | UUID primary key |
| `filename` | Display name shown in the UI |
| `storage_path` | Filename inside `uploads/` (null for URL jobs until download) |
| `source_url` | Set for URL jobs; null for file uploads |
| `status` | Current `JobStatus` |
| `output_type` | `detailed` \| `mom` \| `quick_summary` \| `action_items` |
| `publish_to_confluence` | Boolean — whether to publish at all |
| `custom_instructions` | Free-text appended to the extraction prompt |
| `confluence_space_key` | Target Confluence space (per-job) |
| `confluence_parent_page_id` | Optional parent page ID |
| `confluence_page_title` | Optional explicit page title override |
| `context_text` | Optional meeting context injected into the prompt |
| `confluence_reference_url` | Optional reference URL embedded in the page |
| `screenshots_enabled` | Boolean stub for future screenshot capture |
| `error_message` | Non-null only when `status == failed` |
| `confluence_url` | URL of the published Confluence page |
| `result_json` | Full extraction result as JSON text |

**Database choice:** PostgreSQL in production (Railway plugin). SQLite supported for local development — only `DATABASE_URL` needs to change. The `connect_args` guard in `create_engine` and the `db_url` normaliser in `config.py` handle both transparently.

**`init_db()`** — called at API startup. Runs `Base.metadata.create_all()` then a safe `ALTER TABLE` migration list (idempotent — each statement is silently skipped if the column already exists).

---

### `auth.py` — API key middleware

**Class: `APIKeyMiddleware(BaseHTTPMiddleware)`**

Every request must carry `X-API-Key: <value>` matching `ORG_API_KEY`. Exempt paths: `/health`, `/docs`, `/openapi.json`. Returns 401 (missing) or 403 (wrong key). The frontend sends the key via `VITE_API_KEY`, baked into the React bundle at build time.

The `GET /jobs/{job_id}/download` endpoint also accepts `?api_key=` as a query param because it is opened via a browser anchor click (no header injection possible from `<a href>`).

---

### `main.py` — FastAPI routes

**`POST /upload`** — file upload  
Validates extension (`.mp4 .mp3 .webm .wav .m4a`) and MIME type. Streams to disk in 256 KB chunks, enforcing a 500 MB cap. Accepts all job options as form fields (output_type, confluence destination, custom instructions, etc.). Creates a `Job` row and queues the task.

**`POST /upload-url`** — URL submission  
Accepts a JSON body with `url` plus the same job options. Validates `http`/`https` scheme. The actual download happens inside the worker.

**`GET /status/{job_id}`** — job polling  
Returns current status, filename, timestamps, and `error_message`. Frontend polls every 2 s.

**`GET /result/{job_id}`** — completed result  
Returns 409 if not done yet. Returns full result JSON and `confluence_url` when done.

**`GET /jobs/{job_id}/download`** — DOCX export  
Generates a `.docx` from `result_json` using `python-docx` and streams it. Format depends on `output_type` — each type has its own section layout.

**`GET /confluence/spaces`** — Confluence space list  
Calls `ConfluenceClient.get_spaces()` and returns `[{key, name}]` for the destination picker in the UI.

**`GET /confluence/pages?space_key=`** — Confluence page list  
Returns `[{id, title}]` for the parent page picker.

---

### `recording_downloader.py` — Download abstraction

**Class hierarchy:**

```
RecordingDownloader (ABC)
  ├─ PublicDownloader   — yt-dlp, no auth (RECORDING_AUTH_MODE=none, default)
  ├─ TokenDownloader    — static Bearer token (RECORDING_AUTH_MODE=token, TODO)
  └─ OAuthDownloader    — per-user OAuth (RECORDING_AUTH_MODE=oauth, TODO)
```

**`get_downloader()`** — factory that reads `RECORDING_AUTH_MODE` and returns the right implementation. `tasks.py` calls only this function; adding a new auth mode requires no changes to `tasks.py`.

`PublicDownloader` uses yt-dlp with: prefer m4a audio, 2 GB max, 30 s socket timeout, 3 retries. After download, resolves the actual filename via `prepare_filename` (extension can change after yt-dlp post-processing), falling back to globbing `{job_id}.*`.

`_friendly_download_error()` maps raw yt-dlp exception strings to user-readable messages (bot detection, private video, geo-restriction, format unavailable, network error, etc.).

---

### `tasks.py` — Celery task

**Function: `process_recording(job_id)`** (the Celery task)

```
Load job from DB
  │
  ├─ source_url set and no storage_path?
  │    └─ Step 0: get_downloader().download()    → status: DOWNLOADING
  │
  ├─ Step 1: _extract_audio()                    → status: EXTRACTING_AUDIO
  │    ffmpeg: input → 16 kHz mono MP3 @ 64 kbps
  │    Raises RuntimeError if output > 24 MB (Whisper API limit)
  │
  ├─ Step 2: _transcribe()                       → status: TRANSCRIBING
  │    OpenAI whisper-1 API → transcript.json    → status: PROCESSING
  │
  └─ Step 3: run_agent(job_id)                   (agent.py sets PUBLISHING → DONE)
```

Each step is wrapped in `try/except`. Any failure calls `_mark_failed_new_session()` and returns early — the job is marked `FAILED` with a descriptive error message.

**Audio size limit:** If the extracted MP3 exceeds 24 MB (≈50 min of audio at 64 kbps), the job fails with a user-readable message asking to split the recording. No chunking is implemented.

**Celery config:** `task_acks_late=True`, `task_reject_on_worker_lost=True` — tasks survive worker crashes and are re-queued. No `autoretry_for` — these are long-running operations where retrying immediately is unlikely to help.

---

### `agent.py` — AI extraction and Confluence publishing

**Class: `MeetingAgent`**

Runs two sequential Gemini calls.

**Phase 1 — Structured extraction**

`gemini-2.5-flash`, JSON response mode (`response_mime_type="application/json"`, `temperature=0`). Returns:

```json
{
  "meeting_type": "sprint-review | planning | incident | general",
  "title": "...",
  "attendees": ["..."],
  "summary": "...",
  "decisions": ["..."],
  "action_items": [{"owner": "...", "task": "...", "due": "..."}],
  "open_questions": ["..."],
  "highlights": ["..."]
}
```

Falls back to empty defaults if JSON parsing fails so Phase 2 still runs.

**Phase 2 — Confluence tool loop**

`gemini-2.5-flash` with 4 function-calling tools:

| Tool | Args | What it does |
|---|---|---|
| `search_confluence` | `query` | CQL title search in the target space |
| `create_confluence_page` | `title`, `space_key`, `parent_id` | POST new page |
| `update_confluence_page` | `page_id`, `title` | PUT existing page (auto-increments version) |
| `flag_incomplete_action_items` | `action_items` | Returns items missing owner or due date |

**Important:** `body` is intentionally absent from `create_confluence_page` and `update_confluence_page`. Gemini generating Confluence Storage Format XML inline in a function call argument causes `MALFORMED_FUNCTION_CALL` errors. The agent always uses `self._pre_rendered_body` (rendered by `prompts.py` before Phase 2 starts) — whatever body Gemini might supply is ignored.

A `StopCandidateException` with `MALFORMED_FUNCTION_CALL` is caught in the tool loop. If the page was already actioned (created/updated) by that turn, the exception is swallowed and the result is returned normally.

**Template selection:** Based on `meeting_type` from Phase 1 — one Confluence Storage Format template per type (`sprint-review`, `planning`, `incident`, `general`). Templates live in `prompts.py`. The incident template adds a `<ac:structured-macro ac:name="warning">` block.

---

### `prompts.py` — AI prompts and Confluence templates

Contains:
- `SYSTEM_PROMPT` — Gemini system instruction
- `EXTRACTION_PROMPT` — Phase 1 prompt template (slots: `{transcript}`, `{custom_instructions}`)
- `TEMPLATE_MAP` — dict mapping `meeting_type → render function`
- `build_extraction_prompt()`, `build_action_prompt()` — prompt builders called by `agent.py`
- `render_confluence_body()` — entry point that picks and calls the right template renderer
- Per-type renderer functions that produce Confluence Storage Format XHTML (all values escaped with `html.escape()`)

---

### `confluence.py` — Confluence API client

**Class: `ConfluenceClient`**

Authentication: **HTTP Basic** (`Authorization: Basic base64(email:api_token)`) — required for Confluence Cloud. Bearer token auth is only for Confluence Data Center PATs.

| Method | Endpoint | Purpose |
|---|---|---|
| `get_spaces(limit)` | `GET /rest/api/space` | List accessible spaces for destination picker |
| `get_pages(space_key, limit)` | `GET /rest/api/content` | List pages in a space for parent picker |
| `search_pages(query, limit)` | `GET /rest/api/content/search` | CQL title search; returns empty list on failure (non-fatal) |
| `create_page(title, body, space_key, parent_id)` | `POST /rest/api/content` | Create new page |
| `update_page(page_id, title, body)` | `PUT /rest/api/content/{id}` | Update existing page (fetches current version first) |

---

### `storage.py` — Storage abstraction (stub)

**Class hierarchy:**

```
StorageBackend (ABC)
  └─ LocalStorage   — filesystem under upload_dir (current default)
  (S3Storage — TODO: implement with boto3)
```

`get_storage()` factory returns `LocalStorage` today. Swap to `S3Storage` by reading a `STORAGE_BACKEND` env var here — no callers need to change.

---

### `start.sh` — Combined process launcher (Railway)

Runs `celery worker --concurrency=2` in the background then `exec uvicorn` as PID 1. Used as the Railway backend service CMD. Railway's health checks and shutdown signals target uvicorn (PID 1); if uvicorn exits, the container restarts, which also restarts celery.

---

## Frontend files

### `src/api.js`

All HTTP calls to the backend. `apiFetch` helper injects `X-API-Key` (from `VITE_API_KEY`) and the base URL.

- `uploadMeeting(file, options)` — multipart/form-data POST to `/upload`
- `submitUrlMeeting(url, options)` — JSON POST to `/upload-url`
- `pollStatus(job_id)` — GET `/status/{job_id}`
- `fetchResult(job_id)` — GET `/result/{job_id}`
- `fetchSpaces()` / `fetchPages(spaceKey)` — GET `/confluence/spaces` and `/confluence/pages`

### `src/views/UploadView.jsx`

Landing page with two modes (`file` | `url`). Includes:
- Output type selector (Detailed Notes / MOM / Quick Summary / Action Items Only)
- Confluence destination picker (space + optional parent page, loaded from API)
- Optional: custom instructions, context text, reference URL, screenshots toggle

### `src/views/ProcessingView.jsx`

Polls `/status/{job_id}` every 2 s. Animated stepper:

```
Downloading → Extracting Audio → Transcribing → AI Processing → Publishing
```

Each active step shows a `StepProgressBar` (exponential ease toward 90%, snaps to 100% on completion) and a description subtitle. The subtitle is **inline on desktop** (≥640 px) and **below the label on mobile** (<640 px) to avoid text overlap.

### `src/views/ResultView.jsx`

Displays completed result (title, summary, attendees, decisions, action items, open questions) and a link to the Confluence page. Includes a "Download .docx" button that calls `GET /jobs/{job_id}/download`.

---

## Key design decisions

**Why Celery + Redis instead of async background tasks?**  
Transcription takes minutes. FastAPI `BackgroundTasks` runs in the same process and would block. Celery isolates heavy work in a separate process and survives server restarts — the task stays in Redis if the backend restarts mid-job.

**Why remove `body` from Confluence tool schemas?**  
Gemini 2.5 Flash's thinking mode generates `MALFORMED_FUNCTION_CALL` when asked to produce large Confluence Storage Format XML inline in a function argument. Since the agent always uses `self._pre_rendered_body` anyway (Gemini's body is ignored to prevent XML corruption), removing the argument from the schema eliminates the failure surface entirely.

**Why two separate Gemini phases?**  
Phase 1 uses JSON response mode with `temperature=0` for deterministic structured output. Phase 2 uses function calling for real-world actions. Mixing them in one call makes both worse — the model can't simultaneously produce constrained JSON and reason about tool sequencing.

**Why PostgreSQL in production / SQLite locally?**  
Railway isolates services in separate containers with no shared filesystem. SQLite can't be shared across two containers, so PostgreSQL (Railway plugin) is required. Locally, SQLite is zero-infrastructure. Only `DATABASE_URL` changes — the `db_url` normaliser and `connect_args` guard handle both.

**Why one Celery task for the full pipeline?**  
Steps share file paths and need linear error handling. A Celery chain would require passing paths between tasks and complicates the failure path.

**How to scale**

- **Concurrency:** Increase `--concurrency` on the worker or run more worker instances. Each process pays the Whisper/Gemini API latency independently.
- **Storage:** Implement `S3Storage` in `storage.py` and set `STORAGE_BACKEND=s3` — no other code changes needed.
- **Download auth:** Add a new `RecordingDownloader` subclass in `recording_downloader.py` and handle it in `get_downloader()` — `tasks.py` needs no changes.
- **Authentication:** Swap `APIKeyMiddleware` in `main.py` for an OIDC middleware — routes are unaffected.
- **Database:** Already on PostgreSQL in production. For higher load, point `DATABASE_URL` at a larger Railway Postgres plan or an external managed DB.
