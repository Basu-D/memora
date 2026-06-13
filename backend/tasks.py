"""
Celery task definitions for Memora.
process_recording is the entry point for upload/URL-submit flows.
process_webex_recording is the entry point for Webex webhook flows.
Both converge on _run_pipeline() after the source file is on disk.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

# Ensure the app directory is on sys.path when the worker starts from a
# different working directory (e.g. Railway without PYTHONPATH=/app set).
sys.path.insert(0, str(Path(__file__).parent))

from celery import Celery
from celery.utils.log import get_task_logger

from config import settings
from recording_downloader import get_downloader


logger = get_task_logger(__name__)

celery_app = Celery(
    "memora",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
)

VIDEO_EXTENSIONS = {".mp4", ".webm"}

CHUNK_SIZE_LIMIT = 20 * 1024 * 1024   # trigger chunking above 20 MB (API limit is 25 MB)
CHUNK_DURATION   = 600                  # seconds per chunk (10 minutes)
CHUNK_OVERLAP    = 10                   # seconds of overlap to preserve boundary context

WEBEX_API_BASE   = "https://webexapis.com/v1"
WEBEX_MAX_BYTES  = 2 * 1024 * 1024 * 1024  # 2 GB download cap


# ---------------------------------------------------------------------------
# Public Celery tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="tasks.process_recording")
def process_recording(self, job_id: str) -> None:
    """
    Orchestrate the full recording pipeline for upload/URL-submit jobs.

    Step 0 (URL jobs only) — download source file.
    Steps 1-3 — audio extraction → transcription → agent (via _run_pipeline).
    """
    from database import JobStatus, SessionLocal, get_job, update_job_status

    # ── Load routing info from DB ─────────────────────────────────────────────
    db = SessionLocal()
    try:
        job = get_job(db, job_id)
        storage_path = job.storage_path
        source_url   = job.source_url
    except ValueError as exc:
        logger.error("process_recording: job %s not found — %s", job_id, exc)
        db.close()
        return
    except Exception as exc:
        logger.exception("process_recording: unexpected error loading job %s", job_id)
        _mark_failed_new_session(job_id, f"Failed to load job: {exc}")
        db.close()
        return
    finally:
        db.close()

    # ── Step 0: URL download ─────────────────────────────────────────────────
    if not storage_path and source_url:
        db = SessionLocal()
        try:
            update_job_status(db, job_id, JobStatus.DOWNLOADING)
        finally:
            db.close()

        try:
            downloader  = get_downloader()
            upload_path = downloader.download(source_url, job_id, Path(settings.upload_dir))
            storage_path = upload_path.name
            logger.info("[%s] Download complete: %s", job_id, upload_path)
        except Exception as exc:
            logger.exception("[%s] Download failed", job_id)
            _mark_failed_new_session(job_id, f"Download failed: {exc}")
            return

        # Persist the storage_path so the pipeline can find the file.
        db = SessionLocal()
        try:
            j = get_job(db, job_id)
            j.storage_path = storage_path
            db.commit()
        finally:
            db.close()

    elif not storage_path:
        _mark_failed_new_session(job_id, "Job has no storage_path and no source_url.")
        return

    upload_path = Path(settings.upload_dir) / storage_path
    if not upload_path.exists():
        _mark_failed_new_session(job_id, f"Upload file not found: {upload_path}")
        return

    _run_pipeline(job_id, upload_path)


@celery_app.task(bind=True, name="tasks.process_webex_recording")
def process_webex_recording(self, job_id: str, recording_id: str) -> None:
    """
    Download a Webex cloud recording via the Webex API and run the pipeline.

    Triggered by POST /webhooks/webex.  Separate from process_recording so the
    webhook handler can return 200 immediately without waiting for the download.
    """
    from database import JobStatus, SessionLocal, get_job, update_job_status

    # ── Step 0: Download from Webex ──────────────────────────────────────────
    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.DOWNLOADING)
    finally:
        db.close()

    try:
        storage_filename = _download_webex_recording(job_id, recording_id)
    except Exception as exc:
        logger.exception("[%s] Webex download failed (recording=%s)", job_id, recording_id)
        _mark_failed_new_session(job_id, f"Webex download failed: {exc}")
        return

    # Persist storage_path so history / retries can find the file.
    db = SessionLocal()
    try:
        j = get_job(db, job_id)
        j.storage_path = storage_filename
        db.commit()
    except Exception as exc:
        logger.exception("[%s] Failed to persist storage_path after Webex download", job_id)
        _mark_failed_new_session(job_id, f"Failed to save download path: {exc}")
        return
    finally:
        db.close()

    upload_path = Path(settings.upload_dir) / storage_filename
    _run_pipeline(job_id, upload_path)


# ---------------------------------------------------------------------------
# Shared pipeline — steps 1-3 for any job with a file already on disk
# ---------------------------------------------------------------------------

def _run_pipeline(job_id: str, upload_path: Path) -> None:
    """
    Run audio extraction → transcription → agent for a job whose source file
    is already saved at upload_path.

    Called by both process_recording and process_webex_recording.
    Reads agent config (output_type, confluence_destination, …) fresh from DB.
    """
    from database import JobStatus, SessionLocal, get_job, update_job_status

    # Read agent config — done here so callers don't need to pass it through.
    db = SessionLocal()
    try:
        job = get_job(db, job_id)
        output_type           = job.output_type or "detailed"
        publish_to_confluence = job.publish_to_confluence if job.publish_to_confluence is not None else True
        custom_instructions   = job.custom_instructions or ""
        confluence_destination = {
            "space_key":      job.confluence_space_key or "",
            "parent_page_id": job.confluence_parent_page_id or "",
            "page_title":     job.confluence_page_title or "",
        }
        context_text = job.context_text or ""
    except ValueError as exc:
        logger.error("[%s] _run_pipeline: job not found — %s", job_id, exc)
        return
    except Exception:
        logger.exception("[%s] _run_pipeline: error loading job config", job_id)
        _mark_failed_new_session(job_id, "Failed to load job config.")
        return
    finally:
        db.close()

    job_dir = Path(settings.jobs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Audio extraction ─────────────────────────────────────────────
    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.EXTRACTING_AUDIO)
    finally:
        db.close()

    try:
        audio_path = _extract_audio(upload_path, job_dir)
        logger.info("[%s] Audio ready: %s", job_id, audio_path)
    except Exception as exc:
        logger.exception("[%s] Audio extraction failed", job_id)
        _mark_failed_new_session(job_id, f"Audio extraction failed: {exc}")
        return

    # ── Step 2: Transcription ────────────────────────────────────────────────
    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.TRANSCRIBING)
    finally:
        db.close()

    try:
        transcript_path = _transcribe(audio_path, job_dir, job_id)
        logger.info("[%s] Transcript written: %s", job_id, transcript_path)
    except Exception as exc:
        logger.exception("[%s] Transcription failed", job_id)
        _mark_failed_new_session(job_id, f"Transcription failed: {exc}")
        return

    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.PROCESSING)
    finally:
        db.close()

    # ── Step 3: Agent ────────────────────────────────────────────────────────
    try:
        from agent import run_agent
        run_agent(
            job_id,
            output_type=output_type,
            publish_to_confluence=publish_to_confluence,
            custom_instructions=custom_instructions,
            confluence_destination=confluence_destination,
            context_text=context_text,
        )
        logger.info("[%s] Agent complete", job_id)
    except Exception as exc:
        logger.exception("[%s] Agent/publish step failed", job_id)
        _mark_failed_new_session(job_id, f"Agent processing failed: {exc}")
        return

    # ── Email notification (non-fatal) ───────────────────────────────────────
    _try_send_completion_email(job_id)


# ---------------------------------------------------------------------------
# Webex download helper
# ---------------------------------------------------------------------------

def _download_webex_recording(job_id: str, recording_id: str) -> str:
    """
    Fetch recording metadata from the Webex API and stream the file to
    upload_dir.  Returns the storage filename (basename only, e.g.
    "{job_id}.mp4") so the caller can persist it to the DB.

    Auth priority for the actual file download:
      1. temporaryDirectDownloadLinks.audioVideoDownloadLink — pre-signed,
         no Authorization header needed (preferred).
      2. temporaryDirectDownloadLinks.audioDownloadLink — audio-only fallback.
      3. downloadUrl — requires the bearer token.
    """
    import httpx

    bot_token = settings.webex_bot_token
    if not bot_token:
        raise RuntimeError("WEBEX_BOT_TOKEN is not configured.")

    auth_headers = {"Authorization": f"Bearer {bot_token}"}

    # 1. Get recording metadata.
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{WEBEX_API_BASE}/recordings/{recording_id}", headers=auth_headers)
        if r.status_code == 404:
            raise RuntimeError(f"Recording {recording_id!r} not found on Webex.")
        r.raise_for_status()
        details = r.json()

    # 2. Pick the best download URL.
    direct = details.get("temporaryDirectDownloadLinks") or {}
    pre_signed_url = direct.get("audioVideoDownloadLink") or direct.get("audioDownloadLink")
    fallback_url   = details.get("downloadUrl")

    if pre_signed_url:
        download_url     = pre_signed_url
        download_headers = {}          # pre-signed URLs carry auth in query params
    elif fallback_url:
        download_url     = fallback_url
        download_headers = auth_headers
    else:
        raise RuntimeError(f"No download URL in Webex recording metadata for {recording_id!r}.")

    # 3. Stream the file to disk, choosing extension from Content-Type.
    dest_path = Path(settings.upload_dir) / f"{job_id}.mp4"   # default

    written = 0
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)) as client:
        with client.stream("GET", download_url, headers=download_headers, follow_redirects=True) as r:
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "audio/mpeg" in ct or "mp3" in ct:
                dest_path = Path(settings.upload_dir) / f"{job_id}.mp3"
            elif "audio/mp4" in ct or "m4a" in ct:
                dest_path = Path(settings.upload_dir) / f"{job_id}.m4a"
            elif "webm" in ct:
                dest_path = Path(settings.upload_dir) / f"{job_id}.webm"

            with dest_path.open("wb") as fh:
                for chunk in r.iter_bytes(chunk_size=256 * 1024):
                    written += len(chunk)
                    if written > WEBEX_MAX_BYTES:
                        dest_path.unlink(missing_ok=True)
                        raise RuntimeError("Recording exceeds the 2 GB download limit.")
                    fh.write(chunk)

    size_mb = dest_path.stat().st_size / 1024 / 1024
    logger.info("[%s] Webex download complete: %s (%.1f MB)", job_id, dest_path.name, size_mb)
    return dest_path.name


# ---------------------------------------------------------------------------
# Step 1 helper — ffmpeg audio extraction
# ---------------------------------------------------------------------------

def _extract_audio(source: Path, job_dir: Path) -> Path:
    """
    Convert *source* to a 16 kHz mono MP3 file in *job_dir*.

    MP3 at 64 kbps keeps file sizes well below the OpenAI API 25 MB limit
    (a 60-minute recording produces ~29 MB as WAV but only ~29 MB... wait,
    at 64 kbps: 60 min × 60 s × 64000 bits / 8 = ~28 MB). For recordings
    longer than ~50 minutes the caller should split before sending.

    Returns:
        Path to the MP3 file.

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero return code.
        subprocess.TimeoutExpired: If extraction takes longer than 10 minutes.
    """
    audio_path = job_dir / "audio.mp3"

    cmd = [
        "ffmpeg",
        "-y",                    # overwrite without prompting
        "-i", str(source),
        "-vn",                   # drop video stream
        "-acodec", "libmp3lame",
        "-ar", "16000",          # 16 kHz — matches Whisper's expected sample rate
        "-ac", "1",              # mono
        "-b:a", "64k",           # 64 kbps — good quality for speech, small file
        str(audio_path),
    ]

    logger.info("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        stderr_tail = result.stderr[-800:].strip()
        raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr_tail}")

    size_mb = audio_path.stat().st_size / 1024 / 1024
    logger.info("[audio] %s → %.1f MB", audio_path.name, size_mb)
    return audio_path


# ---------------------------------------------------------------------------
# Chunking helpers — used by _transcribe when the file exceeds CHUNK_SIZE_LIMIT
# ---------------------------------------------------------------------------

def _get_audio_duration(audio_path: Path) -> float:
    """Return duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return float(result.stdout.strip())


