import { useEffect, useMemo, useState } from "react";
import { getHistory, getUserJobs } from "../api.js";

// TODO: replace with SSO — reading email from localStorage is temporary
const STORAGE_KEY = "current_user_email";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MEETING_TYPE_META = {
  "sprint-review": { label: "Sprint Review", color: "bg-blue-100 text-blue-700"  },
  planning:        { label: "Planning",       color: "bg-teal-100 text-teal-700"  },
  incident:        { label: "Incident",       color: "bg-red-100  text-red-700"   },
  general:         { label: "General",        color: "bg-gray-100 text-gray-600"  },
};

function typeMeta(type) {
  return MEETING_TYPE_META[type] ?? { label: type, color: "bg-gray-100 text-gray-600" };
}

function formatDate(iso) {
  if (!iso) return "";
  const d    = new Date(iso);
  const now  = new Date();
  const days = Math.floor((now - d) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7)  return `${days} days ago`;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function ArrowLeftIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
    </svg>
  );
}

function SearchIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
    </svg>
  );
}

function ExternalLinkIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25
           2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
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

function WarningIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71
           c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898
           0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
    </svg>
  );
}

function CogIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 0 1 0-.255c.007-.378-.138-.75-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Meeting card
// ---------------------------------------------------------------------------

const STATUS_STYLES = {
  failed:     "bg-red-100   text-red-700",
  uploading:  "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  publishing: "bg-amber-100 text-amber-700",
};

