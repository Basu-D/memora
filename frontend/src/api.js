/**
 * Memora API client.
 *
 * Environment variables (set in .env or passed as Vite build args):
 *   VITE_API_BASE_URL  — backend origin, e.g. http://localhost:8000
 *
 * Authentication: on module load, /api/session is called once to obtain a
 * short-lived signed session token. The token is kept in memory (never
 * compiled into the bundle) and injected into every subsequent request via
 * the X-Session-Token header.
 */

// In Docker, VITE_API_BASE_URL is "" and nginx proxies /api/* to the backend.
// In local dev (outside Docker), VITE_API_BASE_URL is also "" and the Vite
// dev-server proxy forwards /api/* to http://localhost:8000.
// All paths in this file therefore start with /api/.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

// Prefix for every backend route — must match the nginx location block.
const API_ROOT = `${BASE_URL}/api`;

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

let _sessionToken = null;
let _sessionPromise = null;

/**
 * Fetch (or return the in-flight fetch of) a session token from the server.
 * Invoked automatically before every API request.
 */
function _ensureSession() {
  if (_sessionToken) return Promise.resolve(_sessionToken);
  if (!_sessionPromise) {
    _sessionPromise = fetch(`${API_ROOT}/session`)
      .then((r) => r.json())
      .then((data) => {
        _sessionToken = data.token ?? null;
        return _sessionToken;
      })
      .catch(() => {
        _sessionPromise = null; // allow retry on next request
        return null;
      });
  }
  return _sessionPromise;
}

// Kick off the session fetch immediately so it's ready when the first
// real API call arrives.
_ensureSession();

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

/**
 * Core fetch wrapper — injects the session token and throws a descriptive
 * Error on any non-2xx response. `detail` from FastAPI JSON error bodies
 * is surfaced.
 */
async function apiFetch(path, options = {}) {
  const token = await _ensureSession();
  const authHeader = token ? { "X-Session-Token": token } : {};

  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...authHeader,
      ...options.headers,
    },
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      // FastAPI errors: { detail: "string" } or { detail: { message, ... } }
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail?.message)       message = body.detail.message;
    } catch (_) { /* non-JSON body — keep default message */ }
    throw new Error(message);
  }

  return response;
}

// ---------------------------------------------------------------------------
// Confluence destination helpers
// ---------------------------------------------------------------------------

/** @returns {Promise<{key: string, name: string}[]>} */
export async function getConfluenceSpaces() {
  const response = await apiFetch("/confluence/spaces");
  return response.json();
}

/**
 * @param {string} spaceKey
 * @returns {Promise<{id: string, title: string}[]>}
 */
export async function getConfluencePages(spaceKey) {
  const response = await apiFetch(`/confluence/pages?space_key=${encodeURIComponent(spaceKey)}`);
  return response.json();
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

/**
 * Upload a meeting recording and queue processing.
 *
 * @param {File}   file
 * @param {string} title
 * @param {string} outputType
 * @param {boolean} publishToConfluence
 * @param {string} customInstructions
 * @param {{space_key?:string, parent_page_id?:string, page_title?:string}} confluenceDest
 * @param {string} contextText
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function uploadMeeting(
  file, title = "", outputType = "detailed", publishToConfluence = true,
  customInstructions = "", confluenceDest = {}, contextText = "",
) {
  const formData = new FormData();
  formData.append("file", file);
  if (title.trim()) formData.append("title", title.trim());
  formData.append("output_type", outputType);
  formData.append("publish_to_confluence", String(publishToConfluence));
  if (customInstructions.trim()) formData.append("custom_instructions", customInstructions.trim());
  if (confluenceDest.space_key)       formData.append("confluence_space_key", confluenceDest.space_key);
  if (confluenceDest.parent_page_id)  formData.append("confluence_parent_page_id", confluenceDest.parent_page_id);
  if (confluenceDest.page_title)      formData.append("confluence_page_title", confluenceDest.page_title);
  if (contextText.trim())             formData.append("context_text", contextText.trim());

  const response = await apiFetch("/upload", {
    method: "POST",
    body: formData,
    // Content-Type must NOT be set manually — browser adds the multipart boundary
  });

  return response.json();
}

// ---------------------------------------------------------------------------
// URL submission
// ---------------------------------------------------------------------------

/**
 * Submit a recording URL for processing (downloaded by the worker).
 *
 * @param {string} url
 * @param {string} title
 * @param {string} outputType
 * @param {boolean} publishToConfluence
 * @param {string} customInstructions
 * @param {{space_key?:string, parent_page_id?:string, page_title?:string}} confluenceDest
 * @param {string} contextText
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function submitUrlMeeting(
  url, title = "", outputType = "detailed", publishToConfluence = true,
  customInstructions = "", confluenceDest = {}, contextText = "",
) {
  const response = await apiFetch("/upload-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      title: title.trim(),
      output_type: outputType,
      publish_to_confluence: publishToConfluence,
      custom_instructions: customInstructions.trim(),
      confluence_space_key: confluenceDest.space_key || "",
      confluence_parent_page_id: confluenceDest.parent_page_id || "",
      confluence_page_title: confluenceDest.page_title || "",
      context_text: contextText.trim(),
    }),
  });
  return response.json();
}

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

/**
 * Fetch the current status of a processing job.
 * Poll this until status is "done" or "failed".
 *
 * @param {string} jobId
 * @returns {Promise<{
 *   job_id: string,
 *   status: string,
 *   filename: string,
 *   created_at: string,
 *   updated_at: string,
 *   error_message: string|null
 * }>}
 */
export async function getJobStatus(jobId) {
  const response = await apiFetch(`/status/${jobId}`);
  return response.json();
}

// ---------------------------------------------------------------------------
// Result
// ---------------------------------------------------------------------------

/**
 * Fetch the structured result for a completed job.
 * Returns 409 if not yet done — only call this after status === "done".
 *
 * @param {string} jobId
 * @returns {Promise<{
 *   job_id: string,
 *   status: string,
 *   confluence_url: string|null,
 *   result: {
 *     meeting_type: string,
 *     title: string,
 *     summary: string,
 *     attendees: string[],
 *     decisions: string[],
 *     action_items: {owner:string, task:string, due:string}[],
 *     open_questions: string[],
 *     highlights: string[],
 *     incomplete_action_items: {owner:string, task:string, due:string, missing:string[]}[]
 *   }
 * }>}
 */
export async function getJobResult(jobId) {
  const response = await apiFetch(`/result/${jobId}`);
  return response.json();
}

// ---------------------------------------------------------------------------
// Publish retry
// ---------------------------------------------------------------------------

/**
 * Re-queue Confluence publishing for a job that completed with publish_failed=true.
 * Poll getJobStatus until status returns to "done", then call getJobResult for
 * the updated result (which will have publish_failed=false on success).
 *
 * @param {string} jobId
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function retryPublish(jobId) {
  const response = await apiFetch(`/jobs/${jobId}/retry-publish`, { method: "POST" });
  return response.json();
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

/**
 * Return the URL for downloading the .docx for a completed job.
 * The session token is passed as a query param because this URL is used as
 * an anchor href — the browser navigates directly to it without custom headers.
 * The token is short-lived (24 h) and is never compiled into the JS bundle.
 */
export function getDownloadUrl(jobId) {
  const token = _sessionToken;
  return `${API_ROOT}/jobs/${jobId}/download${token ? `?session_token=${encodeURIComponent(token)}` : ""}`;
}
