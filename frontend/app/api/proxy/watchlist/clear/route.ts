/**
 * app/api/proxy/watchlist/clear/route.ts
 * ────────────────────────────────────────
 * Next.js Route Handler that proxies DELETE /api/v1/watchlist to the FastAPI
 * backend, clearing every item from the watchlist in one shot.
 *
 * WHY adminFetch?
 * ───────────────
 * "Clear all" is a destructive, irreversible operation — it wipes the entire
 * watchlist with a single request. FastAPI therefore gates it behind the
 * elevated admin API key (X-API-Key: <admin_key>) rather than the public read
 * key that is baked into the client bundle (NEXT_PUBLIC_API_READ_KEY).
 *
 * adminFetch reads process.env.API_ADMIN_KEY at request time, inside this
 * server-side Route Handler, so the key is NEVER included in the browser
 * bundle and cannot be extracted by a client.
 *
 * Called by the watchlist UI via:
 *   fetch("/api/proxy/watchlist/clear", { method: "DELETE" })
 */

import { NextResponse } from "next/server";
// adminFetch uses process.env.API_ADMIN_KEY — server-side only, never bundled.
import { adminFetch, ApiError } from "@/lib/api";

export async function DELETE(): Promise<NextResponse> {
  try {
    // Proxy the destructive clear-all to FastAPI with the admin key.
    await adminFetch<unknown>("/api/v1/watchlist", { method: "DELETE" });

    return NextResponse.json({ success: true });
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(
        { success: false, error: err.message },
        { status: err.status >= 400 ? err.status : 502 }
      );
    }

    // Never leak internal details to the client.
    console.error("[proxy/watchlist/clear] Unexpected error:", err);
    return NextResponse.json(
      { success: false, error: "Internal proxy error" },
      { status: 502 }
    );
  }
}
