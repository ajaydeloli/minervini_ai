/**
 * components/FundamentalCard.tsx
 * ────────────────────────────────
 * 7 Minervini fundamental conditions checklist card.
 * Props:
 *   pass    — overall fundamental pass/fail
 *   details — Record<string, boolean> keyed on FundamentalDetails field names
 *   score   — composite SEPA score (0–100) shown as context
 */
"use client";

import * as React from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

// ── Ordered condition definitions ────────────────────────────────────────────
const CONDITIONS: { key: string; label: string }[] = [
  { key: "eps_growth_yoy",          label: "EPS positive (latest quarter)" },
  { key: "eps_growth_qoq",          label: "EPS accelerating (QoQ)" },
  { key: "revenue_growth_yoy",      label: "Sales growth ≥ 10% YoY" },
  { key: "roe_positive",            label: "ROE ≥ 15%" },
  { key: "debt_to_equity_ok",       label: "D/E ratio ≤ 1.0" },
  { key: "institutional_sponsorship", label: "Promoter holding ≥ 35%" },
  { key: "earnings_surprise",       label: "Positive profit growth" },
];

// ── Condition row ────────────────────────────────────────────────────────────

function ConditionRow({ label, met }: { label: string; met: boolean }) {
  return (
    <div className="flex items-center gap-2.5 py-1.5 border-b border-zinc-800/40 last:border-0">
      {met ? (
        <CheckCircle2 className="h-4 w-4 text-teal-400 shrink-0" />
      ) : (
        <XCircle className="h-4 w-4 text-red-400 shrink-0" />
      )}
      <span className={cn("text-xs font-medium", met ? "text-zinc-200" : "text-zinc-500")}>
        {label}
      </span>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

interface FundamentalCardProps {
  pass: boolean;
  details: Record<string, boolean>;
  score: number;
}

export default function FundamentalCard({ pass, details, score }: FundamentalCardProps) {
  const metCount = CONDITIONS.filter((c) => details[c.key]).length;

  return (
    <div
      className={cn(
        "rounded-xl border bg-zinc-900 p-5",
        pass ? "border-teal-500/40 shadow-[0_0_20px_rgba(20,184,166,0.12)]" : "border-zinc-800/60"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200 tracking-wide">Fundamentals</h3>
        <span
          className={cn(
            "text-xs font-bold px-2.5 py-1 rounded-full tracking-wide border",
            pass
              ? "bg-teal-500/15 text-teal-400 border-teal-500/30"
              : "bg-red-500/15 text-red-400 border-red-500/30"
          )}
        >
          {pass ? "✓ PASS" : "✗ FAIL"}
        </span>
      </div>

      {/* Single-column condition list */}
      <div className="flex flex-col">
        {CONDITIONS.map((c) => (
          <ConditionRow key={c.key} label={c.label} met={details[c.key] ?? false} />
        ))}
      </div>

      {/* Footer: count + attribution */}
      <div className="mt-4 pt-3 border-t border-zinc-800/60 flex flex-col gap-1.5">
        <p className="text-xs text-zinc-500">
          <span className={cn("font-bold", metCount === 7 ? "text-teal-400" : "text-zinc-300")}>
            {metCount}/7
          </span>{" "}
          fundamental conditions met
        </p>
        <p className="text-[10px] text-zinc-600">
          Data:{" "}
          <a
            href="https://www.screener.in"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-zinc-400 transition-colors"
          >
            Screener.in
          </a>{" "}
          (7-day cache)
        </p>
      </div>
    </div>
  );
}
