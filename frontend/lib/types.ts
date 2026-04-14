/**
 * lib/types.ts
 * ─────────────
 * TypeScript types that mirror the FastAPI Pydantic schemas in api/schemas/.
 *
 * Naming conventions
 * ──────────────────
 * - SetupQuality / RunScope — union string literals (Pydantic Literal fields)
 * - SEPAResult              — mirrors StockDetail (full single-symbol schema)
 * - PortfolioSummary        — mirrors PortfolioSummary + PositionRow
 * - Trade                   — mirrors TradeRow
 * - WatchlistItem           — mirrors WatchlistEntry
 * - HealthResponse          — mirrors the data dict from GET /api/v1/health
 * - MetaResponse            — mirrors the data dict from GET /api/v1/meta
 * - APIResponse<T>          — generic envelope returned by every endpoint
 */

// ─── Scalar union types ────────────────────────────────────────────────────

export type SetupQuality = "A+" | "A" | "B" | "C" | "FAIL";

export type RunScope = "all" | "universe" | "watchlist";

// ─── Trend Template (8 boolean conditions) ─────────────────────────────────

export interface TrendTemplateDetails {
  above_150_200_ma: boolean;
  ma_150_above_ma_200: boolean;
  ma_200_trending_up: boolean;
  ma_50_above_ma_150_200: boolean;
  price_above_ma_50: boolean;
  rs_52w_high: boolean;
  price_above_52w_low_30pct: boolean;
  price_within_25pct_52w_high: boolean;
}

// ─── Fundamental Details (7 conditions) ────────────────────────────────────

export interface FundamentalDetails {
  eps_growth_qoq: boolean;
  eps_growth_yoy: boolean;
  revenue_growth_yoy: boolean;
  roe_positive: boolean;
  debt_to_equity_ok: boolean;
  institutional_sponsorship: boolean;
  earnings_surprise: boolean;
}

// ─── VCP Details ───────────────────────────────────────────────────────────

export interface VCPDetails {
  contraction_count: number | null;
  max_depth_pct: number | null;
  final_depth_pct: number | null;
  vol_ratio: number | null;
  base_weeks: number | null;
  fail_reason: string | null;
  quality_grade: string | null;
}

// ─── SEPAResult — full single-symbol SEPA evaluation ──────────────────────

export interface SEPAResult {
  // Core identification
  symbol: string;
  score: number;                       // 0–100 composite SEPA score
  setup_quality: SetupQuality;
  stage: number;                       // Weinstein stage 1–4
  stage_label: string;

  // Relative strength
  rs_rating: number;                   // 0–99

  // Trend template
  trend_template_pass: boolean;
  conditions_met: number;              // 0–8 TT conditions passed
  trend_template_details: TrendTemplateDetails | null;

  // VCP pattern
  vcp_qualified: boolean;
  vcp_details: VCPDetails | null;

  // Entry / risk
  breakout_triggered: boolean;
  entry_price: number | null;
  stop_loss: number | null;
  risk_pct: number | null;             // (entry − stop) / entry × 100
  rr_ratio: number | null;
  target_price: number | null;

  // Fundamental
  fundamental_pass: boolean | null;
  fundamental_details: FundamentalDetails | null;

  // News / narrative
  news_score: number | null;           // −100 to +100
  narrative: string | null;

  // Metadata
  run_date: string;                    // ISO date "YYYY-MM-DD"
}

// ─── PortfolioSummary ──────────────────────────────────────────────────────

export interface PositionRow {
  symbol: string;
  entry_price: number;
  qty: number;
  stop_loss: number;
  entry_date: string;                  // ISO date "YYYY-MM-DD"
  setup_quality: SetupQuality;
  current_price: number | null;
  unrealised_pnl: number | null;
  unrealised_pnl_pct: number | null;
  pyramided: boolean;
}

export interface PortfolioSummary {
  cash: number;
  open_value: number;
  total_value: number;
  initial_capital: number;
  total_return_pct: number;
  realised_pnl: number;
  unrealised_pnl: number;
  total_trades: number;
  win_rate: number;
  open_positions: number;
  positions: PositionRow[];
}

// ─── Trade (TradeRow) ──────────────────────────────────────────────────────

export interface Trade {
  symbol: string;
  entry_price: number;
  exit_price: number | null;
  qty: number;
  entry_date: string;                  // ISO date "YYYY-MM-DD"
  exit_date: string | null;
  status: "open" | "closed";
  setup_quality: SetupQuality;
  pnl: number | null;
  pnl_pct: number | null;
}

// ─── WatchlistItem (WatchlistEntry) ───────────────────────────────────────

export interface WatchlistItem {
  symbol: string;
  note: string | null;
  added_at: string;                    // ISO datetime "YYYY-MM-DDTHH:MM:SS"
  added_via: string;                   // cli | api | dashboard | file_upload | test
  last_score: number | null;
  last_quality: SetupQuality | null;
  last_run_at: string | null;
}

// ─── HealthResponse ────────────────────────────────────────────────────────

export interface HealthResponse {
  status: "ok" | "degraded" | "no_data" | "error";
  last_run_date: string | null;
  last_run_status: string | null;
  last_run_duration_sec: number | null;
  api_version: string;
}

// ─── MetaResponse ──────────────────────────────────────────────────────────

export interface MetaResponse {
  universe_size: number | null;
  watchlist_size: number | null;
  last_screen_date: string | null;
  a_plus_count: number | null;
  a_count: number | null;
  git_sha: string | null;
  config_hash: string | null;
}

// ─── Generic API envelope ──────────────────────────────────────────────────

export interface APIResponse<T> {
  success: boolean;
  data: T;
  meta?: Record<string, unknown> | null;
  error?: string | null;
}

// ─── Bulk watchlist response ───────────────────────────────────────────────

export interface BulkAddResult {
  added: string[];
  already_exists: string[];
  invalid: string[];
}

// ─── Watchlist file upload response ───────────────────────────────────────

export interface WatchlistUploadResult {
  added: number;
  skipped: number;
  invalid: string[];
  watchlist: WatchlistItem[];
}

// ─── Stock history point ───────────────────────────────────────────────────

export interface StockHistoryPoint {
  run_date: string;
  score: number;
  setup_quality: SetupQuality;
}

// ─── Backtest types ────────────────────────────────────────────────────────

export interface BacktestRunSummary {
  run_id: string;
  run_date: string;
  status: string;
  duration_sec: number;
  a_plus_count: number;
  a_count: number;
}

export interface BacktestReport {
  run_id: string;
  start_date: string;
  end_date: string;
  total_return_pct: number;
  cagr_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate: number;
  total_trades: number;
  regime_stats: {
    regime: string;
    trades: number;
    win_rate: number;
    avg_return: number;
    total_return: number;
  }[];
}

export interface EquityCurvePoint {
  date: string;
  portfolio_value: number;
  benchmark_value: number;
  regime: string;
}

export interface OHLCVPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  sma_50: number | null;
  sma_200: number | null;
  sma_21: number | null;
  sma_150: number | null;
}
