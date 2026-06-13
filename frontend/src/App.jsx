import { useState, useCallback } from "react";
import UploadView from "./views/UploadView.jsx";
import ProcessingView from "./views/ProcessingView.jsx";
import ResultView from "./views/ResultView.jsx";

/**
 * Root — owns the three-view state machine and the shared jobId.
 *
 *  upload ──(job created)──► processing ──(done)──► result
 *    ▲                           │                     │
 *    └───────────(reset)─────────┘─────────────────────┘
 *
 * jobId is the only piece of state passed between views.
 * ProcessingView polls status and calls onDone() when status === "done".
 * ResultView fetches /result/{jobId} on mount.
 */
export default function App() {
  const [view,  setView]  = useState("upload");   // "upload" | "processing" | "result"
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

  return <UploadView onJobCreated={handleJobCreated} />;
}
