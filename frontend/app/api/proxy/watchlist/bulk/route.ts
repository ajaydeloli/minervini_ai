/**
 * app/api/proxy/watchlist/bulk/route.ts
 * ──────────────────────────────────────
 * Next.js Route Handler that proxies POST /api/v1/watchlist/bulk to the
 * FastAPI backend using the server-side API_ADMIN_KEY.
 *
 * The admin key is NEVER exposed to the client bundle — it is read from
 * process.env.API_ADMIN_KEY inside this server-only Route Handler and
 * injected by adminFetch at request time.
 *
 * Request body:
 *   { symbols: string[] }
 *
 * Response: FastAPI's APIResponse<BulkAddResult> forwarded as-is.
 *   { added: string[], already_exists: string[], invalid: string[] }
 */

import { NextRequest, NextResponse } from "next/server";
import { adminFetch, ApiError } from "@/lib/api";

interface BulkAddRequestBody {
  symbols: string[];
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  let body: BulkAddRequestBody;

  try {
    body = (await req.json()) as BulkAddRequestBody;
  } catch {
    return NextResponse.json(
      { success: false, data: null, error: "Invalid JSON body" },
      { status: 400 }
    );
  }

  if (!Array.isArray(body.symbols)) {
    return NextResponse.json(
      { success: false, data: null, error: "symbols must be an array" },
      { status: 400 }
    );
  }

  try {
    const result = await adminFetch<unknown>("/api/v1/watchlist/bulk", {
      method: "POST",
      body: JSON.stringify({ symbols: body.symbols }),
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

    // Never leak internal details to the client.
    console.error("[proxy/watchlist/bulk] Unexpected error:", err);
    return NextResponse.json(
      { success: false, data: null, error: "Internal proxy error" },
      { status: 502 }
    );
  }
}
