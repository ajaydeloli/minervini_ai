/**
 * app/portfolio/page.tsx
 * ──────────────────────
 * Paper Portfolio page.
 *
 * Layout
 * ──────
 * - Page header: "Paper Portfolio" + subtitle
 * - PortfolioSummary KPI cards
 * - EquityCurve (closed trades)
 * - Tab bar: "Open Positions (N)" | "Trade History (N)"
 *   - Open tab  → TradesTable mode="open"
 *   - History tab → TradesTable mode="closed"
 *
 * Data
 * ────
 * - SWR: fetchPortfolio() → PortfolioSummary
 * - SWR: fetchTrades()    → Trade[] (all statuses)
 * - refreshInterval: 60 000 ms (1 minute)
 *
 * States
 * ──────
 * - Loading: skeleton pulse
 * - Empty portfolio (no positions, no trades): full-page onboarding message
 * - Error: friendly error card
 */
"use client";

import * as React from "react";
import useSWR from "swr";
import { Briefcase, Loader2, AlertCircle, Play } from "lucide-react";
import { fetchPortfolio, fetchTrades } from "@/lib/api";
import PortfolioSummaryCards from "@/components/PortfolioSummary";
import EquityCurve from "@/components/EquityCurve";
import TradesTable from "@/components/TradesTable";
import { Skeleton } from "@/components/ui/skeleton";
import type { Trade } from "@/lib/types";

// ─── SWR config ──────────────────────────────────────────────────────────────

const SWR_OPTS = {
  refreshInterval: 60_000,
  revalidateOnFocus: true,
};

// ─── Tab bar ─────────────────────────────────────────────────────────────────

type TabId = "open" | "closed";

interface TabBarProps {
  active: TabId;
  onChange: (id: TabId) => void;
  openCount: number;
  closedCount: number;
}

function TabBar({ active, onChange, openCount, closedCount }: TabBarProps) {
  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: "open",   label: "Open Positions", count: openCount },
    { id: "closed", label: "Trade History",  count: closedCount },
  ];

  return (
    <div className="flex gap-0 border-b border-zinc-800">
      {tabs.map(({ id, label, count }) => {
        const isActive = active === id;
        return (
          <button
            key={id}
            onClick={() => onChange(id)}
            className={[
              "relative px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap",
              "after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:rounded-t",
              isActive
                ? "text-white after:bg-teal-500"
                : "text-zinc-500 hover:text-zinc-300 after:bg-transparent",
            ].join(" ")}
          >
            {label}
            <span
              className={[
                "ml-2 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
                isActive
                  ? "bg-teal-500/20 text-teal-400"
                  : "bg-zinc-800 text-zinc-500",
              ].join(" ")}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Loading skeleton ─────────────────────────────────────────────────────────

function PageSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4 space-y-3">
            <div className="flex items-center justify-between">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-8 w-8 rounded-lg" />
            </div>
            <Skeleton className="h-8 w-28" />
          </div>
        ))}
      </div>
      {/* Chart */}
      <Skeleton className="h-64 w-full rounded-xl" />
      {/* Table */}
      <Skeleton className="h-48 w-full rounded-xl" />
    </div>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 text-center px-6">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-teal-500/10">
        <Briefcase size={28} className="text-teal-400" />
      </div>

      <div className="space-y-2 max-w-md">
        <h2 className="text-lg font-semibold text-white">
          No paper trades yet
        </h2>
        <p className="text-sm text-zinc-400 leading-relaxed">
          Paper trading is enabled automatically after each daily screener run.
          The system simulates entering positions on every stock that triggers a
          breakout signal, starting with{" "}
          <span className="text-white font-medium">₹1,00,000</span> of virtual
          capital.
        </p>
        <p className="text-sm text-zinc-500 mt-3 leading-relaxed">
          Run the screener first to generate signals — paper trades will appear
          here as soon as the first breakout is detected.
        </p>
      </div>

      <div className="flex items-center gap-2 rounded-lg bg-[#161618] border border-[#1E1E21] px-4 py-3 text-xs text-zinc-400">
        <Play size={12} className="text-teal-400 flex-shrink-0" />
        Go to <strong className="text-zinc-200 mx-1">Dashboard</strong> → click{" "}
        <strong className="text-zinc-200 mx-1">Run Full Screen</strong> to get started.
      </div>
    </div>
  );
}

// ─── Error state ──────────────────────────────────────────────────────────────

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[40vh] gap-4 text-center px-6">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10">
        <AlertCircle size={22} className="text-red-400" />
      </div>
      <div>
        <p className="text-sm font-medium text-zinc-300">Failed to load portfolio</p>
        <p className="text-xs text-zinc-600 mt-1 max-w-xs">{message}</p>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const [activeTab, setActiveTab] = React.useState<TabId>("open");

  // SWR data fetching
  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
  } = useSWR("portfolio", fetchPortfolio, SWR_OPTS);

  const {
    data: allTrades,
    isLoading: tradesLoading,
    error: tradesError,
  } = useSWR("trades-all", () => fetchTrades(), SWR_OPTS);

  const isLoading = summaryLoading || tradesLoading;
  const error = summaryError ?? tradesError;

  // Partition trades
  const openTrades: Trade[] = React.useMemo(
    () => (allTrades ?? []).filter((t) => t.status === "open"),
    [allTrades]
  );
  const closedTrades: Trade[] = React.useMemo(
    () => (allTrades ?? []).filter((t) => t.status === "closed"),
    [allTrades]
  );

  // Determine empty portfolio (no trades at all and no open positions)
  const isEmpty =
    !isLoading &&
    !error &&
    summary != null &&
    summary.open_positions === 0 &&
    (allTrades ?? []).length === 0;

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-6">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-xl font-semibold text-white tracking-tight">
          Paper Portfolio
        </h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          ₹1,00,000 starting capital · auto-refreshes every minute
        </p>
      </div>

      {/* ── Loading skeleton ─────────────────────────────────────────────── */}
      {isLoading && <PageSkeleton />}

      {/* ── Error state ──────────────────────────────────────────────────── */}
      {!isLoading && error && (
        <ErrorState
          message={
            error instanceof Error ? error.message : "Unknown error occurred"
          }
        />
      )}

      {/* ── Empty state ──────────────────────────────────────────────────── */}
      {isEmpty && <EmptyState />}

      {/* ── Main content ─────────────────────────────────────────────────── */}
      {!isLoading && !error && !isEmpty && summary && (
        <>
          {/* KPI cards */}
          <PortfolioSummaryCards summary={summary} />

          {/* Equity curve — closed trades only */}
          <EquityCurve trades={closedTrades} />

          {/* Trades section */}
          <div className="rounded-xl border border-[#1E1E21] bg-[#161618] overflow-hidden">
            {/* Tab bar */}
            <TabBar
              active={activeTab}
              onChange={setActiveTab}
              openCount={openTrades.length}
              closedCount={closedTrades.length}
            />

            {/* Tab content */}
            <div className="min-h-[200px]">
              {activeTab === "open" ? (
                <TradesTable trades={openTrades} mode="open" />
              ) : (
                <TradesTable trades={closedTrades} mode="closed" />
              )}
            </div>
          </div>

          {/* Inline refresh indicator */}
          <p className="text-center text-[11px] text-zinc-700 flex items-center justify-center gap-1.5">
            <Loader2 size={11} className="animate-spin opacity-50" />
            Data refreshes every 60 s
          </p>
        </>
      )}
    </div>
  );
}
