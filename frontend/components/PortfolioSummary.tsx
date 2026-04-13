/**
 * components/PortfolioSummary.tsx
 * ────────────────────────────────
 * Five KPI cards for the portfolio overview.
 * - Count-up animation on mount (600ms, requestAnimationFrame)
 * - Indian currency format (₹X,XX,XXX)
 * - Teal icons, dark bg cards
 * - 5 cards in a row on desktop, 2-per-row on mobile
 */
"use client";

import * as React from "react";
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  BarChart2,
  Target,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { PortfolioSummary } from "@/lib/types";

// ─── Formatters ─────────────────────────────────────────────────────────────

const inrCurrency = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const inrCurrencyDec = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

function fmtInr(val: number): string {
  return inrCurrency.format(val);
}

function fmtInrSigned(val: number): string {
  const formatted = inrCurrencyDec.format(Math.abs(val));
  return val >= 0 ? `+${formatted}` : `-${formatted}`;
}

// ─── Count-up hook ──────────────────────────────────────────────────────────

function useCountUp(target: number, duration = 600): number {
  const [current, setCurrent] = React.useState(0);
  const startRef = React.useRef<number | null>(null);
  const rafRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    startRef.current = null;

    function step(timestamp: number) {
      if (startRef.current === null) startRef.current = timestamp;
      const elapsed = timestamp - startRef.current;
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setCurrent(target * eased);
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        setCurrent(target);
      }
    }

    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return current;
}

// ─── Individual KPI card ─────────────────────────────────────────────────────

interface KpiCardProps {
  icon: React.ElementType;
  label: string;
  displayValue: React.ReactNode;
  positive?: boolean;   // undefined = neutral
  target: number;       // raw numeric target for count-up
}

function KpiCard({ icon: Icon, label, displayValue, positive }: KpiCardProps) {
  const colorClass =
    positive === undefined
      ? "text-white"
      : positive
      ? "text-green-400"
      : "text-red-400";

  return (
    <div className="flex flex-col gap-3 rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">
          {label}
        </span>
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-500/10">
          <Icon size={15} className="text-teal-400" />
        </div>
      </div>
      <span className={cn("font-mono text-2xl font-semibold tabular-nums leading-tight", colorClass)}>
        {displayValue}
      </span>
    </div>
  );
}

// ─── Animated value components ───────────────────────────────────────────────

function AnimatedInr({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{fmtInr(animated)}</>;
}

function AnimatedInrSigned({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{fmtInrSigned(animated)}</>;
}

function AnimatedPct({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{(animated >= 0 ? "+" : "") + animated.toFixed(2)}%</>;
}

function AnimatedCount({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{Math.round(animated)}</>;
}

function AnimatedWinRate({ value }: { value: number }) {
  const animated = useCountUp(value);
  return <>{animated.toFixed(1)}%</>;
}

// ─── Main component ──────────────────────────────────────────────────────────

interface PortfolioSummaryProps {
  summary: PortfolioSummary;
}

export default function PortfolioSummaryCards({ summary }: PortfolioSummaryProps) {
  const returnPositive =
    summary.total_return_pct === 0 ? undefined : summary.total_return_pct > 0;
  const realisedPositive =
    summary.realised_pnl === 0 ? undefined : summary.realised_pnl > 0;
  const winRatePositive =
    summary.win_rate === 50
      ? undefined
      : summary.win_rate > 50;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">

      {/* Total Value */}
      <KpiCard
        icon={Wallet}
        label="Total Value"
        target={summary.total_value}
        displayValue={<AnimatedInr value={summary.total_value} />}
      />

      {/* Total Return */}
      <KpiCard
        icon={returnPositive !== false ? TrendingUp : TrendingDown}
        label="Total Return"
        target={summary.total_return_pct}
        positive={returnPositive}
        displayValue={
          <span className={cn(
            "inline-flex items-center gap-1",
            returnPositive === undefined
              ? "text-white"
              : returnPositive
              ? "text-green-400"
              : "text-red-400"
          )}>
            {returnPositive === true ? (
              <TrendingUp size={18} className="flex-shrink-0" />
            ) : returnPositive === false ? (
              <TrendingDown size={18} className="flex-shrink-0" />
            ) : null}
            <AnimatedPct value={summary.total_return_pct} />
          </span>
        }
      />

      {/* Realised P&L */}
      <KpiCard
        icon={BarChart2}
        label="Realised P&L"
        target={summary.realised_pnl}
        positive={realisedPositive}
        displayValue={
          <span className={cn(
            realisedPositive === undefined
              ? "text-white"
              : realisedPositive
              ? "text-green-400"
              : "text-red-400"
          )}>
            <AnimatedInrSigned value={summary.realised_pnl} />
          </span>
        }
      />

      {/* Open Positions */}
      <KpiCard
        icon={BarChart2}
        label="Open Positions"
        target={summary.open_positions}
        displayValue={<AnimatedCount value={summary.open_positions} />}
      />

      {/* Win Rate */}
      <KpiCard
        icon={Target}
        label="Win Rate"
        target={summary.win_rate}
        positive={winRatePositive}
        displayValue={
          <span className={cn(
            winRatePositive === undefined
              ? "text-white"
              : winRatePositive
              ? "text-green-400"
              : "text-red-400"
          )}>
            <AnimatedWinRate value={summary.win_rate} />
          </span>
        }
      />
    </div>
  );
}
