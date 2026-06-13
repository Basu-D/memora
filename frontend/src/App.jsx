import { useState, useCallback } from "react";
import UploadView from "./views/UploadView.jsx";
import ProcessingView from "./views/ProcessingView.jsx";
import ResultView from "./views/ResultView.jsx";
import HistoryView from "./views/HistoryView.jsx";

/**
 * Root — owns the view state machine and the shared jobId.
 *
 *  upload ──(job created)──► processing ──(done)──► result
 *    ▲  ▲                        │                     │
 *    │  └──────────(reset)───────┘─────────────────────┘
 *    │
 *    └──(onHistory)──► history ──(onBack)──┘
 *
 * jobId is the only piece of state passed between views.
 * ProcessingView polls status and calls onDone() when status === "done".
 * ResultView fetches /result/{jobId} on mount.
 */
export default function App() {
  const [view,  setView]  = useState("upload");   // "upload" | "processing" | "result" | "history"
  const [jobId, setJobId] = useState(null);

  function handleJobCreated(id) {
    setJobId(id);
    setView("processing");
  }

  // Stable references prevent ProcessingView's useEffect (which depends on
  // onDone/onReset) from re-running and spawning duplicate poll loops on
  // every parent re-render.
  const handleDone = useCallback(() => {
    setView("result");
  }, []);

  const handleReset = useCallback(() => {
    setJobId(null);
    setView("upload");
  }, []);

  const handleHistory = useCallback(() => {
    setView("history");
  }, []);

  const handleBackFromHistory = useCallback(() => {
    setView("upload");
  }, []);

  if (view === "processing") {
    return (
      <ProcessingView
        jobId={jobId}
        onDone={handleDone}
        onReset={handleReset}
      />
    );
  }

  if (view === "result") {
    return <ResultView jobId={jobId} onReset={handleReset} />;
  }

  if (view === "history") {
    return <HistoryView onBack={handleBackFromHistory} />;
  }

  return <UploadView onJobCreated={handleJobCreated} onHistory={handleHistory} />;
}
