"""
Gemini 2.5 Flash agentic meeting documentation.

Two-phase design
----------------
Phase 1 — Structured extraction (JSON mode, no tools):
    Gemini reads the transcript and returns a JSON object containing
    meeting_type, title, summary, decisions, action_items, open_questions,
    highlights, and attendees.  Temperature 0 for determinism.

Phase 2 — Confluence actions (function-calling tool loop):
    Gemini receives the pre-rendered page body and the 4 tools.
    It searches Confluence, decides create vs update, calls the right tool,
    then calls flag_incomplete_action_items.  We execute each tool call,
    return the result, and loop until Gemini stops calling tools.

Public interface for tasks.py
------------------------------
    run_agent(job_id) — reads transcript.json, runs both phases,
                        writes result.json, marks the job DONE.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import google.generativeai as genai

from config import settings
from confluence import ConfluenceClient
from prompts import (
    SYSTEM_PROMPT,
    TEMPLATE_MAP,
    build_extraction_prompt,
    build_action_prompt,
    render_confluence_body,
)

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 10      # hard cap on tool-call rounds per job
MEETING_TYPES = frozenset({"sprint-review", "planning", "incident", "general"})

_MEETING_TYPE_LABELS: dict[str, str] = {
    "sprint-review": "Sprint Review",
    "planning":      "Planning",
    "incident":      "Incident",
    "general":       "General Meeting",
}

# ---------------------------------------------------------------------------
# Gemini tool declarations
# ---------------------------------------------------------------------------

_TOOLS = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="search_confluence",
            description=(
                "Search Confluence for existing pages similar to the current meeting topic. "
                "Use a short, specific query (2–5 words)."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "query": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Search terms, e.g. 'Sprint 42 review'.",
                    ),
                },
                required=["query"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="create_confluence_page",
            description=(
                "Create a new Confluence page with the prepared meeting documentation. "
                "The page body is managed by the system — do NOT include it in args."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "title": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Page title.",
                    ),
                    "space_key": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Target Confluence space key.",
                    ),
                    "parent_id": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Parent page ID, or empty string for a top-level page.",
                    ),
                },
                required=["title", "space_key"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="update_confluence_page",
            description=(
                "Update an existing Confluence page with new meeting documentation. "
                "The page body is managed by the system — do NOT include it in args."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "page_id": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Numeric ID of the page to update.",
                    ),
                    "title": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Updated page title.",
                    ),
                },
                required=["page_id", "title"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="flag_incomplete_action_items",
            description=(
                "Validate action items and return those that are missing an owner "
                "or a concrete deadline (i.e. owner is empty or due is 'TBD' / empty)."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "action_items": genai.protos.Schema(
                        type=genai.protos.Type.ARRAY,
                        items=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "owner": genai.protos.Schema(type=genai.protos.Type.STRING),
                                "task":  genai.protos.Schema(type=genai.protos.Type.STRING),
                                "due":   genai.protos.Schema(type=genai.protos.Type.STRING),
                            },
                        ),
                        description="All action items extracted from the meeting.",
                    ),
                },
                required=["action_items"],
            ),
        ),
    ]
)

# ---------------------------------------------------------------------------
# Internal result container for the tool loop
# ---------------------------------------------------------------------------

@dataclass
class _ToolLoopResult:
    confluence_url: str = ""
    page_id: str = ""
    page_action: str = ""                                   # "created" | "updated"
    incomplete_action_items: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MeetingAgent
# ---------------------------------------------------------------------------

class MeetingAgent:
    """
    Orchestrates Gemini 2.5 Flash across two phases to produce and publish
    structured meeting documentation.
    """

    def __init__(self) -> None:
        genai.configure(api_key=settings.gemini_api_key)
        # ConfluenceClient is created lazily in _run_tool_loop_raw with the
        # job's destination space key (no global CONFLUENCE_SPACE_KEY anymore).
        self._confluence: ConfluenceClient | None = None
        self._dest_space_key: str = ""
        self._dest_parent_page_id: str = ""
        self._pre_rendered_body: str = ""
        self._decisions: list[dict] = []
        self._on_decision: Callable[[list[dict]], None] | None = None

        # Phase 1: JSON extraction model (no tools, JSON response mode)
        self._extraction_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        # Phase 2: Tool-calling model
        self._action_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=SYSTEM_PROMPT,
            tools=[_TOOLS],
            generation_config=genai.GenerationConfig(
                temperature=0.2,
            ),
        )

    # ------------------------------------------------------------------
    # Decision log
    # ------------------------------------------------------------------

    @property
    def decisions(self) -> list[dict]:
        """Return a copy of all recorded decision entries."""
        return list(self._decisions)

    def _record_decision(self, step: str, decision: str, detail: str) -> None:
        """Append a decision entry and fire the incremental-save callback."""
        entry = {
            "step":      step,
            "decision":  decision,
            "detail":    detail,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._decisions.append(entry)
        if self._on_decision:
            try:
                self._on_decision(list(self._decisions))
            except Exception:
                logger.warning("Decision callback failed for step=%r", step)

    # ------------------------------------------------------------------
    # Public entry points (two-phase: extract then publish)
    # ------------------------------------------------------------------

    def extract(
        self,
        transcript: str,
        job_id: str,
        output_type: str = "detailed",
        custom_instructions: str = "",
        context_text: str = "",
        confluence_destination: dict | None = None,
    ) -> dict[str, Any]:
        """
        Phase 1 — structured extraction only.

        Runs Gemini extraction and returns the full result dict with an empty
        confluence_url.  Stores the pre-rendered page body and publish title on
        self so that publish() can be called immediately after without re-running
        extraction.

        Args:
            transcript: Plain-text transcript (output of Whisper).
            job_id: Used only for logging context.
            output_type: Controls extraction schema and page format.
            custom_instructions: Optional extra guidance for Gemini.
            context_text: Optional background context passed to Gemini.
            confluence_destination: {space_key, parent_page_id, page_title} from job.

        Returns:
            Result dict suitable for immediate storage (confluence_url will be "").
        """
        if settings.mock_agent:
            logger.info("[%s] MOCK agent extract — output_type=%s", job_id, output_type)
            mock = self._mock_result(output_type)
            mock["confluence_url"] = ""
            mock["page_action"] = "pending"
            # Seed publish context so publish() can be called safely in mock mode
            self._pre_rendered_body = ""
            self._publish_title = mock.get("title", "Meeting Notes")
            self._action_items = mock.get("action_items") or []
            self._record_decision("meeting_type", "Sprint Review", "Detected from transcript content")
            _incomplete = self._tool_flag_incomplete_action_items(self._action_items)
            _n = len(_incomplete)
            self._record_decision(
                "flagging",
                f"{_n} item{'s' if _n != 1 else ''} flagged" if _n else "All action items complete",
                "Missing owner or deadline" if _n else "All items have owners and deadlines",
            )
            return mock

        dest = confluence_destination or {}
        page_title_override = dest.get("page_title") or ""

        logger.info("[%s] Phase 1 — extraction (output_type=%s, custom_instructions=%s, context=%s)",
                    job_id, output_type, bool(custom_instructions), bool(context_text))
        extracted = self._extract_structured(
            transcript, job_id,
            output_type=output_type,
            custom_instructions=custom_instructions,
            context_text=context_text,
        )

        meeting_type = extracted.get("meeting_type", "general") or "general"
        if meeting_type not in MEETING_TYPES:
            logger.warning("[%s] Unknown meeting_type %r → 'general'", job_id, meeting_type)
            meeting_type = "general"

        self._record_decision(
            "meeting_type",
            _MEETING_TYPE_LABELS.get(meeting_type, meeting_type.replace("-", " ").title()),
            "Detected from transcript content",
        )

        page_body = render_confluence_body(output_type, extracted, meeting_type)
        logger.info("[%s] Page body rendered: %d chars", job_id, len(page_body))

        action_items = extracted.get("action_items") or []
        publish_title = page_title_override or extracted.get("title", "Meeting Notes")

        # Compute incomplete action items now — deterministic, no Gemini needed.
        incomplete = self._tool_flag_incomplete_action_items(action_items)
        _n = len(incomplete)
        self._record_decision(
            "flagging",
            f"{_n} item{'s' if _n != 1 else ''} flagged" if _n else "All action items complete",
            "Missing owner or deadline" if _n else "All items have owners and deadlines",
        )

        # Persist publish context for the subsequent publish() call.
        self._pre_rendered_body = page_body
        self._publish_title = publish_title
        self._action_items = action_items

        result: dict[str, Any] = {
            "output_type":             output_type,
            "title":                   publish_title,
            "confluence_url":          "",
            "page_action":             "pending",
            "incomplete_action_items": incomplete,
            "action_items":            action_items,
        }

        if output_type == "detailed":
            result.update({
                "meeting_type":   meeting_type,
                "attendees":      extracted.get("attendees") or [],
                "summary":        extracted.get("summary", ""),
                "decisions":      extracted.get("decisions") or [],
                "open_questions": extracted.get("open_questions") or [],
                "highlights":     extracted.get("highlights") or [],
            })
        elif output_type == "mom":
            result.update({
                "meeting_type":  meeting_type,
                "attendees":     extracted.get("attendees") or [],
                "date":          extracted.get("date", ""),
                "agenda_items":  extracted.get("agenda_items") or [],
                "decisions":     extracted.get("decisions") or [],
                "next_steps":    extracted.get("next_steps") or [],
            })
        elif output_type == "quick_summary":
            result.update({
                "bullets": extracted.get("bullets") or [],
            })

        return result

    def publish(
        self,
        job_id: str,
        space_key: str = "",
        parent_page_id: str = "",
    ) -> dict[str, str]:
        """
        Phase 2 — Confluence publish.

        Must be called after extract() (uses state stored on self).  Raises on
        any Confluence error so the caller can handle it independently of
        extraction failures.

        Returns:
            {"confluence_url": str, "page_action": "created"|"updated"|"skipped"}
        """
        if settings.mock_agent:
            logger.info("[%s] MOCK agent publish — skipping Confluence", job_id)
            self._record_decision("duplicate_check", "No duplicate found", "Mock mode — Confluence skipped")
            self._record_decision("placement", "Mock page", "Mock mode — Confluence publish skipped")
            return {"confluence_url": "", "page_action": "skipped (mock)"}

        logger.info("[%s] Phase 2 — Confluence tool loop (space=%r, parent=%r)",
                    job_id, space_key, parent_page_id)
        loop_result = self._run_tool_loop_raw(
            title=self._publish_title,
            page_body=self._pre_rendered_body,
            action_items=self._action_items,
            job_id=job_id,
            space_key=space_key,
            parent_page_id=parent_page_id,
        )
        return {
            "confluence_url": loop_result.confluence_url,
            "page_action":    loop_result.page_action,
        }

    @staticmethod
    def _mock_result(output_type: str) -> dict[str, Any]:
        """Return a type-appropriate stub result for mock mode."""
        base: dict[str, Any] = {
            "output_type":             output_type,
            "confluence_url":          "",
            "page_action":             "skipped (mock)",
            "incomplete_action_items": [],
            "mocked":                  True,
        }
        mock_actions = [
            {"owner": "Bob",   "task": "Follow up with payment vendor",       "due": "2026-05-20"},
            {"owner": "Carol", "task": "Document current integration status", "due": "2026-05-18"},
        ]
        if output_type == "quick_summary":
            base.update({
                "title": "Sprint 42 Review (mock)",
                "bullets": [
                    "Team completed user authentication and dashboard redesign in sprint 42.",
                    "Mobile release deferred to next sprint due to pending payment vendor API keys.",
                    "Bob to follow up with payment vendor by Friday; Carol to document integration status by Wednesday.",
                ],
                "action_items": mock_actions,
            })
        elif output_type == "action_items":
            base.update({
                "title": "Sprint 42 Review — Action Items (mock)",
                "action_items": mock_actions,
            })
        elif output_type == "mom":
            base.update({
                "title":        "Sprint 42 Review — Minutes of Meeting (mock)",
                "meeting_type": "sprint-review",
                "attendees":    ["Alice", "Bob", "Carol"],
                "date":         "2026-05-16",
                "agenda_items": [
                    "Sprint 42 completions: auth feature and dashboard redesign shipped.",
                    "Blockers review: payment integration pending vendor API keys.",
                    "Mobile release timeline discussion.",
                ],
                "decisions":    [
                    "Defer mobile release to next sprint.",
                    "Bob to follow up with payment vendor by Friday.",
                ],
                "action_items": mock_actions,
                "next_steps":   [
                    "Await vendor API key delivery.",
                    "Payment integration end-to-end testing.",
                    "Mobile release planning for next sprint.",
                ],
            })
        else:  # detailed
            base.update({
                "title":         "Sprint 42 Review (mock)",
                "meeting_type":  "sprint-review",
                "attendees":     ["Alice", "Bob", "Carol"],
                "summary":       "The team reviewed sprint 42 completions and discussed blockers. The mobile release was deferred due to the pending payment integration.",
                "decisions":     ["Defer mobile release to next sprint", "Bob to follow up with payment vendor by Friday"],
                "action_items":  mock_actions,
                "open_questions": ["Will vendor deliver API keys before next sprint?"],
                "highlights":    ["Auth feature completed", "Dashboard redesign shipped"],
            })
        return base

    # ------------------------------------------------------------------
    # Phase 1 — structured extraction
    # ------------------------------------------------------------------

    def _extract_structured(
        self,
        transcript: str,
        job_id: str,
        output_type: str = "detailed",
        custom_instructions: str = "",
        context_text: str = "",
    ) -> dict[str, Any]:
        """
        Ask Gemini to parse the transcript and return a structured JSON dict.

        Falls back to safe empty defaults on any parse failure so Phase 2
        can still proceed (the page will just have less content).
        """
        prompt = build_extraction_prompt(
            transcript,
            output_type=output_type,
            custom_instructions=custom_instructions,
            context_text=context_text,
        )
        response = self._extraction_model.generate_content(prompt)
        raw = response.text.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Gemini sometimes wraps JSON in markdown fences despite the instruction
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

            logger.error("[%s] Failed to parse Phase 1 JSON — using empty defaults.\nRaw: %s", job_id, raw[:500])
            return {
                "meeting_type": "general",
                "title": "Meeting Notes",
                "attendees": [],
                "summary": "",
                "decisions": [],
                "action_items": [],
                "open_questions": [],
                "highlights": [],
            }

    # ------------------------------------------------------------------
    # Phase 2 — Confluence tool loop
    # ------------------------------------------------------------------

    def _run_tool_loop_raw(
        self,
        title: str,
        page_body: str,
        action_items: list[dict],
        job_id: str,
        space_key: str = "",
        parent_page_id: str = "",
    ) -> _ToolLoopResult:
        """
        Start a Gemini chat with the 4 tools, feed the action prompt, then
        execute tool calls until the model stops or MAX_TOOL_TURNS is reached.
        """
        # Create the client with the destination space so search_pages scopes correctly.
        self._confluence = ConfluenceClient(space_key=space_key)
        self._dest_space_key = space_key
        self._dest_parent_page_id = parent_page_id
        # Always use our pre-rendered body — ignore whatever body Gemini supplies in the
        # tool call args (Gemini tends to rewrite it, producing invalid Confluence XML).
        self._pre_rendered_body = page_body

        ctx = _ToolLoopResult()

        prompt = build_action_prompt(
            title=title,
            space_key=space_key,
            parent_page_id=parent_page_id,
            body=page_body,
            action_items_json=json.dumps(action_items, ensure_ascii=False, indent=2),
        )

        chat = self._action_model.start_chat()
        response = chat.send_message(prompt)

        for turn in range(MAX_TOOL_TURNS):
            # Collect all function_call parts in this response
            fc_parts = [
                p for p in response.candidates[0].content.parts
                if p.function_call.name   # empty string when not a function call
            ]

            if not fc_parts:
                logger.info("[%s] Tool loop finished after %d turn(s)", job_id, turn)
                break

            # Execute every tool call and collect responses
            response_parts: list[genai.protos.Part] = []
            for part in fc_parts:
                fc = part.function_call
                logger.info("[%s] Tool call: %s(%s)", job_id, fc.name, dict(fc.args))
                result = self._dispatch_tool(fc.name, dict(fc.args), ctx)
                logger.info("[%s] Tool result: %s", job_id, result)

                response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

            try:
                response = chat.send_message(response_parts)
            except genai.types.StopCandidateException as exc:
                # Gemini 2.5 Flash sometimes emits MALFORMED_FUNCTION_CALL when
                # asked to generate large structured arguments (e.g. Confluence XML).
                # If we already created/updated the page this turn, treat it as done.
                # Otherwise, surface the error so the job is marked FAILED.
                logger.warning("[%s] StopCandidateException on turn %d: %s", job_id, turn, exc)
                if ctx.confluence_url:
                    logger.info("[%s] Page already actioned — ignoring exception", job_id)
                    return ctx
                raise
        else:
            logger.warning("[%s] Tool loop hit MAX_TOOL_TURNS (%d)", job_id, MAX_TOOL_TURNS)

        return ctx

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        ctx: _ToolLoopResult,
    ) -> Any:
        """Route a Gemini function call to the correct implementation."""
        if name == "search_confluence":
            result = self._tool_search_confluence(args.get("query", ""))
            if result:
                self._record_decision(
                    "duplicate_check",
                    "Found existing page",
                    result[0].get("title", ""),
                )
            else:
                self._record_decision(
                    "duplicate_check",
                    "No duplicate found",
                    f"Searched for: {args.get('query', '')}",
                )
            return result

        if name == "create_confluence_page":
            _space = args.get("space_key") or self._dest_space_key
            _parent = args.get("parent_id") or self._dest_parent_page_id
            result = self._tool_create_confluence_page(
                title=args.get("title", ""),
                body="",  # always overridden by self._pre_rendered_body
                space_key=_space,
                parent_id=_parent or None,
            )
            ctx.page_id = result.get("page_id", "")
            ctx.confluence_url = result.get("url", "")
            ctx.page_action = "created"
            self._record_decision(
                "placement",
                args.get("title", "New page"),
                (f"Refined from selected parent using space index" if _parent
                 else f"Created at root of space {_space}"),
            )
            return result

        if name == "update_confluence_page":
            result = self._tool_update_confluence_page(
                page_id=args.get("page_id", ""),
                title=args.get("title", ""),
                body="",  # always overridden by self._pre_rendered_body
            )
            ctx.page_id = result.get("page_id", "")
            ctx.confluence_url = result.get("url", "")
            ctx.page_action = "updated"
            self._record_decision(
                "placement",
                args.get("title", "Existing page"),
                f"Updated existing page {args.get('page_id', '')}",
            )
            return result

        if name == "flag_incomplete_action_items":
            flagged = self._tool_flag_incomplete_action_items(
                args.get("action_items", [])
            )
            ctx.incomplete_action_items = flagged
            return flagged

        logger.warning("Unknown tool called by Gemini: %r", name)
        return {"error": f"Unknown tool: {name}"}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _tool_search_confluence(self, query: str) -> list[dict[str, str]]:
        """
        Search Confluence for pages whose title resembles *query*.

        Returns:
            List of ``{"id", "title", "url"}`` dicts (empty list if nothing found
            or the search request fails).
        """
        if not query.strip():
            return []
        results = self._confluence.search_pages(query, limit=5)
        logger.debug("search_confluence(%r) → %d result(s)", query, len(results))
        return results

    def _tool_create_confluence_page(
        self,
        title: str,
        body: str,  # noqa: ARG002 — ignored; we always use self._pre_rendered_body
        space_key: str,
        parent_id: str | None,
    ) -> dict[str, str]:
        """
        Create a Confluence page and return ``{"page_id", "url"}``.

        The `body` argument from Gemini is intentionally ignored — Gemini often
        rewrites the body with invalid Confluence XML.  We always use the
        pre-rendered body stored in ``self._pre_rendered_body``.
        """
        return self._confluence.create_page(
            title=title,
            body=self._pre_rendered_body,
            space_key=space_key,
            parent_id=parent_id,
        )

    def _tool_update_confluence_page(
        self,
        page_id: str,
        title: str,
        body: str,  # noqa: ARG002 — ignored; see above
    ) -> dict[str, str]:
        """
        Update an existing Confluence page and return ``{"page_id", "url"}``.
        Body is always the pre-rendered version, not Gemini's rewrite.
        """
        return self._confluence.update_page(
            page_id=page_id,
            title=title,
            body=self._pre_rendered_body,
        )

    @staticmethod
    def _tool_flag_incomplete_action_items(
        action_items: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """
        Return action items that are missing an owner or a concrete deadline.

        An item is incomplete if:
        - ``owner`` is an empty string or None
        - ``due`` is an empty string, None, or the literal string "TBD"

        Returns:
            List of incomplete item dicts, each with an added ``"missing"`` key
            listing which fields are absent (e.g. ``["owner", "due"]``).
        """
        flagged: list[dict[str, Any]] = []
        for item in action_items:
            missing: list[str] = []
            owner = (item.get("owner") or "").strip()
            due = (item.get("due") or "").strip()

            if not owner:
                missing.append("owner")
            if not due or due.upper() == "TBD":
                missing.append("due")

            if missing:
                flagged.append({**item, "missing": missing})

        return flagged


# ---------------------------------------------------------------------------
# Public entry point called by tasks.process_recording
# ---------------------------------------------------------------------------

def run_agent(
    job_id: str,
    output_type: str = "detailed",
    publish_to_confluence: bool = True,
    custom_instructions: str = "",
    confluence_destination: dict | None = None,
    context_text: str = "",
) -> None:
    """
    Orchestrate the full agent pipeline for one job.

    Step A — Extraction (raises on failure → task marks job FAILED):
        Run Gemini Phase 1, save result_json to DB, mark job DONE.

    Step B — Confluence publish (failure is non-fatal):
        Attempt Phase 2.  On success: update confluence_url.
        On failure: set publish_failed=True but leave status DONE.

    Any exception from Step A propagates to tasks.process_recording,
    which calls _mark_failed_new_session and returns.
    """
    from database import JobStatus, SessionLocal, update_job_status

    job_dir = Path(settings.jobs_dir) / job_id

    # ----------------------------------------------------------------- read transcript
    transcript_path = job_dir / "transcript.json"
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
    full_text: str = transcript_data.get("full_text", "")

    if not full_text.strip():
        raise ValueError("Transcript is empty — nothing to process.")

    logger.info("[%s] run_agent: %d chars, %d segments",
                job_id, len(full_text), len(transcript_data.get("segments", [])))

    dest = confluence_destination or {}
    space_key      = dest.get("space_key") or ""
    parent_page_id = dest.get("parent_page_id") or ""

    # ----------------------------------------------------------------- Step A: extract
    agent = MeetingAgent()

    # During PUBLISHING, fire a DB write after each tool call so the frontend
    # can poll for decisions in real time.  During extract(), result_json isn't
    # saved yet, so the callback short-circuits until it sees a non-null value.
    def _persist_decisions(decisions: list[dict]) -> None:
        from database import get_job as _gj
        _db = SessionLocal()
        try:
            _job = _gj(_db, job_id)
            if not _job.result_json:
                return  # initial save not done yet — decisions will be bundled there
            _current = json.loads(_job.result_json)
            _current["agent_decisions"] = decisions
            update_job_status(_db, job_id, _job.status,
                              result_json=json.dumps(_current, ensure_ascii=False))
        except Exception:
            logger.warning("[%s] Could not persist incremental decisions", job_id)
        finally:
            _db.close()

    agent._on_decision = _persist_decisions

    result = agent.extract(
        transcript=full_text,
        job_id=job_id,
        output_type=output_type,
        custom_instructions=custom_instructions,
        context_text=context_text,
        confluence_destination=confluence_destination,
    )

    # Include extract-phase decisions (meeting_type + flagging) in the initial save.
    result["agent_decisions"] = agent.decisions

    # Save extraction result immediately so it's never lost, but keep the status
    # at PROCESSING — setting DONE here would cause the frontend poller to transition
    # to ResultView before Confluence publishing runs, making the PUBLISHING step
    # invisible in the stepper.
    result_json_str = json.dumps(result, ensure_ascii=False)
    result_path = job_dir / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.PROCESSING, result_json=result_json_str)
    finally:
        db.close()

    logger.info("[%s] Extraction complete — result saved", job_id)

    # ----------------------------------------------------------------- Step B: publish
    if not publish_to_confluence:
        result["page_action"] = "skipped"
        result["agent_decisions"] = agent.decisions
        result_json_str = json.dumps(result, ensure_ascii=False)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        db = SessionLocal()
        try:
            update_job_status(db, job_id, JobStatus.DONE, result_json=result_json_str)
        finally:
            db.close()
        logger.info("[%s] run_agent complete — Confluence publish skipped", job_id)
        return

    db = SessionLocal()
    try:
        update_job_status(db, job_id, JobStatus.PUBLISHING)
    finally:
        db.close()

    try:
        pub = agent.publish(job_id=job_id, space_key=space_key, parent_page_id=parent_page_id)

        result["confluence_url"] = pub["confluence_url"]
        result["page_action"]    = pub["page_action"]
        result["agent_decisions"] = agent.decisions  # all four decisions now present
        result_json_str = json.dumps(result, ensure_ascii=False)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        db = SessionLocal()
        try:
            update_job_status(
                db, job_id, JobStatus.DONE,
                confluence_url=pub["confluence_url"],
                result_json=result_json_str,
                publish_failed=False,
            )
        finally:
            db.close()

        logger.info("[%s] run_agent complete — page %s (%s), %d incomplete action items",
                    job_id, pub["confluence_url"], pub["page_action"],
                    len(result.get("incomplete_action_items", [])))

    except Exception:
        logger.exception("[%s] Confluence publish failed — marking publish_failed", job_id)
        # Preserve whatever decisions were recorded before the failure.
        result["agent_decisions"] = agent.decisions
        result_json_str = json.dumps(result, ensure_ascii=False)
        db = SessionLocal()
        try:
            update_job_status(db, job_id, JobStatus.DONE, publish_failed=True,
                              result_json=result_json_str)
        finally:
            db.close()


def retry_publish(
    job_id: str,
    result: dict[str, Any],
    space_key: str,
    parent_page_id: str,
) -> dict[str, str]:
    """
    Re-attempt Confluence publishing using a stored result dict.

    Called by the retry_confluence_publish Celery task.  Reconstructs the
    page body from the stored result dict, then runs MeetingAgent.publish().

    Returns:
        {"confluence_url": str, "page_action": str}
    """
    output_type  = result.get("output_type", "detailed")
    meeting_type = result.get("meeting_type", "general") or "general"
    title        = result.get("title", "Meeting Notes")
    action_items = result.get("action_items") or []

    page_body = render_confluence_body(output_type, result, meeting_type)

    agent = MeetingAgent()
    agent._pre_rendered_body = page_body
    agent._publish_title     = title
    agent._action_items      = action_items

    return agent.publish(job_id=job_id, space_key=space_key, parent_page_id=parent_page_id)
