"""
FastAPI application — upload endpoint, job status, and result retrieval.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import APIKeyMiddleware
from config import settings
from database import JobStatus, create_job, get_db, get_job, init_db, update_job_status
from tasks import process_recording

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 500 * 1024 * 1024          # 500 MB
CHUNK_SIZE = 256 * 1024                        # 256 KB read chunks
ALLOWED_EXTENSIONS = {".mp4", ".mp3", ".webm", ".wav", ".m4a"}
ALLOWED_CONTENT_TYPES = {
    "video/mp4",
    "audio/mpeg",
    "audio/mp3",
    "video/webm",
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/x-m4a",
}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Memora API",
    description="Converts meeting recordings into structured Confluence documentation.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(APIKeyMiddleware)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Memora API ready — uploads dir: %s", settings.upload_dir)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------

@app.post("/upload", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
async def upload_meeting(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    Accept a meeting recording, persist it, create a job record, and queue processing.

    - Allowed formats: .mp4 .mp3 .webm .wav .m4a
    - Maximum size: 500 MB (enforced by streaming — Content-Length is not trusted)
    - Returns immediately with {job_id, status} — poll /status/{job_id} for progress
    """
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()

    # --- Extension check (first line of defence; MIME checked below) --------
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # --- MIME type check (second line of defence) ---------------------------
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type '{content_type}'.",
        )

    # --- Stream to disk, enforcing the 500 MB cap ---------------------------
    file_id = str(uuid.uuid4())
    storage_filename = f"{file_id}{ext}"           # e.g. "a1b2c3d4....mp4"
    dest_path = Path(settings.upload_dir) / storage_filename

    bytes_written = 0
    try:
        with dest_path.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds the 500 MB limit.",
                    )
                out.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)   # clean up partial file
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        logger.exception("Unexpected error while saving upload")
        raise HTTPException(status_code=500, detail="Failed to save file.") from exc

    if bytes_written == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    # --- Create DB record ---------------------------------------------------
    job = create_job(db, filename=original_name, storage_path=storage_filename)

    # --- Queue Celery task --------------------------------------------------
    process_recording.delay(job.id)

    logger.info("Job %s queued — file: %s (%d bytes)", job.id, storage_filename, bytes_written)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job.id, "status": job.status.value},
    )


# ---------------------------------------------------------------------------
# POST /upload-url
# ---------------------------------------------------------------------------

class UrlSubmitRequest(BaseModel):
    url: str
    title: str = ""


