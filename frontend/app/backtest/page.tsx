/**
 * app/backtest/page.tsx
 * ──────────────────────
 * Backtest results viewer.
 *
 * Layout
 * ──────
 * - Header + run-selector <select>
 * - 6 KPI cards (Total Return, CAGR, Max Drawdown, Sharpe, Win Rate, Trades)
 * - Equity curve area chart (portfolio vs benchmark)
 * - Per-regime stats table: Regime | Trades | Win Rate | Avg Return | Total Return
 *
 * Data
 * ────
 * - SWR: fetchBacktestRuns()              → BacktestRunSummary[] (60 s refresh)
 * - SWR: fetchBacktestReport(runId)       → BacktestReport
 * - SWR: fetchBacktestEquityCurve(runId)  → EquityCurvePoint[]
 */
"use client";

import * as React from "react";
import useSWR from "swr";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { BarChart2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchBacktestRuns,
  fetchBacktestReport,
  fetchBacktestEquityCurve,
} from "@/lib/api";
import type { EquityCurvePoint } from "@/lib/types";

// ─── KPI card ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string;
  value: string;
  sub?: string;
  positive?: boolean | null;
}

function KpiCard({ label, value, sub, positive }: KpiCardProps) {
  const valueColor =
    positive === true
      ? "text-green-400"
      : positive === false
      ? "text-red-400"
      : "text-white";

  return (
    <div className="rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4 space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </p>
      <p className={cn("font-mono text-2xl font-semibold tabular-nums", valueColor)}>
        {value}
      </p>
      {sub && <p className="text-[11px] text-zinc-600">{sub}</p>}
    </div>
  );
}

// ─── Equity curve chart ───────────────────────────────────────────────────────

interface EquityCurveChartProps {
  data: EquityCurvePoint[];
}

