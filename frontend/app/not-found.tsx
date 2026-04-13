/**
 * app/not-found.tsx
 * ─────────────────
 * Global 404 page.
 * Rendered automatically by Next.js whenever notFound() is thrown
 * or a route doesn't match.
 */
import Link from "next/link";
import { SearchX } from "lucide-react";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] gap-6 px-4 text-center">
      {/* Icon */}
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800/60 border border-zinc-700">
        <SearchX size={28} className="text-zinc-400" />
      </div>

      {/* Copy */}
      <div className="space-y-2 max-w-sm">
        <p className="text-xs font-mono text-zinc-500 uppercase tracking-widest">404</p>
        <h1 className="text-xl font-semibold text-white tracking-tight">
          Page not found
        </h1>
        <p className="text-sm text-zinc-400 leading-relaxed">
          This symbol or page doesn&apos;t exist. It may have been removed or
          the URL is incorrect.
        </p>
      </div>

      {/* Back button */}
      <Link
        href="/screener"
        className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-5 py-2.5
                   text-sm font-semibold text-white hover:bg-teal-400 transition-colors"
      >
        ← Back to Screener
      </Link>
    </div>
  );
}
