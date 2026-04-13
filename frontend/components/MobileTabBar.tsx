/**
 * components/MobileTabBar.tsx
 * ────────────────────────────
 * Bottom tab bar rendered on mobile (< md).  Mirrors the desktop NavBar
 * items as icon-only tabs with active teal highlight.
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

interface TabItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const TAB_ITEMS: TabItem[] = [
  { href: "/",          label: "Dashboard", icon: LayoutDashboard },
  { href: "/screener",  label: "Screener",  icon: ScanLine },
  { href: "/watchlist", label: "Watchlist", icon: Star },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
];

function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

export default function MobileTabBar() {
  const pathname = usePathname();

  return (
    <div className="flex items-stretch bg-[#0D0D0F] border-t border-[#1E1E21]">
      {TAB_ITEMS.map(({ href, label, icon: Icon }) => {
        const active = isActive(href, pathname);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex flex-1 flex-col items-center justify-center gap-1 py-2.5 text-[10px] font-medium transition-colors",
              active
                ? "text-teal-400"
                : "text-zinc-500 hover:text-zinc-300"
            )}
          >
            <Icon size={18} className="flex-shrink-0" />
            <span>{label}</span>
          </Link>
        );
      })}
    </div>
  );
}
