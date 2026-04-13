/**
 * app/watchlist/page.tsx
 * ──────────────────────
 * Watchlist management page — three stacked sections:
 *
 *  A. Add Symbols
 *     • Manual text entry (comma-separated) → bulkAddWatchlist()
 *     • File upload (FileUpload component) → uploadWatchlistFile()
 *     Both refresh the SWR watchlist cache on success.
 *
 *  B. Current Watchlist
 *     • WatchlistTable with all items
 *     • "Clear All" destructive button with confirm dialog
 *
 *  C. Today's Watchlist Results
 *     • fetchTopStocks() filtered to watchlist symbols
 *     • StockTable with watchlistSymbols prop (★ markers)
 *     • [Run Watchlist Now] button → triggerRun({ scope: "watchlist" })
 */
"use client";

import * as React from "react";
import useSWR from "swr";
import {
  Loader2,
  Plus,
  Trash2,
  Play,
  AlertTriangle,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fetchWatchlist,
  fetchTopStocks,
  bulkAddWatchlist,
  removeFromWatchlist,
  triggerRun,
  ApiError,
} from "@/lib/api";
import type { WatchlistItem, WatchlistUploadResult } from "@/lib/types";
import FileUpload from "@/components/FileUpload";
import WatchlistTable from "@/components/WatchlistTable";
import StockTable from "@/components/StockTable";

// ─── SWR config ──────────────────────────────────────────────────────────────

const SWR_OPTS = { revalidateOnFocus: true };

// ─── Toast ───────────────────────────────────────────────────────────────────

interface ToastState { msg: string; kind: "ok" | "err" }

