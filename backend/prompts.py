"""
All AI prompt templates and Confluence page templates for Memora.

Keeping prompts here (not inside agent.py) means:
- Output types (§4.1) add new prompt variants here without touching agent logic.
- Custom instructions (§4.2) are appended here before sending.
- Prompt changes are easy to review in isolation.
"""

from __future__ import annotations

import html
from typing import Any

from confluence import MeetingDocument


# ---------------------------------------------------------------------------
# Gemini system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a meeting documentation agent. "
    "Extract information precisely. Only include what is explicitly stated."
)


# ---------------------------------------------------------------------------
# Phase 1 — structured extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
Analyse the meeting transcript below and return a single JSON object.
Do not include markdown fences — return raw JSON only.

Required fields:
{{
  "meeting_type": "<one of: sprint-review | sprint-planning | sprint-retrospective | pi-planning | pi-retrospective | backlog-refinement | standup | planning | incident | post-mortem | one-on-one | design-review | architecture-review | stakeholder-update | kick-off | general>",
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

{custom_instructions}Transcript:
{transcript}
"""


# ---------------------------------------------------------------------------
# Output-type specific extraction prompts
# ---------------------------------------------------------------------------

_MOM_EXTRACTION_PROMPT = """\
Analyse the meeting transcript below and return a formal Minutes of Meeting document as JSON.
Do not include markdown fences — return raw JSON only.

Required fields:
{{
  "meeting_type": "<one of: sprint-review | sprint-planning | sprint-retrospective | pi-planning | pi-retrospective | backlog-refinement | standup | planning | incident | post-mortem | one-on-one | design-review | architecture-review | stakeholder-update | kick-off | general>",
  "title": "<formal meeting title, include date if mentioned>",
  "date": "<YYYY-MM-DD if mentioned in the transcript, else empty string>",
  "attendees": ["<name>", ...],
  "agenda_items": [
    "<brief description of each topic discussed — not decisions or actions>",
    ...
  ],
  "decisions": ["<formal decision statement>", ...],
  "action_items": [
    {{"owner": "<name or empty>", "task": "<description>", "due": "<date or TBD>"}}
  ],
  "next_steps": ["<broader follow-up or next step>", ...]
}}

Rules:
- Use formal, professional language throughout.
- agenda_items describe topics discussed, not the decisions or actions themselves.
- decisions are explicitly stated choices or agreements made in the meeting.
- next_steps are broader follow-ups beyond specific assigned tasks.
- Only include what is explicitly stated; never invent information.

{custom_instructions}Transcript:
{transcript}
"""

_QUICK_SUMMARY_EXTRACTION_PROMPT = """\
Analyse the meeting transcript below and return a concise summary as JSON.
Do not include markdown fences — return raw JSON only.

Required fields:
{{
  "title": "<concise meeting title>",
  "bullets": [
    "<3 to 5 short bullet points covering what was discussed, decided, and who owns what>"
  ],
  "action_items": [
    {{"owner": "<name or empty>", "task": "<description>", "due": "<date or TBD>"}}
  ]
}}

Rules:
- Return exactly 3 to 5 items in "bullets". Each must be a single clear sentence.
- Bullets should together cover: main topics, key decisions, and ownership.
- action_items may be an empty array if none were mentioned.
- Only include what is explicitly stated.

{custom_instructions}Transcript:
{transcript}
"""

_ACTION_ITEMS_EXTRACTION_PROMPT = """\
Analyse the meeting transcript below and extract only the action items.
Return a single JSON object. Do not include markdown fences — return raw JSON only.

Required fields:
{{
  "title": "<concise meeting title>",
  "action_items": [
    {{"owner": "<name or empty>", "task": "<clear task description>", "due": "<date or TBD>"}}
  ]
}}

Rules:
- Only include explicit tasks that someone is responsible for completing.
- If owner is not mentioned, use empty string.
- If due date is not mentioned, use "TBD".
- action_items may be an empty array if none were mentioned.

{custom_instructions}Transcript:
{transcript}
"""


def build_extraction_prompt(
    transcript: str,
    output_type: str = "detailed",
    custom_instructions: str = "",
    context_text: str = "",
) -> str:
    """Return the correct extraction prompt for the given output type."""
    prefix_block = ""
    if context_text.strip():
        prefix_block += f"Meeting context provided by the user:\n{context_text.strip()}\n\n"
    if custom_instructions.strip():
        prefix_block += f"Additional instructions from the user:\n{custom_instructions.strip()}\n\n"

    template_map = {
        "mom":           _MOM_EXTRACTION_PROMPT,
        "quick_summary": _QUICK_SUMMARY_EXTRACTION_PROMPT,
        "action_items":  _ACTION_ITEMS_EXTRACTION_PROMPT,
    }
    template = template_map.get(output_type, EXTRACTION_PROMPT)
    return template.format(transcript=transcript, custom_instructions=prefix_block)


# ---------------------------------------------------------------------------
# Phase 2 — Confluence tool-loop prompt
# ---------------------------------------------------------------------------

ACTION_PROMPT = """\
You are publishing meeting notes to Confluence.  Follow these steps in order:

1. Call search_confluence with a short query derived from the meeting title to
   check whether a page about this meeting already exists.

2. Based on the search results:
   - If a very similar page already exists → call update_confluence_page with its ID.
   - Otherwise → call create_confluence_page using EXACTLY these destination values:
       space_key : "{space_key}"
       parent_id : "{parent_page_id}"   (empty string = create at the space root)
   Use the page body provided below exactly as-is.

3. Call flag_incomplete_action_items with the action items listed below.

Meeting title  : {title}
Space          : {space_key}
Parent page ID : {parent_page_id_display}

--- PAGE BODY (Confluence Storage Format) ---
{body}
--- END PAGE BODY ---

Action items to validate:
{action_items_json}
"""


def build_action_prompt(
    title: str,
    space_key: str,
    body: str,
    action_items_json: str,
    parent_page_id: str = "",
) -> str:
    """Return the Phase 2 Confluence publish prompt."""
    return ACTION_PROMPT.format(
        title=title,
        space_key=space_key,
        parent_page_id=parent_page_id or "",
        parent_page_id_display=parent_page_id if parent_page_id else "(space root)",
        body=body,
        action_items_json=action_items_json,
    )


# ---------------------------------------------------------------------------
# Confluence Storage Format helpers
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


# ---------------------------------------------------------------------------
# Confluence page body renderers (one per meeting type)
# ---------------------------------------------------------------------------

def render_sprint_review(d: MeetingDocument) -> str:
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


def render_planning(d: MeetingDocument) -> str:
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


def render_incident(d: MeetingDocument) -> str:
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


def render_general(d: MeetingDocument) -> str:
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


def render_retrospective(d: MeetingDocument) -> str:
    # Field repurposing for retro context:
    #   highlights     → What Went Well
    #   open_questions → What Could Be Improved
    #   decisions      → Agreed Improvements
    return f"""
<h2>Retrospective Summary</h2>
<p>{_e(d.summary)}</p>

<h2>Attendees</h2>
<p>{_e(', '.join(d.attendees)) if d.attendees else '<em>Not recorded.</em>'}</p>

<h2>What Went Well</h2>
{_ul(d.highlights)}

<h2>What Could Be Improved</h2>
{_ul(d.open_questions)}

<h2>Agreed Improvements</h2>
{_ul(d.decisions)}

<h2>Action Items</h2>
{_action_table(d.action_items)}
""".strip()


TEMPLATE_MAP: dict[str, Any] = {
    # original
    "sprint-review":       render_sprint_review,
    "planning":            render_planning,
    "incident":            render_incident,
    "general":             render_general,
    # planning-family
    "sprint-planning":     render_planning,
    "pi-planning":         render_planning,
    "kick-off":            render_planning,
    # retrospective-family (field meanings differ — use dedicated renderer)
    "sprint-retrospective": render_retrospective,
    "pi-retrospective":    render_retrospective,
    # post-mortem shares incident structure
    "post-mortem":         render_incident,
    # general-family
    "standup":             render_general,
    "backlog-refinement":  render_general,
    "one-on-one":          render_general,
    "design-review":       render_general,
    "architecture-review": render_general,
    "stakeholder-update":  render_general,
}


# ---------------------------------------------------------------------------
# Non-detailed Confluence renderers (take raw dict, not MeetingDocument)
# ---------------------------------------------------------------------------

def render_mom_page(d: dict) -> str:
    attendees_str = _e(", ".join(d.get("attendees") or [])) or "<em>Not recorded.</em>"
    date_str = _e(d.get("date") or "Not recorded")
    return f"""
<h2>Meeting Details</h2>
<p><strong>Date:</strong> {date_str} &nbsp;|&nbsp; <strong>Attendees:</strong> {attendees_str}</p>

<h2>Agenda Items Discussed</h2>
{_ul(d.get("agenda_items") or [])}

<h2>Decisions Made</h2>
{_ul(d.get("decisions") or [])}

<h2>Action Items</h2>
{_action_table(d.get("action_items") or [])}

<h2>Next Steps</h2>
{_ul(d.get("next_steps") or [])}
""".strip()


def render_quick_summary_page(d: dict) -> str:
    bullets = d.get("bullets") or []
    action_items = d.get("action_items") or []
    ai_lines = [
        f"{i.get('owner') or 'Unassigned'}: {i.get('task','')} (by {i.get('due') or 'TBD'})"
        for i in action_items
    ]
    return f"""
<h2>Summary</h2>
{_ul(bullets)}

<h2>Action Items</h2>
{_ul(ai_lines) if ai_lines else "<p><em>No action items recorded.</em></p>"}
""".strip()


def render_action_items_page(d: dict) -> str:
    return f"""
<h2>Action Items</h2>
{_action_table(d.get("action_items") or [])}
""".strip()


def render_confluence_body(output_type: str, extracted: dict, meeting_type: str = "general") -> str:
    """Route to the correct Confluence page renderer based on output_type."""
    if output_type == "mom":
        return render_mom_page(extracted)
    if output_type == "quick_summary":
        return render_quick_summary_page(extracted)
    if output_type == "action_items":
        return render_action_items_page(extracted)
    # detailed: use meeting-type-specific template via MeetingDocument
    from confluence import MeetingDocument
    document = MeetingDocument(
        title=extracted.get("title", "Meeting Notes"),
        meeting_type=meeting_type,
        summary=extracted.get("summary", ""),
        attendees=extracted.get("attendees") or [],
        decisions=extracted.get("decisions") or [],
        action_items=extracted.get("action_items") or [],
        open_questions=extracted.get("open_questions") or [],
        highlights=extracted.get("highlights") or [],
    )
    renderer = TEMPLATE_MAP.get(meeting_type, render_general)
    return renderer(document)
