"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchApi, Summary } from "@/lib/api";

function MetricCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
      <p className="text-sm text-[var(--muted)] mb-1">{label}</p>
      <p className="text-3xl font-bold">{value}</p>
    </div>
  );
}

function HBar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-48 truncate text-right text-[var(--muted)]" title={label}>{label}</span>
      <div className="flex-1 bg-[var(--border)] rounded-full h-5 overflow-hidden">
        <div className="bg-[var(--accent)] h-full rounded-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right font-medium">{value}</span>
    </div>
  );
}

const scoreColors = [
  "bg-emerald-500",
  "bg-emerald-400",
  "bg-amber-400",
  "bg-orange-400",
  "bg-red-500",
];

function QuickLinks() {
  const links = [
    { href: "/readmissions", label: "Readmissions", description: "View all readmission pairs" },
    { href: "/reviews", label: "Reviews", description: "Browse completed reviews" },
    { href: "/analytics", label: "Analytics", description: "Explore cohort analytics" },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-10">
      {links.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5 hover:border-[var(--accent)] transition-colors group"
        >
          <p className="font-semibold group-hover:text-[var(--accent)] transition-colors">{link.label}</p>
          <p className="text-sm text-[var(--muted)] mt-1">{link.description}</p>
        </Link>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchApi<Summary>("/api/summary").then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!data) return <p className="text-[var(--muted)]">Loading...</p>;

  const rcEntries = Object.entries(data.root_cause_distribution);
  const rcMax = Math.max(...rcEntries.map(([, v]) => v.count), 1);

  const sdEntries = Object.entries(data.preventability_score_distribution);
  const sdMax = Math.max(...sdEntries.map(([, v]) => v), 1);

  const mpdEntries = Object.entries(data.mean_preventability_by_diagnosis);

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
        <MetricCard label="Patients" value={data.patients} />
        <MetricCard label="Encounters" value={data.encounters} />
        <MetricCard label="Readmission Pairs" value={data.pairs} />
        <MetricCard label="Reviews Completed" value={data.reviews} />
      </div>

      <QuickLinks />

      {data.reviews === 0 ? (
        <p className="text-[var(--muted)]">No completed reviews yet. Run the review pipeline first.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Root cause distribution */}
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
            <h2 className="font-semibold mb-4">Root Cause Distribution</h2>
            <div className="space-y-2">
              {rcEntries.map(([cat, info]) => (
                <HBar key={cat} label={cat} value={info.count} max={rcMax} />
              ))}
            </div>
          </div>

          {/* Preventability score distribution */}
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
            <h2 className="font-semibold mb-4">Preventability Score Distribution</h2>
            <div className="flex items-end gap-3 h-52 px-4 pt-4">
              {sdEntries.map(([score, count], i) => {
                const pct = sdMax > 0 ? (count / sdMax) * 100 : 0;
                const color = scoreColors[i] ?? "bg-[var(--accent)]";
                return (
                  <div key={score} className="flex-1 flex flex-col items-center gap-1">
                    <span className="text-xs font-medium">{count}</span>
                    <div className="w-full rounded-t overflow-hidden relative" style={{ height: "160px" }}>
                      <div className="absolute bottom-0 w-full bg-[var(--border)] rounded-t" style={{ height: "100%" }} />
                      <div
                        className={`absolute bottom-0 w-full ${color} rounded-t transition-all duration-500`}
                        style={{ height: `${pct}%` }}
                      />
                    </div>
                    <span className="text-xs text-[var(--muted)] font-medium">Score {score}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Mean preventability by diagnosis */}
          {mpdEntries.length > 0 && (
            <div className="lg:col-span-2 bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
              <h2 className="font-semibold mb-4">Mean Preventability by Diagnosis (Top 10)</h2>
              <div className="space-y-2">
                {mpdEntries.map(([diag, score]) => (
                  <HBar key={diag} label={diag} value={Number(score.toFixed(1))} max={5} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
