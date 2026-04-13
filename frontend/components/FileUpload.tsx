/**
 * components/FileUpload.tsx
 * ──────────────────────────
 * Drag-and-drop file upload widget for bulk watchlist import.
 * Accepts: .csv  .json  .xlsx  .txt
 *
 * UX flow:
 *   1. User drags a file onto the zone OR clicks "browse" to pick one.
 *   2. Filename is shown + [Upload] button appears.
 *   3. On upload: calls uploadWatchlistFile(file) from lib/api.ts.
 *   4. Shows result: "Added N symbols, skipped N, invalid: [X, Y]"
 *   5. Invalid symbols rendered as red pills.
 *   6. Loading spinner on the [Upload] button while in-flight.
 *   7. API error shown inline.
 *
 * Props:
 *   onSuccess?: (result: WatchlistUploadResult) => void
 *     Called after a successful upload so the parent can refresh SWR cache.
 */
"use client";

import * as React from "react";
import { Upload, FileText, X, Loader2, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { uploadWatchlistFile, ApiError } from "@/lib/api";
import type { WatchlistUploadResult } from "@/lib/types";

// ─── Accepted types ─────────────────────────────────────────────────────────

const ACCEPTED_MIME = [
  "text/csv",
  "application/json",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/plain",
];
const ACCEPTED_EXT = ".csv,.json,.xlsx,.txt";

function isAccepted(file: File): boolean {
  if (ACCEPTED_MIME.includes(file.type)) return true;
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return ["csv", "json", "xlsx", "txt"].includes(ext);
}

// ─── Types ──────────────────────────────────────────────────────────────────

interface FileUploadProps {
  onSuccess?: (result: WatchlistUploadResult) => void;
  className?: string;
}

type UploadState =
  | { phase: "idle" }
  | { phase: "selected"; file: File }
  | { phase: "uploading"; file: File }
  | { phase: "success"; file: File; result: WatchlistUploadResult }
  | { phase: "error"; file: File; message: string };

// ─── Result summary ──────────────────────────────────────────────────────────

function UploadResult({ result }: { result: WatchlistUploadResult }) {
  return (
    <div className="mt-3 space-y-2 text-sm">
      <div className="flex items-center gap-2 text-teal-400 font-medium">
        <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
        <span>
          Added {result.added} symbol{result.added !== 1 ? "s" : ""},{" "}
          skipped {result.skipped}
        </span>
      </div>
      {result.invalid.length > 0 && (
        <div className="space-y-1">
          <span className="text-zinc-400 text-xs">
            Invalid ({result.invalid.length}):
          </span>
          <div className="flex flex-wrap gap-1.5">
            {result.invalid.map((sym) => (
              <span
                key={sym}
                className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-mono font-medium
                           bg-red-500/15 text-red-400 border border-red-500/20"
              >
                {sym}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function FileUpload({ onSuccess, className }: FileUploadProps) {
  const [state, setState] = React.useState<UploadState>({ phase: "idle" });
  const [dragging, setDragging] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  // ── File selection ──────────────────────────────────────────────────────
  function selectFile(file: File) {
    if (!isAccepted(file)) {
      setState({
        phase: "error",
        file,
        message: `Unsupported file type: .${file.name.split(".").pop()}`,
      });
      return;
    }
    setState({ phase: "selected", file });
  }

  // ── Drag handlers ────────────────────────────────────────────────────────
  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragging(true);
  }
  function onDragLeave() {
    setDragging(false);
  }
  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) selectFile(file);
  }

  // ── Input change ─────────────────────────────────────────────────────────
  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) selectFile(file);
    e.target.value = ""; // reset so same file can be re-selected
  }

  // ── Upload ───────────────────────────────────────────────────────────────
  async function handleUpload() {
    if (state.phase !== "selected" && state.phase !== "error") return;
    const file = state.file;
    setState({ phase: "uploading", file });
    try {
      const result = await uploadWatchlistFile(file);
      setState({ phase: "success", file, result });
      onSuccess?.(result);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Upload failed";
      setState({ phase: "error", file, message: msg });
    }
  }

  // ── Reset ────────────────────────────────────────────────────────────────
  function reset() {
    setState({ phase: "idle" });
  }

  // ── Derived ──────────────────────────────────────────────────────────────
  const hasFile = state.phase !== "idle";
  const isUploading = state.phase === "uploading";
  const canUpload = state.phase === "selected" || state.phase === "error";
  const fileName = hasFile ? state.file.name : null;

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className={cn("space-y-3", className)}>
      {/* Drop zone */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => !hasFile && inputRef.current?.click()}
        className={cn(
          "relative flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed",
          "px-6 py-8 text-center transition-colors duration-150",
          dragging
            ? "border-teal-500 bg-teal-500/5"
            : hasFile
            ? "border-zinc-700 bg-[#161618] cursor-default"
            : "border-zinc-700 bg-[#161618] cursor-pointer hover:border-zinc-500 hover:bg-zinc-800/30"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXT}
          className="sr-only"
          onChange={onInputChange}
        />

        {!hasFile ? (
          <>
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-800">
              <Upload className="h-5 w-5 text-zinc-400" />
            </div>
            <div>
              <p className="text-sm font-medium text-zinc-200">
                Drop a file here{" "}
                <span className="text-teal-400">or click to browse</span>
              </p>
              <p className="text-xs text-zinc-500 mt-0.5">
                Supports .csv · .json · .xlsx · .txt
              </p>
            </div>
          </>
        ) : (
          <div className="flex w-full items-center gap-3">
            <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-zinc-800">
              <FileText className="h-5 w-5 text-zinc-300" />
            </div>
            <div className="flex-1 min-w-0 text-left">
              <p className="text-sm font-medium text-white truncate">{fileName}</p>
              <p className="text-xs text-zinc-500">
                {state.phase === "uploading" && "Uploading…"}
                {state.phase === "success" && "Upload complete"}
                {state.phase === "error" && (
                  <span className="text-red-400">{state.message}</span>
                )}
                {state.phase === "selected" && "Ready to upload"}
              </p>
            </div>
            {/* Clear button */}
            {!isUploading && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  reset();
                }}
                className="ml-auto flex-shrink-0 rounded-md p-1.5 text-zinc-500 hover:text-white hover:bg-zinc-700 transition-colors"
                aria-label="Clear file"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
      </div>

      {/* Upload button — shown when a file is selected (not after success) */}
      {hasFile && state.phase !== "success" && (
        <button
          onClick={handleUpload}
          disabled={!canUpload || isUploading}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors",
            "bg-teal-500 text-white hover:bg-teal-400 disabled:opacity-50 disabled:cursor-not-allowed"
          )}
        >
          {isUploading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Upload className="h-4 w-4" />
          )}
          {isUploading ? "Uploading…" : "Upload"}
        </button>
      )}

      {/* Result summary */}
      {state.phase === "success" && <UploadResult result={state.result} />}
    </div>
  );
}
