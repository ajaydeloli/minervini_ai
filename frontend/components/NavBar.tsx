/**
 * components/NavBar.tsx
 * ──────────────────────
 * Desktop sidebar navigation. Hidden on mobile (layout handles bottom tabs).
 * Active link detected with usePathname(); exact match for "/" and prefix
 * match for all other routes.
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  ScanLine,
  Star,
  Briefcase,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

/* ── Nav item definitions ─────────────────────────────────────────────────── */
interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/",          label: "Dashboard", icon: LayoutDashboard },
  { href: "/screener",  label: "Screener",  icon: ScanLine },
  { href: "/watchlist", label: "Watchlist", icon: Star },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
];

/* ── Active-route check ───────────────────────────────────────────────────── */
function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

/* ── Component ────────────────────────────────────────────────────────────── */
export default function NavBar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-full w-56 flex-col bg-[#0D0D0F] border-r border-[#1E1E21] select-none">

      {/* ── Logo ────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-[#1E1E21]">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-teal-500 text-white text-sm font-bold leading-none">
          M
        </div>
        <span className="text-white font-semibold text-sm tracking-wide">
          Minervini AI
        </span>
      </div>

      {/* ── Nav items ───────────────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto px-2 py-4 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = isActive(href, pathname);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium",
                "border-l-2 transition-colors duration-150",
                active
                  ? "border-teal-500 bg-[#161618] text-white"
                  : "border-transparent text-zinc-400 hover:bg-[#161618] hover:text-zinc-100"
              )}
            >
              <Icon
                size={15}
                className={cn(
                  "flex-shrink-0 transition-colors",
                  active ? "text-teal-400" : "text-zinc-500 group-hover:text-zinc-300"
                )}
              />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* ── Version tag ─────────────────────────────────────────────────── */}
      <div className="px-4 py-3 border-t border-[#1E1E21]">
        <span className="font-mono text-[11px] text-zinc-600 tracking-widest uppercase">
          SEPA v1.5
        </span>
      </div>
    </aside>
  );
}
