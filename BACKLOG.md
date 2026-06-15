# Memora — Backlog

---

## 1. SSO integration (replace email-based identity)

**Labels:** `enhancement` `auth`

Replace the temporary localStorage email identity with org SSO (SAML/OIDC). On first SSO login, merge the SSO identity into the existing stub user created from the Webex webhook. No passwords, no signup flow — one "Login with org account" button.

---

## 2. RAG context retrieval using pgvector

**Labels:** `enhancement` `ai`

On user onboarding, index all Confluence pages in the user's target space into pgvector (add extension to existing Postgres). At processing time, embed the meeting transcript, retrieve top-5 relevant chunks, prepend as context to the Gemini agent system prompt. Use OpenAI embeddings (already a vendor) or Gemini embeddings to consolidate.

---

## 3. Nightly Confluence space re-index

**Labels:** `enhancement` `ai`

Add a Celery beat scheduled task that re-indexes each user's target Confluence space nightly into pgvector. Keeps RAG context fresh as documentation grows.

---

## 4. Webex webhook secret validation (harden for production)

**Labels:** `security`

`WEBEX_WEBHOOK_SECRET` validation is currently optional (skipped if not set). Make it required in production (when `ENVIRONMENT=production` env var is set). Add to deployment checklist.

---

## 5. Placement feedback loop

**Labels:** `enhancement`

Track when users click "wrong location?" from the email and update their preferences. Log the correction event to a new `placement_feedback` table (`job_id`, `old_space`, `new_space`, `corrected_at`). Use this data to improve placement heuristics over time.

---

## 6. Meeting history RAG (index past transcripts)

**Labels:** `enhancement` `ai`

In addition to Confluence page RAG, index past meeting transcripts per user into pgvector. Retrieve top-3 relevant past meetings as additional context for the agent. Particularly useful for recurring meetings with ongoing decisions.

---

## 7. Webex recording download resilience

**Labels:** `reliability`

Add retry logic for Webex recording download (transient 403s are common while the file is still being processed on Webex's end). Implement exponential backoff with max 3 retries before failing the job.

---

## 8. Admin dashboard

**Labels:** `enhancement`

Org-level admin view showing all jobs across all users, usage stats, and ability to set org-wide default Confluence space (overridable per user).
