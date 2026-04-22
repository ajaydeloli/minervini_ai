/**
 * app/api/proxy/watchlist/[symbol]/route.ts
 * ──────────────────────────────────────────
 * Next.js Route Handler that proxies per-symbol watchlist mutations to the
 * FastAPI backend.
 *
 *   POST   /api/proxy/watchlist/:symbol  → POST   /api/v1/watchlist/{symbol}
 *   DELETE /api/proxy/watchlist/:symbol  → DELETE /api/v1/watchlist/{symbol}
 *
 * WHY adminFetch?
 * ───────────────
 * Both mutations require the elevated admin API key (X-API-Key: <admin_key>).
 * adminFetch reads process.env.API_ADMIN_KEY at request time, inside this
 * server-side Route Handler, so the key is NEVER included in the browser
 * bundle and cannot be extracted by a client.
 *
 * Called by the watchlist UI via:
 *   addToWatchlist    → fetch(`/api/proxy/watchlist/${symbol}`, { method: "POST", … })
 *   removeFromWatchlist → fetch(`/api/proxy/watchlist/${symbol}`, { method: "DELETE" })
 */

import { NextRequest, NextResponse } from "next/server";
// adminFetch uses process.env.API_ADMIN_KEY — server-side only, never bundled.
import { adminFetch, ApiError } from "@/lib/api";

// ─── Shared error handler ──────────────────────────────────────────────────

function handleError(err: unknown, label: string): NextResponse {
  if (err instanceof ApiError) {
    return NextResponse.json(
      { success: false, error: err.message },
      { status: err.status >= 400 ? err.status : 502 }
    );
  }
  console.error(`[proxy/watchlist/[symbol]] ${label} — Unexpected error:`, err);
  return NextResponse.json(
    { success: false, error: "Internal proxy error" },
    { status: 502 }
  );
}

// ─── POST /api/proxy/watchlist/[symbol] ───────────────────────────────────

export async function POST(
  request: NextRequest,
  { params }: { params: { symbol: string } }
): Promise<NextResponse> {
  const { symbol } = params;

  let body: { note?: string } = {};
  try {
    body = await request.json();
  } catch {
    // Body is optional — an empty / malformed body is fine.
  }

  try {
    const data = await adminFetch<unknown>(
      `/api/v1/watchlist/${encodeURIComponent(symbol)}`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    );

    // Return the FastAPI JSON response as-is.
    return NextResponse.json(data);
  } catch (err) {
    return handleError(err, "POST");
  }
}

// ─── DELETE /api/proxy/watchlist/[symbol] ─────────────────────────────────

export async function DELETE(
  _request: NextRequest,
  { params }: { params: { symbol: string } }
): Promise<NextResponse> {
  const { symbol } = params;

  try {
    await adminFetch<unknown>(
      `/api/v1/watchlist/${encodeURIComponent(symbol)}`,
      { method: "DELETE" }
    );

    return NextResponse.json({ success: true });
  } catch (err) {
    return handleError(err, "DELETE");
  }
}
