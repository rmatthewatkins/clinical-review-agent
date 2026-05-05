import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Nav from "./nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clinical Review Agent",
  description: "AI-powered clinical peer-review dashboard (readmissions, mortality)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased min-h-screen flex flex-col`}>
        <Nav />
        <main className="max-w-7xl mx-auto px-6 py-8 flex-1 w-full">{children}</main>
        <footer className="border-t border-[var(--border)] px-6 py-4 text-center text-sm text-[var(--muted)]">
          Clinical Review Agent &middot; Powered by Claude
        </footer>
      </body>
    </html>
  );
}
