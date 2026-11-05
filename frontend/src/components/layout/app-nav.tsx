"use client";

import { FilePlus2, GitCompare, History, LayoutList, Network, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: LayoutList },
  { href: "/taxonomy", label: "Taxonomy", icon: Network },
  { href: "/improvement", label: "Improvement", icon: TrendingUp },
  { href: "/compare", label: "Compare runs", icon: GitCompare },
  { href: "/runs", label: "Validation runs", icon: History },
  { href: "/sources/new", label: "New data source", icon: FilePlus2 },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto md:flex-col md:gap-0.5 md:overflow-visible">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              // The left bar marks the active page on the sidebar; on the top bar it sits underneath.
              "flex shrink-0 items-center gap-2.5 border-b-2 border-transparent px-3 py-2 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground md:border-b-0 md:border-l-2",
              active && "border-primary font-medium text-foreground",
            )}
          >
            <Icon className={cn("size-4", active ? "text-primary" : "opacity-70")} strokeWidth={1.75} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
