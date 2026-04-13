/**
 * components/EquityCurve.tsx
 * ──────────────────────────
 * Cumulative P&L area chart built with Recharts.
 * - Accepts closed trades sorted by exit_date
 * - Computes cumulative P&L client-side
 * - Teal filled area, dashed break-even reference line
 * - Custom tooltip with date, cumulative P&L, and symbol
 * - Empty state with instructional message
 */
"use client";

import * as React from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { TrendingUp } from "lucide-react";
import type { Trade } from "@/lib/types";

// ─── Formatters ─────────────────────────────────────────────────────────────

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function fmtInr(val: number): string {
  return inrFmt.format(val);
}

function abbreviateDate(iso: string): string {
  // "2024-06-15" → "15 Jun"
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
    });
  } catch {
    return iso;
  }
}

// ─── Types ───────────────────────────────────────────────────────────────────

interface CurvePoint {
  date: string;          // abbreviated date string for X axis
  dateRaw: string;       // full ISO date
  cumPnl: number;        // cumulative P&L up to this trade
  symbol: string;
  tradePnl: number;      // this trade's individual P&L
}

// ─── Custom tooltip ──────────────────────────────────────────────────────────

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; payload: CurvePoint }>;
}

function CurveTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0].payload;
  const positive = point.cumPnl >= 0;

  return (
    <div className="rounded-lg border border-[#2A2A2E] bg-[#1A1A1D] px-3 py-2.5 text-xs shadow-xl">
      <p className="font-mono text-zinc-400 mb-1">{point.dateRaw}</p>
      <p className="font-mono font-semibold text-white mb-0.5">
        <span className="text-zinc-400 font-normal">Symbol: </span>
        {point.symbol}
      </p>
      <p className={`font-mono font-semibold ${positive ? "text-green-400" : "text-red-400"}`}>
        <span className="text-zinc-400 font-normal">Cum. P&L: </span>
        {fmtInr(point.cumPnl)}
      </p>
      <p className="font-mono text-zinc-400 text-[11px] mt-0.5">
        Trade: {point.tradePnl >= 0 ? "+" : ""}{fmtInr(point.tradePnl)}
      </p>
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

interface EquityCurveProps {
  trades: Trade[];
}

export default function EquityCurve({ trades }: EquityCurveProps) {
  // Filter to closed trades and sort by exit_date ascending
  const closedTrades = React.useMemo(
    () =>
      trades
        .filter((t) => t.status === "closed" && t.exit_date != null && t.pnl != null)
        .sort((a, b) => {
          const da = a.exit_date ?? "";
          const db = b.exit_date ?? "";
          return da < db ? -1 : da > db ? 1 : 0;
        }),
    [trades]
  );

  // Compute cumulative P&L
  const data = React.useMemo<CurvePoint[]>(() => {
    let cumPnl = 0;
    return closedTrades.map((t) => {
      cumPnl += t.pnl ?? 0;
      return {
        date: abbreviateDate(t.exit_date!),
        dateRaw: t.exit_date!,
        cumPnl: Math.round(cumPnl),
        symbol: t.symbol,
        tradePnl: t.pnl ?? 0,
      };
    });
  }, [closedTrades]);

  // Determine final color based on overall result
  const finalPnl = data.length > 0 ? data[data.length - 1].cumPnl : 0;
  const isPositive = finalPnl >= 0;
  const lineColor = isPositive ? "#14B8A6" : "#F87171";

  // Empty state
  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-[#1E1E21] bg-[#161618] py-16 text-center px-6">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-teal-500/10">
          <TrendingUp size={22} className="text-teal-400" />
        </div>
        <p className="text-sm font-medium text-zinc-300">No closed trades yet</p>
        <p className="text-xs text-zinc-500 max-w-xs leading-relaxed">
          Paper trading results will appear here. Positions are closed automatically
          when the screener flags an exit signal.
        </p>
      </div>
    );
  }

  // Y-axis tick formatter
  function fmtYAxis(val: number): string {
    if (Math.abs(val) >= 1_00_000) return `₹${(val / 1_00_000).toFixed(1)}L`;
    if (Math.abs(val) >= 1_000) return `₹${(val / 1_000).toFixed(0)}K`;
    return `₹${val}`;
  }

  return (
    <div className="rounded-xl border border-[#1E1E21] bg-[#161618] px-4 pt-5 pb-3">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-400">
            Equity Curve
          </p>
          <p className={`font-mono text-xl font-semibold mt-0.5 ${isPositive ? "text-green-400" : "text-red-400"}`}>
            {finalPnl >= 0 ? "+" : ""}{fmtInr(finalPnl)}
          </p>
        </div>
        <span className="text-xs text-zinc-600 font-mono">
          {data.length} closed trade{data.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="min-h-[250px] h-[250px]">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <defs>
            <linearGradient id="tealGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={lineColor} stopOpacity={0.18} />
              <stop offset="95%" stopColor={lineColor} stopOpacity={0.01} />
            </linearGradient>
          </defs>

          <CartesianGrid
            stroke="#1E1E21"
            strokeDasharray="4 4"
            vertical={false}
          />

          <XAxis
            dataKey="date"
            tick={{ fill: "#71717A", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            dy={6}
            interval="preserveStartEnd"
          />

          <YAxis
            tickFormatter={fmtYAxis}
            tick={{ fill: "#71717A", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={60}
          />

          {/* Break-even reference line */}
          <ReferenceLine
            y={0}
            stroke="#52525B"
            strokeDasharray="4 3"
            strokeWidth={1.5}
          />

          <Tooltip content={<CurveTooltip />} cursor={{ stroke: "#3F3F46", strokeWidth: 1 }} />

          <Area
            type="monotone"
            dataKey="cumPnl"
            stroke={lineColor}
            strokeWidth={2}
            fill="url(#tealGradient)"
            dot={false}
            activeDot={{ r: 4, fill: lineColor, stroke: "#0D0D0F", strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
}
