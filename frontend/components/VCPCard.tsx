/**
 * components/VCPCard.tsx
 * ──────────────────────
 * VCP (Volatility Contraction Pattern) qualification details card.
 * Props:
 *   qualified — boolean
 *   details   — VCPDetails from SEPAResult.vcp_details
 */
"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { VCPDetails } from "@/lib/types";

// ── Grade color map ──────────────────────────────────────────────────────────

const GRADE_STYLES: Record<string, string> = {
  A:    "text-teal-400 bg-teal-500/10 border-teal-500/30",
  B:    "text-green-400 bg-green-500/10 border-green-500/30",
  C:    "text-amber-400 bg-amber-500/10 border-amber-500/30",
  FAIL: "text-red-400 bg-red-500/10 border-red-500/30",
};

// ── Mini stat card ───────────────────────────────────────────────────────────

function StatCard({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={cn("rounded-lg border bg-zinc-950/60 px-3 py-2.5 flex flex-col gap-1", className)}>
      <span className="text-[10px] text-zinc-500 font-medium tracking-wide uppercase">{label}</span>
      <span className="text-sm font-mono font-semibold text-zinc-200">{value}</span>
    </div>
  );
}

// ── Contraction bar chart (inline SVG) ───────────────────────────────────────

function ContractionDiagram({ maxDepth, finalDepth, contractions }: {
  maxDepth: number | null;
  finalDepth: number | null;
  contractions: number | null;
}) {
  // Build up to 3 bars representing contraction legs
  const depths = React.useMemo(() => {
    if (!maxDepth || !finalDepth) return [];
    const count = contractions ?? 2;
    const bars: number[] = [];
    // Interpolate depths: first bar = maxDepth, last bar = finalDepth
    for (let i = 0; i < count; i++) {
      const t = count > 1 ? i / (count - 1) : 0;
      bars.push(maxDepth + (finalDepth - maxDepth) * t);
    }
    return bars.slice(0, 3); // cap at 3 for display
  }, [maxDepth, finalDepth, contractions]);

  if (depths.length === 0) {
    return (
      <p className="text-xs text-zinc-600 italic text-center py-2">
        No contraction data available
      </p>
    );
  }

  const maxVal = Math.max(...depths, 1);
  const barH = 14;
  const gap = 10;
  const totalH = depths.length * (barH + gap);
  const maxW = 220;

  return (
    <div className="mt-3">
      <p className="text-[10px] text-zinc-600 uppercase tracking-wide mb-2 font-medium">
        Contraction Legs
      </p>
      <svg width="100%" viewBox={`0 0 ${maxW + 60} ${totalH}`} className="max-h-20">
        {depths.map((d, i) => {
          const barW = (d / maxVal) * maxW;
          const y = i * (barH + gap);
          const opacity = 1 - i * 0.2;
          return (
            <g key={i}>
              <rect
                x={0} y={y}
                width={barW} height={barH}
                rx={3}
                fill="#14B8A6"
                fillOpacity={opacity}
              />
              <text
                x={barW + 6} y={y + barH - 3}
                fontSize="10" fill="#71717a"
                fontFamily="monospace"
              >
                {d.toFixed(1)}%
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

interface VCPCardProps {
  qualified: boolean;
  details: VCPDetails | null;
}

export default function VCPCard({ qualified, details }: VCPCardProps) {
  const d = details;

  const grade = d?.quality_grade ?? "—";
  const gradeStyle = GRADE_STYLES[grade] ?? "text-zinc-400 bg-zinc-800/40 border-zinc-700";

  const fmtPct = (v: number | null) => (v != null ? `${v.toFixed(1)}%` : "—");
  const fmtX   = (v: number | null) => (v != null ? `${v.toFixed(2)}×` : "—");
  const fmtWk  = (v: number | null) => (v != null ? `${v} wks`         : "—");
  const fmtN   = (v: number | null) => (v != null ? String(v)          : "—");

  return (
    <div
      className={cn(
        "rounded-xl border bg-zinc-900 p-5 transition-all",
        qualified
          ? "border-teal-500/40 shadow-[0_0_20px_rgba(20,184,166,0.12)]"
          : "border-zinc-800/60"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-200 tracking-wide">VCP Pattern</h3>
        <span
          className={cn(
            "text-xs font-bold px-2.5 py-1 rounded-full tracking-wide border",
            qualified
              ? "bg-teal-500/15 text-teal-400 border-teal-500/30"
              : "bg-red-500/15 text-red-400 border-red-500/30"
          )}
        >
          {qualified ? "✓ Qualified" : "✗ Not Qualified"}
        </span>
      </div>

      {/* 2×3 stat grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <StatCard label="VCP Grade" value={grade}
          className={cn("border", gradeStyle)} />
        <StatCard label="Contractions"   value={fmtN(d?.contraction_count  ?? null)} className="border-zinc-800/60" />
        <StatCard label="Max Depth"      value={fmtPct(d?.max_depth_pct    ?? null)} className="border-zinc-800/60" />
        <StatCard label="Final Depth"    value={fmtPct(d?.final_depth_pct  ?? null)} className="border-zinc-800/60" />
        <StatCard label="Base Length"    value={fmtWk(d?.base_weeks        ?? null)} className="border-zinc-800/60" />
        <StatCard label="Vol Contraction" value={fmtX(d?.vol_ratio         ?? null)} className="border-zinc-800/60" />
      </div>

      {/* Contraction diagram */}
      <ContractionDiagram
        maxDepth={d?.max_depth_pct ?? null}
        finalDepth={d?.final_depth_pct ?? null}
        contractions={d?.contraction_count ?? null}
      />

      {/* Fail reason, if present */}
      {!qualified && d?.fail_reason && (
        <p className="mt-3 text-[11px] text-red-400/80 bg-red-500/5 border border-red-500/20 rounded-lg px-3 py-2">
          ⚠ {d.fail_reason}
        </p>
      )}
    </div>
  );
}