def _split_into_chunks(audio_path: Path, job_dir: Path, job_id: str) -> list[tuple[Path, float]]:
    """
    Split *audio_path* into overlapping 10-minute MP3 chunks.

    Each chunk is CHUNK_DURATION seconds long with a CHUNK_OVERLAP-second tail
    (so the next chunk's Whisper call has a few seconds of context from the
    boundary).  The overlap region is stripped during stitching.

    Returns:
        List of (chunk_path, chunk_start_seconds) tuples in order.
    """
    chunks_dir = job_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    duration = _get_audio_duration(audio_path)
    logger.info("[%s] Audio duration: %.1f s — splitting into %.0f-s chunks", job_id, duration, CHUNK_DURATION)

    chunks: list[tuple[Path, float]] = []
    chunk_index = 0
    start = 0.0

    while start < duration:
        chunk_path = chunks_dir / f"chunk_{chunk_index:03d}.mp3"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),          # input-side seek (fast, sub-second accuracy)
            "-i", str(audio_path),
            "-t", str(CHUNK_DURATION + CHUNK_OVERLAP),
            "-acodec", "copy",
            str(chunk_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg chunk {chunk_index} failed (rc={result.returncode}): "
                f"{result.stderr[-400:].strip()}"
            )

        size_mb = chunk_path.stat().st_size / 1024 / 1024
        logger.info("[%s] Chunk %d: start=%.1fs size=%.1fMB", job_id, chunk_index, start, size_mb)

        chunks.append((chunk_path, start))
        start += CHUNK_DURATION
        chunk_index += 1

    return chunks