@app.post("/upload-url", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
async def upload_from_url(
    body: UrlSubmitRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    Submit a publicly accessible recording URL for processing.

    The file is downloaded inside the Celery worker (not here), so this
    returns immediately.  Poll /status/{job_id} for progress.

    Supported sources: any URL yt-dlp can handle — direct file links,
    Zoom cloud recordings, Vimeo, YouTube, etc.
    """
    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL must use http or https.",
        )

    # Derive a human-readable display name from the URL path.
    url_path = parsed.path.rstrip("/")
    display_name = Path(url_path).name or "recording"

    # If the user supplied a title, use that as the display filename.
    if body.title.strip():
        display_name = body.title.strip()

    job = create_job(db, filename=display_name, source_url=body.url)
    process_recording.delay(job.id)

    logger.info("Job %s queued from URL: %s", job.id, body.url)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job.id, "status": job.status.value},
    )


# ---------------------------------------------------------------------------
# GET /status/{job_id}
# ---------------------------------------------------------------------------

@app.get("/status/{job_id}", tags=["jobs"])
async def job_status(job_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """
    Return the current processing status of a job.

    Poll this until status is 'done' or 'failed'. Response shape:
    {
        "job_id": "...",
        "status": "transcribing",          # one of the JobStatus values
        "filename": "meeting.mp4",
        "created_at": "2026-05-16T...",
        "updated_at": "2026-05-16T...",
        "error_message": null              # non-null only when status == "failed"
    }
    """
    try:
        job = get_job(db, job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found.")

    return JSONResponse(content={
        "job_id": job.id,
        "status": job.status.value,
        "filename": job.filename,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "error_message": job.error_message,
    })


# ---------------------------------------------------------------------------
# GET /result/{job_id}
# ---------------------------------------------------------------------------

@app.get("/result/{job_id}", tags=["jobs"])
async def job_result(job_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """
    Return the full result of a completed job.

    Returns 404 if the job does not exist.
    Returns 409 if the job has not reached 'done' yet (including 'failed').
    Response shape when done:
    {
        "job_id": "...",
        "status": "done",
        "confluence_url": "https://...",
        "result": {                        # parsed from result_json
            "title": "...",
            "summary": "...",
            "action_items": [...],
            "decisions": [...],
            "attendees": [...]
        }
    }
    """
    try:
        job = get_job(db, job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.status == JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Job failed.", "error": job.error_message},
        )

    if job.status != JobStatus.DONE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Job is not complete yet.", "status": job.status.value},
        )

    try:
        result_data = json.loads(job.result_json) if job.result_json else None
    except json.JSONDecodeError:
        logger.error("Corrupt result_json for job %s", job_id)
        result_data = None

    return JSONResponse(content={
        "job_id": job.id,
        "status": job.status.value,
        "confluence_url": job.confluence_url,
        "result": result_data,
    })


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/download
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}/download", tags=["jobs"])
async def download_job_docx(
    job_id: str,
    api_key: str | None = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Generate and stream a .docx for a completed job.

    The API key may be supplied as the ``api_key`` query param because this
    endpoint is opened via a browser anchor click (no header injection possible).
    """
    try:
        job = get_job(db, job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.status == JobStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job failed.")

    if job.status != JobStatus.DONE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not complete yet.",
        )

    try:
        result = json.loads(job.result_json) if job.result_json else {}
    except json.JSONDecodeError:
        result = {}

    docx_bytes = _build_docx(result)

    raw_title = result.get("title", "meeting-notes")
    safe = re.sub(r"[^\w\s-]", "", raw_title).strip()
    safe = re.sub(r"\s+", "-", safe) or "meeting-notes"
    filename = f"{safe}.docx"

    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# .docx builder
# ---------------------------------------------------------------------------

def _build_docx(result: dict) -> bytes:
    """Build a Word document from the agent result dict and return raw bytes."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()

    # Title
    doc.add_heading(result.get("title") or "Meeting Notes", level=0)

    # Meta line: type + attendees
    meeting_type = result.get("meeting_type", "")
    attendees = result.get("attendees") or []
    meta_parts = []
    if meeting_type:
        meta_parts.append(meeting_type.replace("-", " ").title())
    if attendees:
        meta_parts.append("Attendees: " + ", ".join(attendees))
    if meta_parts:
        p = doc.add_paragraph()
        run = p.add_run("  |  ".join(meta_parts))
        run.italic = True
        run.font.size = Pt(10)

    # Summary
    summary = result.get("summary", "")
    if summary:
        doc.add_heading("Summary", level=1)
        doc.add_paragraph(summary)

    # Decisions
    decisions = result.get("decisions") or []
    if decisions:
        doc.add_heading("Decisions", level=1)
        for i, text in enumerate(decisions, 1):
            doc.add_paragraph(f"{i}. {text}")

    # Action Items — table
    action_items = result.get("action_items") or []
    if action_items:
        doc.add_heading("Action Items", level=1)
        tbl = doc.add_table(rows=1, cols=3)
        tbl.style = "Table Grid"
        hdr = tbl.rows[0].cells
        hdr[0].text = "Owner"
        hdr[1].text = "Task"
        hdr[2].text = "Due"
        for item in action_items:
            row = tbl.add_row().cells
            row[0].text = item.get("owner") or ""
            row[1].text = item.get("task") or ""
            row[2].text = item.get("due") or ""

    # Open Questions
    open_questions = result.get("open_questions") or []
    if open_questions:
        doc.add_heading("Open Questions", level=1)
        for i, text in enumerate(open_questions, 1):
            doc.add_paragraph(f"{i}. {text}")

    # Highlights
    highlights = result.get("highlights") or []
    if highlights:
        doc.add_heading("Highlights", level=1)
        for text in highlights:
            doc.add_paragraph(f"• {text}")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
