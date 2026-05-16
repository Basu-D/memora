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

import html
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import google.generativeai as genai

from config import settings
from confluence import ConfluenceClient, MeetingDocument

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 10      # hard cap on tool-call rounds per job
MEETING_TYPES = frozenset({"sprint-review", "planning", "incident", "general"})

# ---------------------------------------------------------------------------
# System prompt (used for both phases)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a meeting documentation agent. "
    "Extract information precisely. Only include what is explicitly stated."
)

# ---------------------------------------------------------------------------
# Phase 1 — extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Analyse the meeting transcript below and return a single JSON object.
Do not include markdown fences — return raw JSON only.

Required fields:
{{
  "meeting_type": "<one of: sprint-review | planning | incident | general>",
  "title": "<concise page title, include date if mentioned>",
  "attendees": ["<name>", ...],
  "summary": "<2–4 sentence meeting summary>",
  "decisions": ["<decision made>", ...],
  "action_items": [
    {{"owner": "<name or empty string>", "task": "<description>", "due": "<date or TBD>"}},
    ...
  ],
  "open_questions": ["<unresolved question>", ...],
  "highlights": ["<key moment or notable point>", ...]
}}

Rules:
- meeting_type must be exactly one of the four values listed.
- Only include content that is explicitly stated; never invent information.
- If an action item's owner or due date is not stated, use "" and "TBD" respectively.
- decisions, open_questions, and highlights may be empty arrays.

Transcript:
{transcript}
"""

# ---------------------------------------------------------------------------
# Phase 2 — tool-loop prompt
# ---------------------------------------------------------------------------

_ACTION_PROMPT = """\
You are publishing meeting notes to Confluence.  Follow these steps in order:

1. Call search_confluence with a short query derived from the meeting title to
   check whether a page about this meeting already exists.

2. Based on the search results:
   - If a very similar page is found → call update_confluence_page with its ID.
   - Otherwise → call create_confluence_page in space "{space_key}".
   Use the page body provided below exactly as-is.

3. Call flag_incomplete_action_items with the action items listed below.

Meeting title : {title}
Space key     : {space_key}

--- PAGE BODY (Confluence Storage Format) ---
{body}
--- END PAGE BODY ---

Action items to validate:
{action_items_json}
"""

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
# Confluence Storage Format templates (one per meeting type)
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    """HTML-escape a string for safe embedding in Confluence Storage Format."""
    return html.escape(str(text), quote=False)


def _ul(items: list[str]) -> str:
    if not items:
        return "<p><em>None recorded.</em></p>"
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def _action_table(items: list[dict]) -> str:
    if not items:
        return "<p><em>No action items recorded.</em></p>"
    rows = "".join(
        f"<tr><td>{_e(i.get('owner',''))}</td>"
        f"<td>{_e(i.get('task',''))}</td>"
        f"<td>{_e(i.get('due','TBD'))}</td></tr>"
        for i in items
    )
    return (
        "<table><thead>"
        "<tr><th>Owner</th><th>Task</th><th>Due</th></tr>"
        "</thead><tbody>" + rows + "</tbody></table>"
    )


def _render_sprint_review(d: MeetingDocument) -> str:
    return f"""
<h2>Sprint Summary</h2>
<p>{_e(d.summary)}</p>

<h2>Attendees</h2>
<p>{_e(', '.join(d.attendees)) if d.attendees else '<em>Not recorded.</em>'}</p>

<h2>Highlights &amp; Demo Notes</h2>
{_ul(d.highlights)}

<h2>Decisions</h2>
{_ul(d.decisions)}

<h2>Action Items</h2>
{_action_table(d.action_items)}

<h2>Open Questions</h2>
{_ul(d.open_questions)}
""".strip()


def _render_planning(d: MeetingDocument) -> str:
    return f"""
<h2>Planning Summary</h2>
<p>{_e(d.summary)}</p>

<h2>Attendees</h2>
<p>{_e(', '.join(d.attendees)) if d.attendees else '<em>Not recorded.</em>'}</p>

<h2>Goals &amp; Commitments</h2>
{_ul(d.highlights)}

<h2>Decisions</h2>
{_ul(d.decisions)}

<h2>Action Items</h2>
{_action_table(d.action_items)}

<h2>Open Questions &amp; Risks</h2>
{_ul(d.open_questions)}
""".strip()


def _render_incident(d: MeetingDocument) -> str:
    return f"""
<ac:structured-macro ac:name="warning" ac:schema-version="1">
  <ac:rich-text-body>
    <p><strong>Incident Review</strong> — {_e(d.title)}</p>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>Incident Summary</h2>
<p>{_e(d.summary)}</p>

<h2>Attendees</h2>
<p>{_e(', '.join(d.attendees)) if d.attendees else '<em>Not recorded.</em>'}</p>

<h2>Timeline &amp; Impact</h2>
{_ul(d.highlights)}

<h2>Root Cause &amp; Resolution</h2>
{_ul(d.decisions)}

<h2>Follow-up Actions</h2>
{_action_table(d.action_items)}

