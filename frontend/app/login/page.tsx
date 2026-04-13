/**
 * app/login/page.tsx
 * ──────────────────
 * Minimal password gate page.
 * Only rendered when NEXT_PUBLIC_REQUIRE_AUTH=true.
 *
 * Submits to POST /api/auth/login which validates the password
 * and sets the "minervini_auth" HttpOnly cookie on success.
 */
"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Loader2, Lock } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (res.ok) {
        router.replace("/");
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? "Incorrect password. Please try again.");
      }
    } catch {
      setError("Network error — please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0D0D0F] px-4">
      <div className="w-full max-w-sm">
        {/* Logo / brand */}
        <div className="flex flex-col items-center mb-8 gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-teal-500 text-white text-2xl font-bold shadow-lg shadow-teal-500/20">
            M
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-white tracking-tight">
              Minervini AI
            </h1>
            <p className="text-sm text-zinc-500 mt-0.5">
              Enter your access password to continue
            </p>
          </div>
        </div>

        {/* Form card */}
        <div className="rounded-2xl border border-[#1E1E21] bg-[#161618] p-6 shadow-2xl">
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Password field */}
            <div>
              <label
                htmlFor="password"
                className="block text-xs font-medium text-zinc-400 uppercase tracking-wider mb-2"
              >
                Password
              </label>
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
                  <Lock className="h-4 w-4 text-zinc-500" />
                </div>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoFocus
                  autoComplete="current-password"
                  placeholder="Enter password"
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 pl-10 pr-4 py-2.5
                             text-sm text-white placeholder-zinc-600
                             focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500
                             transition-colors"
                />
              </div>
            </div>

            {/* Error message */}
            {error && (
              <p className="text-xs text-red-400 flex items-center gap-1.5">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-red-400 flex-shrink-0" />
                {error}
              </p>
            )}

            {/* Submit button */}
            <button
              type="submit"
              disabled={loading || !password.trim()}
              className="w-full inline-flex items-center justify-center gap-2 rounded-lg
                         bg-teal-500 px-4 py-2.5 text-sm font-semibold text-white
                         hover:bg-teal-400 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Lock className="h-4 w-4" />
              )}
              {loading ? "Verifying…" : "Enter"}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-zinc-700 mt-6">
          Minervini AI · SEPA Stock Screener
        </p>
      </div>
    </div>
  );
}
