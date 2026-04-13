/**
 * app/layout.tsx
 * ───────────────
 * Root layout for Minervini AI.
 *
 * Desktop  → dark sidebar (NavBar) + scrollable main area
 * Mobile   → top bar (title + market status) + main area + bottom tab bar
 *
 * Fonts:
 *   Inter          → sans (body)
 *   JetBrains Mono → mono (scores, symbols, numbers)
 */
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import NavBar from "@/components/NavBar";
import MobileTabBar from "@/components/MobileTabBar";
import MarketStatusBar from "@/components/MarketStatusBar";

/* ── Fonts ────────────────────────────────────────────────────────────────── */
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

/* ── Metadata ─────────────────────────────────────────────────────────────── */
export const metadata: Metadata = {
  title: { default: "Minervini AI", template: "%s · Minervini AI" },
  description: "SEPA stock screener & portfolio dashboard",
};

/* ── Layout ───────────────────────────────────────────────────────────────── */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className="dark"
      suppressHydrationWarning
    >
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased bg-[#0D0D0F] text-white`}
      >
        {/* ── Outer shell: sidebar + content ──────────────────────────── */}
        <div className="flex h-screen w-full overflow-hidden">

          {/* Desktop sidebar — hidden below md ─────────────────────── */}
          <div className="hidden md:flex md:flex-shrink-0 md:h-full">
            <NavBar />
          </div>

          {/* Right column: [mobile top bar] + [main] ───────────────── */}
          <div className="flex flex-1 flex-col overflow-hidden">

            {/* Mobile-only top bar ──────────────────────────────────── */}
            <header className="flex md:hidden items-center justify-between px-4 py-3 bg-[#0D0D0F] border-b border-[#1E1E21] flex-shrink-0">
              <div className="flex items-center gap-2">
                <div className="flex h-7 w-7 items-center justify-center rounded-md bg-teal-500 text-white text-xs font-bold">
                  M
                </div>
                <span className="font-semibold text-sm text-white tracking-wide">
                  Minervini AI
                </span>
              </div>
              {/* Market status pill (client component) */}
              <MarketStatusBar />
            </header>

            {/* Page content ──────────────────────────────────────────── */}
            <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
              {children}
            </main>
          </div>
        </div>

        {/* Mobile bottom tab bar — fixed, hidden above md ─────────── */}
        <div className="md:hidden fixed bottom-0 inset-x-0 z-50">
          <MobileTabBar />
        </div>
      </body>
    </html>
  );
}
