/**
 * components/StockTable.tsx
 * ──────────────────────────
 * Main screener results table.
 * - Sortable columns (client-side)
 * - Watchlist star markers + row highlight
 * - Score progress bar
 * - Pagination (25 per page)
 * - Export CSV
 * - Loading skeletons
 * - Empty state
 * - Mobile: hides Risk%, R:R, Stop Loss columns
 */
"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  Download,
  Star,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import QualityBadge from "@/components/QualityBadge";
import type { SEPAResult } from "@/lib/types";

// ─── Types ─────────────────────────────────────────────────────────────────

type SortKey = keyof SEPAResult | null;
type SortDir = "asc" | "desc";

interface StockTableProps {
  results: SEPAResult[];
  watchlistSymbols: string[];
  isLoading?: boolean;
}

// ─── Column definitions ─────────────────────────────────────────────────────

interface ColDef {
  key: SortKey;
  label: string;
  hideOnMobile?: boolean;
  align?: "left" | "right" | "center";
  className?: string;
}

const COLUMNS: ColDef[] = [
  { key: null,        label: "★",         align: "center", className: "w-8" },
  { key: "symbol",    label: "Symbol",    align: "left" },
  { key: "score",     label: "Score",     align: "left",   className: "min-w-[120px]" },
  { key: "setup_quality", label: "Quality", align: "center" },
  { key: "stage",     label: "Stage",     align: "center" },
  { key: "rs_rating", label: "RS",        align: "right" },
  { key: "vcp_qualified", label: "VCP",   align: "center" },
  { key: "breakout_triggered", label: "Breakout", align: "center" },
  { key: "entry_price", label: "Entry",   align: "right" },
  { key: "stop_loss",  label: "Stop Loss", align: "right", hideOnMobile: true },
  { key: "risk_pct",   label: "Risk %",   align: "right", hideOnMobile: true },
  { key: "rr_ratio",   label: "R:R",      align: "right", hideOnMobile: true },
];

const PAGE_SIZE = 25;

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val == null) return "—";
  return val.toFixed(decimals);
}

function fmtPrice(val: number | null | undefined): string {
  if (val == null) return "—";
  return `$${val.toFixed(2)}`;
}