def _transcribe_chunked(audio_path: Path, job_dir: Path, job_id: str, client) -> dict:
    """
    Transcribe a large audio file by splitting it into chunks and stitching
    the results.

    For each chunk after the first, the leading CHUNK_OVERLAP seconds of
    segments are discarded (they overlap with the tail of the previous chunk).
    Segment timestamps are adjusted to absolute positions in the original file.
    """
    chunks = _split_into_chunks(audio_path, job_dir, job_id)

    all_segments: list[dict] = []
    language = "unknown"

    for i, (chunk_path, chunk_start) in enumerate(chunks):
        logger.info("[%s] Whisper chunk %d/%d (offset=%.1fs)", job_id, i + 1, len(chunks), chunk_start)

        with chunk_path.open("rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        if response.language and language == "unknown":
            language = response.language

        # Drop the overlap region from every chunk except the first so we don't
        # double-count the audio that appears at the end of the previous chunk.
        overlap_cutoff = CHUNK_OVERLAP if i > 0 else 0.0

        for seg in (response.segments or []):
            if seg.start < overlap_cutoff:
                continue
            all_segments.append({
                "start": round(chunk_start + seg.start, 3),
                "end":   round(chunk_start + seg.end,   3),
                "text":  seg.text.strip(),
            })

    full_text = " ".join(s["text"] for s in all_segments)
    logger.info("[%s] Chunked transcript: %d chars across %d segments from %d chunk(s)",
                job_id, len(full_text), len(all_segments), len(chunks))

    return {"full_text": full_text, "segments": all_segments, "language": language}


# ---------------------------------------------------------------------------
# Step 2 helper — OpenAI Whisper API transcription
# ---------------------------------------------------------------------------

def _transcribe(audio_path: Path, job_dir: Path, job_id: str) -> Path:
    """
    Transcribe *audio_path* via the OpenAI Whisper API and write transcript.json.

    When settings.mock_transcription is True, skips the API call and writes a
    stub transcript so the rest of the pipeline can be exercised without cost.

    The saved structure matches what agent.py expects:
        {
            "full_text":  "<complete transcript>",
            "segments":   [{"start": 0.0, "end": 5.2, "text": "..."}, ...],
            "language":   "en"
        }

    Files over CHUNK_SIZE_LIMIT are automatically split into 10-minute chunks,
    transcribed separately, and stitched before writing.

    Raises:
        openai.APIError: On API-level failures (propagates to the task).
    """
    if settings.mock_transcription:
        logger.info("[%s] MOCK transcription — skipping OpenAI API", job_id)
        transcript = {
            "full_text": (
                "Alice: Good morning everyone, let's get started with our sprint review. "
                "Bob: We completed the user authentication feature and the dashboard redesign. "
                "Alice: Great work. Any blockers? "
                "Carol: The payment integration is still pending the vendor API keys. "
                "Alice: Bob, can you follow up with the vendor by Friday? "
                "Bob: Sure, I'll send them an email today. "
                "Alice: Carol, please document the current integration status. "
                "Carol: Will do, I'll have it ready by Wednesday. "
                "Alice: We decided to push the mobile release to next sprint due to the payment blocker. "
                "Bob: Agreed, that gives us time to do proper testing. "
                "Alice: Any other questions? Hearing none, we're adjourned."
            ),
            "segments": [
                {"start": 0.0,  "end": 5.0,  "text": "Good morning everyone, let's get started with our sprint review."},
                {"start": 5.0,  "end": 12.0, "text": "We completed the user authentication feature and the dashboard redesign."},
                {"start": 12.0, "end": 20.0, "text": "The payment integration is still pending the vendor API keys."},
                {"start": 20.0, "end": 30.0, "text": "Bob will follow up with the vendor by Friday. Carol will document the integration status by Wednesday."},
                {"start": 30.0, "end": 40.0, "text": "We decided to push the mobile release to next sprint due to the payment blocker."},
            ],
            "language": "en",
        }
        transcript_path = job_dir / "transcript.json"
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[%s] Mock transcript written (%d chars)", job_id, len(transcript["full_text"]))
        return transcript_path

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    file_size = audio_path.stat().st_size
    size_mb = file_size / 1024 / 1024

    if file_size > CHUNK_SIZE_LIMIT:
        logger.info("[%s] Audio is %.1f MB — using chunked transcription", job_id, size_mb)
        transcript = _transcribe_chunked(audio_path, job_dir, job_id, client)
    else:
        logger.info("[%s] OpenAI Whisper API transcribing %s (%.1f MB)",
                    job_id, audio_path.name, size_mb)

        with audio_path.open("rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        transcript = {
            "full_text": response.text.strip(),
            "segments": [
                {
                    "start": round(seg.start, 3),
                    "end":   round(seg.end, 3),
                    "text":  seg.text.strip(),
                }
                for seg in (response.segments or [])
            ],
            "language": response.language or "unknown",
        }

    transcript_path = job_dir / "transcript.json"
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Transcript: %d chars, %d segments, language=%s",
        job_id,
        len(transcript["full_text"]),
        len(transcript["segments"]),
        transcript["language"],
    )
    return transcript_path


