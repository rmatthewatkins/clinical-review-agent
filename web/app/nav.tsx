"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/", label: "Dashboard" },
  { href: "/readmissions", label: "Readmissions" },
  { href: "/mortality", label: "Mortality" },
  { href: "/reviews", label: "Reviews" },
  { href: "/analytics", label: "Analytics" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <nav className="border-b border-[var(--border)] bg-[var(--card)] px-6 py-3 flex items-center gap-8">
      <span className="font-bold text-lg">Clinical Review Agent</span>
      {navItems.map((item) => {
        const isActive =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`transition-colors text-sm font-medium ${
              isActive
                ? "text-[var(--accent)] border-b-2 border-[var(--accent)] pb-[9px] mb-[-12px]"
                : "text-[var(--muted)] hover:text-[var(--foreground)]"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
