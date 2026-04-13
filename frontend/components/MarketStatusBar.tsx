/**
 * components/MarketStatusBar.tsx
 * ───────────────────────────────
 * Small status indicator: market open/closed (IST), last run time,
 * A+ count and A count.  Auto-refreshes every 60 s via SWR.
 */
"use client";

import useSWR from "swr";
import { fetchMeta } from "@/lib/api";

/* ── IST market-hours helper ─────────────────────────────────────────────── */
function isMarketOpen(): boolean {
  // IST = UTC + 5 h 30 m
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const ist = new Date(utcMs + 5.5 * 3_600_000);

  const day = ist.getDay(); // 0 = Sun, 6 = Sat
  if (day === 0 || day === 6) return false;

  const mins = ist.getHours() * 60 + ist.getMinutes();
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30; // 09:15 – 15:30
}

/* ── Format last_screen_date from MetaResponse ───────────────────────────── */
function formatLastRun(raw: string | null): string {
  if (!raw) return "—";
  try {
    const d = new Date(raw);
    // If it looks like a plain date (no time) just return it
    if (!raw.includes("T")) return raw;
    return (
      d.toLocaleTimeString("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }) + " IST"
    );
  } catch {
    return raw;
  }
}

/* ── Component ────────────────────────────────────────────────────────────── */
export default function MarketStatusBar() {
  const { data } = useSWR("meta/status", fetchMeta, {
    refreshInterval: 60_000,
    revalidateOnFocus: false,
  });

  const open = isMarketOpen();

  return (
    <div className="flex items-center gap-3 text-xs text-zinc-400 flex-wrap">
      {/* Open / Closed pill */}
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            open ? "bg-teal-400 shadow-[0_0_6px_#2dd4bf]" : "bg-zinc-600"
          }`}
        />
        <span className={open ? "text-teal-400 font-medium" : "text-zinc-500"}>
          {open ? "Market Open" : "Closed"}
        </span>
      </span>

      {data && (
        <>
          {/* Last run */}
          {data.last_screen_date && (
            <span className="hidden sm:inline text-zinc-500">
              Last run:{" "}
              <span className="text-zinc-300">{formatLastRun(data.last_screen_date)}</span>
            </span>
          )}

          {/* A+ count */}
          <span className="hidden sm:inline">
            <span className="font-semibold text-teal-400">{data.a_plus_count ?? 0}</span>
            <span className="text-zinc-600 ml-0.5"> A+</span>
          </span>

          {/* A count */}
          <span className="hidden sm:inline">
            <span className="font-semibold text-green-400">{data.a_count ?? 0}</span>
            <span className="text-zinc-600 ml-0.5"> A</span>
          </span>
        </>
      )}
    </div>
  );
}
