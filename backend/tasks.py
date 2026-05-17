"""
Celery task definitions for Memora.
process_recording is the single entry point queued by the upload endpoint.
It runs three sequential steps: audio extraction → Whisper transcription → agent.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

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

OPENAI_AUDIO_SIZE_LIMIT = 24 * 1024 * 1024  # 24 MB (API limit is 25 MB)


# ---------------------------------------------------------------------------
# Public Celery task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="tasks.process_recording")
def process_recording(self, job_id: str) -> None:
    """
    Orchestrate the full recording pipeline for one job.

    Step 1 — Audio extraction (ffmpeg):
        Convert upload to 16 kHz mono WAV.  Status → EXTRACTING_AUDIO.
    Step 2 — Transcription (OpenAI Whisper API):
        Transcribe WAV, write jobs/{job_id}/transcript.json.  Status → PROCESSING.
    Step 3 — Agent (Gemini extraction + Confluence publish):
        Call run_agent(job_id) from agent.py.  Status → PUBLISHING → DONE (set inside run_agent).

    Each step is wrapped independently.  Any failure marks the job FAILED with a
    descriptive error_message and returns early — there is no automatic retry,
    since these are long-running CPU/network operations that are unlikely to
    succeed on an immediate retry.
    """
    from database import JobStatus, SessionLocal, get_job, update_job_status

    # ------------------------------------------------------------------ setup
    db = SessionLocal()
    try:
        job = get_job(db, job_id)           # raises ValueError if missing
        storage_path = job.storage_path
        source_url = job.source_url
        output_type = job.output_type or "detailed"
        publish_to_confluence = job.publish_to_confluence if job.publish_to_confluence is not None else True
        custom_instructions = job.custom_instructions or ""
        confluence_destination = {
            "space_key":      job.confluence_space_key or "",
            "parent_page_id": job.confluence_parent_page_id or "",
            "page_title":     job.confluence_page_title or "",
        }
        context_text = job.context_text or ""
        # screenshots_enabled = job.screenshots_enabled  # §4.4 TODO
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

    # ------------------------------------------------- Step 0: URL download
    if not storage_path and source_url:
        db = SessionLocal()
        try:
            update_job_status(db, job_id, JobStatus.DOWNLOADING)
        finally:
            db.close()

        try:
            downloader = get_downloader()
            upload_path = downloader.download(source_url, job_id, Path(settings.upload_dir))
            storage_path = upload_path.name
            logger.info("[%s] Download complete: %s", job_id, upload_path)
        except Exception as exc:
            logger.exception("[%s] Download failed", job_id)
            _mark_failed_new_session(job_id, f"Download failed: {exc}")
            return

        # Persist the storage_path so the rest of the pipeline can find the file.
        db = SessionLocal()
        try:
            from database import get_job as _get_job
            j = _get_job(db, job_id)
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

    job_dir = Path(settings.jobs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- Step 1: audio
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

    # ------------------------------------------------------- Step 2: transcribe
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

    # Step 3b: Screenshot capture (screenshots_enabled) — §4.4 TODO
    # if screenshots_enabled:
    #     from screenshot_capture import capture_screenshots
    #     capture_screenshots(job_id, upload_path, job_dir, transcript_data)

    # --------------------------------------------------------- Step 3: agent
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

    Raises:
        RuntimeError: If the audio exceeds the 25 MB API limit.
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

    file_size = audio_path.stat().st_size
    if file_size > OPENAI_AUDIO_SIZE_LIMIT:
        size_mb = file_size / 1024 / 1024
        raise RuntimeError(
            f"Audio file is {size_mb:.0f} MB — OpenAI Whisper API limit is 25 MB. "
            "This recording is too long (approx. >50 minutes). "
            "Please split it into shorter segments and resubmit."
        )

    client = OpenAI(api_key=settings.openai_api_key)

    logger.info("[%s] OpenAI Whisper API transcribing %s (%.1f MB)",
                job_id, audio_path.name, file_size / 1024 / 1024)

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
