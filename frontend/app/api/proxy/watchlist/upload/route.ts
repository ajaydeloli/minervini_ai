/**
 * app/api/proxy/watchlist/upload/route.ts
 * ────────────────────────────────────────
 * Next.js Route Handler that proxies POST /api/v1/watchlist/upload to the
 * FastAPI backend, forwarding a multipart file upload with the admin API key.
 *
 * WHY raw fetch() instead of adminFetch?
 * ───────────────────────────────────────
 * adminFetch hard-codes `Content-Type: application/json`, which would corrupt
 * the multipart boundary that the browser attaches to FormData bodies.
 * Instead, we forward the FormData directly and let fetch set the correct
 * `Content-Type: multipart/form-data; boundary=...` header automatically.
 *
 * The admin key is read from process.env.API_ADMIN_KEY at request time inside
 * this server-side Route Handler — it is NEVER included in the client bundle.
 *
 * Called by the frontend via uploadWatchlistFile() in lib/api.ts:
 *   fetch("/api/proxy/watchlist/upload", { method: "POST", body: formData })
 */

import { NextRequest, NextResponse } from "next/server";

const BASE_URL =
  (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    // Parse the incoming multipart FormData from the browser.
    const formData = await request.formData();

    // Forward to FastAPI — do NOT set Content-Type so fetch can attach the
    // correct multipart boundary automatically.
    const backendRes = await fetch(`${BASE_URL}/api/v1/watchlist/upload`, {
      method: "POST",
      headers: {
        "X-API-Key": process.env.API_ADMIN_KEY ?? "",
      },
      body: formData,
    });

    // Parse the JSON response from FastAPI.
    let payload: unknown;
    try {
      payload = await backendRes.json();
    } catch {
      payload = { success: false, error: "Invalid JSON from upstream" };
    }

    // Relay FastAPI's status code so the client receives meaningful errors.
    if (!backendRes.ok) {
      return NextResponse.json(payload, { status: backendRes.status });
    }

    return NextResponse.json(payload);
  } catch (err) {
    // Never leak internal details to the client.
    console.error("[proxy/watchlist/upload] Unexpected error:", err);
    return NextResponse.json(
      { success: false, error: "Internal proxy error" },
      { status: 502 }
    );
  }
}
