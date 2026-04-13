/**
 * components/TrendTemplateCard.tsx
 * ──────────────────────────────────
 * Minervini 8-condition Trend Template checklist card.
 * Props:
 *   details — Record<string, boolean> keyed on TrendTemplateDetails field names
 *   pass    — overall template pass/fail
 */
"use client";

import * as React from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

// ── Ordered condition definitions ────────────────────────────────────────────
const CONDITIONS: { key: string; label: string; annotation?: string }[] = [
  { key: "above_150_200_ma",       label: "Price > SMA 150 & SMA 200" },
  { key: "ma_150_above_ma_200",    label: "SMA 150 > SMA 200" },
  { key: "ma_200_trending_up",     label: "SMA 200 trending up (20 days)" },
  { key: "ma_50_above_ma_150_200", label: "SMA 50 > SMA 150 & SMA 200" },
  { key: "price_above_ma_50",      label: "Price > SMA 50" },
  { key: "price_above_52w_low_30pct",    label: "Price ≥ 25% above 52-week low" },
  { key: "price_within_25pct_52w_high",  label: "Price within 25% of 52-week high" },
  { key: "rs_52w_high",            label: "RS Rating ≥ 70", annotation: "Threshold: 70" },
];

// ── Sub-components ───────────────────────────────────────────────────────────

interface ConditionRowProps {
  label: string;
  met: boolean;
  annotation?: string;
}

function ConditionRow({ label, met, annotation }: ConditionRowProps) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      {met ? (
        <CheckCircle2 className="h-4 w-4 text-teal-400 mt-0.5 shrink-0" />
      ) : (
        <XCircle className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
      )}
      <div className="flex flex-col min-w-0">
        <span className={cn("text-xs font-medium leading-tight", met ? "text-zinc-200" : "text-zinc-500")}>
          {label}
        </span>
        {annotation && (
          <span className="text-[10px] text-zinc-600 font-mono mt-0.5">{annotation}</span>
        )}
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

interface TrendTemplateCardProps {
  details: Record<string, boolean>;
  pass: boolean;
}

export default function TrendTemplateCard({ details, pass }: TrendTemplateCardProps) {
  const metCount = CONDITIONS.filter((c) => details[c.key]).length;

  return (
    <div
      className={cn(
        "rounded-xl border bg-zinc-900 p-5 transition-all",
        pass
          ? "border-teal-500/40 shadow-[0_0_20px_rgba(20,184,166,0.12)]"
          : "border-zinc-800/60"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200 tracking-wide">
          Trend Template
        </h3>
        <span
          className={cn(
            "text-xs font-bold px-2.5 py-1 rounded-full tracking-wide",
            pass
              ? "bg-teal-500/15 text-teal-400 border border-teal-500/30"
              : "bg-red-500/15 text-red-400 border border-red-500/30"
          )}
        >
          {pass ? "✓ PASS" : "✗ FAIL"}
        </span>
      </div>

      {/* Conditions grid — 2 columns */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 divide-y divide-zinc-800/40 sm:divide-y-0">
        {CONDITIONS.map((c, i) => (
          <div key={c.key} className={cn(i < CONDITIONS.length - 1 && i % 2 === 0 ? "" : "")}>
            <ConditionRow
              label={c.label}
              met={details[c.key] ?? false}
              annotation={c.annotation}
            />
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="mt-4 pt-3 border-t border-zinc-800/60">
        <p className="text-xs text-zinc-500">
          <span className={cn("font-bold", metCount === 8 ? "text-teal-400" : "text-zinc-300")}>
            {metCount}/8
          </span>{" "}
          conditions met
        </p>
      </div>
    </div>
  );
}
