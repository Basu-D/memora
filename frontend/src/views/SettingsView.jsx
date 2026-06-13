import { useEffect, useState } from "react";
import { getUserPreferences, updateUserPreferences } from "../api.js";

// TODO: replace with SSO — email-based identity is a temporary placeholder
const STORAGE_KEY = "current_user_email";

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

function SpinnerIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4Z" />
    </svg>
  );
}

function CheckIcon({ className }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
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

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SettingsView({ onBack }) {
  const [email,        setEmail]        = useState("");
  const [spaceKey,     setSpaceKey]     = useState("");
  const [parentPageId, setParentPageId] = useState("");
  const [prefsLoading, setPrefsLoading] = useState(false);
  const [saving,       setSaving]       = useState(false);
  const [toast,        setToast]        = useState(null); // { type: "success"|"error", message }

  // Load email from localStorage; if present fetch saved prefs from API.
  useEffect(() => {
    // TODO: replace with SSO — reading email from localStorage is temporary
    const saved = localStorage.getItem(STORAGE_KEY) || "";
    setEmail(saved);

    if (saved) {
      setPrefsLoading(true);
      getUserPreferences(saved)
        .then((data) => {
          setSpaceKey(data.confluence_space_key || "");
          setParentPageId(data.confluence_parent_page_id || "");
        })
        .catch(() => {}) // 404 is fine — user hasn't saved prefs yet
        .finally(() => setPrefsLoading(false));
    }
  }, []);

  // Auto-dismiss toast after 3 s.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  async function handleSave(e) {
    e.preventDefault();
    const trimmedEmail = email.trim();

    if (trimmedEmail && !trimmedEmail.includes("@")) {
      setToast({ type: "error", message: "Please enter a valid email address." });
      return;
    }

    setSaving(true);
    setToast(null);

    // TODO: replace with SSO — persisting email to localStorage is temporary
    if (trimmedEmail) {
      localStorage.setItem(STORAGE_KEY, trimmedEmail);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }

    if (trimmedEmail) {
      try {
        await updateUserPreferences(trimmedEmail, {
          confluence_space_key:      spaceKey.trim(),
          confluence_parent_page_id: parentPageId.trim(),
        });
        setToast({ type: "success", message: "Settings saved." });
      } catch (err) {
        setToast({ type: "error", message: err.message || "Failed to save settings." });
      }
    } else {
      setToast({ type: "success", message: "Email cleared." });
    }

    setSaving(false);
  }

  const inputCls =
    "w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 " +
    "text-sm text-gray-900 placeholder:text-gray-400 " +
    "focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-500 transition-colors";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Sticky header ─────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-100 sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <ArrowLeftIcon className="w-4 h-4" />
            Back
          </button>
          <div className="flex items-center gap-1.5 ml-auto">
            <span className="text-lg leading-none">🎙</span>
            <span className="font-bold text-gray-900">Memora</span>
          </div>
        </div>
      </header>

      {/* ── Page body ─────────────────────────────────────────────────────── */}
      <main className="max-w-3xl mx-auto px-6 py-8 flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
          <p className="text-sm text-gray-500 mt-1">Configure your Memora preferences.</p>
        </div>

        <form onSubmit={handleSave} className="flex flex-col gap-5">
          {/* ── Identity ────────────────────────────────────────────────────── */}
          <div className="card p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-gray-900">Your identity</h2>
              {/* TODO: replace with SSO */}
              <p className="text-xs text-amber-600 mt-0.5">
                Temporary email login — SSO will replace this.
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="settings-email" className="text-sm font-medium text-gray-700">
                Email address
              </label>
              <input
                id="settings-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                autoComplete="email"
                className={inputCls}
              />
              <p className="text-xs text-gray-400">
                Used to filter your meetings on the dashboard and receive completion notifications.
              </p>
            </div>
          </div>

          {/* ── Confluence defaults ──────────────────────────────────────────── */}
          <div className="card p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-gray-900">Default Confluence destination</h2>
              <p className="text-sm text-gray-500 mt-0.5">
                Pre-fills the upload form for every new recording.
              </p>
            </div>

            {prefsLoading ? (
              <div className="flex items-center gap-2 text-sm text-gray-400 py-2">
                <SpinnerIcon className="w-4 h-4 animate-spin" />
                Loading saved preferences…
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="settings-space-key" className="text-sm font-medium text-gray-700">
                    Default Space Key
                  </label>
                  <input
                    id="settings-space-key"
                    type="text"
                    value={spaceKey}
                    onChange={(e) => setSpaceKey(e.target.value)}
                    placeholder="e.g. ~TEAM or ENG"
                    maxLength={64}
                    className={inputCls}
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <label htmlFor="settings-parent-page" className="text-sm font-medium text-gray-700">
                    Default Parent Page ID
                  </label>
                  <input
                    id="settings-parent-page"
                    type="text"
                    value={parentPageId}
                    onChange={(e) => setParentPageId(e.target.value)}
                    placeholder="e.g. 123456789"
                    maxLength={64}
                    className={inputCls}
                  />
                  <p className="text-xs text-gray-400">
                    Find it in Confluence — open the page, click ⋯, then "Page information".
                  </p>
                </div>
              </>
            )}
          </div>

          {/* ── Toast ───────────────────────────────────────────────────────── */}
          {toast && (
            <div
              className={[
                "flex items-center gap-2.5 rounded-xl px-4 py-3 text-sm font-medium",
                toast.type === "success"
                  ? "bg-teal-50 border border-teal-100 text-teal-700"
                  : "bg-red-50 border border-red-100 text-red-700",
              ].join(" ")}
            >
              {toast.type === "success"
                ? <CheckIcon className="w-4 h-4 shrink-0" />
                : <XCircleIcon className="w-4 h-4 shrink-0" />}
              {toast.message}
            </div>
          )}

          {/* ── Save ────────────────────────────────────────────────────────── */}
          <button
            type="submit"
            disabled={saving || prefsLoading}
            className="btn-primary w-full py-3 text-base"
          >
            {saving ? (
              <>
                <SpinnerIcon className="w-4 h-4 animate-spin" />
                Saving…
              </>
            ) : (
              "Save settings"
            )}
          </button>
        </form>
      </main>
    </div>
  );
}
