/**
 * app/portfolio/loading.tsx
 * ─────────────────────────
 * Page-level skeleton shown while the Portfolio page is loading.
 */
import { Skeleton } from "@/components/ui/skeleton";

export default function PortfolioLoading() {
  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-6 animate-pulse">
      {/* Page header */}
      <div className="space-y-2">
        <Skeleton className="h-7 w-40 bg-zinc-800" />
        <Skeleton className="h-4 w-64 bg-zinc-800" />
      </div>

      {/* KPI cards — 2×3 on mobile, 5-across on desktop */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4 space-y-3"
          >
            <div className="flex items-center justify-between">
              <Skeleton className="h-3 w-20 bg-zinc-700" />
              <Skeleton className="h-8 w-8 rounded-lg bg-zinc-700" />
            </div>
            <Skeleton className="h-7 w-28 bg-zinc-700" />
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <Skeleton className="h-[250px] w-full rounded-xl bg-zinc-800" />

      {/* Tab bar */}
      <div className="rounded-xl border border-[#1E1E21] bg-[#161618] overflow-hidden">
        <div className="flex border-b border-zinc-800">
          <Skeleton className="h-11 flex-1 bg-zinc-800/50 rounded-none" />
          <Skeleton className="h-11 flex-1 bg-zinc-800/30 rounded-none" />
        </div>
        {/* Table rows */}
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="flex gap-4 items-center px-4 py-3 border-b border-zinc-800/60"
          >
            <Skeleton className="h-4 w-20 bg-zinc-800" />
            <Skeleton className="h-4 w-28 bg-zinc-800" />
            <Skeleton className="h-4 w-16 bg-zinc-800" />
            <Skeleton className="h-4 w-20 bg-zinc-800 ml-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}
