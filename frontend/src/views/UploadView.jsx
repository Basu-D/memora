import { useState, useRef, useCallback } from "react";
import { uploadMeeting, submitUrlMeeting } from "../api.js";

const ACCEPTED_EXTENSIONS = [".mp4", ".mp3", ".webm", ".wav", ".m4a"];
const ACCEPTED_ACCEPT      = ACCEPTED_EXTENSIONS.join(",");
const MAX_BYTES            = 500 * 1024 * 1024; // 500 MB

const OUTPUT_TYPES = [
  { value: "detailed",      label: "Detailed Document",   description: "Full notes: summary, decisions, action items, open questions and highlights." },
  { value: "mom",           label: "Minutes of Meeting",  description: "Formal MoM: agenda items discussed, decisions made, action items, next steps." },
  { value: "quick_summary", label: "Quick Summary",       description: "3–5 bullet points covering what was discussed, decided, and who owns what." },
  { value: "action_items",  label: "Action Items Only",   description: "Just the action items table: owner, task, and deadline." },
];

// Output types where Confluence should be on by default.
const CONFLUENCE_ON_BY_DEFAULT = new Set(["detailed", "mom"]);

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function validateFile(file) {
  if (!file) return null;
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (!ACCEPTED_EXTENSIONS.includes(ext))
    return `Unsupported format "${ext}". Accepted: ${ACCEPTED_EXTENSIONS.join(" ")}`;
  if (file.size > MAX_BYTES)
    return `File is ${formatBytes(file.size)} — maximum is 500 MB.`;
  return null;
}

// ---------------------------------------------------------------------------
// Icons (inline SVG — no icon library dependency)
// ---------------------------------------------------------------------------

function UploadCloudIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775
           5.25 5.25 0 0 1 10.338-2.32 3.75 3.75 0 0 1 3.47 4.24
           A4.5 4.5 0 0 1 17.25 19.5H6.75Z" />
    </svg>
  );
}

function FileAudioIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 0 1-1.632 2.163l-1.32.377
           a1.803 1.803 0 1 1-.99-3.467l2.31-.66a2.25 2.25 0 0 0 1.632-2.163
           Zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 0 1-1.632 2.163
           l-1.32.377a1.803 1.803 0 0 1-.99-3.467l2.31-.66A2.25 2.25 0
           0 0 9 15.553Z" />
    </svg>
  );
}

function XCircleIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9.75 9.75 14.25 14.25M9.75 14.25l4.5-4.5M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18Z" />
    </svg>
  );
}

function SpinnerIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4Z" />
    </svg>
  );
}

function LinkIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757
           m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function UploadView({ onJobCreated }) {
  const [mode,      setMode]      = useState("file"); // "file" | "url"

  // ── file mode state ─────────────────────────────────────────────────────
  const [file,      setFile]      = useState(null);
  const [dragging,  setDragging]  = useState(false);
  const [fileError, setFileError] = useState(null);

  // ── url mode state ──────────────────────────────────────────────────────
  const [url,       setUrl]       = useState("");
  const [urlError,  setUrlError]  = useState(null);

  // ── shared state ────────────────────────────────────────────────────────
  const [title,               setTitle]               = useState("");
  const [outputType,          setOutputType]          = useState("detailed");
  const [publishToConfluence, setPublishToConfluence] = useState(true);
  const [submitting,          setSubmitting]          = useState(false);
  const [submitErr,           setSubmitErr]           = useState(null);

  function handleOutputTypeChange(val) {
    setOutputType(val);
    setPublishToConfluence(CONFLUENCE_ON_BY_DEFAULT.has(val));
  }

  const inputRef = useRef(null);

  // ── file selection ──────────────────────────────────────────────────────
  const pickFile = useCallback((picked) => {
    if (!picked) return;
    const err = validateFile(picked);
    if (err) { setFileError(err); setFile(null); return; }
    setFile(picked);
    setFileError(null);
    setSubmitErr(null);
    setTitle((t) => {
      if (t.trim()) return t;
      return picked.name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ");
    });
  }, []);

  function handleInputChange(e) {
    pickFile(e.target.files?.[0]);
    e.target.value = "";
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    pickFile(e.dataTransfer.files?.[0]);
  }

  function clearFile() {
    setFile(null);
    setFileError(null);
  }

  function switchMode(m) {
    setMode(m);
    setSubmitErr(null);
    setUrlError(null);
    setFileError(null);
  }

  // ── submit ──────────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    if (submitting) return;

    if (mode === "file") {
      if (!file) return;
      setSubmitting(true);
      setSubmitErr(null);
      try {
        const { job_id } = await uploadMeeting(file, title, outputType, publishToConfluence);
        onJobCreated(job_id);
      } catch (err) {
        setSubmitErr(err.message);
        setSubmitting(false);
      }
    } else {
      const trimmed = url.trim();
      if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
        setUrlError("URL must start with http:// or https://");
        return;
      }
      setUrlError(null);
      setSubmitting(true);
      setSubmitErr(null);
      try {
        const { job_id } = await submitUrlMeeting(trimmed, title, outputType, publishToConfluence);
        onJobCreated(job_id);
      } catch (err) {
        setSubmitErr(err.message);
        setSubmitting(false);
      }
    }
  }

  const canSubmit = mode === "file" ? !!file && !submitting : !!url.trim() && !submitting;

  // ── render ──────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-gray-50">
      {/* Logo */}
      <div className="mb-8 text-center">
        <div className="inline-flex items-center gap-2 mb-2">
          <span className="text-3xl">🎙</span>
          <span className="text-2xl font-bold text-gray-900">Memora</span>
        </div>
        <p className="text-sm text-gray-500">Turn meeting recordings into structured documentation</p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="w-full max-w-lg card p-8 flex flex-col gap-5"
      >
        {/* Mode toggle */}
        <div className="flex rounded-xl border border-gray-200 bg-gray-100 p-1 gap-1">
          <button
            type="button"
            onClick={() => switchMode("file")}
            className={[
              "flex-1 flex items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium transition-colors",
              mode === "file"
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700",
            ].join(" ")}
          >
            <UploadCloudIcon className="w-4 h-4" />
            Upload File
          </button>
          <button
            type="button"
            onClick={() => switchMode("url")}
            className={[
              "flex-1 flex items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium transition-colors",
              mode === "url"
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700",
            ].join(" ")}
          >
            <LinkIcon className="w-4 h-4" />
            Paste URL
          </button>
        </div>

        {/* ── File mode ─────────────────────────────────────────────────── */}
        {mode === "file" && (
          <>
            <div
              role="button"
              tabIndex={0}
              aria-label="Upload meeting recording"
              onClick={() => !file && inputRef.current?.click()}
              onKeyDown={(e) => e.key === "Enter" && !file && inputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              className={[
                "relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed",
                "transition-colors duration-150 min-h-[180px] px-6 py-8",
                file
                  ? "border-teal-400 bg-teal-50 cursor-default"
                  : dragging
                  ? "border-teal-500 bg-teal-50 cursor-copy"
                  : "border-gray-300 bg-white hover:border-teal-400 hover:bg-teal-50/40 cursor-pointer",
              ].join(" ")}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_ACCEPT}
                className="sr-only"
                onChange={handleInputChange}
              />
              {file ? (
                <div className="flex flex-col items-center gap-3 w-full">
                  <FileAudioIcon className="w-10 h-10 text-teal-600" />
                  <div className="text-center">
                    <p className="font-semibold text-gray-800 break-all leading-snug">{file.name}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{formatBytes(file.size)}</p>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); clearFile(); }}
                    className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 transition-colors mt-1"
                  >
                    <XCircleIcon className="w-4 h-4" />
                    Remove
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3 text-center pointer-events-none">
                  <UploadCloudIcon className="w-12 h-12 text-gray-400" />
                  <div>
                    <p className="text-sm text-gray-600">
                      Drop your recording here, or{" "}
                      <span className="text-teal-600 font-semibold pointer-events-auto cursor-pointer">browse</span>
                    </p>
                    <p className="text-xs text-gray-400 mt-1">
                      {ACCEPTED_EXTENSIONS.join("  ")} &middot; max 500 MB
                    </p>
                  </div>
                </div>
              )}
            </div>
            {fileError && (
              <p className="text-sm text-red-600 flex items-start gap-1.5">
                <XCircleIcon className="w-4 h-4 mt-0.5 shrink-0" />
                {fileError}
              </p>
            )}
          </>
        )}

        {/* ── URL mode ──────────────────────────────────────────────────── */}
        {mode === "url" && (
          <div className="flex flex-col gap-2">
            <label htmlFor="recording-url" className="text-sm font-medium text-gray-700">
              Recording URL
            </label>
            <div className="relative">
              <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              <input
                id="recording-url"
                type="url"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setUrlError(null); }}
                placeholder="https://webex.us/rec/… or direct .mp4 link"
                className="w-full rounded-xl border border-gray-200 bg-white pl-9 pr-4 py-2.5
                           text-sm text-gray-900 placeholder:text-gray-400
                           focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                           transition-colors"
              />
            </div>
            {urlError && (
              <p className="text-sm text-red-600 flex items-start gap-1.5">
                <XCircleIcon className="w-4 h-4 mt-0.5 shrink-0" />
                {urlError}
              </p>
            )}
            <p className="text-xs text-gray-400">
              Supports direct file links, Webex and Zoom cloud recordings, YouTube and more.
            </p>
          </div>
        )}

        {/* Meeting title (shared) */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="meeting-title" className="text-sm font-medium text-gray-700">
            Meeting title <span className="text-gray-400 font-normal">(optional)</span>
          </label>
          <input
            id="meeting-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Sprint 42 Review"
            maxLength={200}
            className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5
                       text-sm text-gray-900 placeholder:text-gray-400
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                       transition-colors"
          />
        </div>

        {/* Output type */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="output-type" className="text-sm font-medium text-gray-700">
            Output type
          </label>
          <select
            id="output-type"
            value={outputType}
            onChange={(e) => handleOutputTypeChange(e.target.value)}
            className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5
                       text-sm text-gray-900
                       focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                       transition-colors"
          >
            {OUTPUT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <p className="text-xs text-gray-400">
            {OUTPUT_TYPES.find((t) => t.value === outputType)?.description}
          </p>
        </div>

        {/* Confluence toggle */}
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={publishToConfluence}
            onChange={(e) => setPublishToConfluence(e.target.checked)}
            className="w-4 h-4 rounded border-gray-300 text-teal-600 accent-teal-600 cursor-pointer"
          />
          <span className="text-sm text-gray-700">Create Confluence page</span>
        </label>

        {/* Submit error */}
        {submitErr && (
          <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3">
            <p className="text-sm text-red-700">{submitErr}</p>
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={!canSubmit}
          className="btn-primary w-full py-3 text-base"
        >
          {submitting ? (
            <>
              <SpinnerIcon className="w-4 h-4 animate-spin" />
              {mode === "file" ? "Uploading…" : "Submitting…"}
            </>
          ) : (
            "Process Meeting"
          )}
        </button>
      </form>

      <p className="mt-6 text-xs text-gray-400">
        Transcription via OpenAI Whisper · AI extraction via Google Gemini · Notes published to your Confluence
      </p>
    </div>
  );
}
