import { useEffect, useRef, useState } from "react";
import { getJobStatus } from "../api.js";

const POLL_MS = 2000;

// ---------------------------------------------------------------------------
// Stepper config
// ---------------------------------------------------------------------------

const STEPS = [
  {
    key:         "downloading",
    label:       "Downloading",
    description: "Downloading recording from URL",
    estSeconds:  30,
  },
  {
    key:         "extracting_audio",
    label:       "Extracting Audio",
    description: "Converting recording to audio",
    estSeconds:  20,
  },
  {
    key:         "transcribing",
    label:       "Transcribing",
    description: "Transcribing speech to text with Whisper",
    estSeconds:  120,
  },
  {
    key:         "processing",
    label:       "AI Processing",
    description: "Extracting insights with Gemini",
    estSeconds:  30,
  },
  {
    key:         "publishing",
    label:       "Publishing",
    description: "Publishing notes to Confluence",
    estSeconds:  10,
  },
];

/**
 * Map backend status → index of the currently active step.
 *  5  = all steps done
 * -2  = failed
 */
const STATUS_TO_ACTIVE = {
  uploaded:         0,
  downloading:      0,
  extracting_audio: 1,
  transcribing:     2,
  processing:       3,
  publishing:       4,
  done:             5,
  failed:           -2,
};

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function CheckIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75 10.5 18.75 19.5 5.25" />
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

function AlertIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948
           3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949
           3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Per-step progress bar
// Fills toward 90% over estSeconds while active, snaps to 100% when done.
// ---------------------------------------------------------------------------

