/**
 * components/TradesTable.tsx
 * ──────────────────────────
 * Displays open or closed paper trades.
 *
 * OPEN columns:
 *   Symbol | Entry Price | Current Price | Qty | Entry Date |
 *   Stop Loss | Unrealised P&L (₹) | Unrealised P&L % | Quality
 *
 * CLOSED columns:
 *   Symbol | Entry | Exit | Qty | Exit Date | P&L (₹) | R-Multiple | Quality
 *
 * Features:
 * - Sortable P&L column
 * - Row click → /screener/[symbol]
 * - Indian currency format throughout
 * - R-multiple color scale: >2r bright green, 1-2r green, 0-1r amber, <0 red
 * - Empty state per mode
 */
"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import QualityBadge from "@/components/QualityBadge";
import type { Trade } from "@/lib/types";

// ─── Formatters ─────────────────────────────────────────────────────────────

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

const inrFmtInt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function fmtInr(val: number | null | undefined): string {
  if (val == null) return "—";
  return inrFmt.format(val);
}

function fmtInrInt(val: number | null | undefined): string {
  if (val == null) return "—";
  return inrFmtInt.format(val);
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtPct(val: number | null | undefined): string {
  if (val == null) return "—";
  return `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`;
}

// ─── R-Multiple helpers ───────────────────────────────────────────────────────

function computeRMultiple(trade: Trade): number | null {
  // R-Multiple = pnl / (entry_price * qty * risk_per_unit)
  // We cannot compute risk_per_unit without stop_loss on the Trade type.
  // If pnl_pct is available, we derive it as: pnl / (entry_price * qty) ÷ risk_pct_per_unit
  // Since we have no stop_loss on Trade, return null (displayed as —).
  // If backend adds stop_loss to Trade later, compute here.
  return null;
}

function fmtRMultiple(r: number | null): string {
  if (r == null) return "—";
  return `${r >= 0 ? "+" : ""}${r.toFixed(1)}r`;
}

function rMultipleClass(r: number | null): string {
  if (r == null) return "text-zinc-500";
  if (r >= 2) return "text-emerald-400 font-semibold";
  if (r >= 1) return "text-green-400";
  if (r >= 0) return "text-amber-400";
  return "text-red-400";
}

// ─── P&L color ───────────────────────────────────────────────────────────────

function pnlClass(val: number | null | undefined): string {
  if (val == null || val === 0) return "text-zinc-400";
  return val > 0 ? "text-green-400" : "text-red-400";
}

// ─── Sort types ───────────────────────────────────────────────────────────────

type SortDir = "asc" | "desc";

// ─── Sort icon ────────────────────────────────────────────────────────────────

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <ChevronsUpDown className="h-3 w-3 opacity-30 ml-1" />;
  return dir === "asc" ? (
    <ChevronUp className="h-3 w-3 text-teal-400 ml-1" />
  ) : (
    <ChevronDown className="h-3 w-3 text-teal-400 ml-1" />
  );
}

// ─── Th cell ─────────────────────────────────────────────────────────────────

interface ThProps {
  label: string;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  active?: boolean;
  dir?: SortDir;
  onClick?: () => void;
  className?: string;
  hideOnMobile?: boolean;
}

function Th({
  label,
  align = "left",
  sortable,
  active,
  dir,
  onClick,
  className,
  hideOnMobile,
}: ThProps) {
  return (
    <th
      onClick={sortable ? onClick : undefined}
      className={cn(
        "px-4 py-3 text-[11px] uppercase tracking-widest font-medium text-zinc-400 whitespace-nowrap select-none",
        align === "right" && "text-right",
        align === "center" && "text-center",
        sortable && "cursor-pointer hover:text-zinc-200",
        hideOnMobile && "hidden sm:table-cell",
        className
      )}
    >
      <span className="inline-flex items-center">
        {label}
        {sortable && (
          <SortIcon active={!!active} dir={dir ?? "desc"} />
        )}
      </span>
    </th>
  );
}

// ─── Open trades table ────────────────────────────────────────────────────────

