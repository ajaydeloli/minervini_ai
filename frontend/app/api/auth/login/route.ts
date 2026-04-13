/**
 * app/api/auth/login/route.ts
 * ────────────────────────────
 * Route Handler: POST /api/auth/login
 *
 * Validates the submitted password against the SITE_PASSWORD env var
 * and sets (or clears) the "minervini_auth" HttpOnly cookie.
 *
 * Server-side only — SITE_PASSWORD is never exposed to the browser.
 */

import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME   = "minervini_auth";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7; // 7 days in seconds

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const submitted: string = body?.password ?? "";

  const sitePassword = process.env.SITE_PASSWORD ?? "";

  // Guard: if auth is not required or no password is configured, reject
  if (!sitePassword) {
    return NextResponse.json(
      { error: "Auth not configured on this server." },
      { status: 403 }
    );
  }

  if (!submitted || submitted !== sitePassword) {
    return NextResponse.json(
      { error: "Incorrect password." },
      { status: 401 }
    );
  }

  // Password correct — set cookie and return 200
  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name:     COOKIE_NAME,
    value:    "1",
    httpOnly: true,
    sameSite: "lax",
    path:     "/",
    maxAge:   COOKIE_MAX_AGE,
    secure:   process.env.NODE_ENV === "production",
  });

  return response;
}
