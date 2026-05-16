# Memora — Architecture & Design

## What it does

Memora converts meeting recordings into structured Confluence pages. You give it a file or a URL; it transcribes the audio with Whisper, extracts decisions/action items/attendees with Gemini, and publishes a formatted page to Confluence.

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
│                     │◀──DB────│   different CMD)        │
└──────────┬──────────┘         └───────────┬────────────┘
           │                                │
           ▼                                ▼
┌──────────────────┐            ┌────────────────────────┐
│  SQLite (jobs DB)│            │  Redis (task queue +   │
│  /app/data/db/   │            │  result backend)       │
└──────────────────┘            └────────────────────────┘
```

Four Docker services:

| Service | Image | Purpose |
|---|---|---|
| `redis` | `redis:alpine` | Celery broker and result store |
| `backend` | custom Python | FastAPI API server |
| `worker` | same custom Python | Celery worker that does the heavy processing |
| `frontend` | custom nginx | Serves the React SPA; proxies `/api/*` to backend |

All persistent data (uploads, transcripts, results, the SQLite DB) lives in a single named volume `app_data` mounted into both `backend` and `worker`, so they share the same files.

---

## Data flow for a single job

```
1. User uploads file or pastes URL
   → POST /upload  or  POST /upload-url
   → Job row created in SQLite (status: uploaded)
   → process_recording.delay(job_id) queued in Redis

2. Celery worker picks up the task
   Step 0 (URL jobs only):
     yt-dlp downloads the file → status: downloading
   Step 1:
     ffmpeg converts to 16 kHz mono WAV → status: extracting_audio
   Step 2:
     Whisper large-v3 transcribes → writes transcript.json → status: transcribing
   Step 3 (agent):
     Gemini Phase 1: JSON extraction (title, decisions, action items, …)
     Gemini Phase 2: Confluence tool loop (search → create/update → flag)
     → writes result.json → status: done (or failed at any step)

3. Frontend polls GET /status/{job_id} every 2 s
   → switches to ResultView when status == "done"
   → shows error when status == "failed"
```

---

## Backend files

### `config.py` — Settings

**Class: `Settings(BaseSettings)`**

Single source of truth for every environment variable the app reads. All other modules import from `config.settings`; none read `os.environ` directly.

```
gemini_api_key      → GEMINI_API_KEY
confluence_url      → CONFLUENCE_URL
confluence_token    → CONFLUENCE_TOKEN
confluence_space_key→ CONFLUENCE_SPACE_KEY
org_api_key         → ORG_API_KEY          (shared secret for X-API-Key auth)
redis_url           → REDIS_URL
database_url        → DATABASE_URL
upload_dir          → UPLOAD_DIR           (where uploaded files are saved)
jobs_dir            → JOBS_DIR             (where transcript.json / result.json live)
cors_origins        → CORS_ORIGINS         (JSON list, e.g. ["http://localhost:5173"])
```

Values come from the `.env` file at startup. Docker Compose passes them as environment variables which override `.env` defaults (e.g. overriding SQLite path to a volume mount).

**Why pydantic-settings?** Type-checking and `.env` parsing in one place. The `cors_origins: list[str]` field requires the value to be valid JSON in the `.env` file (`["a","b"]` not `a,b`).

---

### `database.py` — Data model & CRUD

**Enum: `JobStatus`**

Represents every state a job can be in:

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
| `storage_path` | Filename inside `uploads/` (e.g. `abc123.mp4`) |
| `source_url` | Set for URL jobs; null for file uploads |
| `status` | Current `JobStatus` |
| `error_message` | Non-null only when `status == failed` |
| `confluence_url` | URL of the published Confluence page |
| `result_json` | Full extraction result as JSON text |

**Why SQLite?** Zero-infrastructure for local/self-hosted deploy. Switching to PostgreSQL requires only changing `DATABASE_URL` — the `connect_args` guard in `create_engine` removes the SQLite-specific flag automatically.

**Functions**

- `create_job(db, filename, storage_path, source_url)` — inserts a new row
- `get_job(db, job_id)` — fetches by UUID; raises `ValueError` if not found
- `update_job_status(db, job_id, status, ...)` — transitions status and optionally sets result fields
- `get_db()` — FastAPI dependency that yields a session and closes it in `finally`
- `init_db()` — called at API startup; creates tables and runs a safe `ALTER TABLE` migration to add `source_url` to existing databases

---

### `auth.py` — API key middleware

**Class: `APIKeyMiddleware(BaseHTTPMiddleware)`**

Every request must carry `X-API-Key: <value>` matching `ORG_API_KEY`. The `/health`, `/docs`, and `/openapi.json` endpoints are exempt (used by Docker health probes and Swagger UI).

Returns 401 (missing header) or 403 (wrong key). The frontend sends the key via `VITE_API_KEY`, baked into the React bundle at build time.

**Why middleware instead of a FastAPI dependency?** Middleware runs before routing, so it catches all routes uniformly including ones added later. The comment at the bottom of the file describes exactly how to swap it for an OIDC/JWT middleware when the org moves to SSO.

---

### `main.py` — FastAPI routes

Four routes:

**`POST /upload`** — file upload
- Validates extension and MIME type
- Streams the file to disk in 256 KB chunks, enforcing a 500 MB cap
- Creates a `Job` row with `storage_path` set
- Queues `process_recording.delay(job.id)`
- Returns `{job_id, status}` immediately (202)

**`POST /upload-url`** — URL submission
- Validates scheme is `http` or `https`
- Creates a `Job` row with `source_url` set (no `storage_path` yet)
- Queues `process_recording.delay(job.id)`
- Returns `{job_id, status}` immediately (202)
- The actual download happens inside the worker (Step 0)

**`GET /status/{job_id}`** — job polling
- Returns current status, filename, timestamps, and `error_message` if failed
- Frontend polls this every 2 seconds

**`GET /result/{job_id}`** — completed result
- Returns 409 if still processing
- Returns full result JSON when `status == done`

---

### `tasks.py` — Celery task

**Function: `process_recording(job_id)`** (the Celery task)

This is the entire processing pipeline in one function. It runs sequentially in the Celery worker process.

```
Load job from DB
  │
  ├─ storage_path is None AND source_url set?
  │    └─ Step 0: _download_from_url()    → status: DOWNLOADING
  │
  ├─ Step 1: _extract_audio()             → status: EXTRACTING_AUDIO
  │    ffmpeg converts to 16 kHz mono WAV
  │
  ├─ Step 2: _transcribe()                → status: TRANSCRIBING
  │    Whisper large-v3 → transcript.json
  │    then:                              → status: PROCESSING
  │
  └─ Step 3: run_agent(job_id)            (agent.py sets PUBLISHING → DONE)
```

Each step is wrapped in `try/except`. Any failure calls `_mark_failed_new_session()` and returns — the job is marked `FAILED` with a descriptive message and the pipeline stops.

**Function: `_download_from_url(url, job_id, upload_dir)`**

Uses yt-dlp to download from any supported URL (direct file, Zoom, Vimeo, YouTube, etc.). Key options:

- `format`: prefers m4a audio, falls back progressively to any video
- `max_filesize`: 2 GB hard cap
- `socket_timeout`: 30 s — prevents infinite hangs on stalled connections
- `retries`: 3 retries on transient network errors

After download, uses `ydl.prepare_filename(info)` to find the actual filename yt-dlp wrote (extension can change after post-processing). Falls back to globbing `{job_id}.*` if the file has been renamed.

**Function: `_extract_audio(source, job_dir)`**

Runs `ffmpeg -i <source> -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav`. Always re-encodes even `.wav` inputs to ensure Whisper gets exactly 16 kHz mono PCM. 10-minute timeout.

**Function: `_get_whisper_model()`**

Loads `whisper.load_model("large-v3")` once per worker process and caches it in a module-level variable. The model is ~3 GB and takes ~30 s to load; this means only the first task in each worker pays the load cost.

**Function: `_transcribe(audio_path, job_dir, job_id)`**

Runs Whisper on the WAV file. Writes `transcript.json`:

```json
{
  "full_text": "...",
  "segments": [{"start": 0.0, "end": 5.2, "text": "..."}],
  "language": "en"
}
```

**Function: `_friendly_download_error(raw)`**

Maps raw yt-dlp error strings to human-readable messages. Handles: YouTube bot detection, private videos, geo-restrictions, unsupported format, extractor bugs, network errors.

**Why one Celery task (not a chain)?** The steps share state (paths, the DB session) and need sequential error handling. A chain would require passing paths between tasks and makes the failure-handling pattern more complex for little benefit at this scale.

---

### `agent.py` — AI extraction and Confluence publishing

**Class: `MeetingAgent`**

Runs two sequential Gemini calls ("phases") to go from transcript text to a published Confluence page.

**Phase 1 — Structured extraction**

Uses `gemini-2.5-flash` in JSON response mode (`response_mime_type="application/json"`, `temperature=0`). Sends the full transcript with a prompt that asks for a specific JSON shape:

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

Falls back to empty defaults if JSON parsing fails (so Phase 2 still runs and writes a stub page rather than crashing the job).

**Phase 2 — Confluence tool loop**

Uses the same model with 4 function-calling tools declared as `genai.protos.FunctionDeclaration`:

| Tool | What it does |
|---|---|
| `search_confluence` | CQL search for pages similar to the meeting title |
| `create_confluence_page` | POST new page in the configured space |
| `update_confluence_page` | PUT update of an existing page (auto-increments version) |
| `flag_incomplete_action_items` | Filter action items missing owner or due date |

The agent sends the rendered page body (in Confluence Storage Format) and instructs Gemini to: search first, then create or update, then flag incomplete action items. The loop runs until Gemini stops calling tools or hits `MAX_TOOL_TURNS = 10`.

**Template renderers** (`_render_sprint_review`, `_render_planning`, `_render_incident`, `_render_general`)

Each produces an XHTML string in Confluence Storage Format. Chosen by `meeting_type`. The incident template adds a `<ac:structured-macro ac:name="warning">` block for visibility. All use `html.escape()` to prevent injection.

**Function: `run_agent(job_id)`** (public entry point)

Called by `tasks.process_recording`. Reads `transcript.json`, runs the agent, writes `result.json`, updates the DB to `DONE`.

---

### `confluence.py` — Confluence API client

**Dataclass: `MeetingDocument`**

A plain Python dataclass holding all the structured data extracted by Phase 1. Passed to the template renderers.

**Class: `ConfluenceClient`**

Thin synchronous wrapper around the Confluence REST API (v1 content endpoints). Uses `httpx.Client` for all HTTP calls (sync is fine inside Celery workers).

| Method | Endpoint | Purpose |
|---|---|---|
| `search_pages(query, limit)` | `GET /rest/api/content/search` | CQL search by title in the configured space |
| `create_page(title, body, ...)` | `POST /rest/api/content` | Create a new page |
| `update_page(page_id, title, body)` | `PUT /rest/api/content/{id}` | Update existing page |
| `_get_page_version(page_id)` | `GET /rest/api/content/{id}?expand=version` | Fetch current version before updating (Confluence requires the exact current version number) |

Authentication uses a `Bearer` token in the `Authorization` header. `search_pages` swallows exceptions and returns an empty list — a search failure is non-fatal; the agent will just create a new page.

---

## Frontend files

### `src/api.js`

All HTTP calls to the backend. Two functions for job submission:

- `uploadMeeting(file, title)` — multipart/form-data POST to `/upload`
- `submitUrlMeeting(url, title)` — JSON POST to `/upload-url`

Both use a shared `apiFetch` helper that injects the `X-API-Key` header (from `VITE_API_KEY`) and the correct base URL.

### `src/views/UploadView.jsx`

The landing page. Two modes controlled by `mode` state (`"file"` | `"url"`):

- **File mode**: drag-and-drop zone with extension + size validation
- **URL mode**: text input with `http://`/`https://` client-side check

Both modes share a "Meeting title" field and submit to their respective API call. Switches to `ProcessingView` on success via `onJobCreated(job_id)`.

### `src/views/ProcessingView.jsx`

Polls `GET /status/{job_id}` every 2 seconds. Shows an animated stepper with one row per pipeline step:

```
Downloading → Extracting Audio → Transcribing → AI Processing → Publishing
```

Each step has a `StepProgressBar` that uses `requestAnimationFrame` to animate smoothly toward 90% while active (exponential ease: `90 * (1 - e^(-t/estSeconds))`), then snaps to 100% when the step completes. Steps that finish instantly (e.g. `downloading` for file uploads) jump through without showing the bar.

On `failed`: shows the `error_message` from the API with a "Start over" button.

### `src/views/ResultView.jsx`

Displays the completed result fetched from `GET /result/{job_id}`: title, summary, attendees, decisions, action items, open questions, and a link to the Confluence page.

---

## Key design decisions

**Why Celery + Redis instead of async background tasks?**
Whisper large-v3 takes minutes of CPU time. FastAPI's `BackgroundTasks` runs in the same process and would block the event loop. Celery pushes heavy work to a separate process and survives server restarts — the task is still in Redis if the backend restarts mid-job.

**Why SQLite?**
No external database needed for self-hosted deploys. The `DATABASE_URL` env var is the only thing that changes when moving to PostgreSQL. The SQLite-specific `connect_args` is already conditional on the URL prefix.

**Why store everything in one `Job` row?**
Simplicity. `result_json` is a denormalized JSON blob — no joins needed to fetch a complete result. At the scale Memora targets (one team, tens of jobs per day), this is fine.

**Why two separate Gemini calls (two-phase)?**
Phase 1 uses JSON response mode with `temperature=0` for deterministic structured output — function calling is not ideal here because the model would have to call a tool to return data that the code directly consumes. Phase 2 uses function calling to take real actions (Confluence API). Mixing these in one call makes both worse.

**Why one Celery task for the full pipeline?**
The steps share file paths and need linear error handling. A Celery chain would require passing state between tasks and complicates the failure path. The entire pipeline for one recording is a single unit of work.

**How to scale**
- More concurrent recordings: increase `--concurrency` on the worker, or run more worker replicas. Each worker loads Whisper once on first use.
- More reliability: replace SQLite with PostgreSQL and Redis with a managed equivalent.
- Authentication: swap `APIKeyMiddleware` in `main.py` for an OIDC middleware — the routes don't change.
- Storage: add S3 support in `storage.py` (already stubbed) and write `_download_to_s3` / `_upload_path_from_s3`.
