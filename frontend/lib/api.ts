/**
 * lib/api.ts
 * ──────────
 * Typed API client for the Minervini AI FastAPI backend.
 *
 * Architecture
 * ────────────
 * - `apiFetch`   — client-side helper; injects the public read key header.
 * - `adminFetch` — NEVER called from the browser. Used only by the
 *                  Next.js Route Handler at /api/proxy/run which holds the
 *                  server-side API_ADMIN_KEY.
 * - All functions throw `ApiError` on non-2xx responses.
 * - `triggerRun` proxies through /api/proxy/run so the admin key is never
 *   exposed to the client bundle.
 */

import type {
  APIResponse,
  BulkAddResult,
  HealthResponse,
  MetaResponse,
  PortfolioSummary,
  RunScope,
  SEPAResult,
  SetupQuality,
  StockHistoryPoint,
  Trade,
  WatchlistItem,
  WatchlistUploadResult,
} from "./types";

// ─── Error class ───────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ─── Base helpers ──────────────────────────────────────────────────────────

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const READ_KEY = process.env.NEXT_PUBLIC_API_READ_KEY ?? "";

/** Client-side fetch with public read key. */
async function apiFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": READ_KEY,
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      res.status,
      `API error ${res.status}: ${res.statusText}`,
      detail
    );
  }

  const envelope = (await res.json()) as APIResponse<T>;

  if (!envelope.success) {
    throw new ApiError(res.status, envelope.error ?? "Unknown API error");
  }

  return envelope.data;
}

/**
 * Server-side fetch with admin key.
 * ONLY imported by the Route Handler — not the browser bundle.
 */
export async function adminFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  // API_ADMIN_KEY is a server-only env var — never prefixed with NEXT_PUBLIC_
  const adminKey = process.env.API_ADMIN_KEY ?? "";
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": adminKey,
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      res.status,
      `Admin API error ${res.status}: ${res.statusText}`,
      detail
    );
  }

  return res.json() as Promise<T>;
}

// ─── Stock / Screener endpoints ────────────────────────────────────────────

/**
 * GET /api/v1/stocks/top
 * Returns top SEPA results filtered by quality and/or date.
 */
export async function fetchTopStocks(
  quality?: SetupQuality,
  limit = 50,
  date?: string
): Promise<SEPAResult[]> {
  const params = new URLSearchParams();
  if (quality) params.set("min_quality", quality);
  if (limit) params.set("limit", String(limit));
  if (date) params.set("date", date);
  const qs = params.toString();
  return apiFetch<SEPAResult[]>(`/api/v1/stocks/top${qs ? `?${qs}` : ""}`);
}

/**
 * GET /api/v1/stock/{symbol}
 * Returns full SEPA detail for a single symbol.
 */
export async function fetchStock(
  symbol: string,
  date?: string
): Promise<SEPAResult> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  const qs = params.toString();
  return apiFetch<SEPAResult>(
    `/api/v1/stock/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`
  );
}

/**
 * GET /api/v1/stock/{symbol}/history
 * Returns historical scoring for a symbol across multiple run dates.
 */
export async function fetchStockHistory(
  symbol: string,
  days = 30
): Promise<StockHistoryPoint[]> {
  const params = new URLSearchParams({ days: String(days) });
  const raw = await apiFetch<{ symbol: string; history: StockHistoryPoint[] }>(
    `/api/v1/stock/${encodeURIComponent(symbol)}/history?${params}`
  );
  // Unwrap the nested history array from the StockHistory envelope
  return raw.history ?? (raw as unknown as StockHistoryPoint[]);
}

// ─── Watchlist endpoints ───────────────────────────────────────────────────

/** GET /api/v1/watchlist */
export async function fetchWatchlist(): Promise<WatchlistItem[]> {
  return apiFetch<WatchlistItem[]>("/api/v1/watchlist");
}

/** POST /api/v1/watchlist/{symbol} */
export async function addToWatchlist(
  symbol: string,
  note?: string
): Promise<WatchlistItem[]> {
  return apiFetch<WatchlistItem[]>(
    `/api/v1/watchlist/${encodeURIComponent(symbol)}`,
    {
      method: "POST",
      body: JSON.stringify({ note: note ?? null }),
    }
  );
}

/** DELETE /api/v1/watchlist/{symbol} */
export async function removeFromWatchlist(symbol: string): Promise<void> {
  await apiFetch<null>(
    `/api/v1/watchlist/${encodeURIComponent(symbol)}`,
    { method: "DELETE" }
  );
}

/** POST /api/v1/watchlist/bulk */
export async function bulkAddWatchlist(
  symbols: string[]
): Promise<BulkAddResult> {
  return apiFetch<BulkAddResult>("/api/v1/watchlist/bulk", {
    method: "POST",
    body: JSON.stringify({ symbols }),
  });
}

/** POST /api/v1/watchlist/upload — multipart form upload */
export async function uploadWatchlistFile(
  file: File
): Promise<WatchlistUploadResult> {
  const form = new FormData();
  form.append("file", file);

  const url = `${BASE_URL}/api/v1/watchlist/upload`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "X-API-Key": READ_KEY },
    body: form,
  });

  if (!res.ok) {
    throw new ApiError(res.status, `Upload failed: ${res.statusText}`);
  }

  const envelope = (await res.json()) as APIResponse<WatchlistUploadResult>;
  if (!envelope.success) {
    throw new ApiError(res.status, envelope.error ?? "Upload error");
  }
  return envelope.data;
}

// ─── Portfolio endpoints ───────────────────────────────────────────────────

/** GET /api/v1/portfolio */
export async function fetchPortfolio(): Promise<PortfolioSummary> {
  return apiFetch<PortfolioSummary>("/api/v1/portfolio");
}

/** GET /api/v1/portfolio/trades */
export async function fetchTrades(
  status?: "open" | "closed"
): Promise<Trade[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const qs = params.toString();
  return apiFetch<Trade[]>(`/api/v1/portfolio/trades${qs ? `?${qs}` : ""}`);
}

// ─── System endpoints ──────────────────────────────────────────────────────

/** GET /api/v1/health — no auth required */
export async function fetchHealth(): Promise<HealthResponse> {
  const url = `${BASE_URL}/api/v1/health`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(res.status, `Health check failed: ${res.statusText}`);
  }
  const envelope = (await res.json()) as APIResponse<HealthResponse>;
  return envelope.data;
}

/** GET /api/v1/meta — requires read key */
export async function fetchMeta(): Promise<MetaResponse> {
  return apiFetch<MetaResponse>("/api/v1/meta");
}

// ─── Admin: trigger a pipeline run ─────────────────────────────────────────

export interface TriggerRunOptions {
  scope?: RunScope;
  symbols?: string[];
}

/**
 * Triggers a pipeline run by calling the Next.js Route Handler at
 * /api/proxy/run, which proxies to FastAPI with the server-side admin key.
 * The admin key is NEVER sent from the browser.
 */
export async function triggerRun(
  options: TriggerRunOptions = {}
): Promise<unknown> {
  const res = await fetch("/api/proxy/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope: options.scope ?? "all",
      symbols: options.symbols ?? null,
    }),
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      res.status,
      `Run trigger failed: ${res.statusText}`,
      detail
    );
  }

  return res.json();
}