# ---------------------------------------------------------------------------
# Retry publish task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="tasks.retry_confluence_publish")
def retry_confluence_publish(self, job_id: str) -> None:
    """
    Re-attempt Confluence publishing for a DONE job with publish_failed=True.

    On success: clears publish_failed and sets confluence_url.
    On failure: restores publish_failed=True so the user can retry again.
    """
    from database import JobStatus, SessionLocal, get_job, update_job_status

    db = SessionLocal()
    try:
        job = get_job(db, job_id)
        result_json_str        = job.result_json
        confluence_space_key   = job.confluence_space_key or ""
        confluence_parent_id   = job.confluence_parent_page_id or ""
    except Exception as exc:
        logger.error("[%s] retry_confluence_publish: failed to load job: %s", job_id, exc)
        return
    finally:
        db.close()

    if not result_json_str:
        logger.error("[%s] retry_confluence_publish: no result_json to publish", job_id)
        return

    try:
        result = json.loads(result_json_str)
    except json.JSONDecodeError:
        logger.error("[%s] retry_confluence_publish: corrupt result_json", job_id)
        return

    try:
        from agent import retry_publish
        pub = retry_publish(job_id, result, confluence_space_key, confluence_parent_id)

        result["confluence_url"] = pub["confluence_url"]
        result["page_action"]    = pub["page_action"]
        updated_json = json.dumps(result, ensure_ascii=False)

        db = SessionLocal()
        try:
            update_job_status(
                db, job_id, JobStatus.DONE,
                confluence_url=pub["confluence_url"],
                result_json=updated_json,
                publish_failed=False,
            )
        finally:
            db.close()

        logger.info("[%s] Retry publish succeeded: %s", job_id, pub["confluence_url"])
        _try_send_completion_email(job_id)

    except Exception as exc:
        logger.exception("[%s] Retry publish failed", job_id)
        db = SessionLocal()
        try:
            update_job_status(db, job_id, JobStatus.DONE, publish_failed=True)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Email notification helper