function OpenTradesTable({ trades }: { trades: Trade[] }) {
  const router = useRouter();
  const [sortDir, setSortDir] = React.useState<SortDir>("desc");

  const sorted = React.useMemo(
    () =>
      [...trades].sort((a, b) => {
        const av = a.pnl ?? 0;
        const bv = b.pnl ?? 0;
        return sortDir === "desc" ? bv - av : av - bv;
      }),
    [trades, sortDir]
  );

  if (trades.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-14 text-center gap-2">
        <p className="text-sm text-zinc-400">No open positions</p>
        <p className="text-xs text-zinc-600">
          Positions open automatically after a screener run triggers a breakout.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-800">
          <tr>
            <Th label="Symbol"            align="left" />
            <Th label="Entry Price"       align="right" />
            <Th label="Cur. Price"        align="right" />
            <Th label="Qty"               align="right" />
            <Th label="Entry Date"        align="left"   hideOnMobile />
            <Th label="Stop Loss"         align="right"  hideOnMobile />
            <Th
              label="Unr. P&L ₹"
              align="right"
              sortable
              active
              dir={sortDir}
              onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
            />
            <Th label="Unr. P&L %"        align="right" hideOnMobile />
            <Th label="Quality"           align="center" />
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/50">
          {sorted.map((t) => (
            <tr
              key={`${t.symbol}-${t.entry_date}`}
              onClick={() => router.push(`/screener/${t.symbol}`)}
              className="cursor-pointer transition-colors hover:bg-zinc-800/40"
            >
              {/* Symbol */}
              <td className="px-4 py-3 font-mono font-semibold text-white whitespace-nowrap">
                {t.symbol}
              </td>

              {/* Entry Price */}
              <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                {fmtInr(t.entry_price)}
              </td>

              {/* Current Price — Trade type has no current_price; display exit_price if set else — */}
              <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200">
                {t.exit_price != null ? fmtInr(t.exit_price) : "—"}
              </td>

              {/* Qty */}
              <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-300">
                {t.qty}
              </td>

              {/* Entry Date */}
              <td className="px-4 py-3 text-left font-mono tabular-nums text-zinc-400 hidden sm:table-cell whitespace-nowrap">
                {fmtDate(t.entry_date)}
              </td>

              {/* Stop Loss — not on Trade type, show — */}
              <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-500 hidden sm:table-cell">
                —
              </td>

              {/* Unrealised P&L ₹ */}
              <td className={cn("px-4 py-3 text-right font-mono tabular-nums font-medium", pnlClass(t.pnl))}>
                {t.pnl != null
                  ? `${t.pnl >= 0 ? "+" : ""}${fmtInrInt(t.pnl)}`
                  : "—"}
              </td>

              {/* Unrealised P&L % */}
              <td className={cn("px-4 py-3 text-right font-mono tabular-nums hidden sm:table-cell", pnlClass(t.pnl_pct))}>
                {fmtPct(t.pnl_pct)}
              </td>

              {/* Quality badge */}
              <td className="px-4 py-3 text-center">
                <QualityBadge quality={t.setup_quality} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Closed trades table ──────────────────────────────────────────────────────

function ClosedTradesTable({ trades }: { trades: Trade[] }) {
  const router = useRouter();
  const [sortDir, setSortDir] = React.useState<SortDir>("desc");

  const sorted = React.useMemo(
    () =>
      [...trades].sort((a, b) => {
        const av = a.pnl ?? 0;
        const bv = b.pnl ?? 0;
        return sortDir === "desc" ? bv - av : av - bv;
      }),
    [trades, sortDir]
  );

  if (trades.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-14 text-center gap-2">
        <p className="text-sm text-zinc-400">No closed trades yet</p>
        <p className="text-xs text-zinc-600">
          Paper positions are closed automatically at exit signals.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-800">
          <tr>
            <Th label="Symbol"      align="left" />
            <Th label="Entry"       align="right" />
            <Th label="Exit"        align="right" />
            <Th label="Qty"         align="right" hideOnMobile />
            <Th label="Exit Date"   align="left"  hideOnMobile />
            <Th
              label="P&L ₹"
              align="right"
              sortable
              active
              dir={sortDir}
              onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
            />
            <Th label="R-Multiple"  align="right" hideOnMobile />
            <Th label="Quality"     align="center" />
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/50">
          {sorted.map((t) => {
            const r = computeRMultiple(t);
            return (
              <tr
                key={`${t.symbol}-${t.entry_date}-${t.exit_date}`}
                onClick={() => router.push(`/screener/${t.symbol}`)}
                className="cursor-pointer transition-colors hover:bg-zinc-800/40"
              >
                {/* Symbol */}
                <td className="px-4 py-3 font-mono font-semibold text-white whitespace-nowrap">
                  {t.symbol}
                </td>

                {/* Entry */}
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-300">
                  {fmtInr(t.entry_price)}
                </td>

                {/* Exit */}
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-300">
                  {fmtInr(t.exit_price)}
                </td>

                {/* Qty */}
                <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-400 hidden sm:table-cell">
                  {t.qty}
                </td>

                {/* Exit Date */}
                <td className="px-4 py-3 text-left font-mono tabular-nums text-zinc-400 hidden sm:table-cell whitespace-nowrap">
                  {fmtDate(t.exit_date)}
                </td>

                {/* P&L ₹ */}
                <td className={cn("px-4 py-3 text-right font-mono tabular-nums font-medium", pnlClass(t.pnl))}>
                  {t.pnl != null
                    ? `${t.pnl >= 0 ? "+" : ""}${fmtInrInt(t.pnl)}`
                    : "—"}
                </td>

                {/* R-Multiple */}
                <td className={cn("px-4 py-3 text-right font-mono tabular-nums hidden sm:table-cell", rMultipleClass(r))}>
                  {fmtRMultiple(r)}
                </td>

                {/* Quality badge */}
                <td className="px-4 py-3 text-center">
                  <QualityBadge quality={t.setup_quality} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main export ─────────────────────────────────────────────────────────────

interface TradesTableProps {
  trades: Trade[];
  mode: "open" | "closed";
}

export default function TradesTable({ trades, mode }: TradesTableProps) {
  return mode === "open" ? (
    <OpenTradesTable trades={trades} />
  ) : (
    <ClosedTradesTable trades={trades} />
  );
}
