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
from pathlib import Path
from typing import Any

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
            description="Create a new Confluence page with the prepared meeting documentation.",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "title": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Page title.",
                    ),
                    "body": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Full page body in Confluence Storage Format.",
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
                required=["title", "body", "space_key"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="update_confluence_page",
            description="Update an existing Confluence page with new meeting documentation.",
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
                    "body": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="New full page body in Confluence Storage Format.",
                    ),
                },
                required=["page_id", "title", "body"],
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
        self._confluence = ConfluenceClient()

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
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        transcript: str,
        job_id: str,
        output_type: str = "detailed",
        publish_to_confluence: bool = True,
    ) -> dict[str, Any]:
        """
        Execute the full two-phase pipeline.

        Args:
            transcript: Plain-text transcript (output of Whisper).
            job_id: Used only for logging context.
            output_type: Controls extraction schema and page format.
            publish_to_confluence: When False, Phase 2 is skipped entirely.

        Returns:
            Result dict suitable for writing to result.json.

        Raises:
            Any exception from either phase propagates to the caller
            (tasks.process_recording), which marks the job FAILED.
        """
        if settings.mock_agent:
            logger.info("[%s] MOCK agent — output_type=%s publish=%s", job_id, output_type, publish_to_confluence)
            return self._mock_result(output_type)

        logger.info("[%s] Phase 1 — extraction (output_type=%s)", job_id, output_type)
        extracted = self._extract_structured(transcript, job_id, output_type=output_type)

        meeting_type = extracted.get("meeting_type", "general") or "general"
        if meeting_type not in MEETING_TYPES:
            logger.warning("[%s] Unknown meeting_type %r → 'general'", job_id, meeting_type)
            meeting_type = "general"

        page_body = render_confluence_body(output_type, extracted, meeting_type)
        logger.info("[%s] Page body rendered: %d chars", job_id, len(page_body))

        action_items = extracted.get("action_items") or []

        if publish_to_confluence:
            logger.info("[%s] Phase 2 — Confluence tool loop", job_id)
            loop_result = self._run_tool_loop_raw(
                title=extracted.get("title", "Meeting Notes"),
                page_body=page_body,
                action_items=action_items,
                job_id=job_id,
            )
        else:
            logger.info("[%s] Confluence publish skipped (publish_to_confluence=False)", job_id)
            incomplete = self._tool_flag_incomplete_action_items(action_items)
            loop_result = _ToolLoopResult(
                page_action="skipped",
                incomplete_action_items=incomplete,
            )

        result: dict[str, Any] = {
            "output_type":             output_type,
            "title":                   extracted.get("title", "Meeting Notes"),
            "confluence_url":          loop_result.confluence_url,
            "page_action":             loop_result.page_action,
            "incomplete_action_items": loop_result.incomplete_action_items,
            "action_items":            action_items,
        }

        if output_type == "detailed":
            result.update({
                "meeting_type":  meeting_type,
                "attendees":     extracted.get("attendees") or [],
                "summary":       extracted.get("summary", ""),
                "decisions":     extracted.get("decisions") or [],
                "open_questions": extracted.get("open_questions") or [],
                "highlights":    extracted.get("highlights") or [],
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
        # action_items: only title + action_items (already in base result)

        return result

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

    def _extract_structured(self, transcript: str, job_id: str, output_type: str = "detailed") -> dict[str, Any]:
        """
        Ask Gemini to parse the transcript and return a structured JSON dict.

        Falls back to safe empty defaults on any parse failure so Phase 2
        can still proceed (the page will just have less content).
        """
        prompt = build_extraction_prompt(transcript, output_type=output_type)
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
    ) -> _ToolLoopResult:
        """
        Start a Gemini chat with the 4 tools, feed the action prompt, then
        execute tool calls until the model stops or MAX_TOOL_TURNS is reached.
        """
        ctx = _ToolLoopResult()

        prompt = build_action_prompt(
            title=title,
            space_key=settings.confluence_space_key,
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

            response = chat.send_message(response_parts)
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
            return self._tool_search_confluence(args.get("query", ""))

        if name == "create_confluence_page":
            result = self._tool_create_confluence_page(
                title=args.get("title", ""),
                body=args.get("body", ""),
                space_key=args.get("space_key", settings.confluence_space_key),
                parent_id=args.get("parent_id", "") or None,
            )
            ctx.page_id = result.get("page_id", "")
            ctx.confluence_url = result.get("url", "")
            ctx.page_action = "created"
            return result

        if name == "update_confluence_page":
            result = self._tool_update_confluence_page(
                page_id=args.get("page_id", ""),
                title=args.get("title", ""),
                body=args.get("body", ""),
            )
            ctx.page_id = result.get("page_id", "")
            ctx.confluence_url = result.get("url", "")
            ctx.page_action = "updated"
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
        body: str,
        space_key: str,
        parent_id: str | None,
    ) -> dict[str, str]:
        """
        Create a Confluence page and return ``{"page_id", "url"}``.

        Raises:
            httpx.HTTPStatusError: Propagates to the tool loop which will log it
            and mark the job FAILED.
        """
        return self._confluence.create_page(
            title=title,
            body=body,
            space_key=space_key or settings.confluence_space_key,
            parent_id=parent_id,
        )

    def _tool_update_confluence_page(
        self,
        page_id: str,
        title: str,
        body: str,
    ) -> dict[str, str]:
        """
        Update an existing Confluence page and return ``{"page_id", "url"}``.

        Raises:
            httpx.HTTPStatusError: Propagates to the tool loop.
        """
        return self._confluence.update_page(
            page_id=page_id,
            title=title,
            body=body,
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

def run_agent(job_id: str, output_type: str = "detailed", publish_to_confluence: bool = True) -> None:
    """
    Orchestrate the full agent pipeline for one job:
      1. Read jobs/{job_id}/transcript.json
      2. Run MeetingAgent (extraction + Confluence publish)
      3. Write jobs/{job_id}/result.json
      4. Update DB status → DONE

    Any exception propagates to tasks.process_recording, which calls
    _mark_failed_new_session and returns.

    Args:
        job_id: UUID matching the Job row and the directory under jobs_dir.
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

    # ----------------------------------------------------------------- run agent
    agent = MeetingAgent()
    result = agent.run(
        transcript=full_text,
        job_id=job_id,
        output_type=output_type,
        publish_to_confluence=publish_to_confluence,
    )

    # ----------------------------------------------------------------- write result.json
    result_path = job_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[%s] result.json written: %s", job_id, result_path)

    # ----------------------------------------------------------------- update DB → DONE
    db = SessionLocal()
    try:
        update_job_status(
            db,
            job_id,
            JobStatus.DONE,
            confluence_url=result.get("confluence_url", ""),
            result_json=json.dumps(result, ensure_ascii=False),
        )
    finally:
        db.close()

    logger.info("[%s] run_agent complete — page %s (%s), %d incomplete action items",
                job_id,
                result.get("confluence_url"),
                result.get("page_action"),
                len(result.get("incomplete_action_items", [])))
