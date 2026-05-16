import { useEffect, useState } from "react";
import { getJobResult, getDownloadUrl } from "../api.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MEETING_TYPE_META = {
  "sprint-review": { label: "Sprint Review", color: "bg-blue-100  text-blue-700"  },
  planning:        { label: "Planning",       color: "bg-teal-100  text-teal-700"  },
  incident:        { label: "Incident",       color: "bg-red-100   text-red-700"   },
  general:         { label: "General",        color: "bg-gray-100  text-gray-700"  },
};

function meetingTypeMeta(type) {
  return MEETING_TYPE_META[type] ?? { label: type ?? "Meeting", color: "bg-gray-100 text-gray-600" };
}

/** Badge for action-item completeness — drives the "priority colour" display. */
function completenessLabel(item, incompleteSet) {
  const flagged = incompleteSet.find(
    (f) => f.task === item.task && f.owner === item.owner
  );
  if (!flagged) return { label: "Complete", color: "bg-green-100 text-green-700" };

  const missing = flagged.missing ?? [];
  if (missing.includes("owner") && missing.includes("due"))
    return { label: "Missing owner & due", color: "bg-red-100 text-red-700" };
  if (missing.includes("owner"))
    return { label: "Missing owner",       color: "bg-orange-100 text-orange-700" };
  if (missing.includes("due"))
    return { label: "Missing due date",    color: "bg-amber-100  text-amber-700"  };
  return { label: "Incomplete", color: "bg-red-100 text-red-700" };
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function SpinnerIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4Z" />
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

function DownloadIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5
           12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
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

function ArrowLeftIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
    </svg>
  );
}

function CheckIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75 10.5 18.75 19.5 5.25" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Section components
// ---------------------------------------------------------------------------

function SectionCard({ title, icon, children }) {
  return (
    <section className="card overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100 flex items-center gap-2">
        {icon}
        <h2 className="font-semibold text-gray-800">{title}</h2>
      </div>
      <div className="px-6 py-4">{children}</div>
    </section>
  );
}

function EmptyState({ message }) {
  return <p className="text-sm text-gray-400 italic">{message}</p>;
}

