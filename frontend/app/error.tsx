/**
 * app/error.tsx
 * ─────────────
 * Global error boundary (Client Component).
 * Catches unhandled runtime errors in any page or layout below the root.
 *
 * Next.js requirement: must be "use client" and export a default component
 * that accepts { error, reset } props.
 */
"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    // Log to your error tracker here (e.g. Sentry.captureException(error))
    console.error("[GlobalError]", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] gap-6 px-4 text-center">
      {/* Icon */}
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-red-500/10 border border-red-500/20">
        <AlertTriangle size={28} className="text-red-400" />
      </div>

      {/* Copy */}
      <div className="space-y-2 max-w-sm">
        <h1 className="text-xl font-semibold text-white tracking-tight">
          Something went wrong
        </h1>
        {error.message && (
          <p className="text-sm text-zinc-400 font-mono break-words leading-relaxed">
            {error.message}
          </p>
        )}
        {error.digest && (
          <p className="text-xs text-zinc-600 font-mono">
            Error ID: {error.digest}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center justify-center gap-3">
        <button
          onClick={reset}
          className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-5 py-2.5
                     text-sm font-semibold text-white hover:bg-teal-400 transition-colors"
        >
          Retry
        </button>
        <a
          href="/"
          className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800
                     px-5 py-2.5 text-sm font-semibold text-zinc-300 hover:bg-zinc-700 hover:text-white
                     transition-colors"
        >
          Go to Dashboard
        </a>
      </div>
    </div>
  );
}
