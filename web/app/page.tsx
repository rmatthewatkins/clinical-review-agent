"use client";

import { useEffect, useState } from "react";
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

      <div className="grid grid-cols-4 gap-4 mb-10">
        <MetricCard label="Patients" value={data.patients} />
        <MetricCard label="Encounters" value={data.encounters} />
        <MetricCard label="Readmission Pairs" value={data.pairs} />
        <MetricCard label="Reviews Completed" value={data.reviews} />
      </div>

      {data.reviews === 0 ? (
        <p className="text-[var(--muted)]">No completed reviews yet. Run the review pipeline first.</p>
      ) : (
        <div className="grid grid-cols-2 gap-8">
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
            <div className="flex items-end gap-3 h-48 px-4">
              {sdEntries.map(([score, count]) => {
                const pct = sdMax > 0 ? (count / sdMax) * 100 : 0;
                return (
                  <div key={score} className="flex-1 flex flex-col items-center gap-1">
                    <span className="text-xs font-medium">{count}</span>
                    <div className="w-full bg-[var(--border)] rounded-t overflow-hidden" style={{ height: "140px" }}>
                      <div
                        className="w-full bg-[var(--accent)] rounded-t"
                        style={{ height: `${pct}%`, marginTop: `${100 - pct}%` }}
                      />
                    </div>
                    <span className="text-xs text-[var(--muted)]">{score}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Mean preventability by diagnosis */}
          {mpdEntries.length > 0 && (
            <div className="col-span-2 bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
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
