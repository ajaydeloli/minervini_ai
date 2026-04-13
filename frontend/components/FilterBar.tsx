/**
 * components/FilterBar.tsx
 * ─────────────────────────
 * Horizontal filter toolbar for the screener page.
 * Desktop: inline row of controls.
 * Mobile:  collapsed [Filters] button that opens a bottom-sheet dialog.
 */
"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { SlidersHorizontal, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SetupQuality } from "@/lib/types";

// ─── Types ─────────────────────────────────────────────────────────────────

export interface FilterState {
  quality: SetupQuality | "All";
  stage: number | "All";
  minRs: number;
  watchlistOnly: boolean;
}

export const DEFAULT_FILTERS: FilterState = {
  quality: "All",
  stage: "All",
  minRs: 0,
  watchlistOnly: false,
};

interface FilterBarProps {
  onChange: (filters: FilterState) => void;
  className?: string;
}

// ─── Inner controls (shared between desktop bar & mobile sheet) ─────────────

interface ControlsProps {
  draft: FilterState;
  setDraft: React.Dispatch<React.SetStateAction<FilterState>>;
}

function FilterControls({ draft, setDraft }: ControlsProps) {
  return (
    <div className="flex flex-wrap gap-3 items-end">
      {/* Quality */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-widest text-zinc-400 font-medium">
          Quality
        </label>
        <Select
          value={String(draft.quality)}
          onValueChange={(v) =>
            setDraft((d) => ({ ...d, quality: v as FilterState["quality"] }))
          }
        >
          <SelectTrigger className="h-9 w-28 bg-zinc-900 border-zinc-700 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="All">All</SelectItem>
            <SelectItem value="A+">A+</SelectItem>
            <SelectItem value="A">A</SelectItem>
            <SelectItem value="B">B</SelectItem>
            <SelectItem value="C">C</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Stage */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-widest text-zinc-400 font-medium">
          Stage
        </label>
        <Select
          value={String(draft.stage)}
          onValueChange={(v) =>
            setDraft((d) => ({
              ...d,
              stage: v === "All" ? "All" : (Number(v) as 1 | 2 | 3 | 4),
            }))
          }
        >
          <SelectTrigger className="h-9 w-32 bg-zinc-900 border-zinc-700 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="All">All</SelectItem>
            <SelectItem value="1">Stage 1</SelectItem>
            <SelectItem value="2">Stage 2</SelectItem>
            <SelectItem value="3">Stage 3</SelectItem>
            <SelectItem value="4">Stage 4</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Min RS */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-widest text-zinc-400 font-medium">
          Min RS Rating
        </label>
        <Input
          type="number"
          min={0}
          max={99}
          value={draft.minRs}
          onChange={(e) =>
            setDraft((d) => ({
              ...d,
              minRs: Math.min(99, Math.max(0, Number(e.target.value))),
            }))
          }
          className="h-9 w-24 bg-zinc-900 border-zinc-700 text-sm font-mono"
        />
      </div>

      {/* Watchlist only */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-widest text-zinc-400 font-medium">
          Watchlist
        </label>
        <button
          type="button"
          onClick={() =>
            setDraft((d) => ({ ...d, watchlistOnly: !d.watchlistOnly }))
          }
          className={cn(
            "h-9 px-3 rounded-md border text-sm font-medium transition-colors",
            draft.watchlistOnly
              ? "bg-teal-500/20 border-teal-500/40 text-teal-400"
              : "bg-zinc-900 border-zinc-700 text-zinc-400 hover:border-zinc-500"
          )}
        >
          {draft.watchlistOnly ? "★ Only" : "All"}
        </button>
      </div>
    </div>
  );
}

// ─── Main FilterBar ─────────────────────────────────────────────────────────

export default function FilterBar({ onChange, className }: FilterBarProps) {
  const [draft, setDraft] = React.useState<FilterState>(DEFAULT_FILTERS);
  const [mobileOpen, setMobileOpen] = React.useState(false);

  const handleApply = (filters: FilterState) => {
    onChange(filters);
    setMobileOpen(false);
  };

  const handleReset = () => {
    setDraft(DEFAULT_FILTERS);
    onChange(DEFAULT_FILTERS);
    setMobileOpen(false);
  };

  return (
    <>
      {/* ── Desktop bar ── */}
      <div
        className={cn(
          "hidden md:flex items-end gap-3 flex-wrap px-6 py-4",
          "bg-zinc-900/60 border border-zinc-800 rounded-xl",
          className
        )}
      >
        <FilterControls draft={draft} setDraft={setDraft} />
        <div className="flex gap-2 ml-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            className="h-9 border-zinc-700 text-zinc-400 hover:text-white"
          >
            Reset
          </Button>
          <Button
            size="sm"
            onClick={() => handleApply(draft)}
            className="h-9 bg-teal-600 hover:bg-teal-500 text-white"
          >
            Apply Filters
          </Button>
        </div>
      </div>

      {/* ── Mobile: collapsed button → bottom sheet ── */}
      <div className="md:hidden">
        <DialogPrimitive.Root open={mobileOpen} onOpenChange={setMobileOpen}>
          <DialogPrimitive.Trigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="gap-2 border-zinc-700 text-zinc-300"
            >
              <SlidersHorizontal className="h-4 w-4" />
              Filters
            </Button>
          </DialogPrimitive.Trigger>

          <DialogPrimitive.Portal>
            {/* Overlay */}
            <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />

            {/* Bottom sheet */}
            <DialogPrimitive.Content
              className={cn(
                "fixed bottom-0 left-0 right-0 z-50 rounded-t-2xl",
                "bg-zinc-900 border-t border-zinc-700 p-6 pb-8",
                "data-[state=open]:animate-in data-[state=closed]:animate-out",
                "data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
                "duration-300"
              )}
            >
              {/* Handle */}
              <div className="mx-auto mb-5 h-1 w-10 rounded-full bg-zinc-700" />

              <div className="flex items-center justify-between mb-5">
                <DialogPrimitive.Title className="text-base font-semibold text-white">
                  Filters
                </DialogPrimitive.Title>
                <DialogPrimitive.Close asChild>
                  <button className="text-zinc-400 hover:text-white">
                    <X className="h-5 w-5" />
                  </button>
                </DialogPrimitive.Close>
              </div>

              <FilterControls draft={draft} setDraft={setDraft} />

              <div className="flex gap-3 mt-6">
                <Button
                  variant="outline"
                  className="flex-1 border-zinc-700 text-zinc-400"
                  onClick={handleReset}
                >
                  Reset
                </Button>
                <Button
                  className="flex-1 bg-teal-600 hover:bg-teal-500 text-white"
                  onClick={() => handleApply(draft)}
                >
                  Apply Filters
                </Button>
              </div>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      </div>
    </>
  );
}
