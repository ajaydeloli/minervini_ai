/**
 * app/screener/page.tsx
 * ──────────────────────
 * Full universe screener page.
 * - SWR polls fetchTopStocks + fetchWatchlist every 2 min
 * - Client-side filtering via FilterBar (no re-fetch on filter change)
 * - [Run Now] triggers POST /api/proxy/run scope:universe
 * - Sticky FilterBar, result count, last updated timestamp
 */
"use client";

import * as React from "react";
import useSWR from "swr";
import { RefreshCw, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import FilterBar, {
  type FilterState,
  DEFAULT_FILTERS,
} from "@/components/FilterBar";
import StockTable from "@/components/StockTable";
import { fetchTopStocks, fetchWatchlist, triggerRun } from "@/lib/api";
import type { SEPAResult, SetupQuality } from "@/lib/types";

// ─── Toast (minimal inline toast, no external dep) ─────────────────────────

interface Toast {
  id: number;
  message: string;
  type: "success" | "error";
}

let toastId = 0;

function ToastContainer({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="fixed bottom-20 md:bottom-6 right-4 z-50 flex flex-col gap-2 items-end pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "px-4 py-3 rounded-xl text-sm font-medium shadow-xl pointer-events-auto",
            "animate-in slide-in-from-bottom-4 fade-in duration-300",
            t.type === "success"
              ? "bg-teal-800/90 text-teal-100 border border-teal-600/40"
              : "bg-red-900/90 text-red-100 border border-red-600/40"
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

// ─── Filter logic ───────────────────────────────────────────────────────────

function applyFilters(results: SEPAResult[], filters: FilterState): SEPAResult[] {
  return results.filter((r) => {
    if (filters.quality !== "All" && r.setup_quality !== (filters.quality as SetupQuality)) return false;
    if (filters.stage !== "All" && r.stage !== filters.stage) return false;
    if (r.rs_rating < filters.minRs) return false;
    return true;
  });
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function ScreenerPage() {
  const [filters, setFilters] = React.useState<FilterState>(DEFAULT_FILTERS);
  const [runLoading, setRunLoading] = React.useState(false);
  const [toasts, setToasts] = React.useState<Toast[]>([]);

  function addToast(message: string, type: "success" | "error") {
    const id = ++toastId;
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }

  // SWR: top stocks (full universe, limit 500)
  const {
    data: allStocks = [],
    isLoading: stocksLoading,
    mutate: mutateStocks,
    isValidating,
  } = useSWR("top-stocks", () => fetchTopStocks(undefined, 500), {
    refreshInterval: 120_000,
  });

  // SWR: watchlist
  const { data: watchlistItems = [] } = useSWR("watchlist", fetchWatchlist, {
    refreshInterval: 120_000,
  });

  const watchlistSymbols = React.useMemo(
    () => watchlistItems.map((w) => w.symbol),
    [watchlistItems]
  );

  // Client-side filtering (+ watchlist-only gate)
  const filtered = React.useMemo(() => {
    let rows = applyFilters(allStocks, filters);
    if (filters.watchlistOnly) {
      const set = new Set(watchlistSymbols);
      rows = rows.filter((r) => set.has(r.symbol));
    }
    return rows;
  }, [allStocks, filters, watchlistSymbols]);

  // Run date from first result
  const runDate = allStocks[0]?.run_date ?? null;

  // Last-updated wall clock time (tracks last successful SWR response)
  const [lastUpdated, setLastUpdated] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!isValidating && allStocks.length > 0) {
      setLastUpdated(
        new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      );
    }
  }, [isValidating, allStocks]);

  async function handleRunNow() {
    setRunLoading(true);
    try {
      await triggerRun({ scope: "universe" });
      addToast("Run triggered — results will refresh shortly.", "success");
      // Give the pipeline ~15 s then re-fetch
      setTimeout(() => mutateStocks(), 15_000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Run failed";
      addToast(msg, "error");
    } finally {
      setRunLoading(false);
    }
  }

  return (
    <div className="px-4 md:px-8 py-6 max-w-[1600px] mx-auto">
      {/* ── Header row ────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">
            Screener
          </h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            Full universe results
            {runDate && (
              <span className="ml-1 font-mono text-zinc-400">— {runDate}</span>
            )}
          </p>
        </div>

        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <Button
            size="sm"
            onClick={handleRunNow}
            disabled={runLoading}
            className="gap-2 bg-teal-600 hover:bg-teal-500 text-white h-9"
          >
            {runLoading ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Run Now
          </Button>
          {lastUpdated && (
            <span className="text-[11px] text-zinc-500 tabular-nums">
              Updated {lastUpdated}
            </span>
          )}
        </div>
      </div>

      {/* ── Filter bar ────────────────────────────────────────────────── */}
      <FilterBar onChange={setFilters} className="mb-4" />

      {/* ── Result count ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mt-4 mb-3">
        <p className="text-sm text-zinc-400">
          Showing{" "}
          <span className="text-white font-medium">{filtered.length}</span>
          {" "}of{" "}
          <span className="text-white font-medium">{allStocks.length}</span>
          {" "}results
        </p>
        {isValidating && !stocksLoading && (
          <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
            <RefreshCw className="h-3 w-3 animate-spin" />
            Refreshing…
          </span>
        )}
      </div>

      {/* ── Table ─────────────────────────────────────────────────────── */}
      <StockTable
        results={filtered}
        watchlistSymbols={watchlistSymbols}
        isLoading={stocksLoading}
      />

      <ToastContainer toasts={toasts} />
    </div>
  );
}
