/**
 * app/page.tsx
 * ─────────────
 * Home dashboard — KPI cards, top A+ setups table, quick-action buttons.
 *
 * Data sources (SWR):
 *   fetchMeta()              → universe_size, watchlist_size, a_plus_count, a_count
 *   fetchTopStocks("A+", 5)  → top 5 A+ setups for the table
 *
 * All SWR hooks use revalidateOnFocus: true, refreshInterval: 300_000 (5 min).
 */
"use client";

import useSWR from "swr";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  BarChart2,
  Star,
  TrendingUp,
  Briefcase,
  CheckCircle2,
  XCircle,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";
import { fetchMeta, fetchTopStocks, triggerRun } from "@/lib/api";
import QualityBadge from "@/components/QualityBadge";
import MarketStatusBar from "@/components/MarketStatusBar";
import type { SEPAResult } from "@/lib/types";

/* ── SWR config ────────────────────────────────────────────────────────────── */
const SWR_OPTS = { revalidateOnFocus: true, refreshInterval: 300_000 };

/* ── KPI card ─────────────────────────────────────────────────────────────── */
function KpiCard({
  icon: Icon,
  value,
  label,
  accent,
}: {
  icon: React.ElementType;
  value: number | null | undefined;
  label: string;
  accent?: string;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-[#161618] border border-[#1E1E21] px-5 py-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-zinc-500 font-medium uppercase tracking-wider">
          {label}
        </span>
        <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${accent ?? "bg-zinc-800"}`}>
          <Icon size={15} className="text-zinc-300" />
        </div>
      </div>
      <span className="font-mono text-3xl font-semibold text-white tabular-nums">
        {value ?? <span className="text-zinc-600">—</span>}
      </span>
    </div>
  );
}

/* ── Bool icon ────────────────────────────────────────────────────────────── */
function BoolIcon({ value }: { value: boolean }) {
  return value ? (
    <CheckCircle2 size={14} className="text-teal-400" />
  ) : (
    <XCircle size={14} className="text-zinc-600" />
  );
}

/* ── Toast (inline, no external lib) ─────────────────────────────────────── */
interface ToastState {
  msg: string;
  kind: "ok" | "err";
}

/* ── Main page ────────────────────────────────────────────────────────────── */
export default function DashboardPage() {
  const router = useRouter();

  /* data */
  const { data: meta } = useSWR("meta", fetchMeta, SWR_OPTS);
  const { data: topStocks, isLoading: stocksLoading } = useSWR(
    "top-aplus",
    () => fetchTopStocks("A+", 5),
    SWR_OPTS
  );

  /* run buttons */
  const [runningAll, setRunningAll] = useState(false);
  const [runningWl, setRunningWl] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  function showToast(msg: string, kind: "ok" | "err") {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 4000);
  }

  async function handleRun(scope: "all" | "watchlist") {
    const setter = scope === "all" ? setRunningAll : setRunningWl;
    setter(true);
    try {
      await triggerRun({ scope });
      showToast(
        scope === "all" ? "Full screen triggered ✓" : "Watchlist screen triggered ✓",
        "ok"
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Run failed";
      showToast(msg, "err");
    } finally {
      setter(false);
    }
  }

  return (
    <div className="min-h-full px-4 py-6 md:px-8 md:py-8 space-y-8">

      {/* ── Page header ───────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Dashboard</h1>
          <p className="text-sm text-zinc-500 mt-0.5">SEPA setup overview · NSE universe</p>
        </div>
        {/* Desktop market status (sidebar pages don't have a top bar) */}
        <div className="hidden md:block">
          <MarketStatusBar />
        </div>
      </div>

      {/* ── KPI cards ─────────────────────────────────────────────── */}
      <section>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KpiCard icon={BarChart2}  value={meta?.universe_size}  label="Universe"       accent="bg-zinc-800" />
          <KpiCard icon={TrendingUp} value={meta?.a_plus_count}   label="A+ Setups"      accent="bg-teal-500/10" />
          <KpiCard icon={Star}       value={meta?.a_count}        label="A Setups"        accent="bg-green-500/10" />
          <KpiCard icon={Briefcase}  value={meta?.watchlist_size} label="Watchlist"      accent="bg-zinc-800" />
        </div>
      </section>

      {/* ── Today's Best Setups ───────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">
          Today&apos;s Best Setups
        </h2>

        <div className="rounded-xl border border-[#1E1E21] bg-[#161618] overflow-hidden">
          {stocksLoading ? (
            <div className="flex items-center justify-center py-12 text-zinc-600">
              <Loader2 size={20} className="animate-spin mr-2" />
              <span className="text-sm">Loading setups…</span>
            </div>
          ) : !topStocks || topStocks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2 text-center">
              <p className="text-zinc-400 text-sm">No A+ setups found today.</p>
              <p className="text-zinc-600 text-xs">Run the screener to update.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1E1E21] text-zinc-500 text-xs uppercase tracking-wider">
                    <th className="px-4 py-3 text-left font-medium">Symbol</th>
                    <th className="px-4 py-3 text-right font-medium">Score</th>
                    <th className="px-4 py-3 text-center font-medium">Quality</th>
                    <th className="px-4 py-3 text-center font-medium hidden sm:table-cell">Stage</th>
                    <th className="px-4 py-3 text-right font-medium hidden sm:table-cell">RS</th>
                    <th className="px-4 py-3 text-center font-medium hidden sm:table-cell">VCP</th>
                    <th className="px-4 py-3 text-center font-medium hidden sm:table-cell">BO</th>
                  </tr>
                </thead>
                <tbody>
                  {topStocks.map((stock: SEPAResult, i: number) => (
                    <tr
                      key={stock.symbol}
                      onClick={() => router.push(`/screener/${stock.symbol}`)}
                      className={`cursor-pointer transition-colors hover:bg-[#1E1E21] ${
                        i < topStocks.length - 1 ? "border-b border-[#1E1E21]" : ""
                      }`}
                    >
                      <td className="px-4 py-3 font-mono font-semibold text-white">{stock.symbol}</td>
                      <td className="px-4 py-3 text-right font-mono text-zinc-200 tabular-nums">
                        {stock.score.toFixed(0)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <QualityBadge quality={stock.setup_quality} />
                      </td>
                      <td className="px-4 py-3 text-center text-zinc-400 hidden sm:table-cell">{stock.stage_label}</td>
                      <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-200 hidden sm:table-cell">{stock.rs_rating}</td>
                      <td className="px-4 py-3 text-center hidden sm:table-cell"><BoolIcon value={stock.vcp_qualified} /></td>
                      <td className="px-4 py-3 text-center hidden sm:table-cell"><BoolIcon value={stock.breakout_triggered} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* ── Quick Actions ─────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">
          Quick Actions
        </h2>

        <div className="flex flex-wrap gap-3">
          {/* Run Full Screen */}
          <button
            onClick={() => handleRun("all")}
            disabled={runningAll || runningWl}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-4 py-2.5 text-sm font-semibold text-white
                       transition-colors hover:bg-teal-400 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {runningAll ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Play size={14} />
            )}
            Run Full Screen
          </button>

          {/* Run Watchlist */}
          <button
            onClick={() => handleRun("watchlist")}
            disabled={runningAll || runningWl}
            className="inline-flex items-center gap-2 rounded-lg border border-[#1E1E21] bg-[#161618] px-4 py-2.5
                       text-sm font-semibold text-zinc-200 transition-colors hover:bg-[#1E1E21] hover:text-white
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {runningWl ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            Run Watchlist
          </button>
        </div>
      </section>

      {/* ── Inline toast ──────────────────────────────────────────── */}
      {toast && (
        <div
          className={`fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-lg text-sm font-medium shadow-lg
            ${toast.kind === "ok"
              ? "bg-teal-500/90 text-white"
              : "bg-red-500/90 text-white"
            }`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