function StepProgressBar({ state, estSeconds }) {
  const [pct, setPct] = useState(0);
  const rafRef  = useRef(null);
  const startRef = useRef(null);

  useEffect(() => {
    if (state === "done") {
      setPct(100);
      cancelAnimationFrame(rafRef.current);
      return;
    }
    if (state === "active") {
      startRef.current = performance.now();
      const tick = () => {
        const elapsed = (performance.now() - startRef.current) / 1000;
        // Ease toward 90% asymptotically so it never "finishes" early
        const progress = 90 * (1 - Math.exp(-elapsed / estSeconds));
        setPct(progress);
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(rafRef.current);
    }
    // pending — reset
    setPct(0);
    cancelAnimationFrame(rafRef.current);
  }, [state, estSeconds]);

  if (state === "pending") return null;

  return (
    <div className="mt-2 h-1 w-full bg-gray-100 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full transition-all ${
          state === "done" ? "bg-teal-500 duration-300" : "bg-teal-400"
        }`}
        style={{ width: `${pct}%`, transition: state === "done" ? "width 0.3s ease" : "none" }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step row
// ---------------------------------------------------------------------------

function StepCircle({ state, index }) {
  const base = "w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition-all duration-300";
  if (state === "done") {
    return (
      <div className={`${base} bg-teal-600`}>
        <CheckIcon className="w-4 h-4 text-white" />
      </div>
    );
  }
  if (state === "active") {
    return (
      <div className={`${base} bg-teal-600 ring-4 ring-teal-200`}>
        <SpinnerIcon className="w-4 h-4 text-white animate-spin" />
      </div>
    );
  }
  return (
    <div className={`${base} bg-gray-100 border-2 border-gray-200`}>
      <span className="text-xs font-semibold text-gray-400">{index + 1}</span>
    </div>
  );
}

function StepConnector({ done }) {
  return (
    <div className="ml-[17px] w-0.5 h-6 transition-colors duration-500"
         style={{ background: done ? "#0d9488" : "#e5e7eb" }} />
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ProcessingView({ jobId, onDone, onReset }) {
  const [status,    setStatus]    = useState("uploaded");
  const [filename,  setFilename]  = useState("");
  const [error,     setError]     = useState(null);
  const [pollError, setPollError] = useState(null);

  const timerRef = useRef(null);

  // ── polling ──────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const data = await getJobStatus(jobId);
        if (cancelled) return;

        setPollError(null);
        setStatus(data.status);
        if (data.filename) setFilename(data.filename);

        if (data.status === "done") {
          onDone();
          return;
        }
        if (data.status === "failed") {
          setError(data.error_message ?? "An unknown error occurred.");
          return;
        }

        timerRef.current = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (cancelled) return;
        setPollError(err.message);
        timerRef.current = setTimeout(poll, POLL_MS);
      }
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, [jobId, onDone]);

  // ── derived state ────────────────────────────────────────────────────────
  const activeIdx = STATUS_TO_ACTIVE[status] ?? 0;
  const isFailed  = status === "failed";

  // For file uploads, skip the "downloading" step visually — it's never active.
  const visibleSteps = status === "uploaded" || STATUS_TO_ACTIVE[status] === undefined
    // We don't know yet — show all
    ? STEPS
    // Hide downloading step only if it was never the active step AND job is past it
    : STEPS.filter((s, i) => !(s.key === "downloading" && activeIdx > 0 && status !== "downloading" && i === 0 && activeIdx === 1 && false));
  // Simpler: always show all steps; file-upload jobs just skip "downloading" instantly.

  // ── render ──────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-gray-50">
      {/* Logo */}
      <div className="mb-8 text-center">
        <div className="inline-flex items-center gap-2">
          <span className="text-2xl">🎙</span>
          <span className="text-xl font-bold text-gray-900">Memora</span>
        </div>
      </div>

      <div className="w-full max-w-md card p-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-gray-900">
            {isFailed ? "Processing failed" : "Processing your meeting"}
          </h1>
          {filename && (
            <p className="text-sm text-gray-500 mt-0.5 truncate" title={filename}>
              {filename}
            </p>
          )}
          <p className="text-xs text-gray-400 mt-0.5 font-mono">{jobId}</p>
        </div>

        {/* Error state */}
        {isFailed ? (
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-3 rounded-xl bg-red-50 border border-red-100 p-4">
              <AlertIcon className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-red-700">Something went wrong</p>
                <p className="text-sm text-red-600 mt-1">{error}</p>
              </div>
            </div>
            <button onClick={onReset} className="btn-primary w-full">
              Start over
            </button>
          </div>
        ) : (
          /* Stepper */
          <div className="flex flex-col">
            {STEPS.map((step, i) => {
              const stepState =
                i < activeIdx  ? "done"
                : i === activeIdx ? "active"
                : "pending";
              const isLast = i === STEPS.length - 1;

              return (
                <div key={step.key}>
                  <div className="flex items-start gap-4">
                    <StepCircle state={stepState} index={i} />
                    <div className="flex-1 pt-1.5 pb-1">
                      <div className="flex items-center justify-between">
                        <span
                          className={`text-sm font-semibold transition-colors duration-200 ${
                            stepState === "done"    ? "text-teal-700"
                            : stepState === "active" ? "text-gray-900"
                            : "text-gray-400"
                          }`}
                        >
                          {step.label}
                        </span>
                        {stepState === "active" && (
                          <span className="text-xs text-teal-600 font-medium animate-pulse">
                            {step.description}
                          </span>
                        )}
                        {stepState === "done" && (
                          <span className="text-xs text-teal-500">Done</span>
                        )}
                      </div>
                      <StepProgressBar state={stepState} estSeconds={step.estSeconds} />
                    </div>
                  </div>
                  {!isLast && <StepConnector done={i < activeIdx} />}
                </div>
              );
            })}
          </div>
        )}

        {/* Poll error banner */}
        {pollError && !isFailed && (
          <p className="mt-4 text-xs text-amber-600 text-center">
            Network error — retrying… ({pollError})
          </p>
        )}
      </div>

      {/* Cancel */}
      {!isFailed && (
        <button
          onClick={onReset}
          className="mt-5 text-sm text-gray-400 hover:text-gray-600 transition-colors"
        >
          Cancel and start over
        </button>
      )}
    </div>
  );
}
