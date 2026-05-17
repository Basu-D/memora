# Memora — Full Requirements Summary for Claude Code
> Hand this document to Claude Code at the start of every session.
> Last updated: May 2026 (rev 2 — Confluence destination + recording auth added)

---

## 1. What is Memora

Memora converts meeting recordings into structured, permanent documentation.
It was built to solve two real problems inside an engineering organisation:
1. Meeting recordings expire after certain days (Organisation/Webex policy) — Memora preserves the content forever.
2. People don't watch long recordings — Memora converts them into readable, structured documents.

The app is being built for real internal use within our team and eventually
the broader organisation. It started as a hackathon project and is being
evolved into a production-grade internal tool.

---

## 2. Current State (Prototype — Working)

The following is already built and working. Do NOT rewrite or remove these.

### Frontend (React + Vite + Tailwind)
- Upload screen with two tabs: **Upload File** and **Paste URL**
- Drag-and-drop file upload zone (accepts .mp4, .mp3, .webm, .wav, .m4a, max 500MB)
- URL input tab (accepts direct links, Zoom, YouTube, Vimeo, etc.)
- Optional meeting title input field
- "Process Meeting" button
- Processing screen with vertical stepper: Downloading → Extracting Audio →
  Transcribing → AI Processing → Publishing
- Result screen showing: meeting type badge, attendees, summary card,
  decisions list, action items table (owner/task/due/status), open questions
- "Download .docx" button and "Open in Confluence" button
- "New recording" back navigation
- Footer note: "Audio is transcribed locally — your recording never leaves your infrastructure."
  NOTE: This footer text is now inaccurate — Whisper is API-based (OpenAI cloud).
  Update footer to: "Your recording is processed securely and never stored beyond your session."

### Backend (Python + FastAPI + Celery + Redis)
- POST /upload — file upload endpoint
- POST /process-url — URL-based recording ingestion
- GET /status/{job_id} — polling endpoint for processing progress
- GET /result/{job_id} — returns structured insights JSON + confluence_url
- GET /download/{job_id} — returns .docx file
- GET /health — health check
- SQLite database via SQLAlchemy (Job model with status tracking)
- Local file storage abstraction (StorageBackend base class → LocalStorage impl)
- API key auth middleware (X-API-Key header vs ORG_API_KEY env var)

### AI Pipeline (Celery worker)
- OpenAI Whisper API for transcription (moved from local Whisper to API — no local model)
- Gemini 2.5 Flash for AI extraction with function calling (agentic)
- Gemini agent tools: search_confluence, create_confluence_page,
  update_confluence_page, flag_incomplete_action_items
- pyannote.audio for speaker diarization (speaker labels in transcript)

### Infrastructure
- Docker Compose: backend + worker + redis + nginx (frontend)
- MOCK_TRANSCRIPTION and MOCK_AGENT flags in .env for dev mode
- .env.example with all required variables

---

## 3. Tech Stack (Final — Do Not Change)

| Layer | Choice |
|---|---|
| Frontend | React + Vite + Tailwind CSS |
| Backend | Python + FastAPI |
| Background jobs | Celery + Redis |
| Transcription | OpenAI Whisper API (cloud, NOT local) |
| Speaker diarization | pyannote.audio |
| AI extraction | Google Gemini 2.5 Flash (function calling) |
| Database | SQLite via SQLAlchemy (extensible to PostgreSQL — only connection string changes) |
| File storage | LocalStorage class implementing StorageBackend ABC (extensible to S3) |
| Output | Confluence REST API v2 + .docx (python-docx) |
| Containerisation | Docker Compose |
| Deployment | Railway (Hobby plan, $5/month) |
| Auth | X-API-Key middleware (extensible to SSO later) |

---

## 4. New Features to Build (Prioritised)

### 4.1 Output Type Selection (HIGH PRIORITY — build now)

Add a **dropdown** on the upload screen (before processing) that lets users
choose what kind of document they want generated.

**Options:**

