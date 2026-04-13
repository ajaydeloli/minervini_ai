/**
 * app/api/proxy/run/route.ts
 * ──────────────────────────
 * Next.js Route Handler that proxies POST /api/v1/run to the FastAPI backend
 * using the server-side API_ADMIN_KEY environment variable.
 *
 * The admin key is NEVER exposed to the client bundle — it lives only in the
 * server-side environment and is injected here at request time.
 *
 * Request body (forwarded verbatim to FastAPI):
 *   { scope?: "all" | "universe" | "watchlist", symbols?: string[] }
 *
 * Response: FastAPI's JSON response, forwarded as-is.
 */

import { NextRequest, NextResponse } from "next/server";
import { adminFetch, ApiError } from "@/lib/api";
import type { RunScope } from "@/lib/types";

interface RunRequestBody {
  scope?: RunScope;
  symbols?: string[] | null;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  let body: RunRequestBody;

  try {
    body = (await req.json()) as RunRequestBody;
  } catch {
    return NextResponse.json(
      { success: false, data: null, error: "Invalid JSON body" },
      { status: 400 }
    );
  }

  const payload: RunRequestBody = {
    scope: body.scope ?? "all",
    symbols: body.symbols ?? null,
  };

  try {
    const result = await adminFetch<unknown>("/api/v1/run", {
      method: "POST",
      body: JSON.stringify(payload),
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

    // Unexpected error — don't leak internals
    console.error("[proxy/run] Unexpected error:", err);
    return NextResponse.json(
      { success: false, data: null, error: "Internal proxy error" },
      { status: 502 }
    );
  }
}
