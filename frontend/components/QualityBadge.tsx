/**
 * components/QualityBadge.tsx
 * ───────────────────────────
 * Reusable badge for SEPA setup quality grades.
 * Color map:
 *   A+   → teal bg   (accent color)
 *   A    → green bg
 *   B    → amber bg
 *   C    → orange bg
 *   FAIL → red bg
 */
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SetupQuality } from "@/lib/types";

const qualityStyles: Record<SetupQuality, string> = {
  "A+":   "border-transparent bg-teal-500/20   text-teal-400   hover:bg-teal-500/25",
  "A":    "border-transparent bg-green-500/20  text-green-400  hover:bg-green-500/25",
  "B":    "border-transparent bg-amber-500/20  text-amber-400  hover:bg-amber-500/25",
  "C":    "border-transparent bg-orange-500/20 text-orange-400 hover:bg-orange-500/25",
  "FAIL": "border-transparent bg-red-500/20    text-red-400    hover:bg-red-500/25",
};

interface QualityBadgeProps {
  quality: SetupQuality;
  className?: string;
}

export default function QualityBadge({ quality, className }: QualityBadgeProps) {
  return (
    <Badge className={cn("font-mono font-semibold tracking-wide", qualityStyles[quality], className)}>
      {quality}
    </Badge>
  );
}