function Toast({ toast, onDismiss }: { toast: ToastState; onDismiss: () => void }) {
  return (
    <div
      className={cn(
        "fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50",
        "flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium shadow-xl",
        toast.kind === "ok" ? "bg-teal-500/90 text-white" : "bg-red-500/90 text-white"
      )}
    >
      <span>{toast.msg}</span>
      <button onClick={onDismiss} className="opacity-70 hover:opacity-100">
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ─── Confirm dialog ───────────────────────────────────────────────────────────

function ConfirmDialog({
  open,
  onConfirm,
  onCancel,
  count,
}: {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  count: number;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[#1A1A1D] border border-zinc-700 rounded-2xl p-6 max-w-sm w-full mx-4 shadow-2xl space-y-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 flex h-9 w-9 items-center justify-center rounded-lg bg-red-500/15">
            <AlertTriangle className="h-4.5 w-4.5 text-red-400" />
          </div>
          <div>
            <h3 className="font-semibold text-white text-sm">Clear entire watchlist?</h3>
            <p className="text-xs text-zinc-400 mt-1">
              This will permanently remove all {count} symbol{count !== 1 ? "s" : ""}.
              This action cannot be undone.
            </p>
          </div>
        </div>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-sm text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-red-500 text-white hover:bg-red-400 transition-colors"
          >
            Clear All
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Section wrapper ──────────────────────────────────────────────────────────

function Section({
  title,
  action,
  children,
}: {
  title: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function WatchlistPage() {
  // ── State ─────────────────────────────────────────────────────────────────
  const [manualInput, setManualInput] = React.useState("");
  const [addLoading, setAddLoading] = React.useState(false);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [clearLoading, setClearLoading] = React.useState(false);
  const [runLoading, setRunLoading] = React.useState(false);
  const [toast, setToast] = React.useState<ToastState | null>(null);
  // Optimistic removed symbols (removed locally before API confirms)
  const [optimisticRemoved, setOptimisticRemoved] = React.useState<Set<string>>(new Set());

  // ── SWR — watchlist ───────────────────────────────────────────────────────
  const {
    data: watchlistRaw,
    isLoading: watchlistLoading,
    mutate: mutateWatchlist,
  } = useSWR("watchlist", fetchWatchlist, SWR_OPTS);

  // Apply optimistic removals to the display list
  const watchlistItems: WatchlistItem[] = React.useMemo(
    () => (watchlistRaw ?? []).filter((i) => !optimisticRemoved.has(i.symbol)),
    [watchlistRaw, optimisticRemoved]
  );
  const watchlistSymbols = watchlistItems.map((i) => i.symbol);

  // ── SWR — top stocks (for Section C) ─────────────────────────────────────
  const {
    data: topStocks,
    isLoading: topLoading,
    mutate: mutateTopStocks,
  } = useSWR("top-stocks-all", () => fetchTopStocks(undefined, 200), SWR_OPTS);

  // Filter to watchlist symbols only
  const watchlistResults = React.useMemo(() => {
    if (!topStocks || watchlistSymbols.length === 0) return [];
    const wlSet = new Set(watchlistSymbols);
    return topStocks.filter((s) => wlSet.has(s.symbol));
  }, [topStocks, watchlistSymbols]);

  // ── Helpers ───────────────────────────────────────────────────────────────

  function showToast(msg: string, kind: "ok" | "err") {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 4500);
  }

  // ── Manual add ────────────────────────────────────────────────────────────

  async function handleManualAdd() {
    const symbols = manualInput
      .toUpperCase()
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (symbols.length === 0) return;

    setAddLoading(true);
    try {
      const result = await bulkAddWatchlist(symbols);
      const added = result.added.length;
      const skipped = result.already_exists.length;
      const invalid = result.invalid.length;
      let msg = `Added ${added} symbol${added !== 1 ? "s" : ""} to watchlist`;
      if (skipped > 0) msg += `, ${skipped} already existed`;
      if (invalid > 0) msg += `, ${invalid} invalid`;
      showToast(msg, "ok");
      setManualInput("");
      setOptimisticRemoved(new Set());   // clear any stale optimistic state
      await mutateWatchlist();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to add symbols", "err");
    } finally {
      setAddLoading(false);
    }
  }

  // ── File upload success ────────────────────────────────────────────────────

  async function handleUploadSuccess(result: WatchlistUploadResult) {
    setOptimisticRemoved(new Set());
    await mutateWatchlist();
    showToast(
      `Added ${result.added} symbol${result.added !== 1 ? "s" : ""} via file`,
      "ok"
    );
  }

  // ── Remove single symbol ──────────────────────────────────────────────────

  async function handleRemove(symbol: string) {
    // Optimistic update: hide row immediately
    setOptimisticRemoved((prev) => new Set([...prev, symbol]));
    try {
      await removeFromWatchlist(symbol);
      await mutateWatchlist();
    } catch (err) {
      // Roll back optimistic update on error
      setOptimisticRemoved((prev) => {
        const next = new Set(prev);
        next.delete(symbol);
        return next;
      });
      showToast(err instanceof ApiError ? err.message : "Failed to remove symbol", "err");
    }
  }

  // ── Clear all ─────────────────────────────────────────────────────────────

  async function handleClearAll() {
    setConfirmOpen(false);
    setClearLoading(true);
    try {
      await fetch("/api/proxy/watchlist/clear", { method: "DELETE" });
      setOptimisticRemoved(new Set());
      await mutateWatchlist();
      showToast("Watchlist cleared", "ok");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clear watchlist", "err");
    } finally {
      setClearLoading(false);
    }
  }

  // ── Run watchlist screen ──────────────────────────────────────────────────

  async function handleRunWatchlist() {
    setRunLoading(true);
    try {
      await triggerRun({ scope: "watchlist" });
      showToast("Watchlist screen triggered ✓", "ok");
      await mutateTopStocks();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Run failed", "err");
    } finally {
      setRunLoading(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-10">

      {/* ── Page header ─────────────────────────────────────────────── */}
      <div>
        <h1 className="text-xl font-semibold text-white tracking-tight">Watchlist</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Manage your tracked symbols and view their latest SEPA scores
        </p>
      </div>

      {/* ══════════════════════════════════════════════════════════════ */}
      {/* SECTION A — Add Symbols                                       */}
      {/* ══════════════════════════════════════════════════════════════ */}
      <Section title="Add Symbols">
        {/* Manual entry */}
        <div className="rounded-xl border border-zinc-800 bg-[#161618] p-5 space-y-5">
          <div>
            <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-2 block">
              Manual entry
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={manualInput}
                onChange={(e) => setManualInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !addLoading && handleManualAdd()}
                placeholder="RELIANCE, TCS, DIXON"
                className={cn(
                  "flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2",
                  "text-sm text-white placeholder-zinc-600",
                  "focus:outline-none focus:ring-1 focus:ring-teal-500 focus:border-teal-500",
                  "transition-colors"
                )}
              />
              <button
                onClick={handleManualAdd}
                disabled={addLoading || !manualInput.trim()}
                className={cn(
                  "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold",
                  "bg-teal-500 text-white hover:bg-teal-400 transition-colors",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                {addLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="h-4 w-4" />
                )}
                Add
              </button>
            </div>
            <p className="text-xs text-zinc-600 mt-1.5">
              Separate multiple symbols with commas or spaces
            </p>
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-zinc-800" />
            <span className="text-xs text-zinc-600 uppercase tracking-wider">or upload a file</span>
            <div className="flex-1 h-px bg-zinc-800" />
          </div>

          {/* File upload */}
          <FileUpload onSuccess={handleUploadSuccess} />
        </div>
      </Section>

      {/* ══════════════════════════════════════════════════════════════ */}
      {/* SECTION B — Current Watchlist                                 */}
      {/* ══════════════════════════════════════════════════════════════ */}
      <Section
        title={`Current Watchlist (${watchlistItems.length} symbol${watchlistItems.length !== 1 ? "s" : ""})`}
        action={
          watchlistItems.length > 0 ? (
            <button
              onClick={() => setConfirmOpen(true)}
              disabled={clearLoading}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 px-3 py-1.5",
                "text-xs font-semibold text-red-400 hover:bg-red-500/10 transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {clearLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              Clear All
            </button>
          ) : undefined
        }
      >
        {watchlistLoading ? (
          <div className="flex items-center justify-center py-12 rounded-xl border border-zinc-800 bg-[#161618]">
            <Loader2 className="h-5 w-5 animate-spin text-zinc-500 mr-2" />
            <span className="text-sm text-zinc-500">Loading watchlist…</span>
          </div>
        ) : (
          <WatchlistTable items={watchlistItems} onRemove={handleRemove} />
        )}
      </Section>

      {/* ══════════════════════════════════════════════════════════════ */}
      {/* SECTION C — Today's Watchlist Results                         */}
      {/* ══════════════════════════════════════════════════════════════ */}
      <Section
        title={
          watchlistResults.length > 0
            ? `${watchlistResults.length} watchlist symbol${watchlistResults.length !== 1 ? "s" : ""} in today's screen`
            : "Today's Watchlist Results"
        }
        action={
          <button
            onClick={handleRunWatchlist}
            disabled={runLoading}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-[#161618]",
              "px-3 py-1.5 text-xs font-semibold text-zinc-300 hover:bg-zinc-800 hover:text-white",
              "transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            {runLoading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Screening {watchlistSymbols.length} symbol{watchlistSymbols.length !== 1 ? "s" : ""}…
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" />
                Run Watchlist Now
              </>
            )}
          </button>
        }
      >
        {topLoading ? (
          <div className="flex items-center justify-center py-12 rounded-xl border border-zinc-800 bg-[#161618]">
            <Loader2 className="h-5 w-5 animate-spin text-zinc-500 mr-2" />
            <span className="text-sm text-zinc-500">Loading results…</span>
          </div>
        ) : watchlistResults.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 rounded-xl border border-zinc-800 bg-[#161618] gap-2 text-center">
            <p className="text-zinc-400 text-sm">Run the watchlist screen to see results for your symbols.</p>
            <p className="text-zinc-600 text-xs">
              Click &ldquo;Run Watchlist Now&rdquo; above to trigger a screen.
            </p>
          </div>
        ) : (
          <StockTable
            results={watchlistResults}
            watchlistSymbols={watchlistSymbols}
            isLoading={topLoading}
          />
        )}
      </Section>

      {/* ── Confirm dialog ───────────────────────────────────────────── */}
      <ConfirmDialog
        open={confirmOpen}
        count={watchlistItems.length}
        onConfirm={handleClearAll}
        onCancel={() => setConfirmOpen(false)}
      />

      {/* ── Toast ────────────────────────────────────────────────────── */}
      {toast && <Toast toast={toast} onDismiss={() => setToast(null)} />}
    </div>
  );
}
