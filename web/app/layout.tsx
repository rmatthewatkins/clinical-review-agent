import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Readmissions Review Agent",
  description: "AI-powered clinical readmissions review dashboard",
};

const navItems = [
  { href: "/", label: "Dashboard" },
  { href: "/reviews", label: "Reviews" },
  { href: "/analytics", label: "Analytics" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <nav className="border-b border-[var(--border)] bg-[var(--card)] px-6 py-3 flex items-center gap-8">
          <span className="font-bold text-lg">Readmissions Agent</span>
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-[var(--muted)] hover:text-[var(--foreground)] transition-colors text-sm font-medium"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
