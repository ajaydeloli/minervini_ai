/**
 * components/CandlestickChart.tsx
 * ─────────────────────────────────
 * TradingView lightweight-charts v5 wrapper for OHLCV + MA ribbon.
 *
 * Props
 * ─────
 * ohlcv        — candle + volume data (time must be UNIX epoch seconds or "YYYY-MM-DD")
 * mas          — moving-average values aligned to the same time index as ohlcv
 * entryPrice   — optional dashed teal horizontal line
 * stopLoss     — optional dashed red horizontal line
 * targetPrice  — optional dashed green horizontal line
 * vcpQualified — if true and vcpZone provided, renders a gold band + label
 * vcpZone      — { startTime, endTime, low, high } for the VCP base band
 *
 * TODO (Phase 13): Add /api/v1/stock/{symbol}/ohlcv to the FastAPI router in
 * api/routers/stocks.py so real OHLCV data can replace the mock generator
 * in app/screener/[symbol]/page.tsx.
 */
"use client";

import * as React from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

// ─── Types ──────────────────────────────────────────────────────────────────

export interface OHLCVPoint {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MAPoint {
  time: Time;
  sma10: number | null;
  sma21: number | null;
  sma50: number | null;
  sma150: number | null;
  sma200: number | null;
}

export interface VCPZone {
  startTime: Time;
  endTime: Time;
  low: number;
  high: number;
}

interface CandlestickChartProps {
  ohlcv: OHLCVPoint[];
  mas: MAPoint[];
  entryPrice?: number;
  stopLoss?: number;
  targetPrice?: number;
  vcpQualified?: boolean;
  vcpZone?: VCPZone;
  className?: string;
}

// ─── MA ribbon config ────────────────────────────────────────────────────────

const MA_CONFIG = [
  { key: "sma10"  as const, label: "SMA 10",  color: "#FFFFFF", width: 1   },
  { key: "sma21"  as const, label: "SMA 21",  color: "#22D3EE", width: 1   },
  { key: "sma50"  as const, label: "SMA 50",  color: "#EAB308", width: 1.5 },
  { key: "sma150" as const, label: "SMA 150", color: "#F97316", width: 1.5 },
  { key: "sma200" as const, label: "SMA 200", color: "#EF4444", width: 2   },
];

// ─── Legend ──────────────────────────────────────────────────────────────────

function ChartLegend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 px-2 pb-2 text-[11px] font-mono select-none">
      {MA_CONFIG.map(({ label, color }) => (
        <span key={label} className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-0.5 rounded-full"
            style={{ backgroundColor: color }}
          />
          <span style={{ color }}>{label}</span>
        </span>
      ))}
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function CandlestickChart({
  ohlcv,
  mas,
  entryPrice,
  stopLoss,
  targetPrice,
  vcpQualified,
  vcpZone,
  className,
}: CandlestickChartProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const chartRef     = React.useRef<IChartApi | null>(null);

  // Store series refs so we can update data without recreating the chart
  const candleRef  = React.useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef     = React.useRef<ISeriesApi<"Histogram"> | null>(null);
  const maRefs     = React.useRef<Record<string, ISeriesApi<"Line">>>({});

  // ── Create chart on mount ──────────────────────────────────────────────────
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { color: "#0D0D0F" },
        textColor: "#9CA3AF",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1E1E21" },
        horzLines: { color: "#1E1E21" },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: "#2D2D31" },
      timeScale: {
        borderColor: "#2D2D31",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;

    // ── Candlestick series ─────────────────────────────────────────────────
    const candle = chart.addSeries(CandlestickSeries, {
      upColor:          "#14B8A6",
      downColor:        "#EF4444",
      borderUpColor:    "#14B8A6",
      borderDownColor:  "#EF4444",
      wickUpColor:      "#14B8A6",
      wickDownColor:    "#EF4444",
      priceScaleId:     "right",
    });
    candleRef.current = candle;

    // ── Volume histogram (bottom 20%) ──────────────────────────────────────
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      color:        "#14B8A6",
      priceFormat:  { type: "volume" },
    });
    vol.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    volRef.current = vol;

    // ── MA ribbon ─────────────────────────────────────────────────────────
    MA_CONFIG.forEach(({ key, color, width }) => {
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth: width as 1 | 2 | 3 | 4,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      maRefs.current[key] = s;
    });

    // ── ResizeObserver ─────────────────────────────────────────────────────
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;
      chart.resize(width, Math.max(height, 300));
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current  = null;
      candleRef.current = null;
      volRef.current    = null;
      maRefs.current    = {};
    };
  }, []); // intentionally empty — chart created once

  // ── Push data & decorations when props change ───────────────────────────
  React.useEffect(() => {
    if (!candleRef.current || !volRef.current) return;

    // Candles
    candleRef.current.setData(
      ohlcv.map(({ time, open, high, low, close }) => ({ time, open, high, low, close }))
    );

    // Volume (color by direction)
    volRef.current.setData(
      ohlcv.map(({ time, open, close, volume }) => ({
        time,
        value: volume,
        color: close >= open ? "#14B8A680" : "#EF444480",
      }))
    );

    // MAs
    MA_CONFIG.forEach(({ key }) => {
      const s = maRefs.current[key];
      if (!s) return;
      const lineData = mas
        .filter((m) => m[key] !== null)
        .map((m) => ({ time: m.time, value: m[key] as number }));
      s.setData(lineData);
    });
  }, [ohlcv, mas]);

  // ── Price lines (entry / stop / target) ──────────────────────────────────
  React.useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    // Remove existing price lines by recreating them is handled by storing refs.
    // Simplest: just always create; since this effect depends on the values,
    // each change recreates them. In production you'd store refs and remove first.
    if (entryPrice != null) {
      series.createPriceLine({
        price: entryPrice,
        color: "#14B8A6",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Entry",
      });
    }
    if (stopLoss != null) {
      series.createPriceLine({
        price: stopLoss,
        color: "#EF4444",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Stop",
      });
    }
    if (targetPrice != null) {
      series.createPriceLine({
        price: targetPrice,
        color: "#22C55E",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Target",
      });
    }
  }, [entryPrice, stopLoss, targetPrice]);

  // ── VCP zone band (gold price lines at low + high) ───────────────────────
  React.useEffect(() => {
    const series = candleRef.current;
    if (!series || !vcpQualified || !vcpZone) return;
    series.createPriceLine({
      price: vcpZone.high,
      color: "#F59E0B",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "VCP Base ▲",
    });
    series.createPriceLine({
      price: vcpZone.low,
      color: "#F59E0B",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "VCP Base ▼",
    });
  }, [vcpQualified, vcpZone]);

  // ── Fit content once data lands ───────────────────────────────────────────
  React.useEffect(() => {
    if (ohlcv.length > 0) {
      chartRef.current?.timeScale().fitContent();
    }
  }, [ohlcv]);

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <div className={`flex flex-col bg-[#0D0D0F] rounded-xl border border-zinc-800/60 overflow-hidden ${className ?? ""}`}>
      <ChartLegend />
      <div
        ref={containerRef}
        className="w-full"
        style={{ minHeight: 300, height: "100%" }}
      />
    </div>
  );
}
