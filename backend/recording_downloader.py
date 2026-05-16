"""
Recording download abstraction layer.

Auth mode is selected via the RECORDING_AUTH_MODE environment variable:
  none   → PublicDownloader  (default — unauthenticated, uses yt-dlp)
  token  → TokenDownloader   (static Bearer token, e.g. Webex service account)
  oauth  → OAuthDownloader   (user-level OAuth — not yet implemented)

Adding a new auth mode only requires adding an implementation here and
updating get_downloader(). No changes needed in tasks.py.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class RecordingDownloader(ABC):
    """
    Abstract interface for downloading a recording from a URL to local disk.

    All implementations must handle their own auth and return the Path of
    the downloaded file. Errors should raise RuntimeError with a user-friendly
    message — tasks.py surfaces this as the job's error_message.
    """

    @abstractmethod
    def download(self, url: str, job_id: str, destination_dir: Path) -> Path:
        """
        Download the recording at *url* into *destination_dir*.

        Args:
            url: Publicly or privately accessible recording URL.
            job_id: Used to name the output file (e.g. {job_id}.mp4).
            destination_dir: Directory to write the downloaded file into.

        Returns:
            Path to the downloaded file.

        Raises:
            RuntimeError: With a user-readable message on any failure.
        """
        ...


# ---------------------------------------------------------------------------
# PublicDownloader — unauthenticated, yt-dlp backed (current default)
# ---------------------------------------------------------------------------

class PublicDownloader(RecordingDownloader):
    """
    Downloads recordings from public URLs using yt-dlp.

    Handles direct file links (.mp4, .mp3, .webm …) as well as platform
    URLs that yt-dlp supports (YouTube, Vimeo, Zoom cloud recordings, etc.).
    No authentication is performed — the URL must be publicly accessible.
    """

    def download(self, url: str, job_id: str, destination_dir: Path) -> Path:
        import yt_dlp

        outtmpl = str(destination_dir / f"{job_id}.%(ext)s")

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[ext=mp4]/best",
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "socket_timeout": 30,
            "retries": 3,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(_friendly_download_error(str(exc))) from exc

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            actual_filename = ydl.prepare_filename(info)
        downloaded = Path(actual_filename)

        if not downloaded.exists():
            candidates = sorted(destination_dir.glob(f"{job_id}.*"))
            if not candidates:
                raise RuntimeError(
                    f"yt-dlp reported success but no file found for job {job_id}"
                )
            downloaded = candidates[0]

        logger.info("[%s] Downloaded: %s (%.1f MB)", job_id, downloaded.name,
                    downloaded.stat().st_size / 1024 / 1024)
        return downloaded


# ---------------------------------------------------------------------------
# TokenDownloader — static Bearer token (e.g. Webex org service account)
# ---------------------------------------------------------------------------

class TokenDownloader(RecordingDownloader):
    """
    Downloads recordings using a static Bearer token from WEBEX_SERVICE_TOKEN.

    Suitable for Webex org recordings accessed via a service account token.
    Set RECORDING_AUTH_MODE=token and WEBEX_SERVICE_TOKEN in the environment.

    TODO: Implement when org-wide Webex access is needed.
      1. Use httpx to GET the URL with Authorization: Bearer {token} header.
      2. Stream response to destination_dir / f"{job_id}.{ext}".
      3. Detect content-type to determine extension.
      4. Respect MAX_DOWNLOAD_BYTES — abort if content-length exceeds limit.
    """

    def __init__(self) -> None:
        self._token = os.getenv("WEBEX_SERVICE_TOKEN", "")
        if not self._token:
            raise RuntimeError(
                "RECORDING_AUTH_MODE=token requires WEBEX_SERVICE_TOKEN to be set."
            )

    def download(self, url: str, job_id: str, destination_dir: Path) -> Path:
        raise NotImplementedError(
            "TokenDownloader is not yet implemented. "
            "Set RECORDING_AUTH_MODE=none to use the public downloader."
        )


# ---------------------------------------------------------------------------
# OAuthDownloader — user-level OAuth (future Webex integration)
# ---------------------------------------------------------------------------

class OAuthDownloader(RecordingDownloader):
    """
    Downloads recordings using a per-user OAuth token.

    Suitable for accessing Webex recordings on behalf of individual users.
    Requires a full OAuth 2.0 flow to be implemented first.

    TODO: Implement when user accounts and Webex OAuth are added.
      1. Accept user_id; look up their stored OAuth token.
      2. Refresh the token if expired.
      3. Download the recording with the user's Bearer token.
    """

    def download(self, url: str, job_id: str, destination_dir: Path) -> Path:
        raise NotImplementedError(
            "OAuthDownloader is not yet implemented. "
            "Set RECORDING_AUTH_MODE=none to use the public downloader."
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_downloader() -> RecordingDownloader:
    """
    Return the correct RecordingDownloader based on RECORDING_AUTH_MODE.

    To switch auth modes, change the environment variable — no code change needed.
    """
    mode = os.getenv("RECORDING_AUTH_MODE", "none").strip().lower()
    if mode == "token":
        logger.info("RecordingDownloader: TokenDownloader (RECORDING_AUTH_MODE=token)")
        return TokenDownloader()
    if mode == "oauth":
        logger.info("RecordingDownloader: OAuthDownloader (RECORDING_AUTH_MODE=oauth)")
        return OAuthDownloader()
    logger.info("RecordingDownloader: PublicDownloader (RECORDING_AUTH_MODE=none)")
    return PublicDownloader()


# ---------------------------------------------------------------------------
# Error message helper (shared with PublicDownloader)
# ---------------------------------------------------------------------------

def _friendly_download_error(raw: str) -> str:
    """Convert a raw yt-dlp error string into a user-readable message."""
    r = raw.lower()
    if "sign in" in r or "confirm you're not a bot" in r or "cookies" in r:
        return (
            "YouTube blocked the download — it requires browser sign-in. "
            "Use a direct file URL (.mp4 / .mp3), a Vimeo link, or a Zoom/Google Drive "
            "direct download link instead."
        )
    if "private video" in r or "video unavailable" in r:
        return "The video is private or unavailable. Make sure the link is publicly accessible."
    if "not available in your country" in r or "geo" in r:
        return "This video is geo-restricted and cannot be downloaded from this server's location."
    if "requested format is not available" in r:
        return "No compatible audio/video format was found. Try a direct file link (.mp4 / .mp3)."
    if "extractor error" in r or "keyerror" in r:
        return "The downloader hit a bug with this platform's URL. Try a direct file link instead."
    if "unable to download" in r or "http error" in r or "connection" in r:
        return "Could not reach the URL. Make sure it is publicly accessible and try again."
    if "no such file" in r or "permission denied" in r:
        return "Server could not save the downloaded file. Check storage permissions."
    cleaned = raw
    for prefix in ("ERROR: ", "[youtube] ", "[generic] "):
        cleaned = cleaned.replace(prefix, "")
    return f"Download failed: {cleaned.strip()}"
