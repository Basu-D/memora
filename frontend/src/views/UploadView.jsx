import { useState, useRef, useCallback, useEffect } from "react";
import {
  uploadMeeting, submitUrlMeeting,
  getConfluenceSpaces, getConfluencePages,
} from "../api.js";

const ACCEPTED_EXTENSIONS = [".mp4", ".mp3", ".webm", ".wav", ".m4a"];
const ACCEPTED_ACCEPT      = ACCEPTED_EXTENSIONS.join(",");
const MAX_BYTES            = 500 * 1024 * 1024; // 500 MB

const OUTPUT_TYPES = [
  { value: "detailed",      label: "Detailed Document",   description: "Full notes: summary, decisions, action items, open questions and highlights." },
  { value: "mom",           label: "Minutes of Meeting",  description: "Formal MoM: agenda items discussed, decisions made, action items, next steps." },
  { value: "quick_summary", label: "Quick Summary",       description: "3–5 bullet points covering what was discussed, decided, and who owns what." },
  { value: "action_items",  label: "Action Items Only",   description: "Just the action items table: owner, task, and deadline." },
];

const CONFLUENCE_ON_BY_DEFAULT = new Set(["detailed", "mom"]);

// ---------------------------------------------------------------------------
// localStorage preferences
// ---------------------------------------------------------------------------

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("memora_preferences") || "{}"); }
  catch { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem("memora_preferences", JSON.stringify(prefs)); }
  catch { /* quota exceeded */ }
}

