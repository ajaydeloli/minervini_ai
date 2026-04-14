/**
 * app/screener/[symbol]/page.tsx
 * ───────────────────────────────
 * Stock deep-dive page for a single SEPA result.
 *
 * Layout (desktop)
 * ────────────────
 *   [Header: Symbol · QualityBadge · Stage · RS · ★ Watchlist · ← Back]
 *   ┌────────────── 60% ──────────────┐  ┌──── 40% ────┐
 *   │  CandlestickChart               │  │ ScoreGauge  │
 *   │  Score-history sparkline        │  │ Info pills  │
 *   └─────────────────────────────────┘  └─────────────┘
 *   [Tab bar: Trend Template | VCP | Fundamentals | AI Brief]
 *
 * Mobile: columns stack vertically; chart first, gauge + pills below, tabs last.
 *
 * TODO (Phase 13): Replace generateMockOHLCV() with a real fetch once
 * GET /api/v1/stock/{symbol}/ohlcv is added to api/routers/stocks.py
 */
"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  LineChart, Line, ResponsiveContainer, Tooltip, YAxis,
} from "recharts";
import { ArrowLeft, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import QualityBadge from "@/components/QualityBadge";
import ScoreGauge from "@/components/ScoreGauge";
import CandlestickChart, {
  type OHLCVPoint,
  type MAPoint,
} from "@/components/CandlestickChart";
import TrendTemplateCard from "@/components/TrendTemplateCard";
import VCPCard from "@/components/VCPCard";
import FundamentalCard from "@/components/FundamentalCard";
import LLMBrief from "@/components/LLMBrief";
import {
  fetchStock,
  fetchStockHistory,
  fetchWatchlist,
  addToWatchlist,
  fetchOHLCV,
} from "@/lib/api";
import type { SEPAResult, StockHistoryPoint } from "@/lib/types";

// ─── Mock OHLCV generator ────────────────────────────────────────────────────
// TODO (Phase 13): Remove this and fetch GET /api/v1/stock/{symbol}/ohlcv instead.

function generateMockOHLCV(
  stock: SEPAResult,
  days = 120
): { ohlcv: OHLCVPoint[]; mas: MAPoint[] } {
  const entry = stock.entry_price ?? 100;
  const stop  = stock.stop_loss  ?? entry * 0.93;
  const range = entry - stop;

  // Deterministic seed from entry_price so it's stable across re-renders
  let seed = Math.round(entry * 1000);
  function rand() {
    seed = (seed * 1664525 + 1013904223) & 0xffffffff;
    return (seed >>> 0) / 0xffffffff;
  }

  const ohlcv: OHLCVPoint[] = [];
  const mas:   MAPoint[]    = [];

  let price = entry * (0.85 + rand() * 0.1);
  const now = Math.floor(Date.now() / 1000);
  const DAY = 86400;

  // Buffers for MA calculation
  const closes: number[] = [];

  function sma(n: number): number | null {
    if (closes.length < n) return null;
    const slice = closes.slice(-n);
    return slice.reduce((a, b) => a + b, 0) / n;
  }

  for (let i = days; i >= 0; i--) {
    const t = now - i * DAY;
    // Skip weekends
    const dow = new Date(t * 1000).getDay();
    if (dow === 0 || dow === 6) continue;

    const dailyMove = (rand() - 0.48) * range * 0.4;
    const open  = price;
    price = Math.max(stop * 0.9, price + dailyMove);
    const close = price;
    const wick  = range * 0.15 * rand();
    const high  = Math.max(open, close) + wick;
    const low   = Math.min(open, close) - wick * 0.5;
    const volume = Math.round((500_000 + rand() * 2_000_000));

    ohlcv.push({ time: t as unknown as import("lightweight-charts").Time, open, high, low, close, volume });
    closes.push(close);

    mas.push({
      time:   t as unknown as import("lightweight-charts").Time,
      sma10:  sma(10),
      sma21:  sma(21),
      sma50:  sma(50),
      sma150: sma(150),
      sma200: sma(200),
    });
  }

  return { ohlcv, mas };
}

// ─── Loading skeleton ────────────────────────────────────────────────────────

function DeepDiveSkeleton() {
  return (
    <div className="px-4 md:px-8 py-6 max-w-[1400px] mx-auto space-y-4 animate-pulse">
      <Skeleton className="h-8 w-48 bg-zinc-800" />
      <div className="flex gap-4">
        <Skeleton className="h-6 w-16 bg-zinc-800" />
        <Skeleton className="h-6 w-20 bg-zinc-800" />
        <Skeleton className="h-6 w-14 bg-zinc-800" />
      </div>
      <div className="flex flex-col lg:flex-row gap-4">
        <Skeleton className="h-[420px] lg:w-[60%] bg-zinc-800 rounded-xl" />
        <div className="flex flex-col gap-3 lg:w-[40%]">
          <Skeleton className="h-[160px] bg-zinc-800 rounded-xl" />
          <Skeleton className="h-16 bg-zinc-800 rounded-xl" />
          <Skeleton className="h-16 bg-zinc-800 rounded-xl" />
        </div>
      </div>
      <Skeleton className="h-10 w-full bg-zinc-800 rounded-xl" />
    </div>
  );
}

// ─── Info pill ───────────────────────────────────────────────────────────────

function InfoPill({
  label,
  value,
  color,
}: {
  label: string;
  value: string | null;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-zinc-900 border border-zinc-800/60 px-4 py-3">
      <span className="text-xs text-zinc-500 font-medium tracking-wide uppercase">{label}</span>
      <span
        className={cn("font-mono font-semibold text-sm", color ?? "text-white")}
      >
        {value ?? "—"}
      </span>
    </div>
  );
}

// ─── Score sparkline ─────────────────────────────────────────────────────────

function ScoreSparkline({ history }: { history: StockHistoryPoint[] }) {
  if (history.length === 0) return null;
  return (
    <div className="rounded-xl bg-zinc-900 border border-zinc-800/60 px-4 py-3">
      <p className="text-[11px] text-zinc-500 font-medium tracking-wide uppercase mb-2">
        Score — last 30 days
      </p>
      <ResponsiveContainer width="100%" height={64}>
        <LineChart data={history} margin={{ top: 2, right: 4, bottom: 2, left: 0 }}>
          <YAxis domain={[0, 100]} hide />
          <Tooltip
            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }}
            labelFormatter={(v) => String(v)}
            formatter={(v) => [`${v ?? ""}`, "Score"]}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#14B8A6"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: "#14B8A6" }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function StockDeepDivePage() {
  const params = useParams();
  const router = useRouter();
  const symbol = (Array.isArray(params.symbol) ? params.symbol[0] : params.symbol ?? "").toUpperCase();

  // ── Data fetching ─────────────────────────────────────────────────────────
  const {
    data: stock,
    isLoading: stockLoading,
    error: stockError,
  } = useSWR(symbol ? `stock/${symbol}` : null, () => fetchStock(symbol), {
    refreshInterval: 120_000,
  });

  const { data: history = [] } = useSWR(
    symbol ? `stock/${symbol}/history` : null,
    () => fetchStockHistory(symbol, 30),
    { refreshInterval: 120_000 }
  );

  const { data: watchlistItems = [], mutate: mutateWatchlist } = useSWR(
    "watchlist",
    fetchWatchlist,
    { refreshInterval: 120_000 }
  );

  const inWatchlist = watchlistItems.some((w) => w.symbol === symbol);
  const [wlLoading, setWlLoading] = React.useState(false);

  async function handleWatchlist() {
    setWlLoading(true);
    try {
      await addToWatchlist(symbol);
      await mutateWatchlist();
    } finally {
      setWlLoading(false);
    }
  }

  // ── Derived chart data ─────────────────────────────────────────────────────
  const { ohlcv, mas } = React.useMemo(
    () => (stock ? generateMockOHLCV(stock) : { ohlcv: [], mas: [] }),
    [stock]
  );

  // ── Loading / error states ─────────────────────────────────────────────────
  if (stockLoading) return <DeepDiveSkeleton />;

  if (stockError || !stock) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[50vh] gap-4 px-4">
        <p className="text-zinc-400 text-sm">Symbol not found or no recent data.</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => router.push("/screener")}
          className="gap-2"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Screener
        </Button>
      </div>
    );
  }

  // ── Derived display values ─────────────────────────────────────────────────
  const riskPct  = stock.risk_pct != null ? `${stock.risk_pct.toFixed(1)}%` : null;
  const rrRatio  = stock.rr_ratio != null ? `${stock.rr_ratio.toFixed(1)}×`  : null;
  const entryFmt = stock.entry_price != null ? `$${stock.entry_price.toFixed(2)}` : null;
  const stopFmt  = stock.stop_loss   != null ? `$${stock.stop_loss.toFixed(2)}`   : null;

  // VCP zone (rough band: entry ± base range)
  const vcpZone = stock.vcp_qualified && stock.entry_price && stock.stop_loss
    ? {
        startTime: ohlcv[Math.max(0, ohlcv.length - 30)]?.time ?? ohlcv[0]?.time,
        endTime:   ohlcv[ohlcv.length - 1]?.time,
        low:  stock.stop_loss,
        high: stock.entry_price * 1.03,
      }
    : undefined;

  // ── Flatten details for tab components ────────────────────────────────────
  const trendDetails: Record<string, boolean> =
    (stock.trend_template_details as Record<string, boolean> | null) ?? {};

  const fundDetails: Record<string, boolean> =
    (stock.fundamental_details as Record<string, boolean> | null) ?? {};

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="px-4 md:px-8 py-6 max-w-[1400px] mx-auto space-y-5">

      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* Left: symbol + badges */}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/screener")}
            className="gap-1.5 text-zinc-400 hover:text-white -ml-2 px-2"
          >
            <ArrowLeft className="h-4 w-4" />
            Screener
          </Button>

          <h1 className="text-3xl font-bold text-white tracking-tight font-mono">
            {symbol}
          </h1>

          <QualityBadge quality={stock.setup_quality} />

          {/* Stage pill */}
          <span className="rounded-full bg-zinc-800 border border-zinc-700 px-3 py-0.5 text-xs font-semibold text-zinc-300">
            {stock.stage_label}
          </span>

          {/* RS badge */}
          <span className="rounded-full bg-indigo-500/15 border border-indigo-500/30 px-3 py-0.5 text-xs font-mono font-semibold text-indigo-300">
            RS {stock.rs_rating}
          </span>
        </div>

        {/* Right: Watchlist button */}
        <Button
          size="sm"
          onClick={handleWatchlist}
          disabled={wlLoading || inWatchlist}
          className={cn(
            "gap-2 h-9 border transition-colors",
            inWatchlist
              ? "bg-teal-900/40 border-teal-600/50 text-teal-300 cursor-default"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          )}
        >
          <Star
            className="h-4 w-4"
            fill={inWatchlist ? "currentColor" : "none"}
          />
          {inWatchlist ? "In Watchlist" : "Add to Watchlist"}
        </Button>
      </div>

      {/* ── Two-column body ───────────────────────────────────────────── */}
      <div className="flex flex-col lg:flex-row gap-4">

        {/* Left column — 60% */}
        <div className="flex flex-col gap-4 lg:w-[60%]">
          <CandlestickChart
            ohlcv={ohlcv}
            mas={mas}
            entryPrice={stock.entry_price ?? undefined}
            stopLoss={stock.stop_loss ?? undefined}
            targetPrice={stock.target_price ?? undefined}
            vcpQualified={stock.vcp_qualified}
            vcpZone={vcpZone as Parameters<typeof CandlestickChart>[0]["vcpZone"]}
            className="h-[400px]"
          />
          <ScoreSparkline history={history} />
        </div>

        {/* Right column — 40% */}
        <div className="flex flex-col gap-3 lg:w-[40%]">
          {/* Score gauge */}
          <div className="rounded-xl bg-zinc-900 border border-zinc-800/60 p-5 flex flex-col items-center gap-2">
            <p className="text-[11px] text-zinc-500 font-medium tracking-wide uppercase self-start">
              SEPA Score
            </p>
            <ScoreGauge score={stock.score} quality={stock.setup_quality} />
          </div>

          {/* Info pills */}
          <InfoPill label="Entry Price" value={entryFmt} color="text-teal-400" />
          <InfoPill label="Stop Loss"   value={stopFmt}  color="text-red-400" />
          <InfoPill label="Risk %"      value={riskPct}  color="text-amber-400" />
          <InfoPill
            label="R:R Ratio"
            value={rrRatio}
            color={
              stock.rr_ratio != null && stock.rr_ratio >= 2
                ? "text-green-400"
                : "text-zinc-400"
            }
          />
        </div>
      </div>

      {/* ── Tab bar ───────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-zinc-800/60 overflow-hidden">
        <Tabs defaultValue="trend" className="w-full">
          {/* Tab bar */}
          <TabsList className="w-full flex rounded-none bg-zinc-950 border-b border-zinc-800/60 h-auto p-0">
            {[
              { value: "trend",        label: "Trend Template", short: "TT" },
              { value: "vcp",          label: "VCP",            short: "VCP" },
              { value: "fundamentals", label: "Fundamentals",   short: "Fund" },
              { value: "ai",           label: "AI Brief",       short: "AI" },
            ].map(({ value, label, short }) => (
              <TabsTrigger
                key={value}
                value={value}
                className={cn(
                  "flex-1 py-3 rounded-none text-xs font-semibold tracking-wide transition-colors",
                  "text-zinc-500 hover:text-zinc-300",
                  "data-[state=active]:text-teal-400 data-[state=active]:border-b-2",
                  "data-[state=active]:border-teal-500 data-[state=active]:bg-zinc-900",
                  "data-[state=active]:shadow-none"
                )}
              >
                {/* Full label on desktop, abbreviated on mobile */}
                <span className="hidden sm:inline">{label}</span>
                <span className="sm:hidden">{short}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          {/* ── Trend Template tab ─────────────────────────────────────── */}
          <TabsContent value="trend" className="bg-zinc-950 p-0 mt-0">
            <div className="pt-6 px-4 pb-4">
              <TrendTemplateCard
                details={trendDetails}
                pass={stock.trend_template_pass}
              />
            </div>
          </TabsContent>

          {/* ── VCP tab ────────────────────────────────────────────────── */}
          <TabsContent value="vcp" className="bg-zinc-950 p-0 mt-0">
            <div className="pt-6 px-4 pb-4">
              <VCPCard
                qualified={stock.vcp_qualified}
                details={stock.vcp_details}
              />
            </div>
          </TabsContent>

          {/* ── Fundamentals tab ───────────────────────────────────────── */}
          <TabsContent value="fundamentals" className="bg-zinc-950 p-0 mt-0">
            <div className="pt-6 px-4 pb-4">
              <FundamentalCard
                pass={stock.fundamental_pass ?? false}
                details={fundDetails}
                score={stock.score}
              />
            </div>
          </TabsContent>

          {/* ── AI Brief tab ───────────────────────────────────────────── */}
          <TabsContent value="ai" className="bg-zinc-950 p-0 mt-0">
            <div className="pt-6 px-4 pb-4">
              <LLMBrief
                narrative={stock.narrative}
                quality={stock.setup_quality}
              />
            </div>
          </TabsContent>
        </Tabs>
      </div>

    </div>
  );
}
