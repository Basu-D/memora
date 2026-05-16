# Memora

> Turn meeting recordings into structured Confluence documentation — automatically.

Upload any video or audio recording (Webex, Teams, Zoom, etc.). Memora transcribes it with Whisper, extracts decisions, action items and open questions with Gemini 2.5 Flash, and publishes polished notes directly to your Confluence space.

---

## How it works

```
Upload recording (.mp4 / .mp3 / .webm / .wav / .m4a)
        │
        ▼
 ffmpeg  →  16 kHz WAV
        │
        ▼
 Whisper large-v3  →  transcript.json
        │
        ▼
 Gemini 2.5 Flash (function calling)
   • Classify meeting type  (sprint-review / planning / incident / general)
   • Extract summary, decisions, action items, open questions, highlights
   • Search Confluence for existing page  →  create or update
   • Flag incomplete action items (missing owner / deadline)
        │
        ▼
 Confluence page published  +  result.json saved
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Docker Desktop** ≥ 4.x | [Download](https://www.docker.com/products/docker-desktop/) |
| **Google Gemini API key** | Free tier available at [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| **Confluence Personal Access Token** | Create in Confluence → Profile → Personal Access Tokens. Needs *Create Page* permission. |
| **Hugging Face token** | Required to download the pyannote.audio diarization model. Create at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). |
| 8 GB RAM (recommended) | Whisper large-v3 loads ~3 GB of weights into the worker container. |

---

## Setup

### 1 — Clone the repo

```bash
git clone <repo-url>
cd memora
```

### 2 — Create your environment file

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in every value:

```env
# ── Required ────────────────────────────────────────────────────────────────
GEMINI_API_KEY=your-gemini-api-key
HF_TOKEN=your-huggingface-token

CONFLUENCE_URL=https://your-org.atlassian.net/wiki
CONFLUENCE_TOKEN=your-confluence-personal-access-token
CONFLUENCE_SPACE_KEY=TEAM          # the space where pages will be created

# Shared secret — every API call must include X-API-Key: <this value>
ORG_API_KEY=replace-with-a-long-random-string

# ── Optional (defaults work out of the box) ─────────────────────────────────
REDIS_URL=redis://redis:6379/0
```

> **Tip:** generate a strong `ORG_API_KEY` with `openssl rand -hex 32`.

---

## Run

```bash
docker compose up --build
```

First startup takes a few minutes while Docker:
- Builds the backend image (installs ffmpeg + Python packages)
- Builds the frontend image (runs `npm ci` + Vite build)
- Pulls Redis

On **subsequent starts** (images already built, model cache warm):

```bash
docker compose up
```

To rebuild after a code change:

```bash
docker compose up --build backend worker   # rebuild only changed services
```

---

## Access

| URL | What |
|---|---|
| **http://localhost:3000** | Memora web app |
| http://localhost:8000/docs | FastAPI interactive API docs |
| http://localhost:8000/health | Liveness probe (returns `{"status":"ok"}`) |

> The Whisper large-v3 model (~3 GB) is downloaded into a Docker named volume on the **first transcription job**. Subsequent jobs reuse the cached weights.

---

## Usage

1. Open **http://localhost:3000** in your browser.
2. Drag and drop your meeting recording (or click to browse).
3. Optionally enter a meeting title.
4. Click **Process Meeting** — the job is queued immediately and you are taken to the progress view.
5. Watch the four-step progress indicator:
   - **Audio Extracted** — ffmpeg converts your file to 16 kHz WAV
   - **Transcribed** — Whisper converts speech to text
   - **AI Processing** — Gemini extracts structured data
   - **Published** — Notes are posted to Confluence
6. When complete, the result view shows the full structured output. Use the **Open in Confluence** button to view the published page.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Docker host                                                │
│                                                             │
│  ┌──────────┐    /api/*     ┌──────────┐                   │
│  │ frontend │──────────────▶│ backend  │ :8000             │
│  │  nginx   │ :3000         │ FastAPI  │                   │
│  └──────────┘               └─────┬────┘                   │
│                                   │ Celery task             │
│                             ┌─────▼────┐   ┌────────────┐  │
│                             │  worker  │   │   redis    │  │
│                             │  Celery  │◀──│  (broker)  │  │
│                             └──────────┘   └────────────┘  │
│                                   │                         │
│                             ┌─────▼──────────────────────┐  │
│                             │  /app/data  (named volume)  │  │
│                             │    uploads/  jobs/  db/     │  │
│                             └────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Volumes

| Volume | Contents |
|---|---|
| `app_data` | Uploaded files (`uploads/`), job transcripts & results (`jobs/`), SQLite DB (`db/`) |
| `whisper_cache` | Whisper large-v3 model weights (~3 GB, downloaded on first use) |
| `huggingface_cache` | pyannote.audio model weights |

---

## Swapping backends

**PostgreSQL** — set `DATABASE_URL=postgresql://user:pass@host/db` in `.env`. No code changes needed.

**S3 storage** — implement `S3Storage(StorageBackend)` in `backend/storage.py` and update the `get_storage()` factory.

**SSO / OIDC** — replace `APIKeyMiddleware` in `backend/auth.py` with an OIDC middleware. The `app.add_middleware(...)` call in `main.py` stays the same.

---

## Stopping

```bash
docker compose down           # stop containers, keep volumes
docker compose down -v        # stop containers AND delete all data volumes
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Worker exits immediately | Check `HF_TOKEN` is set — pyannote requires it to download the model |
| Jobs stuck in `transcribing` | Worker may still be downloading Whisper large-v3 — check `docker compose logs worker` |
| Confluence returns 403 | Confirm the PAT has *Create Page* permission in the target space |
| `GEMINI_API_KEY` errors | Ensure the key is enabled for Gemini 2.5 Flash in Google AI Studio |
| Port 3000 already in use | Change `"3000:80"` in `docker-compose.yml` to `"3001:80"` (or any free port) |