function updatePrefsAfterSubmit({ outputType, publishToConfluence, selectedSpace, selectedParentPage }) {
  const prefs = loadPrefs();
  prefs.output_type = outputType;

  const conf = prefs.confluence || {};
  conf.create_page_default = publishToConfluence;

  if (selectedSpace) {
    conf.last_space = { key: selectedSpace.key, name: selectedSpace.name };
    const rs = (conf.recent_spaces || []).filter(s => s.key !== selectedSpace.key);
    conf.recent_spaces = [{ key: selectedSpace.key, name: selectedSpace.name }, ...rs].slice(0, 5);
  }

  if (selectedSpace && selectedParentPage) {
    conf.last_parent_page = { id: selectedParentPage.id, title: selectedParentPage.title };
    const rp = conf.recent_parent_pages || {};
    const pages = (rp[selectedSpace.key] || []).filter(p => p.id !== selectedParentPage.id);
    rp[selectedSpace.key] = [
      { id: selectedParentPage.id, title: selectedParentPage.title },
      ...pages,
    ].slice(0, 5);
    conf.recent_parent_pages = rp;
  }

  prefs.confluence = conf;
  savePrefs(prefs);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
// Icons (inline SVG)
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

function SearchIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// ParentPageSelect — searchable dropdown with "Recently used" / "All pages"
// ---------------------------------------------------------------------------

function ParentPageSelect({ pages, recentPages, selectedPage, onSelect, loading }) {
  const [search, setSearch]   = useState("");
  const [open, setOpen]       = useState(false);
  const containerRef          = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handler(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const q = search.toLowerCase();
  const filteredAll    = pages.filter(p => p.title.toLowerCase().includes(q));
  const filteredRecent = recentPages.filter(p => p.title.toLowerCase().includes(q));

  function choose(page) {
    onSelect(page);
    setOpen(false);
    setSearch("");
  }

  function clearSelection(e) {
    e.stopPropagation();
    onSelect(null);
  }

  const inputCls = "w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 " +
    "text-sm text-gray-900 placeholder:text-gray-400 " +
    "focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500 transition-colors";

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
        <SpinnerIcon className="w-4 h-4 animate-spin" />
        Loading pages…
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Display box */}
      <div
        className={`${inputCls} flex items-center justify-between cursor-pointer`}
        onClick={() => setOpen(o => !o)}
      >
        <span className={selectedPage ? "text-gray-900" : "text-gray-400"}>
          {selectedPage ? selectedPage.title : "None (create at space root)"}
        </span>
        <div className="flex items-center gap-1 shrink-0">
          {selectedPage && (
            <button
              type="button"
              onClick={clearSelection}
              className="text-gray-400 hover:text-red-500 transition-colors"
            >
              <XCircleIcon className="w-4 h-4" />
            </button>
          )}
          <span className="text-gray-400 text-xs ml-1">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-xl border border-gray-200 bg-white shadow-lg overflow-hidden">
          {/* Search input */}
          <div className="p-2 border-b border-gray-100">
            <div className="relative">
              <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                autoFocus
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search pages…"
                className="w-full rounded-lg border border-gray-200 pl-7 pr-3 py-1.5 text-sm
                           focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500"
              />
            </div>
          </div>

          <div className="max-h-56 overflow-y-auto">
            {/* Recently used section */}
            {filteredRecent.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide bg-gray-50">
                  Recently used
                </div>
                {filteredRecent.map(p => (
                  <button
                    key={`recent-${p.id}`}
                    type="button"
                    onClick={() => choose(p)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-teal-50 hover:text-teal-700 transition-colors flex items-center gap-2
                                ${selectedPage?.id === p.id ? "bg-teal-50 text-teal-700 font-medium" : "text-gray-700"}`}
                  >
                    <span className="text-gray-400">📄</span>
                    {p.title}
                  </button>
                ))}
                {filteredAll.length > 0 && <div className="border-t border-gray-100" />}
              </>
            )}

            {/* All pages section */}
            {filteredAll.length > 0 && (
              <>
                {filteredRecent.length > 0 && (
                  <div className="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide bg-gray-50">
                    All pages
                  </div>
                )}
                {filteredAll.map(p => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => choose(p)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-teal-50 hover:text-teal-700 transition-colors flex items-center gap-2
                                ${selectedPage?.id === p.id ? "bg-teal-50 text-teal-700 font-medium" : "text-gray-700"}`}
                  >
                    <span className="text-gray-400">📄</span>
                    {p.title}
                  </button>
                ))}
              </>
            )}

            {filteredAll.length === 0 && filteredRecent.length === 0 && (
              <div className="px-3 py-4 text-sm text-gray-400 text-center">No pages found</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function UploadView({ onJobCreated }) {
  const [mode,      setMode]      = useState("file"); // "file" | "url"

  // ── file mode ───────────────────────────────────────────────────────────
  const [file,      setFile]      = useState(null);
  const [dragging,  setDragging]  = useState(false);
  const [fileError, setFileError] = useState(null);

  // ── url mode ────────────────────────────────────────────────────────────
  const [url,       setUrl]       = useState("");
  const [urlError,  setUrlError]  = useState(null);

  // ── shared state ────────────────────────────────────────────────────────
  const [title,                  setTitle]                  = useState("");
  const [outputType,             setOutputType]             = useState("detailed");
  const [publishToConfluence,    setPublishToConfluence]    = useState(true);
  const [customInstructions,     setCustomInstructions]     = useState("");
  const [showCustomInstructions, setShowCustomInstructions] = useState(false);
  const [submitting,             setSubmitting]             = useState(false);
  const [submitErr,              setSubmitErr]              = useState(null);

  // ── §4.5 Confluence destination ─────────────────────────────────────────
  const [spaces,              setSpaces]              = useState([]);
  const [spacesLoading,       setSpacesLoading]       = useState(false);
  const [spacesError,         setSpacesError]         = useState(null);
  const [selectedSpace,       setSelectedSpace]       = useState(null);  // {key, name}
  const [pages,               setPages]               = useState([]);
  const [pagesLoading,        setPagesLoading]        = useState(false);
  const [selectedParentPage,  setSelectedParentPage]  = useState(null);  // {id, title}
  const [pageTitle,           setPageTitle]           = useState("");
  const [pageTitleTouched,    setPageTitleTouched]    = useState(false);

  // ── §4.3 Context input ──────────────────────────────────────────────────
  const [showContext,          setShowContext]          = useState(false);
  const [contextTab,           setContextTab]           = useState("text"); // "text" | "confluence"
  const [contextText,          setContextText]          = useState("");
  const [contextReferenceUrl,  setContextReferenceUrl]  = useState("");

  const CUSTOM_INSTRUCTIONS_MAX = 500;
  const inputRef = useRef(null);

  // ── Load preferences on mount ───────────────────────────────────────────
  useEffect(() => {
    const prefs = loadPrefs();
    if (prefs.output_type) {
      setOutputType(prefs.output_type);
    }
    const conf = prefs.confluence;
    if (conf) {
      const shouldPublish = conf.create_page_default !== undefined
        ? conf.create_page_default
        : CONFLUENCE_ON_BY_DEFAULT.has(prefs.output_type || "detailed");
      setPublishToConfluence(shouldPublish);
    }
  }, []);

  // ── Sync page title with meeting title (if not manually touched) ────────
  useEffect(() => {
    if (!pageTitleTouched) setPageTitle(title);
  }, [title, pageTitleTouched]);

  // ── Fetch Confluence spaces when publish is toggled on ──────────────────
  const fetchSpaces = useCallback(() => {
    setSpacesLoading(true);
    setSpacesError(null);
    getConfluenceSpaces()
      .then(data => {
        setSpaces(data);
        const prefs = loadPrefs();
        const lastSpaceKey = prefs.confluence?.last_space?.key;
        if (lastSpaceKey) {
          const match = data.find(s => s.key === lastSpaceKey);
          if (match) setSelectedSpace(match);
        } else if (data.length === 1) {
          setSelectedSpace(data[0]);
        }
      })
      .catch(err => setSpacesError(err.message || "Failed to load spaces"))
      .finally(() => setSpacesLoading(false));
  }, []);

  useEffect(() => {
    if (publishToConfluence && spaces.length === 0 && !spacesLoading) {
      fetchSpaces();
    }
  }, [publishToConfluence]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Fetch pages when space changes ──────────────────────────────────────
  useEffect(() => {
    if (!selectedSpace) { setPages([]); setSelectedParentPage(null); return; }
    setPagesLoading(true);
    getConfluencePages(selectedSpace.key)
      .then(data => {
        setPages(data);
        // Restore last used page for this space
        const prefs = loadPrefs();
        const recents = prefs.confluence?.recent_parent_pages?.[selectedSpace.key] || [];
        if (recents.length > 0) {
          const lastId = recents[0].id;
          const match = data.find(p => p.id === lastId);
          setSelectedParentPage(match || null);
        } else {
          setSelectedParentPage(null);
        }
      })
      .catch(() => setPages([]))
      .finally(() => setPagesLoading(false));
  }, [selectedSpace]);

  // ── Output type handling ────────────────────────────────────────────────
  function handleOutputTypeChange(val) {
    setOutputType(val);
    setPublishToConfluence(CONFLUENCE_ON_BY_DEFAULT.has(val));
  }

  // ── File handling ───────────────────────────────────────────────────────
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

  function handleInputChange(e) { pickFile(e.target.files?.[0]); e.target.value = ""; }
  function handleDrop(e) { e.preventDefault(); setDragging(false); pickFile(e.dataTransfer.files?.[0]); }
  function clearFile() { setFile(null); setFileError(null); }
  function switchMode(m) { setMode(m); setSubmitErr(null); setUrlError(null); setFileError(null); }

  // ── Submit ──────────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    if (submitting) return;

    const confluenceDest = publishToConfluence ? {
      space_key:      selectedSpace?.key || "",
      parent_page_id: selectedParentPage?.id || "",
      page_title:     pageTitle.trim() || title.trim() || "",
    } : {};

    if (mode === "file") {
      if (!file) return;
      setSubmitting(true);
      setSubmitErr(null);
      try {
        const { job_id } = await uploadMeeting(
          file, title, outputType, publishToConfluence,
          customInstructions, confluenceDest, contextText, contextReferenceUrl,
        );
        updatePrefsAfterSubmit({ outputType, publishToConfluence, selectedSpace, selectedParentPage });
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
        const { job_id } = await submitUrlMeeting(
          trimmed, title, outputType, publishToConfluence,
          customInstructions, confluenceDest, contextText, contextReferenceUrl,
        );
        updatePrefsAfterSubmit({ outputType, publishToConfluence, selectedSpace, selectedParentPage });
        onJobCreated(job_id);
      } catch (err) {
        setSubmitErr(err.message);
        setSubmitting(false);
      }
    }
  }

  const canSubmit = mode === "file" ? !!file && !submitting : !!url.trim() && !submitting;

  const inputCls = "w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 " +
    "text-sm text-gray-900 placeholder:text-gray-400 " +
    "focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500 transition-colors";

  // ── Render ──────────────────────────────────────────────────────────────
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
          {[
            { key: "file", icon: <UploadCloudIcon className="w-4 h-4" />, label: "Upload File" },
            { key: "url",  icon: <LinkIcon className="w-4 h-4" />,         label: "Paste URL" },
          ].map(({ key, icon, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => switchMode(key)}
              className={[
                "flex-1 flex items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium transition-colors",
                mode === key ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700",
              ].join(" ")}
            >
              {icon}{label}
            </button>
          ))}
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
                <XCircleIcon className="w-4 h-4 mt-0.5 shrink-0" />{fileError}
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
                className={`${inputCls} pl-9`}
              />
            </div>
            {urlError && (
              <p className="text-sm text-red-600 flex items-start gap-1.5">
                <XCircleIcon className="w-4 h-4 mt-0.5 shrink-0" />{urlError}
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
            className={inputCls}
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
            className={inputCls}
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

        {/* §4.5 Confluence destination */}
        {publishToConfluence && (
          <div className="flex flex-col gap-3 rounded-xl border border-gray-200 p-4 bg-gray-50">
            <p className="text-sm font-medium text-gray-700">Confluence destination</p>

            {/* Space */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-gray-600">Space</label>
              {spacesLoading ? (
                <div className="flex items-center gap-2 text-sm text-gray-500 py-1">
                  <SpinnerIcon className="w-4 h-4 animate-spin shrink-0" />
                  Loading spaces…
                </div>
              ) : spacesError ? (
                <div className="flex items-center gap-2 text-sm text-red-600">
                  Could not load spaces.
                  <button type="button" onClick={fetchSpaces}
                    className="underline text-teal-600 hover:text-teal-700">Retry</button>
                </div>
              ) : (
                <select
                  value={selectedSpace?.key || ""}
                  onChange={(e) => {
                    const s = spaces.find(sp => sp.key === e.target.value) || null;
                    setSelectedSpace(s);
                  }}
                  className={inputCls}
                >
                  <option value="">Select a space…</option>
                  {spaces.map(s => (
                    <option key={s.key} value={s.key}>{s.name}</option>
                  ))}
                </select>
              )}
            </div>

            {/* Parent page */}
            {selectedSpace && (
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-gray-600">
                  Parent page <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <ParentPageSelect
                  pages={pages}
                  recentPages={loadPrefs().confluence?.recent_parent_pages?.[selectedSpace.key] || []}
                  selectedPage={selectedParentPage}
                  onSelect={setSelectedParentPage}
                  loading={pagesLoading}
                />
              </div>
            )}

            {/* Page title */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-gray-600">Page title</label>
              <input
                type="text"
                value={pageTitle}
                onChange={(e) => { setPageTitle(e.target.value); setPageTitleTouched(true); }}
                placeholder={title || "Meeting Notes"}
                maxLength={512}
                className={inputCls}
              />
            </div>
          </div>
        )}

        {/* §4.2 Custom instructions */}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => setShowCustomInstructions((v) => !v)}
            className="flex items-center gap-1.5 text-sm text-teal-600 hover:text-teal-700 font-medium w-fit transition-colors"
          >
            <span className="text-base leading-none">{showCustomInstructions ? "−" : "+"}</span>
            {showCustomInstructions ? "Hide custom instructions" : "Add custom instructions"}
          </button>

          {showCustomInstructions && (
            <div className="flex flex-col gap-1.5">
              <textarea
                value={customInstructions}
                onChange={(e) => setCustomInstructions(e.target.value.slice(0, CUSTOM_INSTRUCTIONS_MAX))}
                placeholder={"Focus on technical decisions and skip small talk\nHighlight any risks or blockers mentioned\nExtract all dates and deadlines mentioned"}
                rows={3}
                className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5
                           text-sm text-gray-900 placeholder:text-gray-400 resize-none
                           focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                           transition-colors"
              />
              <p className={`text-xs text-right ${customInstructions.length >= CUSTOM_INSTRUCTIONS_MAX ? "text-red-500" : "text-gray-400"}`}>
                {customInstructions.length} / {CUSTOM_INSTRUCTIONS_MAX}
              </p>
            </div>
          )}
        </div>

        {/* §4.3 Context input */}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => setShowContext((v) => !v)}
            className="flex items-center gap-1.5 text-sm text-teal-600 hover:text-teal-700 font-medium w-fit transition-colors"
          >
            <span className="text-base leading-none">{showContext ? "−" : "+"}</span>
            {showContext ? "Hide meeting context" : "Add meeting context"}
          </button>

          {showContext && (
            <div className="flex flex-col gap-2 rounded-xl border border-gray-200 p-3 bg-gray-50">
              {/* Tab toggle */}
              <div className="flex gap-1 text-xs">
                {[
                  { key: "text",       label: "Text context" },
                  { key: "confluence", label: "Confluence reference" },
                ].map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setContextTab(key)}
                    className={[
                      "px-3 py-1 rounded-lg font-medium transition-colors",
                      contextTab === key
                        ? "bg-white text-gray-900 shadow-sm border border-gray-200"
                        : "text-gray-500 hover:text-gray-700",
                    ].join(" ")}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {contextTab === "text" && (
                <textarea
                  value={contextText}
                  onChange={(e) => setContextText(e.target.value)}
                  placeholder="Describe what this meeting is about or provide background, e.g. 'Post-mortem for the payment gateway outage on May 12th'"
                  rows={3}
                  className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5
                             text-sm text-gray-900 placeholder:text-gray-400 resize-none
                             focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500
                             transition-colors"
                />
              )}

              {contextTab === "confluence" && (
                <div className="flex flex-col gap-1.5">
                  <input
                    type="url"
                    value={contextReferenceUrl}
                    onChange={(e) => setContextReferenceUrl(e.target.value)}
                    placeholder="https://your-org.atlassian.net/wiki/spaces/…"
                    className={inputCls}
                  />
                  <p className="text-xs text-gray-400">
                    The content of this Confluence page will be fetched and provided as background context to the AI.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* §4.4 Screenshot capture — coming soon */}
        <label className="flex items-center gap-3 cursor-not-allowed select-none opacity-50" title="Coming soon">
          <input
            type="checkbox"
            disabled
            className="w-4 h-4 rounded border-gray-300 accent-teal-600 cursor-not-allowed"
          />
          <span className="text-sm text-gray-700">
            Capture presentation screenshots
            <span className="ml-1.5 text-xs text-gray-400 font-normal">Coming soon</span>
          </span>
        </label>

        {/* Submit error */}
        {submitErr && (
          <div className="rounded-xl bg-red-50 border border-red-100 px-4 py-3">
            <p className="text-sm text-red-700">{submitErr}</p>
          </div>
        )}

        {/* Submit button */}
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
        Your recording is processed securely and never stored beyond your session.
      </p>
    </div>
  );
}
