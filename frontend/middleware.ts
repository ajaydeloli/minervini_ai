/**
 * middleware.ts
 * ─────────────
 * Optional password gate for the entire app.
 *
 * Activation
 * ──────────
 * Set NEXT_PUBLIC_REQUIRE_AUTH=true in your environment to enable.
 * When disabled (default) this middleware is a no-op — all requests pass through.
 *
 * How it works
 * ────────────
 * 1. On every request the middleware checks for a "minervini_auth" cookie.
 * 2. If the cookie is absent (or has the wrong value), the request is
 *    redirected to /login.
 * 3. The /login page (app/login/page.tsx) submits the password to the
 *    Route Handler at POST /api/auth/login, which sets the cookie on
 *    success and redirects to /.
 * 4. The cookie is HttpOnly, SameSite=Lax, and is valid for 7 days.
 *    The password is stored in the SITE_PASSWORD server-side env var.
 *
 * Environment variables
 * ─────────────────────
 * NEXT_PUBLIC_REQUIRE_AUTH  Set to "true" to activate the password gate.
 * SITE_PASSWORD             The password users must enter (server-side only).
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const AUTH_REQUIRED = process.env.NEXT_PUBLIC_REQUIRE_AUTH === "true";
const COOKIE_NAME   = "minervini_auth";

export function middleware(request: NextRequest) {
  // Gate is disabled — pass every request through
  if (!AUTH_REQUIRED) return NextResponse.next();

  const { pathname } = request.nextUrl;

  // Always allow: /login page, /api/auth/* (login handler), static assets
  const isPublic =
    pathname === "/login" ||
    pathname.startsWith("/api/auth/") ||
    pathname.startsWith("/_next/") ||
    pathname.startsWith("/favicon");

  if (isPublic) return NextResponse.next();

  // Check auth cookie
  const authCookie = request.cookies.get(COOKIE_NAME);
  if (!authCookie || authCookie.value !== "1") {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Run on all routes except Next.js internals and static files
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