function exportCsv(rows: SEPAResult[]) {
  const headers = [
    "Symbol","Score","Quality","Stage","RS Rating","VCP","Breakout",
    "Entry","Stop Loss","Risk %","R:R","Run Date",
  ];
  const lines = rows.map((r) =>
    [
      r.symbol, r.score, r.setup_quality, r.stage, r.rs_rating,
      r.vcp_qualified ? "Yes" : "No",
      r.breakout_triggered ? "Yes" : "No",
      r.entry_price ?? "", r.stop_loss ?? "",
      r.risk_pct != null ? r.risk_pct.toFixed(2) : "",
      r.rr_ratio != null ? r.rr_ratio.toFixed(2) : "",
      r.run_date,
    ].join(",")
  );
  const csv = [headers.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `screener_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Score bar ──────────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1 w-16 rounded-full bg-zinc-800 overflow-hidden flex-shrink-0">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-teal-500 transition-all"
          style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
        />
      </div>
      <span className="font-mono text-xs tabular-nums text-zinc-200">
        {score}
      </span>
    </div>
  );
}

// ─── Sort icon ──────────────────────────────────────────────────────────────

function SortIcon({
  colKey,
  sortKey,
  sortDir,
}: {
  colKey: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
}) {
  if (colKey == null) return null;
  if (colKey !== sortKey)
    return <ChevronsUpDown className="h-3 w-3 opacity-30 ml-1" />;
  return sortDir === "asc" ? (
    <ChevronUp className="h-3 w-3 text-teal-400 ml-1" />
  ) : (
    <ChevronDown className="h-3 w-3 text-teal-400 ml-1" />
  );
}

// ─── Skeleton rows ──────────────────────────────────────────────────────────

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b border-zinc-800/60">
          {COLUMNS.map((col) => (
            <td
              key={col.label}
              className={cn(
                "px-4 py-3",
                col.hideOnMobile && "hidden md:table-cell"
              )}
            >
              <Skeleton className="h-4 w-full rounded" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

// ─── Main StockTable ────────────────────────────────────────────────────────

export default function StockTable({
  results,
  watchlistSymbols,
  isLoading = false,
}: StockTableProps) {
  const router = useRouter();
  const [sortKey, setSortKey] = React.useState<SortKey>("score");
  const [sortDir, setSortDir] = React.useState<SortDir>("desc");
  const [page, setPage] = React.useState(1);

  // Reset to page 1 when results change
  React.useEffect(() => { setPage(1); }, [results]);

  const watchlistSet = React.useMemo(
    () => new Set(watchlistSymbols),
    [watchlistSymbols]
  );

  // ── Sort ────────────────────────────────────────────────────────────────
  const sorted = React.useMemo(() => {
    if (!sortKey) return results;
    return [...results].sort((a, b) => {
      const av = a[sortKey as keyof SEPAResult];
      const bv = b[sortKey as keyof SEPAResult];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [results, sortKey, sortDir]);

  // ── Pagination ──────────────────────────────────────────────────────────
  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);
  const pageRows = sorted.slice(
    (pageClamped - 1) * PAGE_SIZE,
    pageClamped * PAGE_SIZE
  );

  function handleSort(key: SortKey) {
    if (key == null) return;
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-0">
      {/* Export button */}
      <div className="flex justify-end mb-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => exportCsv(sorted)}
          className="gap-2 h-8 text-xs border-zinc-700 text-zinc-400 hover:text-white"
          disabled={isLoading || results.length === 0}
        >
          <Download className="h-3.5 w-3.5" />
          Export CSV
        </Button>
      </div>

      {/* Table wrapper with sticky header */}
      <div className="rounded-xl border border-zinc-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            {/* Sticky header */}
            <thead className="sticky top-0 z-10 bg-[#111113] border-b border-zinc-800">
              <tr>
                {COLUMNS.map((col) => (
                  <th
                    key={col.label}
                    onClick={() => handleSort(col.key)}
                    className={cn(
                      "px-4 py-3 text-[11px] uppercase tracking-widest font-medium text-zinc-400",
                      "select-none whitespace-nowrap",
                      col.key != null && "cursor-pointer hover:text-zinc-200",
                      col.align === "right" && "text-right",
                      col.align === "center" && "text-center",
                      col.align === "left" && "text-left",
                      col.hideOnMobile && "hidden md:table-cell",
                      col.className
                    )}
                  >
                    <span className="inline-flex items-center justify-inherit">
                      {col.label}
                      <SortIcon
                        colKey={col.key}
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>

            <tbody className="divide-y divide-zinc-800/60">
              {isLoading ? (
                <SkeletonRows />
              ) : pageRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={COLUMNS.length}
                    className="px-4 py-16 text-center text-zinc-500"
                  >
                    No results match your filters
                  </td>
                </tr>
              ) : (
                pageRows.map((row) => {
                  const inWatchlist = watchlistSet.has(row.symbol);
                  return (
                    <tr
                      key={row.symbol}
                      onClick={() => router.push(`/screener/${row.symbol}`)}
                      className={cn(
                        "cursor-pointer transition-colors",
                        inWatchlist
                          ? "bg-teal-950/20 hover:bg-teal-950/40"
                          : "hover:bg-zinc-800/50"
                      )}
                    >
                      {/* ★ watchlist marker */}
                      <td className="px-4 py-3 text-center w-8">
                        {inWatchlist && (
                          <Star className="h-3.5 w-3.5 text-teal-400 fill-teal-400 mx-auto" />
                        )}
                      </td>

                      {/* Symbol */}
                      <td className="px-4 py-3 font-mono font-semibold text-white whitespace-nowrap">
                        {row.symbol}
                      </td>

                      {/* Score bar */}
                      <td className="px-4 py-3">
                        <ScoreBar score={row.score} />
                      </td>

                      {/* Quality badge */}
                      <td className="px-4 py-3 text-center">
                        <QualityBadge quality={row.setup_quality} />
                      </td>

                      {/* Stage */}
                      <td className="px-4 py-3 text-center font-mono text-zinc-300">
                        {row.stage_label ?? `Stage ${row.stage}`}
                      </td>

                      {/* RS Rating */}
                      <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                        {row.rs_rating}
                      </td>

                      {/* VCP */}
                      <td className="px-4 py-3 text-center">
                        {row.vcp_qualified ? (
                          <span className="text-teal-400 text-xs font-semibold">✓</span>
                        ) : (
                          <span className="text-zinc-600 text-xs">—</span>
                        )}
                      </td>

                      {/* Breakout */}
                      <td className="px-4 py-3 text-center">
                        {row.breakout_triggered ? (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-green-500/20 text-green-400">
                            BRK
                          </span>
                        ) : (
                          <span className="text-zinc-600 text-xs">—</span>
                        )}
                      </td>

                      {/* Entry */}
                      <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                        {fmtPrice(row.entry_price)}
                      </td>

                      {/* Stop Loss — hidden on mobile */}
                      <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-400 hidden md:table-cell">
                        {fmtPrice(row.stop_loss)}
                      </td>

                      {/* Risk % — hidden on mobile */}
                      <td className="px-4 py-3 text-right font-mono tabular-nums hidden md:table-cell">
                        {row.risk_pct != null ? (
                          <span className={row.risk_pct > 8 ? "text-red-400" : "text-zinc-300"}>
                            {fmt(row.risk_pct, 1)}%
                          </span>
                        ) : (
                          <span className="text-zinc-600">—</span>
                        )}
                      </td>

                      {/* R:R — hidden on mobile */}
                      <td className="px-4 py-3 text-right font-mono tabular-nums hidden md:table-cell">
                        {row.rr_ratio != null ? (
                          <span className={row.rr_ratio >= 2 ? "text-teal-400" : "text-zinc-400"}>
                            {fmt(row.rr_ratio, 1)}x
                          </span>
                        ) : (
                          <span className="text-zinc-600">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {!isLoading && totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 px-1">
          <span className="text-xs text-zinc-500">
            Page {pageClamped} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={pageClamped <= 1}
              className="h-8 px-3 border-zinc-700 text-zinc-400 hover:text-white"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={pageClamped >= totalPages}
              className="h-8 px-3 border-zinc-700 text-zinc-400 hover:text-white"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