function EquityCurveChart({ data }: EquityCurveChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-[#1E1E21] bg-[#161618] h-[300px] gap-3 text-center px-6">
        <BarChart2 size={28} className="text-zinc-600" />
        <p className="text-sm text-zinc-500">No equity curve data for this run.</p>
      </div>
    );
  }

  // Abbreviate date for X axis
  function abbrev(iso: string): string {
    try {
      return new Date(iso).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
      });
    } catch {
      return iso;
    }
  }

  const chartData = data.map((p) => ({
    ...p,
    dateLabel: abbrev(p.date),
  }));

  const last = chartData[chartData.length - 1];
  const isPositive = last ? last.portfolio_value >= (chartData[0]?.portfolio_value ?? 0) : true;
  const lineColor = isPositive ? "#14B8A6" : "#F87171";

  function fmtVal(v: number): string {
    if (Math.abs(v) >= 1_00_000) return `₹${(v / 1_00_000).toFixed(1)}L`;
    if (Math.abs(v) >= 1_000) return `₹${(v / 1_000).toFixed(0)}K`;
    return `₹${v}`;
  }

  return (
    <div className="rounded-xl border border-[#1E1E21] bg-[#161618] px-4 pt-5 pb-3">
      <p className="text-xs font-medium uppercase tracking-wider text-zinc-400 mb-4">
        Equity Curve
      </p>
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="btPortfolio" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={lineColor} stopOpacity={0.18} />
                <stop offset="95%" stopColor={lineColor} stopOpacity={0.01} />
              </linearGradient>
              <linearGradient id="btBenchmark" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366F1" stopOpacity={0.12} />
                <stop offset="95%" stopColor="#6366F1" stopOpacity={0.01} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1E1E21" strokeDasharray="4 4" vertical={false} />
            <XAxis
              dataKey="dateLabel"
              tick={{ fill: "#71717A", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              dy={6}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={fmtVal}
              tick={{ fill: "#71717A", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={64}
            />
            <ReferenceLine
              y={chartData[0]?.portfolio_value ?? 0}
              stroke="#52525B"
              strokeDasharray="4 3"
              strokeWidth={1.5}
            />
            <Tooltip
              contentStyle={{
                background: "#18181b",
                border: "1px solid #3f3f46",
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(v: number, name: string) => [fmtVal(v), name]}
              labelFormatter={(l) => String(l)}
              cursor={{ stroke: "#3F3F46", strokeWidth: 1 }}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, color: "#71717A", paddingTop: 8 }}
            />
            <Area
              type="monotone"
              dataKey="portfolio_value"
              name="Portfolio"
              stroke={lineColor}
              strokeWidth={2}
              fill="url(#btPortfolio)"
              dot={false}
              activeDot={{ r: 3, fill: lineColor }}
            />
            <Area
              type="monotone"
              dataKey="benchmark_value"
              name="Benchmark"
              stroke="#6366F1"
              strokeWidth={1.5}
              fill="url(#btBenchmark)"
              dot={false}
              activeDot={{ r: 3, fill: "#6366F1" }}
              strokeDasharray="4 2"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ─── Regime table ─────────────────────────────────────────────────────────────

interface RegimeStat {
  regime: string;
  trades: number;
  win_rate: number;
  avg_return: number;
  total_return: number;
}

function RegimeTable({ rows }: { rows: RegimeStat[] }) {
  if (rows.length === 0) return null;

  function rowColor(regime: string): string {
    const r = regime.toLowerCase();
    if (r.includes("bull"))    return "bg-green-500/5 border-l-2 border-green-500/40";
    if (r.includes("bear"))    return "bg-red-500/5 border-l-2 border-red-500/40";
    if (r.includes("side") || r.includes("neutral"))
      return "bg-yellow-500/5 border-l-2 border-yellow-500/40";
    return "";
  }

  function fmtPct(v: number): string {
    return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  }

  const headers = ["Regime", "Trades", "Win Rate", "Avg Return", "Total Return"];

  return (
    <div className="rounded-xl border border-[#1E1E21] bg-[#161618] overflow-hidden">
      <p className="text-xs font-medium uppercase tracking-wider text-zinc-400 px-5 py-3 border-b border-[#1E1E21]">
        Regime Statistics
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1E1E21]">
              {headers.map((h) => (
                <th
                  key={h}
                  className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wide text-zinc-500"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.regime}
                className={cn("border-b border-[#1E1E21]/60 last:border-0", rowColor(row.regime))}
              >
                <td className="px-4 py-3 font-medium text-zinc-200 capitalize">
                  {row.regime}
                </td>
                <td className="px-4 py-3 font-mono text-zinc-300">{row.trades}</td>
                <td className="px-4 py-3 font-mono text-zinc-300">
                  {(row.win_rate * 100).toFixed(1)}%
                </td>
                <td
                  className={cn(
                    "px-4 py-3 font-mono",
                    row.avg_return >= 0 ? "text-green-400" : "text-red-400"
                  )}
                >
                  {fmtPct(row.avg_return)}
                </td>
                <td
                  className={cn(
                    "px-4 py-3 font-mono",
                    row.total_return >= 0 ? "text-green-400" : "text-red-400"
                  )}
                >
                  {fmtPct(row.total_return)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Error state ──────────────────────────────────────────────────────────────

function ErrorCard({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[40vh] gap-4 text-center px-6">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10">
        <AlertCircle size={22} className="text-red-400" />
      </div>
      <div>
        <p className="text-sm font-medium text-zinc-300">Failed to load backtest data</p>
        <p className="text-xs text-zinc-600 mt-1 max-w-xs">{message}</p>
      </div>
    </div>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 text-center px-6">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-teal-500/10">
        <BarChart2 size={28} className="text-teal-400" />
      </div>
      <div className="space-y-2 max-w-md">
        <h2 className="text-lg font-semibold text-white">No backtest runs yet</h2>
        <p className="text-sm text-zinc-400 leading-relaxed">
          Run the backtest engine to generate results. Use{" "}
          <code className="font-mono text-xs bg-zinc-800 rounded px-1 py-0.5">
            python scripts/backtest_runner.py
          </code>{" "}
          from the project root.
        </p>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);

  // ── Fetch run list ────────────────────────────────────────────────────────
  const {
    data: runs,
    isLoading: runsLoading,
    error: runsError,
  } = useSWR("backtest/runs", fetchBacktestRuns, { refreshInterval: 60_000 });

  // Default to most-recent run when list loads
  React.useEffect(() => {
    if (runs && runs.length > 0 && selectedRunId === null) {
      setSelectedRunId(runs[0].run_id);
    }
  }, [runs, selectedRunId]);

  const activeRunId = selectedRunId ?? runs?.[0]?.run_id ?? null;

  // ── Fetch report + equity curve for selected run ──────────────────────────
  const {
    data: report,
    isLoading: reportLoading,
    error: reportError,
  } = useSWR(
    activeRunId ? `backtest/report/${activeRunId}` : null,
    () => fetchBacktestReport(activeRunId!),
    { refreshInterval: 60_000 }
  );

  const {
    data: curve = [],
    isLoading: curveLoading,
  } = useSWR(
    activeRunId ? `backtest/curve/${activeRunId}` : null,
    () => fetchBacktestEquityCurve(activeRunId!),
    { refreshInterval: 60_000 }
  );

  const isLoading = runsLoading || (activeRunId !== null && (reportLoading || curveLoading));
  const error = runsError ?? reportError;

  // ── Format helpers ────────────────────────────────────────────────────────
  function pct(v?: number): string {
    if (v == null) return "—";
    return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  }
  function num2(v?: number): string {
    if (v == null) return "—";
    return v.toFixed(2);
  }
  function winPct(v?: number): string {
    if (v == null) return "—";
    return `${(v * 100).toFixed(1)}%`;
  }

  // ── Run date label ────────────────────────────────────────────────────────
  const selectedRun = runs?.find((r) => r.run_id === activeRunId);
  const runDateLabel = selectedRun?.run_date
    ? new Date(selectedRun.run_date).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "—";

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-6">

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">
            Backtest Results
          </h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {activeRunId
              ? `Run ${activeRunId} · ${runDateLabel}`
              : "Select a backtest run to view results"}
          </p>
        </div>

        {/* Run selector */}
        {!runsLoading && (runs?.length ?? 0) > 0 && (
          <select
            value={activeRunId ?? ""}
            onChange={(e) => setSelectedRunId(e.target.value)}
            className="h-9 rounded-lg bg-[#161618] border border-[#1E1E21] px-3 text-sm text-zinc-200
                       focus:outline-none focus:ring-1 focus:ring-teal-500/50 min-w-[220px]"
          >
            {(runs ?? []).map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} — {r.run_date}
              </option>
            ))}
          </select>
        )}
        {runsLoading && (
          <Skeleton className="h-9 w-56 bg-zinc-800 rounded-lg" />
        )}
      </div>

      {/* Error */}
      {error && (
        <ErrorCard
          message={error instanceof Error ? error.message : "Unknown error"}
        />
      )}

      {/* Empty */}
      {!isLoading && !error && (runs?.length ?? 0) === 0 && <EmptyState />}

      {/* Loading KPI skeletons */}
      {isLoading && !report && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4 space-y-3 animate-pulse"
              >
                <Skeleton className="h-3 w-28 bg-zinc-800" />
                <Skeleton className="h-8 w-32 bg-zinc-800" />
              </div>
            ))}
          </div>
          <Skeleton className="h-[300px] w-full rounded-xl bg-zinc-800 animate-pulse" />
        </>
      )}

      {/* Main content */}
      {!error && report && (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <KpiCard
              label="Total Return"
              value={pct(report.total_return_pct)}
              positive={report.total_return_pct >= 0}
            />
            <KpiCard
              label="CAGR"
              value={pct(report.cagr_pct)}
              positive={report.cagr_pct >= 0}
            />
            <KpiCard
              label="Max Drawdown"
              value={pct(report.max_drawdown_pct)}
              positive={report.max_drawdown_pct >= 0}
              sub="lower is better"
            />
            <KpiCard
              label="Sharpe Ratio"
              value={num2(report.sharpe_ratio)}
              positive={report.sharpe_ratio >= 1 ? true : report.sharpe_ratio < 0 ? false : null}
            />
            <KpiCard
              label="Win Rate"
              value={winPct(report.win_rate)}
              positive={report.win_rate >= 0.5}
            />
            <KpiCard
              label="Total Trades"
              value={String(report.total_trades ?? "—")}
            />
          </div>

          {/* Equity curve */}
          <EquityCurveChart data={curve} />

          {/* Regime table */}
          <RegimeTable rows={report.regime_stats ?? []} />
        </>
      )}
    </div>
  );
}
