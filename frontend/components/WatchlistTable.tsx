/**
 * components/WatchlistTable.tsx
 * ──────────────────────────────
 * Table of current watchlist symbols.
 *
 * Props:
 *   items    — WatchlistItem[] from GET /api/v1/watchlist
 *   onRemove — called with symbol string; parent handles the API call
 *
 * Columns:
 *   Symbol | Last Score (progress bar or "—") | Quality (QualityBadge or "—")
 *   Added via (pill) | Added at (relative) | Note (≤40 chars) | [Remove]
 *
 * Behaviour:
 *   - Clicking a symbol row navigates to /screener/[symbol]
 *   - [Remove] calls onRemove(symbol) — optimistic update managed by parent
 *   - Empty state when items is empty
 */
"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import QualityBadge from "@/components/QualityBadge";
import type { WatchlistItem } from "@/lib/types";

// ─── Relative time helper ────────────────────────────────────────────────────

function relativeTime(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months === 1) return "1 month ago";
  if (months < 12) return `${months} months ago`;
  return `${Math.floor(months / 12)}y ago`;
}

// ─── Added-via pill ──────────────────────────────────────────────────────────

const VIA_STYLES: Record<string, string> = {
  cli:         "bg-violet-500/15 text-violet-400  border-violet-500/20",
  api:         "bg-blue-500/15   text-blue-400    border-blue-500/20",
  dashboard:   "bg-teal-500/15   text-teal-400    border-teal-500/20",
  file_upload: "bg-amber-500/15  text-amber-400   border-amber-500/20",
  test:        "bg-zinc-500/15   text-zinc-400    border-zinc-500/20",
};

function ViaPill({ via }: { via: string }) {
  const style = VIA_STYLES[via] ?? VIA_STYLES["test"];
  const label = via.replace("_", " ");
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border",
        style
      )}
    >
      {label}
    </span>
  );
}

// ─── Mini score bar ──────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1 w-14 rounded-full bg-zinc-800 overflow-hidden flex-shrink-0">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-teal-500 transition-all"
          style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
        />
      </div>
      <span className="font-mono text-xs tabular-nums text-zinc-200">{score}</span>
    </div>
  );
}

// ─── Props ───────────────────────────────────────────────────────────────────

interface WatchlistTableProps {
  items: WatchlistItem[];
  onRemove: (symbol: string) => void;
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function WatchlistTable({ items, onRemove }: WatchlistTableProps) {
  const router = useRouter();

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 rounded-xl border border-zinc-800 bg-[#161618]">
        <p className="text-zinc-400 text-sm">Your watchlist is empty.</p>
        <p className="text-zinc-600 text-xs mt-1">Add symbols using the form above.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-zinc-800 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-[#111113] border-b border-zinc-800">
            <tr>
              {["Symbol", "Last Score", "Quality", "Added via", "Added at", "Note", ""].map(
                (h) => (
                  <th
                    key={h}
                    className={cn(
                      "px-4 py-3 text-[11px] uppercase tracking-widest font-medium text-zinc-400",
                      "whitespace-nowrap text-left",
                      h === "" && "w-12"
                    )}
                  >
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/60">
            {items.map((item) => (
              <tr
                key={item.symbol}
                onClick={() => router.push(`/screener/${item.symbol}`)}
                className="cursor-pointer transition-colors hover:bg-zinc-800/50 group"
              >
                {/* Symbol */}
                <td className="px-4 py-3">
                  <span className="font-mono font-semibold text-white whitespace-nowrap">
                    {item.symbol}
                  </span>
                </td>

                {/* Last Score */}
                <td className="px-4 py-3">
                  {item.last_score != null ? (
                    <ScoreBar score={item.last_score} />
                  ) : (
                    <span className="text-zinc-600 font-mono text-xs">—</span>
                  )}
                </td>

                {/* Quality */}
                <td className="px-4 py-3">
                  {item.last_quality != null ? (
                    <QualityBadge quality={item.last_quality} />
                  ) : (
                    <span className="text-zinc-600 text-xs">—</span>
                  )}
                </td>

                {/* Added via */}
                <td className="px-4 py-3">
                  <ViaPill via={item.added_via} />
                </td>

                {/* Added at */}
                <td className="px-4 py-3 text-xs text-zinc-400 whitespace-nowrap">
                  {relativeTime(item.added_at)}
                </td>

                {/* Note */}
                <td className="px-4 py-3 text-xs text-zinc-500 max-w-[160px] truncate">
                  {item.note ? (
                    <span title={item.note}>
                      {item.note.length > 40 ? item.note.slice(0, 40) + "…" : item.note}
                    </span>
                  ) : (
                    <span className="text-zinc-700">—</span>
                  )}
                </td>

                {/* Remove */}
                <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => onRemove(item.symbol)}
                    aria-label={`Remove ${item.symbol}`}
                    className="flex items-center justify-center h-7 w-7 rounded-md text-zinc-600
                               hover:text-red-400 hover:bg-red-500/10 transition-colors opacity-0
                               group-hover:opacity-100"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
