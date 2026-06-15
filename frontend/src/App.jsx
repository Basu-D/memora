import { useState, useCallback } from "react";
import UploadView from "./views/UploadView.jsx";
import ProcessingView from "./views/ProcessingView.jsx";
import ResultView from "./views/ResultView.jsx";
import HistoryView from "./views/HistoryView.jsx";
import SettingsView from "./views/SettingsView.jsx";

/**
 * Root — owns the view state machine and the shared jobId.
 *
 *  upload ──(job created)──► processing ──(done)──► result
 *    ▲  ▲                        │                     │
 *    │  └──────────(reset)───────┘─────────────────────┘
 *    │
 *    ├──(onHistory)──► history ──(onBack)──┘
 *    │                    │
 *    └──(onSettings)──► settings ──(onBack)──► (previous view)
 *
 * jobId is the only piece of state passed between views.
 * ProcessingView polls status and calls onDone() when status === "done".
 * ResultView fetches /result/{jobId} on mount.
 */
export default function App() {
  const [view,     setView]     = useState("upload");  // "upload"|"processing"|"result"|"history"|"settings"
  const [jobId,    setJobId]    = useState(null);
  const [prevView, setPrevView] = useState("upload");  // where Settings navigates back to

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

  const handleSettings = useCallback(() => {
    // Capture the current view so Settings knows where to navigate back to.
    setView((current) => { setPrevView(current); return "settings"; });
  }, []);

  const handleBackFromSettings = useCallback(() => {
    setView(prevView);
  }, [prevView]);

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
    return (
      <HistoryView
        onBack={handleBackFromHistory}
        onSettings={handleSettings}
      />
    );
  }

  if (view === "settings") {
    return <SettingsView onBack={handleBackFromSettings} />;
  }

  return (
    <UploadView
      onJobCreated={handleJobCreated}
      onHistory={handleHistory}
      onSettings={handleSettings}
    />
  );
}