| Value | Label | What it generates |
|---|---|---|
| `detailed` | Detailed Document (default) | Full document: summary, decisions, action items table, open questions, highlights, full transcript (collapsible). This is the current default behaviour. |
| `mom` | Minutes of Meeting | Structured MoM format: attendees, agenda items discussed, decisions made, action items, next steps. Formal tone. |
| `quick_summary` | Quick Summary | 3–5 bullet points covering what was discussed, decided, and who owns what. No tables. No Confluence page unless user opts in. |
| `action_items` | Action Items Only | Just the action items table: Owner, Task, Deadline. Nothing else. |

**Behaviour:**
- The selected output type is passed to the backend with the job submission.
- The Gemini agent prompt changes based on the output type — different
  extraction schema and instructions per type.
- The Confluence page structure changes per type.
- The result screen renders differently per type (quick_summary = bullets only,
  action_items = table only, etc.)

**Confluence toggle:**
- Add an optional checkbox: "Create Confluence page" (checked by default for
  `detailed` and `mom`, unchecked by default for `quick_summary` and `action_items`)
- If unchecked, the pipeline skips the Confluence publish step entirely.
- The result screen still shows the document preview and .docx download.
- The "Open in Confluence" button is hidden if no Confluence page was created.

---

### 4.2 Optional Custom Instructions (HIGH PRIORITY — build now)

Add an optional **"Custom instructions"** text area on the upload screen,
shown below the output type dropdown, collapsed by default with a
"+ Add custom instructions" toggle.

**Purpose:** Let users tell the AI what to focus on.

**Examples to show as placeholder text:**
- "Focus on technical decisions and skip small talk"
- "Highlight any risks or blockers mentioned"
- "Extract all dates and deadlines mentioned"

**Behaviour:**
- The custom instructions text is passed to the backend with the job.
- The Gemini agent appends the custom instructions to its system prompt.
- If no custom instructions provided, behaviour is unchanged.
- Max 500 characters. Show character counter.

---

### 4.3 Optional Context Input (MEDIUM PRIORITY — design open, build later)

**Design requirement (implement the UI shell and backend field now, wire up later):**

Add an optional **"Meeting context"** section on the upload screen,
collapsed by default with a "+ Add context" toggle. This appears below
custom instructions.

Two sub-options (tabs within the section):
1. **Text context** — free text area: "Describe what this meeting is about
   or provide background" (e.g. "This is a post-mortem for the payment
   gateway outage on May 12th")
2. **Confluence reference** — input field to paste a Confluence page URL.
   The backend will fetch that page's content and provide it to the AI
   as background context.

**For now:** Store the context text and confluence_reference_url in the
Job model. Pass text context to Gemini in the prompt. The Confluence
reference URL fetching can be wired up later — just add the field and
store it. Mark as TODO in the code.

---

### 4.4 Key Moment Screenshot Capture (STRETCH GOAL — design open, build later)

**Concept:** During meetings, presenters often share screens showing diagrams,
slides, or data. These are valuable and currently lost. Memora should detect
these moments and capture screenshots from the video to embed in the document.

**Approach (for when this is built):**
1. Gemini analyses the transcript and identifies timestamps where something
   was visually presented (signals: "as you can see", "let me show you",
   "here's the slide", "look at this", screen share start indicators).
2. ffmpeg extracts video frames at those timestamps.
3. Screenshots are stored in /jobs/{job_id}/screenshots/.
4. Gemini decides where in the document each screenshot belongs
   (e.g. after the relevant decision or discussion point).
5. Screenshots are embedded in the Confluence page and .docx.

**Design requirement (now):**
- The Job model should have a `screenshots_enabled` boolean field (default false).
- The storage abstraction should support saving/retrieving image files per job.
- The Confluence page builder should have a placeholder function
  `embed_screenshot(page_body, screenshot_path, position)` stubbed as TODO.
- The pipeline in tasks.py should have a commented-out step:
  `# Step 3b: Screenshot capture (screenshots_enabled) — TODO`.
- The UI should have a hidden/disabled "Capture presentation screenshots"
  checkbox (disabled with tooltip: "Coming soon") so it can be enabled later.

