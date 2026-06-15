# Memora — Future Possibilities

> *This document captures what Memora can grow into. Every item here is a natural extension of what's already working — not a new direction, but a deeper version of the same idea.*

---

## Where we are today

Memora already does something significant: a Webex meeting ends, and within minutes a structured Confluence page exists, filed in the right place, with a link emailed to the host. No one had to remember to do it. That's v2.

Everything below is built on that foundation.

---

## 1. SSO Login

**What it means:** Anyone in the org can open Memora using their existing work account — the same login they use for Webex, Confluence, and every other org tool. No new password, no signup form.

**Why it matters:** Adoption lives or dies on friction. If people have to create a new account, most won't. SSO removes that barrier entirely — the tool is just there, accessible, the moment someone needs it.

**What it unlocks:** Once identity is tied to the org directory, Memora knows who you are, which team you're on, and what spaces you have access to. That makes every other feature smarter.

---

## 2. Auto-create Jira Tickets from Action Items

**What it means:** When Memora extracts action items from a meeting — "Basu to fix the auth bug by Friday", "Priya to review the design by Wednesday" — it automatically creates Jira tickets, assigns them to the right person, sets the due date, and links the ticket back to the Confluence meeting page.

**Why it matters:** The gap between "we decided this in the meeting" and "it's tracked somewhere it won't be forgotten" is where decisions go to die. Today that gap requires someone to manually copy action items into Jira after every meeting. Memora closes it automatically.

**What it unlocks:** Sprint boards stay current without manual effort. Action items from every meeting are trackable, searchable, and accountable. Nothing falls through the cracks.

---

## 3. Context-Aware AI — Learns from Past Meetings

**What it means:** Today Memora processes each meeting in isolation. In the future, it would understand the history — referencing past decisions, recognising recurring participants, knowing what was discussed last sprint, and using that context to produce richer, more accurate documentation.

**Why it matters:** Meetings are full of references to prior conversations. "The approach we agreed on last time", "the customer issue from two weeks ago", "what we decided about the architecture" — without that context, the AI is producing a document with gaps. With it, the documentation actually reflects what the team knows.

**What it unlocks:** Documentation that reads like it was written by someone who understands the project, not just someone who attended one meeting. Recurring meetings — standups, sprint reviews, planning sessions — benefit most, because the context compounds over time.

---

## 4. Meeting Knowledge Accessible Across Org Tools

**What it means:** Memora's meeting history becomes queryable from other tools — not just the Memora dashboard. An AI assistant, a developer's IDE, a project management tool, or a custom internal application could ask Memora: "What did the team decide about the payment gateway?" or "Who owns the API migration action item?" and get an answer.

**Why it matters:** The value of a meeting doesn't live in the Confluence page — it lives in the decisions and commitments made. Today those are buried in documents most people never read again. Making them queryable turns every meeting into searchable institutional knowledge.

**What it unlocks:** New team members can get up to speed by querying past meetings rather than reading through pages of documentation. Leadership can ask "what has the team committed to this quarter?" and get a real answer. Meeting knowledge stops being passive and starts being active.

---

## 5. Org-Wide Admin Controls

**What it means:** A leadership or admin view showing all meetings processed across the org — usage by team, by person, by meeting type. Ability to set org-wide defaults (which Confluence space, which document format) that individual users can override.

**Why it matters:** As Memora scales across teams, someone needs visibility into how it's being used and control over where documentation lands. Without this, every team is configuring independently and documentation ends up scattered.

**What it unlocks:** Consistent documentation standards across the org. Usage visibility for leadership. The ability to mandate Memora for certain meeting types (e.g. all incidents must be documented) while leaving flexibility for others.

---

## 6. Slack / Teams Notifications

**What it means:** When a Confluence page is created, Memora posts a summary to the relevant Slack or Teams channel — the meeting summary, key decisions, and action items, right where the team already communicates.

**Why it matters:** Not everyone checks email immediately. And most people won't navigate to Confluence unprompted. Bringing the summary to where people already are means the information actually reaches them.

**What it unlocks:** Zero-friction information sharing. Meeting participants and stakeholders who weren't in the room see the key decisions immediately, in the channel they're already watching.

---

## 7. Multi-Language Support

**What it means:** Memora transcribes and generates documentation in the language the meeting was conducted in — or translates to English regardless of the source language.

**Why it matters:** TCS and AMEX operate globally. Teams across India, the UK, Europe, and the US run meetings in different languages. Today Memora only handles English well.

**What it unlocks:** Memora becomes useful for every team in every region, not just English-speaking ones. Global consistency in meeting documentation.

---

## 8. Meeting Quality Insights

**What it means:** Over time, Memora sees patterns across meetings — how often action items go unresolved, which meetings consistently run without clear decisions, which topics keep recurring without resolution. It can surface these patterns to teams and leadership.

**Why it matters:** The documentation is the output, but the insight is the value. If the same issue has been discussed in six consecutive sprint reviews without a decision, that's something leadership should know.

**What it unlocks:** Meetings become accountable. Teams can see their own patterns. Leadership gets a view of where decisions are stalling across the org.

---

## 9. On-Premise / Private Deployment

**What it means:** Memora deployed entirely within TCS or AMEX's own infrastructure — no meeting content leaves the org's network. All AI processing happens on internal or org-contracted models (e.g. Azure OpenAI on the AMEX tenant).

**Why it matters:** Meeting content is sensitive. Strategy discussions, incident post-mortems, personnel conversations — these shouldn't leave the org's control boundary. For enterprise deployment at scale, on-premise or private cloud is a requirement, not a preference.

**What it unlocks:** Memora becomes deployable for any meeting, including those with confidential content. Compliance and security requirements are met. Enterprise-wide rollout becomes viable.

---

## Summary

| Capability | Primary Beneficiary | Effort |
|---|---|---|
| SSO Login | All users | Medium |
| Auto-create Jira tickets | Engineering teams | Medium |
| Context-aware AI | All users | High |
| Meeting knowledge across org tools | Leadership, all teams | High |
| Org-wide admin controls | Leadership, IT | Medium |
| Slack / Teams notifications | All users | Low |
| Multi-language support | Global teams | Medium |
| Meeting quality insights | Leadership, team leads | High |
| On-premise deployment | IT, compliance | High |

---

*Memora v2 is live. Everything above is where it goes next.*