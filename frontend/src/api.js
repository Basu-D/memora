/**
 * Memora API client.
 *
 * Environment variables (set in .env or passed as Vite build args):
 *   VITE_API_BASE_URL  — backend origin, e.g. http://localhost:8000
 *   VITE_API_KEY       — value of ORG_API_KEY
 *
 * In development the Vite dev-server proxy forwards /upload, /status, /result,
 * /jobs to localhost:8000, so BASE_URL is effectively empty and API_KEY is
 * still sent via the X-API-Key header.
 */

// In Docker, VITE_API_BASE_URL is "" and nginx proxies /api/* to the backend.
// In local dev (outside Docker), VITE_API_BASE_URL is also "" and the Vite
// dev-server proxy forwards /api/* to http://localhost:8000.
// All paths in this file therefore start with /api/.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const API_KEY  = import.meta.env.VITE_API_KEY ?? "";

// Prefix for every backend route — must match the nginx location block.
const API_ROOT = `${BASE_URL}/api`;

/**
 * Core fetch wrapper — injects auth header and throws a descriptive Error on
 * any non-2xx response.  `detail` from FastAPI JSON error bodies is surfaced.
 */
async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      "X-API-Key": API_KEY,
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
// Upload
// ---------------------------------------------------------------------------

/**
 * Upload a meeting recording and queue processing.
 *
 * @param {File}   file   — Audio or video file (mp4 / mp3 / webm / wav / m4a).
 * @param {string} title  — Optional meeting title hint (sent as a form field).
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function uploadMeeting(file, title = "") {
  const formData = new FormData();
  formData.append("file", file);
  if (title.trim()) formData.append("title", title.trim());

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
 * @param {string} url    — Publicly accessible recording URL.
 * @param {string} title  — Optional meeting title hint.
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function submitUrlMeeting(url, title = "") {
  const response = await apiFetch("/upload-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, title: title.trim() }),
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
// Download
// ---------------------------------------------------------------------------

/**
 * Return the URL for downloading the .docx for a completed job.
 * The API key is included as a query param because this URL is used as an
 * anchor href — the browser navigates directly to it.
 *
 * TODO: replace with a short-lived signed URL or a session-cookie endpoint
 * to avoid exposing the API key in the URL / browser history.
 */
export function getDownloadUrl(jobId) {
  const key = encodeURIComponent(API_KEY);
  return `${API_ROOT}/jobs/${jobId}/download${key ? `?api_key=${key}` : ""}`;
}