---

### 4.5 Confluence Destination — User Controls Where to Publish (HIGH PRIORITY — build now)

**Problem with current behaviour:**
The Gemini agent currently decides which Confluence space and parent page to
publish under. This is wrong — the agent has no knowledge of the org's
Confluence structure, team hierarchy, or naming conventions. The user must
own this decision.

**New behaviour:**
When "Create Confluence page" is checked on the upload screen, show a
**Confluence destination section** with three fields:

```
Space:        [ dropdown — shows display names, uses keys internally ]
Parent page:  [ searchable dropdown — scoped to selected space ]
Page title:   [ text input — pre-filled from meeting title, user can edit ]
```

---

#### Display names — users never see space keys

The Confluence API returns both key and display name: `{key: "ENG", name: "Engineering Team"}`.
Always show the display name in the UI. Store and submit the key internally.
Users should never see a space key or page ID anywhere in the interface.
Same for parent pages — show the page title, use the page ID internally.

---

#### Frontend implementation

**On mount:**
1. Call `GET /confluence/spaces` to fetch available spaces.
2. Read `memora_preferences` from localStorage (see Preferences section below).
3. Pre-select the last used space if one exists in preferences.
4. Show a loading spinner while fetching. On failure show:
   "Could not load Confluence spaces — check settings" with a retry button.

**Space selection:**
- Dropdown shows display names only (e.g. "Engineering Team", "Platform Team")
- When user selects a space, call `GET /confluence/pages?space_key=XYZ`
- Pre-select the last used parent page for that space (from preferences)
- If space changes, reset parent page to that space's last used page or blank

**Parent page — searchable select (not a plain dropdown):**
Structure the parent page field as a searchable select with two sections:

```
Parent page
┌─────────────────────────────────┐
│ 🔍 Search pages...              │
├─────────────────────────────────┤
│ Recently used                   │
│   📄 2026 Meeting Notes         │  ← last used for this space
│   📄 Sprint Reviews             │
├─────────────────────────────────┤
│ All pages                       │
│   📄 Architecture Decisions     │
│   📄 Onboarding Docs            │
│   ...                           │
└─────────────────────────────────┘
```

- "Recently used" section shows up to 5 pages previously selected for this space
- "All pages" section shows all pages fetched from Confluence for this space
- Search filters both sections live as user types
- Parent page is optional — if left blank, page is created at space root

**Page title:**
- Pre-filled from the meeting title input field
- User can override it freely

**On successful job submission:**
- Save selected space, parent page, output type, and "create confluence page"
  preference to localStorage under key `memora_preferences`

---

#### localStorage Preferences Schema

Key: `memora_preferences`

```json
{
  "confluence": {
    "last_space": { "key": "ENG", "name": "Engineering Team" },
    "last_parent_page": { "id": "98765", "title": "2026 Meeting Notes" },
    "create_page_default": true,
    "recent_spaces": [
      { "key": "ENG", "name": "Engineering Team" },
      { "key": "PLAT", "name": "Platform Team" }
    ],
    "recent_parent_pages": {
      "ENG": [
        { "id": "98765", "title": "2026 Meeting Notes" },
        { "id": "88123", "title": "Sprint Reviews" }
      ],
      "PLAT": [
        { "id": "77001", "title": "Platform Standups" }
      ]
    }
  },
  "output_type": "detailed"
}
```

Rules:
- Max 5 entries per `recent_spaces` and per space in `recent_parent_pages`
- Add new entry at top, remove oldest if limit exceeded
- Update only on successful job submission, not on every dropdown change
- `recent_parent_pages` is keyed by space key so each space has its own history
- Each user's browser stores their own preferences independently
- When user accounts are added later, this moves to a server-side user
  preferences table — localStorage is the correct solution for now

**Pre-selection on every subsequent visit:**
- Space: pre-select `last_space`
- Parent page: pre-select `last_parent_page` for the pre-selected space
- "Create Confluence page" checkbox: reflect `create_page_default`
- Output type dropdown: reflect `output_type`

---

#### Backend implementation

