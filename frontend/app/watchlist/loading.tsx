/**
 * app/watchlist/loading.tsx
 * ──────────────────────────
 * Page-level skeleton shown while the Watchlist page is loading.
 */
import { Skeleton } from "@/components/ui/skeleton";

export default function WatchlistLoading() {
  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-10 animate-pulse">
      {/* Page header */}
      <div className="space-y-2">
        <Skeleton className="h-7 w-32 bg-zinc-800" />
        <Skeleton className="h-4 w-72 bg-zinc-800" />
      </div>

      {/* Section A — Add Symbols */}
      <div className="space-y-4">
        <Skeleton className="h-4 w-24 bg-zinc-700" />
        <div className="rounded-xl border border-zinc-800 bg-[#161618] p-5 space-y-5">
          <div className="space-y-2">
            <Skeleton className="h-3 w-20 bg-zinc-700" />
            <div className="flex gap-2">
              <Skeleton className="flex-1 h-10 bg-zinc-800 rounded-lg" />
              <Skeleton className="h-10 w-20 bg-zinc-800 rounded-lg" />
            </div>
          </div>
          <Skeleton className="h-px w-full bg-zinc-800" />
          <Skeleton className="h-24 w-full bg-zinc-800 rounded-lg" />
        </div>
      </div>

      {/* Section B — Current Watchlist */}
      <div className="space-y-4">
        <Skeleton className="h-4 w-44 bg-zinc-700" />
        <div className="rounded-xl border border-zinc-800 overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3 border-b border-zinc-800/60">
              <Skeleton className="h-4 w-24 bg-zinc-800" />
              <Skeleton className="h-4 w-40 bg-zinc-800" />
              <Skeleton className="h-4 w-20 bg-zinc-800 ml-auto" />
            </div>
          ))}
        </div>
      </div>

      {/* Section C — Today's Results */}
      <div className="space-y-4">
        <Skeleton className="h-4 w-56 bg-zinc-700" />
        <div className="rounded-xl border border-zinc-800 bg-[#161618] h-40 flex items-center justify-center">
          <Skeleton className="h-4 w-64 bg-zinc-800" />
        </div>
      </div>
    </div>
  );
}
