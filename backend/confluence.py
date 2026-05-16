"""
Confluence REST API v2 integration.
ConfluenceClient handles all HTTP; agent.py calls it for tool implementations.
"""

from __future__ import annotations

import base64
import logging
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MeetingDocument:
    """Structured meeting data produced by the agent, ready to render + publish."""

    title: str
    meeting_type: str                               # sprint-review | planning | incident | general
    summary: str
    attendees: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[dict[str, str]] = field(default_factory=list)   # {owner, task, due}
    open_questions: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """
    Thin wrapper around the Confluence REST API.
    All methods are synchronous (httpx sync client); fine inside Celery workers.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        email: str | None = None,
        space_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.confluence_url).rstrip("/")
        self.space_key = space_key or ""

        # Confluence Cloud uses HTTP Basic auth: base64(email:api_token).
        # Bearer auth is only for Confluence Data Center PATs.
        _email = email or settings.confluence_email
        _token = token or settings.confluence_token
        _creds = base64.b64encode(f"{_email}:{_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {_creds}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Spaces / Pages — used by API endpoints for the destination picker
    # ------------------------------------------------------------------

    def get_spaces(self, limit: int = 100) -> list[dict[str, str]]:
        """Return all accessible Confluence spaces: [{key, name}, ...]."""
        url = f"{self.base_url}/rest/api/space"
        params = {"limit": limit}
        with httpx.Client(timeout=15) as client:
            response = client.get(url, params=params, headers=self._headers)
            response.raise_for_status()
            return [
                {"key": s["key"], "name": s["name"]}
                for s in response.json().get("results", [])
            ]

    def get_pages(self, space_key: str, limit: int = 200) -> list[dict[str, str]]:
        """Return pages in *space_key*: [{id, title}, ...]."""
        url = f"{self.base_url}/rest/api/content"
        params = {
            "spaceKey": space_key,
            "type": "page",
            "limit": limit,
            "status": "current",
        }
        with httpx.Client(timeout=15) as client:
            response = client.get(url, params=params, headers=self._headers)
            response.raise_for_status()
            return [
                {"id": r["id"], "title": r["title"]}
                for r in response.json().get("results", [])
            ]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_pages(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """
        Search for pages in the configured space whose title contains *query*.

        Uses CQL (Confluence Query Language) via the content/search endpoint.

        Args:
            query: Free-text search terms.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``id``, ``title``, ``url``.
            Returns an empty list if the search fails (non-fatal in the agent loop).
        """
        if self.space_key:
            cql = f'space = "{self.space_key}" AND title ~ "{query}" AND type = page'
        else:
            cql = f'title ~ "{query}" AND type = page'
        url = f"{self.base_url}/rest/api/content/search"
        params = {"cql": cql, "limit": limit, "expand": "version"}

        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(url, params=params, headers=self._headers)
                response.raise_for_status()
                results = response.json().get("results", [])
                return [
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "url": self._page_url(r["id"]),
                    }
                    for r in results
                ]
        except Exception:
            logger.exception("search_pages failed for query %r", query)
            return []

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_page(
        self,
        title: str,
        body: str,
        space_key: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, str]:
        """
        Create a new Confluence page.

        Args:
            title: Page title.
            body: Confluence Storage Format body (XHTML).
            space_key: Target space; falls back to ``self.space_key``.
            parent_id: Optional parent page ID.

        Returns:
            Dict with ``page_id`` and ``url``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key or self.space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]

        url = f"{self.base_url}/rest/api/content"
        with httpx.Client(timeout=30) as client:
            response = client.post(url, json=payload, headers=self._headers)
            if not response.is_success:
                logger.error(
                    "create_page failed %s — title=%r space=%r parent=%r body_len=%d — response: %s",
                    response.status_code, title, payload.get("space"), parent_id,
                    len(body), response.text[:800],
                )
            response.raise_for_status()
            page = response.json()
            return {"page_id": page["id"], "url": self._page_url(page)}

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_page(self, page_id: str, title: str, body: str) -> dict[str, str]:
        """
        Update an existing Confluence page, auto-incrementing the version number.

        Args:
            page_id: Numeric Confluence page ID.
            title: New page title (may be unchanged).
            body: New Confluence Storage Format body.

        Returns:
            Dict with ``page_id`` and ``url``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        current_version = self._get_page_version(page_id)

        payload: dict[str, Any] = {
            "id": page_id,
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }

        url = f"{self.base_url}/rest/api/content/{page_id}"
        with httpx.Client(timeout=30) as client:
            response = client.put(url, json=payload, headers=self._headers)
            response.raise_for_status()
            page = response.json()
            return {"page_id": page["id"], "url": self._page_url(page)}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_page_version(self, page_id: str) -> int:
        """Fetch the current version number of a page (required before updating)."""
        url = f"{self.base_url}/rest/api/content/{page_id}"
        with httpx.Client(timeout=15) as client:
            response = client.get(url, params={"expand": "version"}, headers=self._headers)
            response.raise_for_status()
            return response.json()["version"]["number"]

    # ------------------------------------------------------------------
    # Screenshot embedding — §4.4 stub (TODO: implement with ffmpeg capture)
    # ------------------------------------------------------------------

    @staticmethod
    def embed_screenshot(page_body: str, screenshot_path: str, position: int) -> str:  # noqa: ARG004
        """TODO: embed screenshot into page_body at the given position."""
        return page_body

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _page_url(self, page: dict) -> str:
        # Use the webui link from the API response — it contains the correct
        # space-scoped path (e.g. /wiki/spaces/MPS/pages/393222/Title).
        # Fall back to the bare ID URL only if _links is missing.
        webui = page.get("_links", {}).get("webui", "")
        if webui:
            return f"{self.base_url}{webui}"
        return f"{self.base_url}/pages/{page['id']}"