// Action items table
function ActionItemsTable({ items, incomplete }) {
  if (!items?.length) return <EmptyState message="No action items recorded." />;

  return (
    <div className="overflow-x-auto -mx-6 px-6">
      <table className="data-table">
        <thead>
          <tr>
            <th>Owner</th>
            <th>Task</th>
            <th>Due</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            const badge = completenessLabel(item, incomplete ?? []);
            const isIncomplete = badge.label !== "Complete";
            return (
              <tr key={i}>
                <td>
                  {item.owner ? (
                    <span className="font-medium text-gray-800">{item.owner}</span>
                  ) : (
                    <span className="text-gray-400 italic">Unassigned</span>
                  )}
                </td>
                <td className={isIncomplete ? "text-gray-900" : "text-gray-700"}>
                  {item.task}
                </td>
                <td>
                  {item.due && item.due !== "TBD" ? (
                    item.due
                  ) : (
                    <span className="text-gray-400 italic">TBD</span>
                  )}
                </td>
                <td>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${badge.color}`}
                  >
                    {!isIncomplete && <CheckIcon className="w-3 h-3" />}
                    {isIncomplete && <WarningIcon className="w-3 h-3" />}
                    {badge.label}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Numbered/bulleted list section
function BulletList({ items, numbered = false, emptyText }) {
  if (!items?.length) return <EmptyState message={emptyText} />;
  return (
    <ol className={`flex flex-col gap-2 ${numbered ? "list-none" : ""}`}>
      {items.map((text, i) => (
        <li key={i} className="flex items-start gap-3 text-sm text-gray-700">
          {numbered ? (
            <span className="shrink-0 w-5 h-5 rounded-full bg-teal-100 text-teal-700
                             text-xs font-bold flex items-center justify-center mt-0.5">
              {i + 1}
            </span>
          ) : (
            <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-teal-500 mt-2" />
          )}
          {text}
        </li>
      ))}
    </ol>
  );
}

// Decisions table
function DecisionsSection({ decisions }) {
  if (!decisions?.length) return <EmptyState message="No decisions recorded." />;
  return (
    <ol className="flex flex-col divide-y divide-gray-50">
      {decisions.map((text, i) => (
        <li key={i} className="flex items-start gap-4 py-3 first:pt-0 last:pb-0">
          <span className="shrink-0 w-6 h-6 rounded-full bg-teal-600 text-white
                           text-xs font-bold flex items-center justify-center mt-0.5">
            {i + 1}
          </span>
          <p className="text-sm text-gray-700 leading-relaxed">{text}</p>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ResultView({ jobId, onReset }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    let cancelled = false;
    getJobResult(jobId)
      .then((res) => { if (!cancelled) { setData(res); setLoading(false); } })
      .catch((err) => { if (!cancelled) { setError(err.message); setLoading(false); } });
    return () => { cancelled = true; };
  }, [jobId]);

  // ── loading ──────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 gap-3">
        <SpinnerIcon className="w-6 h-6 text-teal-600 animate-spin" />
        <p className="text-sm text-gray-500">Loading results…</p>
      </div>
    );
  }

  // ── fetch error ──────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50 p-6 gap-4">
        <div className="card p-8 max-w-md w-full text-center flex flex-col gap-4">
          <WarningIcon className="w-10 h-10 text-red-400 mx-auto" />
          <p className="text-sm text-gray-700">Failed to load results: {error}</p>
          <button onClick={onReset} className="btn-primary mx-auto">Start over</button>
        </div>
      </div>
    );
  }

  // ── result data ──────────────────────────────────────────────────────────
  const result     = data?.result ?? {};
  const outputType = result.output_type ?? "detailed";
  const typeMeta   = meetingTypeMeta(result.meeting_type);
  const incomplete = result.incomplete_action_items ?? [];
  const hasWarning = incomplete.length > 0;
  const isMocked   = result.mocked === true;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-100 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-3 flex items-center justify-between gap-4">
          {/* Back */}
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <ArrowLeftIcon className="w-4 h-4" />
            New recording
          </button>

          {/* Actions */}
          <div className="flex items-center gap-2">
            {data?.confluence_url && (
              <a
                href={data.confluence_url}
                target="_blank"
                rel="noreferrer"
                className="btn-primary py-2 text-xs"
              >
                <ExternalLinkIcon className="w-3.5 h-3.5" />
                Open in Confluence
              </a>
            )}
            <a
              href={getDownloadUrl(jobId)}
              download
              className="btn-secondary py-2 text-xs"
            >
              <DownloadIcon className="w-3.5 h-3.5" />
              Download .docx
            </a>
          </div>
        </div>
      </header>

      {/* ── Document body ───────────────────────────────────────────────── */}
      <main className="max-w-4xl mx-auto px-6 py-8 flex flex-col gap-6">

        {/* ── Title block ─────────────────────────────────────────────── */}
        <div className="flex flex-col gap-2">
          {(outputType === "detailed" || outputType === "mom") && (
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`px-3 py-1 rounded-full text-xs font-semibold ${typeMeta.color}`}>
                {typeMeta.label}
              </span>
              {result.attendees?.length > 0 && (
                <span className="text-sm text-gray-400">
                  {result.attendees.length} attendee{result.attendees.length !== 1 ? "s" : ""}
                </span>
              )}
              {outputType === "mom" && result.date && (
                <span className="text-sm text-gray-400">{result.date}</span>
              )}
            </div>
          )}
          <h1 className="text-2xl font-bold text-gray-900 leading-snug">
            {result.title || "Meeting Notes"}
          </h1>
          {(outputType === "detailed" || outputType === "mom") && result.attendees?.length > 0 && (
            <p className="text-sm text-gray-500">{result.attendees.join(" · ")}</p>
          )}
        </div>

        {/* ── Mock data banner ─────────────────────────────────────────── */}
        {isMocked && (
          <div className="flex items-start gap-3 rounded-xl bg-yellow-50 border border-yellow-300 px-5 py-4">
            <WarningIcon className="w-5 h-5 text-yellow-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-yellow-800">Mock data — not a real transcript</p>
              <p className="text-xs text-yellow-700 mt-0.5">
                <code className="font-mono">MOCK_TRANSCRIPTION</code> and <code className="font-mono">MOCK_AGENT</code> are
                enabled. The content below is a hardcoded stub used for development. Set both flags to{" "}
                <code className="font-mono">false</code> in <code className="font-mono">backend/.env</code> to process real recordings.
              </p>
            </div>
          </div>
        )}

        {/* ── Incomplete action items banner (not shown for quick_summary) ── */}
        {hasWarning && outputType !== "quick_summary" && (
          <div className="flex items-start gap-3 rounded-xl bg-amber-50 border border-amber-200 px-5 py-4">
            <WarningIcon className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
            <div className="flex flex-col gap-1">
              <p className="text-sm font-semibold text-amber-800">
                {incomplete.length} action item{incomplete.length !== 1 ? "s" : ""} need attention
              </p>
              <ul className="flex flex-col gap-0.5">
                {incomplete.map((item, i) => (
                  <li key={i} className="text-xs text-amber-700 flex items-center gap-1.5">
                    <span className="w-1 h-1 rounded-full bg-amber-500 shrink-0" />
                    <span className="font-medium">{item.task}</span>
                    <span className="text-amber-500">— missing {item.missing?.join(" and ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════════════
            DETAILED
        ════════════════════════════════════════════════════════════════ */}
        {outputType === "detailed" && (<>
          <SectionCard title="Summary" icon={<span className="text-teal-600 text-base">📋</span>}>
            {result.summary
              ? <p className="text-sm text-gray-700 leading-relaxed">{result.summary}</p>
              : <EmptyState message="No summary available." />}
          </SectionCard>

          <SectionCard title="Decisions" icon={<span className="text-teal-600 text-base">⚖️</span>}>
            <DecisionsSection decisions={result.decisions} />
          </SectionCard>

          <SectionCard
            title={`Action Items${result.action_items?.length ? ` (${result.action_items.length})` : ""}`}
            icon={<span className="text-teal-600 text-base">✅</span>}
          >
            <ActionItemsTable items={result.action_items} incomplete={incomplete} />
          </SectionCard>

          {result.open_questions?.length > 0 && (
            <SectionCard title="Open Questions" icon={<span className="text-teal-600 text-base">❓</span>}>
              <BulletList items={result.open_questions} numbered emptyText="No open questions recorded." />
            </SectionCard>
          )}

          {result.highlights?.length > 0 && (
            <SectionCard title="Highlights" icon={<span className="text-teal-600 text-base">✨</span>}>
              <BulletList items={result.highlights} emptyText="No highlights recorded." />
            </SectionCard>
          )}
        </>)}

        {/* ════════════════════════════════════════════════════════════════
            MINUTES OF MEETING
        ════════════════════════════════════════════════════════════════ */}
        {outputType === "mom" && (<>
          <SectionCard title="Agenda Items Discussed" icon={<span className="text-teal-600 text-base">📋</span>}>
            <BulletList items={result.agenda_items} numbered emptyText="No agenda items recorded." />
          </SectionCard>

          <SectionCard title="Decisions Made" icon={<span className="text-teal-600 text-base">⚖️</span>}>
            <DecisionsSection decisions={result.decisions} />
          </SectionCard>

          <SectionCard
            title={`Action Items${result.action_items?.length ? ` (${result.action_items.length})` : ""}`}
            icon={<span className="text-teal-600 text-base">✅</span>}
          >
            <ActionItemsTable items={result.action_items} incomplete={incomplete} />
          </SectionCard>

          {result.next_steps?.length > 0 && (
            <SectionCard title="Next Steps" icon={<span className="text-teal-600 text-base">➡️</span>}>
              <BulletList items={result.next_steps} numbered emptyText="No next steps recorded." />
            </SectionCard>
          )}
        </>)}

        {/* ════════════════════════════════════════════════════════════════
            QUICK SUMMARY
        ════════════════════════════════════════════════════════════════ */}
        {outputType === "quick_summary" && (<>
          <SectionCard title="Summary" icon={<span className="text-teal-600 text-base">⚡</span>}>
            <BulletList items={result.bullets} emptyText="No summary available." />
          </SectionCard>

          {result.action_items?.length > 0 && (
            <SectionCard
              title={`Action Items (${result.action_items.length})`}
              icon={<span className="text-teal-600 text-base">✅</span>}
            >
              <ol className="flex flex-col gap-2">
                {result.action_items.map((item, i) => (
                  <li key={i} className="flex items-start gap-3 text-sm text-gray-700">
                    <span className="shrink-0 w-5 h-5 rounded-full bg-teal-100 text-teal-700
                                     text-xs font-bold flex items-center justify-center mt-0.5">
                      {i + 1}
                    </span>
                    <span>
                      <span className="font-medium">{item.owner || "Unassigned"}</span>
                      {": "}
                      {item.task}
                      {item.due && item.due !== "TBD" && (
                        <span className="text-gray-400 ml-1">(by {item.due})</span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </SectionCard>
          )}
        </>)}

        {/* ════════════════════════════════════════════════════════════════
            ACTION ITEMS ONLY
        ════════════════════════════════════════════════════════════════ */}
        {outputType === "action_items" && (
          <SectionCard
            title={`Action Items${result.action_items?.length ? ` (${result.action_items.length})` : ""}`}
            icon={<span className="text-teal-600 text-base">✅</span>}
          >
            <ActionItemsTable items={result.action_items} incomplete={incomplete} />
          </SectionCard>
        )}

        {/* ── Footer ───────────────────────────────────────────────────── */}
        <div className="flex flex-col sm:flex-row gap-3 pt-2 border-t border-gray-100">
          {data?.confluence_url && (
            <a
              href={data.confluence_url}
              target="_blank"
              rel="noreferrer"
              className="btn-primary"
            >
              <ExternalLinkIcon className="w-4 h-4" />
              Open in Confluence
            </a>
          )}
          <a href={getDownloadUrl(jobId)} download className="btn-secondary">
            <DownloadIcon className="w-4 h-4" />
            Download .docx
          </a>
          <button onClick={onReset} className="btn-secondary sm:ml-auto">
            Process another recording
          </button>
        </div>
      </main>
    </div>
  );
}