Add two new endpoints:
- `GET /confluence/spaces` — calls Confluence API, returns:
  `[{"key": "ENG", "name": "Engineering Team"}, ...]`
- `GET /confluence/pages?space_key=XYZ` — calls Confluence API, returns:
  `[{"id": "98765", "title": "2026 Meeting Notes"}, ...]`

Update the Job model to store:
- `confluence_space_key` (string, nullable)
- `confluence_parent_page_id` (string, nullable)
- `confluence_page_title` (string, nullable)

Update job submission endpoints (POST /upload and POST /process-url)
to accept a `confluence_destination` object:
```json
{
  "space_key": "ENG",
  "parent_page_id": "98765",
  "page_title": "Sprint 42 Review — 2026-05-16"
}
```

---

#### Agent behaviour change

The Gemini agent NO LONGER decides where to publish. It receives
`confluence_destination` from the job and uses it directly.

The agent still:
- Checks if a page with the same title already exists under that parent
- Decides whether to create new or update existing
- Builds the page content

Remove placement logic from `search_confluence` tool. Keep duplicate detection only.

---

#### Remove `CONFLUENCE_SPACE_KEY` from .env

No longer needed — users pick the space themselves.
Remove from `.env.example` and all code references.
Keep `CONFLUENCE_URL` and `CONFLUENCE_TOKEN` — those remain server config.

---

### 4.6 Recording URL Authentication — Configurable Strategy (DESIGN NOW, WIRE LATER)

**Problem:**
When Memora moves to org-wide use, Webex recording URLs will require
authentication. The current implementation makes unauthenticated HTTP
requests to download recordings from URLs. This will fail for internal
Webex recordings.

**Design: Strategy pattern via `RECORDING_AUTH_MODE` env variable**

Add a `recording_downloader.py` module with an abstract base class
and three implementations:

```python
class RecordingDownloader(ABC):
    @abstractmethod
    async def download(self, url: str, destination_path: str) -> None: ...

class PublicDownloader(RecordingDownloader):
    """No auth — works for public URLs, YouTube, Vimeo, direct links."""
    ...

class TokenDownloader(RecordingDownloader):
    """Static Bearer token — works for org service account Webex access."""
    # Uses WEBEX_SERVICE_TOKEN env var
    ...

class OAuthDownloader(RecordingDownloader):
    """User-level OAuth — full Webex integration, user's own token."""
    # TODO — implement when Webex OAuth flow is added
    ...
```

**Factory function:**
```python
def get_downloader() -> RecordingDownloader:
    mode = os.getenv("RECORDING_AUTH_MODE", "none")
    if mode == "token":
        return TokenDownloader()
    elif mode == "oauth":
        return OAuthDownloader()
    else:
        return PublicDownloader()
```

**Environment variables to add to .env.example:**
```
RECORDING_AUTH_MODE=none         # none | token | oauth
WEBEX_SERVICE_TOKEN=             # only required if RECORDING_AUTH_MODE=token
```

**Current state (build now):**
- Create `recording_downloader.py` with the ABC and `PublicDownloader` fully implemented
- Create `TokenDownloader` stubbed with a TODO comment
- Create `OAuthDownloader` stubbed with a TODO comment
- Update `tasks.py` to use `get_downloader()` instead of direct HTTP calls
- Add `RECORDING_AUTH_MODE` and `WEBEX_SERVICE_TOKEN` to `.env.example`
  with comments explaining each mode

**When moving to org (future):**
- Set `RECORDING_AUTH_MODE=token` in environment
- Set `WEBEX_SERVICE_TOKEN` to the org service account token
- No code changes required — just env var update

---

## 5. Deployment Requirements

### Target Platform: Railway (Hobby — $5/month)

Since Whisper is now API-based (OpenAI), the backend has no heavy local
model requirements. The entire stack fits comfortably within Railway's
Hobby plan resource limits.

### Services to deploy on Railway:
1. **backend** — FastAPI app (PORT env var, Railway injects this automatically)
2. **worker** — Celery worker (same Docker image, different start command)
3. **redis** — Railway managed Redis plugin (one click)
4. **frontend** — React app served via nginx (or deploy to Railway as static site)