# ---------------------------------------------------------------------------

def _try_send_completion_email(job_id: str) -> None:
    """
    Send a job-completion email if the job came from a Webex webhook and
    SMTP is configured.  Conditions for sending (all must be true):
      - job.host_email is set (webhook-originated job, not a manual upload)
      - job.confluence_url is set (Confluence publish succeeded)
      - job.user_id resolves to a User with a non-empty email

    Never raises — email failure must not alter the job's final status.
    """
    try:
        from database import SessionLocal, User
        from database import get_job as _get_job
        from notifications import send_job_complete_email

        db = SessionLocal()
        try:
            job = _get_job(db, job_id)

            if not job.host_email:
                return   # manual upload — no host to notify
            if not job.confluence_url:
                return   # publish_failed path — no page to link to

            user: User | None = None
            if job.user_id:
                user = db.query(User).filter(User.id == job.user_id).first()
            if user is None or not user.email:
                return

            send_job_complete_email(job, user, job.confluence_url)
            logger.info("[%s] Completion email sent to %s", job_id, user.email)
        finally:
            db.close()

    except Exception:
        logger.warning("[%s] Email notification failed (non-fatal)", job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Failure helper
# ---------------------------------------------------------------------------

def _mark_failed_new_session(job_id: str, error: str) -> None:
    """Open a fresh DB session, mark the job FAILED, and close the session.

    Uses its own session so it works even when called after a previous session
    has already been closed or is in a broken state.
    """
    from database import JobStatus, SessionLocal, update_job_status
    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.FAILED, error_message=error)
        logger.error("[%s] Marked FAILED: %s", job_id, error)
    except Exception:
        logger.exception("[%s] Could not mark job as FAILED", job_id)
    finally:
        db.close()