<h2>Open Questions</h2>
{_ul(d.open_questions)}
""".strip()


def _render_general(d: MeetingDocument) -> str:
    return f"""
<h2>Summary</h2>
<p>{_e(d.summary)}</p>

<h2>Attendees</h2>
<p>{_e(', '.join(d.attendees)) if d.attendees else '<em>Not recorded.</em>'}</p>

<h2>Key Points</h2>
{_ul(d.highlights)}

<h2>Decisions</h2>
{_ul(d.decisions)}

<h2>Action Items</h2>
{_action_table(d.action_items)}

<h2>Open Questions</h2>
{_ul(d.open_questions)}
""".strip()


_TEMPLATE_MAP: dict[str, Any] = {
    "sprint-review": _render_sprint_review,
    "planning":      _render_planning,
    "incident":      _render_incident,
    "general":       _render_general,
}


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

    def run(self, transcript: str, job_id: str) -> dict[str, Any]:
        """
        Execute the full two-phase pipeline.

        Args:
            transcript: Plain-text transcript (output of Whisper).
            job_id: Used only for logging context.

        Returns:
            Result dict suitable for writing to result.json.

        Raises:
            Any exception from either phase propagates to the caller
            (tasks.process_recording), which marks the job FAILED.
        """
        if settings.mock_agent:
            logger.info("[%s] MOCK agent — skipping Gemini API", job_id)
            return {
                "meeting_type":            "sprint-review",
                "title":                   "Sprint 42 Review (mock)",
                "attendees":               ["Alice", "Bob", "Carol"],
                "summary":                 "The team reviewed sprint 42 completions and discussed blockers. The mobile release was deferred due to the pending payment integration.",
                "decisions":               ["Defer mobile release to next sprint", "Bob to follow up with payment vendor by Friday"],
                "action_items":            [
                    {"owner": "Bob",   "task": "Follow up with payment vendor",        "due": "2026-05-20"},
                    {"owner": "Carol", "task": "Document current integration status",  "due": "2026-05-18"},
                ],
                "open_questions":          ["Will vendor deliver API keys before next sprint?"],
                "highlights":              ["Auth feature completed", "Dashboard redesign shipped"],
                "confluence_url":          "",
                "page_action":             "skipped (mock)",
                "incomplete_action_items": [],
                "mocked":                  True,
            }

        logger.info("[%s] Phase 1 — structured extraction", job_id)
        extracted = self._extract_structured(transcript, job_id)

        meeting_type = extracted.get("meeting_type", "general")
        if meeting_type not in MEETING_TYPES:
            logger.warning("[%s] Unknown meeting_type %r → falling back to 'general'", job_id, meeting_type)
            meeting_type = "general"

        document = MeetingDocument(
            title=extracted.get("title", "Meeting Notes"),
            meeting_type=meeting_type,
            summary=extracted.get("summary", ""),
            attendees=extracted.get("attendees", []),
            decisions=extracted.get("decisions", []),
            action_items=extracted.get("action_items", []),
            open_questions=extracted.get("open_questions", []),
            highlights=extracted.get("highlights", []),
        )

        render_fn = _TEMPLATE_MAP[meeting_type]
        page_body = render_fn(document)
        logger.info("[%s] Template selected: %s (%d chars)", job_id, meeting_type, len(page_body))

        logger.info("[%s] Phase 2 — Confluence tool loop", job_id)
        loop_result = self._run_tool_loop(document, page_body, job_id)

        return {
            "meeting_type":              meeting_type,
            "title":                     document.title,
            "attendees":                 document.attendees,
            "summary":                   document.summary,
            "decisions":                 document.decisions,
            "action_items":              document.action_items,
            "open_questions":            document.open_questions,
            "highlights":                document.highlights,
            "confluence_url":            loop_result.confluence_url,
            "page_action":               loop_result.page_action,
            "incomplete_action_items":   loop_result.incomplete_action_items,
        }

    # ------------------------------------------------------------------
    # Phase 1 — structured extraction
    # ------------------------------------------------------------------

    def _extract_structured(self, transcript: str, job_id: str) -> dict[str, Any]:
        """
        Ask Gemini to parse the transcript and return a structured JSON dict.

        Falls back to safe empty defaults on any parse failure so Phase 2
        can still proceed (the page will just have less content).
        """
        prompt = _EXTRACTION_PROMPT.format(transcript=transcript)
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

    def _run_tool_loop(
        self,
        document: MeetingDocument,
        page_body: str,
        job_id: str,
    ) -> _ToolLoopResult:
        """
        Start a Gemini chat with the 4 tools, feed the action prompt, then
        execute tool calls until the model stops or MAX_TOOL_TURNS is reached.
        """
        ctx = _ToolLoopResult()

        prompt = _ACTION_PROMPT.format(
            title=document.title,
            space_key=settings.confluence_space_key,
            body=page_body,
            action_items_json=json.dumps(document.action_items, ensure_ascii=False, indent=2),
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

def run_agent(job_id: str) -> None:
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
    result = agent.run(transcript=full_text, job_id=job_id)

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
