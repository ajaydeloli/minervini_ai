/**
 * components/ScoreGauge.tsx
 * ──────────────────────────
 * SVG semicircle gauge (180° arc). Animates stroke-dashoffset on mount.
 *
 * Color thresholds
 * ────────────────
 *   score >= 85  → teal  (#14B8A6)
 *   score >= 70  → green (#22C55E)
 *   score >= 55  → amber (#F59E0B)
 *   else         → red   (#EF4444)
 *
 * Dimensions: width 180 px, height 100 px — fits a card header.
 */
"use client";

import * as React from "react";
import QualityBadge from "@/components/QualityBadge";
import type { SetupQuality } from "@/lib/types";

interface ScoreGaugeProps {
  score: number;       // 0 – 100
  quality: SetupQuality;
  className?: string;
}

// ── Geometry constants ────────────────────────────────────────────────────────
const W        = 180;
const H        = 100;
const CX       = W / 2;          // 90
const CY       = H;              // 100  (bottom of viewBox — arc sits on baseline)
const R        = 78;             // radius
const STROKE   = 10;
const BG_COLOR = "#1E1E21";

// arc length of a semicircle = π * R
const ARC_LEN = Math.PI * R;     // ≈ 245

function arcColor(score: number): string {
  if (score >= 85) return "#14B8A6"; // teal
  if (score >= 70) return "#22C55E"; // green
  if (score >= 55) return "#F59E0B"; // amber
  return "#EF4444";                  // red
}

export default function ScoreGauge({ score, quality, className }: ScoreGaugeProps) {
  const clampedScore = Math.max(0, Math.min(100, score));
  const fillLen      = (clampedScore / 100) * ARC_LEN;
  const gap          = ARC_LEN - fillLen;        // trailing transparent portion
  const color        = arcColor(clampedScore);

  // Animate: start fully hidden, animate to final value after mount
  const [animated, setAnimated] = React.useState(false);
  React.useEffect(() => {
    // Defer one frame so the CSS transition fires
    const id = requestAnimationFrame(() => setAnimated(true));
    return () => cancelAnimationFrame(id);
  }, []);

  // stroke-dasharray = "fill gap" where fill is the arc length to draw
  // stroke-dashoffset shifts the start; we want the arc to start at the
  // left end of the semicircle (angle = 180°, i.e. the leftmost point).
  // With a semicircle path going left→right, dashoffset=0 starts drawing
  // from the leftmost point naturally.
  const dashArray  = `${animated ? fillLen : 0} ${ARC_LEN}`;

  return (
    <div className={`flex flex-col items-center ${className ?? ""}`}>
      <svg
        width={W}
        height={H + 4}
        viewBox={`0 0 ${W} ${H + 4}`}
        aria-label={`Score ${score} – ${quality}`}
      >
        {/* Background arc */}
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke={BG_COLOR}
          strokeWidth={STROKE}
          strokeLinecap="round"
        />

        {/* Filled arc — animated via CSS transition on stroke-dasharray */}
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={dashArray}
          style={{ transition: "stroke-dasharray 0.7s cubic-bezier(0.4,0,0.2,1)" }}
        />

        {/* Score number */}
        <text
          x={CX}
          y={CY - 22}
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize={32}
          fontWeight={700}
          fontFamily="var(--font-mono, monospace)"
          fill="white"
        >
          {clampedScore}
        </text>
      </svg>

      {/* Quality badge below the arc */}
      <div className="-mt-1">
        <QualityBadge quality={quality} />
      </div>
    </div>
  );
}
