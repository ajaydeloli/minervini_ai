/**
 * app/backtest/loading.tsx
 * ─────────────────────────
 * Streaming skeleton shown while backtest/page.tsx data loads.
 */
import { Skeleton } from "@/components/ui/skeleton";

export default function BacktestLoading() {
  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-6 animate-pulse">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1.5">
          <Skeleton className="h-6 w-40 bg-zinc-800" />
          <Skeleton className="h-4 w-64 bg-zinc-800" />
        </div>
        {/* Run selector */}
        <Skeleton className="h-9 w-56 rounded-lg bg-zinc-800" />
      </div>

      {/* 6 KPI cards — 3×2 grid */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4 space-y-3"
          >
            <Skeleton className="h-3 w-28 bg-zinc-800" />
            <Skeleton className="h-8 w-32 bg-zinc-800" />
          </div>
        ))}
      </div>

      {/* Equity curve chart placeholder */}
      <Skeleton className="h-[300px] w-full rounded-xl bg-zinc-800" />

      {/* Regime table — 3 rows */}
      <div className="rounded-xl border border-[#1E1E21] bg-[#161618] overflow-hidden">
        <Skeleton className="h-10 w-full bg-zinc-800/60" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full mt-px bg-zinc-800/40" />
        ))}
      </div>
    </div>
  );
}