### Environment variables on Railway:
All secrets live in Railway's environment variable UI (not in code or .gitignore'd files).

Required variables:
```
OPENAI_API_KEY=
GEMINI_API_KEY=
CONFLUENCE_URL=
CONFLUENCE_TOKEN=
ORG_API_KEY=
REDIS_URL=                    # Railway injects this automatically if using Railway Redis
DATABASE_URL=                 # SQLite for now: sqlite:////app/data/db/memora.db
UPLOAD_DIR=/app/data/uploads
JOBS_DIR=/app/data/jobs
MOCK_TRANSCRIPTION=false
MOCK_AGENT=false
PORT=8000                     # Railway injects this; FastAPI must bind to 0.0.0.0:$PORT
RECORDING_AUTH_MODE=none      # none | token | oauth
WEBEX_SERVICE_TOKEN=          # only required if RECORDING_AUTH_MODE=token
```

### Railway-specific code changes needed:
1. FastAPI must read PORT from environment: `uvicorn main:app --host 0.0.0.0 --port $PORT`
2. Add a `railway.json` or `Procfile` for each service's start command.
3. Persistent volume for /app/data (uploads, jobs, SQLite db) —
   Railway supports persistent volumes on Hobby plan.
4. CORS must allow the deployed frontend domain (Railway provides a .railway.app subdomain).

### Deployment steps (provide these as a README section):
1. Push code to GitHub (main branch)
2. Create Railway project → "Deploy from GitHub repo"
3. Add Redis plugin from Railway dashboard
4. Set all environment variables in Railway dashboard
5. Add persistent volume mounted at /app/data
6. Deploy backend service (auto-detects Dockerfile)
7. Add worker service (same repo, override start command to Celery)
8. Deploy frontend (same repo, frontend/ subdirectory)
9. Set CORS_ORIGINS in backend to include the Railway-assigned frontend URL
10. Test with a real recording

---

## 6. Extensibility Constraints (Non-negotiable — already in place, keep them)

These architectural decisions are intentional. Do not simplify or remove them:

- **storage.py** must use `StorageBackend` ABC with `LocalStorage` implementation.
  S3Storage will be added later by implementing the same interface.
- **database.py** must use SQLAlchemy. Connection string is the only thing
  that changes when moving to PostgreSQL.
- **auth.py** must be middleware, not inline in route handlers. SSO replaces
  the middleware later without touching routes.
- **recording_downloader.py** must use `RecordingDownloader` ABC with
  `PublicDownloader`, `TokenDownloader`, `OAuthDownloader` implementations.
  Auth mode is selected via `RECORDING_AUTH_MODE` env var. No code changes
  required when switching auth modes — only env var changes.
- **space_index.py** must be a separate module. Handles all index
  building, refreshing, and tree operations. `agent.py` calls
  `space_index.get_or_build(space_key)` only — never calls Confluence
  APIs for structure directly.
- **confluence.py** page builder must be a separate module, not inlined in tasks.
- All AI prompt templates must be in a separate `prompts.py` file,
  not hardcoded inside agent.py or tasks.py.

---

## 7. Things That Are Out of Scope Right Now

- Multi-tenant / multi-org support
- User accounts or login screens
- Webex API direct integration (auto-fetch recordings) — UI tab exists (Paste URL) which handles URLs
- Automatic Slack notifications
- Speaker name mapping (voice → real name)
- Mobile app
- Self-hosted LLM

---

## 8. How to Work on This Codebase

At the start of every Claude Code session, run:
```
Read all files in /backend and /frontend and understand the current
state of the project before taking any tasks.
```

When adding a new feature:
1. Add the database field/migration first
2. Add the backend endpoint / task change
3. Add the prompt change in prompts.py
4. Add the frontend UI last
5. Test with MOCK_TRANSCRIPTION=true and MOCK_AGENT=true first

When something breaks, paste the full stack trace. Do not describe errors
in words — paste the exact output.