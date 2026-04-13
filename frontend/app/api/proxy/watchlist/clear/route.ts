/**
 * app/api/proxy/watchlist/clear/route.ts
 * ────────────────────────────────────────
 * Next.js Route Handler that proxies DELETE /api/v1/watchlist to the FastAPI
 * backend using the server-side API_ADMIN_KEY environment variable.
 *
 * The admin key is NEVER exposed to the client bundle — it lives only in the
 * server-side environment and is injected here at request time.
 *
 * Called by the watchlist page's "Clear All" action via:
 *   fetch("/api/proxy/watchlist/clear", { method: "DELETE" })
 */

import { NextResponse } from "next/server";
import { adminFetch, ApiError } from "@/lib/api";

export async function DELETE(): Promise<NextResponse> {
  try {
    const result = await adminFetch<unknown>("/api/v1/watchlist", {
      method: "DELETE",
    });

    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(
        {
          success: false,
          data: null,
          error: err.message,
          detail: err.detail,
        },
        { status: err.status >= 400 ? err.status : 502 }
      );
    }

    console.error("[proxy/watchlist/clear] Unexpected error:", err);
    return NextResponse.json(
      { success: false, data: null, error: "Internal proxy error" },
      { status: 502 }
    );
  }
}
