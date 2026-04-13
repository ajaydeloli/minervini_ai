/**
 * app/screener/loading.tsx
 * ─────────────────────────
 * Page-level skeleton shown while the Screener page is loading.
 * Next.js streams this immediately — the real page replaces it once ready.
 */
import { Skeleton } from "@/components/ui/skeleton";

export default function ScreenerLoading() {
  return (
    <div className="px-4 md:px-8 py-6 max-w-[1600px] mx-auto animate-pulse">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div className="space-y-2">
          <Skeleton className="h-8 w-32 bg-zinc-800" />
          <Skeleton className="h-4 w-52 bg-zinc-800" />
        </div>
        <Skeleton className="h-9 w-28 bg-zinc-800 rounded-lg" />
      </div>

      {/* Filter bar */}
      <Skeleton className="h-16 w-full bg-zinc-800 rounded-xl mb-4" />

      {/* Result count */}
      <Skeleton className="h-4 w-40 bg-zinc-800 mb-3" />

      {/* Table */}
      <div className="rounded-xl border border-zinc-800 overflow-hidden">
        {/* Header row */}
        <div className="flex gap-4 px-4 py-3 border-b border-zinc-800 bg-[#111113]">
          {[8, 16, 24, 14, 12, 10, 10, 14, 14, 12, 10, 10].map((w, i) => (
            <Skeleton key={i} className={`h-3 w-${w} bg-zinc-700`} />
          ))}
        </div>
        {/* Body rows */}
        {Array.from({ length: 12 }).map((_, i) => (
          <div
            key={i}
            className="flex gap-4 items-center px-4 py-3 border-b border-zinc-800/60"
          >
            <Skeleton className="h-3.5 w-8 bg-zinc-800" />
            <Skeleton className="h-3.5 w-20 bg-zinc-800" />
            <Skeleton className="h-2 w-24 bg-zinc-800 rounded-full" />
            <Skeleton className="h-5 w-10 bg-zinc-800 rounded-md" />
            <Skeleton className="h-3.5 w-14 bg-zinc-800" />
            <Skeleton className="h-3.5 w-10 bg-zinc-800" />
          </div>
        ))}
      </div>
    </div>
  );
}