function MeetingCard({ job }) {
  const meta        = typeMeta(job.meeting_type);
  const hasSnippet  = !!job.summary_snippet;
  const showStatus  = job.status !== "done";
  const statusStyle = STATUS_STYLES[job.status] ?? "bg-gray-100 text-gray-600";

  return (
    <article className="card p-5 flex flex-col gap-3 hover:shadow-md transition-shadow duration-150">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Badges */}
          {(job.meeting_type || showStatus) && (
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              {job.meeting_type && (
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${meta.color}`}>
                  {meta.label}
                </span>
              )}
              {showStatus && (
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${statusStyle}`}>
                  {job.status === "failed" && (
                    <WarningIcon className="inline w-3 h-3 mr-0.5 -mt-px" />
                  )}
                  {job.status.replace(/_/g, " ")}
                </span>
              )}
              {job.publish_failed && (
                <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-700">
                  Confluence failed
                </span>
              )}
            </div>
          )}
          {/* Title */}
          <h3 className="font-semibold text-gray-900 leading-snug break-words">
            {job.title || job.filename || "Untitled meeting"}
          </h3>
        </div>
        {/* Date */}
        <time
          className="text-xs text-gray-400 shrink-0 mt-0.5 whitespace-nowrap"
          dateTime={job.created_at}
        >
          {formatDate(job.created_at)}
        </time>
      </div>

      {/* Summary snippet */}
      {hasSnippet && (
        <p className="text-sm text-gray-600 leading-relaxed line-clamp-2">
          {job.summary_snippet}
          {job.summary_snippet.length >= 100 && "…"}
        </p>
      )}

      {/* Footer */}
      {job.confluence_url ? (
        <div className="flex items-center pt-2 border-t border-gray-100">
          <a
            href={job.confluence_url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 text-sm font-medium text-teal-600
                       hover:text-teal-700 transition-colors"
          >
            <ExternalLinkIcon className="w-3.5 h-3.5 shrink-0" />
            Open in Confluence
          </a>
        </div>
      ) : job.status === "done" ? (
        <div className="flex items-center pt-2 border-t border-gray-100">
          <span className="text-xs text-gray-400">No Confluence page — download only</span>
        </div>
      ) : null}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Empty / loading / error states
// ---------------------------------------------------------------------------

function LoadingState() {
  return (
    <div className="flex items-center justify-center py-16 gap-3 text-gray-400">
      <SpinnerIcon className="w-5 h-5 animate-spin" />
      <span className="text-sm">Loading history…</span>
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <div className="card p-8 flex flex-col items-center gap-4 text-center">
      <WarningIcon className="w-8 h-8 text-red-400" />
      <div>
        <p className="font-semibold text-gray-800">Could not load history</p>
        <p className="text-sm text-gray-500 mt-1">{message}</p>
      </div>
      <button onClick={onRetry} className="btn-primary">Retry</button>
    </div>
  );
}

function EmptyState({ hasSearch }) {
  return (
    <div className="card p-12 flex flex-col items-center gap-3 text-center">
      <span className="text-4xl">🎙</span>
      {hasSearch ? (
        <>
          <p className="font-semibold text-gray-800">No meetings match your search</p>
          <p className="text-sm text-gray-500">Try a different title or meeting type.</p>
        </>
      ) : (
        <>
          <p className="font-semibold text-gray-800">No meetings yet</p>
          <p className="text-sm text-gray-500">
            Upload your first recording and it will appear here.
          </p>
        </>
      )}
    </div>
  );
}

function NoEmailBanner({ onSettings }) {
  return (
    <div className="card p-10 flex flex-col items-center gap-4 text-center">
      <span className="text-4xl">👤</span>
      <div>
        <p className="font-semibold text-gray-800">No email set</p>
        <p className="text-sm text-gray-500 mt-1">
          Set your email in Settings to see your meetings.
        </p>
      </div>
      {onSettings && (
        <button onClick={onSettings} className="btn-primary">
          Go to Settings
        </button>
      )}
    </div>
  );
}

// Normalise getUserJobs response to match MeetingCard's expected shape.
function normaliseUserJob(job) {
  return {
    job_id:          job.id,
    title:           job.meeting_title,
    filename:        job.meeting_title,
    meeting_type:    null,
    status:          job.status,
    created_at:      job.created_at,
    confluence_url:  job.confluence_url,
    summary_snippet: null,
    publish_failed:  false,
  };
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function HistoryView({ onBack, onSettings }) {
  const [jobs,    setJobs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [search,  setSearch]  = useState("");

  // TODO: replace with SSO — reading email from localStorage is temporary
  const userEmail = localStorage.getItem(STORAGE_KEY) || "";
  const hasEmail  = !!userEmail;

  function load() {
    if (!hasEmail) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    getUserJobs(userEmail)
      .then((data) => {
        setJobs((data.jobs ?? []).map(normaliseUserJob));
        setLoading(false);
      })
      .catch((err) => { setError(err.message); setLoading(false); });
  }

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Client-side filtering — searches title and snippet.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return jobs;
    return jobs.filter((j) =>
      (j.title           || "").toLowerCase().includes(q) ||
      (j.summary_snippet || "").toLowerCase().includes(q)
    );
  }, [jobs, search]);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Sticky header ───────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-100 sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <ArrowLeftIcon className="w-4 h-4" />
            New recording
          </button>
          <div className="flex items-center gap-1.5 ml-auto">
            <span className="text-lg leading-none">🎙</span>
            <span className="font-bold text-gray-900">Memora</span>
          </div>
          {onSettings && (
            <button
              onClick={onSettings}
              className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-teal-600 font-medium transition-colors"
            >
              <CogIcon className="w-4 h-4" />
              Settings
            </button>
          )}
        </div>
      </header>

      {/* ── Page body ───────────────────────────────────────────────────── */}
      <main className="max-w-3xl mx-auto px-6 py-8 flex flex-col gap-6">

        {/* Title */}
        <div>
          <h1 className="text-2xl font-bold text-gray-900">My Meetings</h1>
          <p className="text-sm text-gray-500 mt-1">
            {hasEmail
              ? `Showing meetings for ${userEmail}`
              : "Your processed meetings, searchable and permanent."}
          </p>
        </div>

        {/* No email — show banner instead of list */}
        {!hasEmail ? (
          <NoEmailBanner onSettings={onSettings} />
        ) : (
          <>
            {/* Search bar */}
            <div className="relative">
              <SearchIcon className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by title…"
                className="w-full rounded-xl border border-gray-200 bg-white pl-10 pr-4 py-2.5
                           text-sm text-gray-900 placeholder:text-gray-400
                           focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                           transition-colors"
              />
            </div>

            {/* Result count when searching */}
            {search.trim() && !loading && !error && (
              <p className="text-xs text-gray-400 -mt-2">
                {filtered.length === 0
                  ? "No results"
                  : `${filtered.length} result${filtered.length !== 1 ? "s" : ""}`}
              </p>
            )}

            {/* Content */}
            {loading ? (
              <LoadingState />
            ) : error ? (
              <ErrorState message={error} onRetry={load} />
            ) : filtered.length === 0 ? (
              <EmptyState hasSearch={!!search.trim()} />
            ) : (
              <div className="flex flex-col gap-4">
                {filtered.map((job) => (
                  <MeetingCard key={job.job_id} job={job} />
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
