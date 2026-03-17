"use client";

import { useEffect, useState } from "react";
import { fetchApi, Analytics } from "@/lib/api";

function HBar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-64 truncate text-right text-[var(--muted)]" title={label}>{label}</span>
      <div className="flex-1 bg-[var(--border)] rounded-full h-5 overflow-hidden">
        <div className="bg-[var(--accent)] h-full rounded-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right font-medium">{value}</span>
    </div>
  );
}

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchApi<Analytics>("/api/analytics").then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!data) return <p className="text-[var(--muted)]">Loading...</p>;

  const factorMax = Math.max(...data.contributing_factors.map((f) => f.count), 1);
  const intervMax = Math.max(...data.recommended_interventions.map((i) => i.count), 1);

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Analytics</h1>

      <div className="grid grid-cols-2 gap-8">
        {/* Contributing Factors */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
          <h2 className="font-semibold mb-4">Top Contributing Factors</h2>
          {data.contributing_factors.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No data available</p>
          ) : (
            <div className="space-y-2">
              {data.contributing_factors.map((f) => (
                <HBar key={f.factor} label={f.factor} value={f.count} max={factorMax} />
              ))}
            </div>
          )}
        </div>

        {/* Factors table */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
          <h2 className="font-semibold mb-4">Contributing Factors — Table</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
                <th className="pb-2">Factor</th>
                <th className="pb-2 text-right">Count</th>
              </tr>
            </thead>
            <tbody>
              {data.contributing_factors.map((f) => (
                <tr key={f.factor} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{f.factor}</td>
                  <td className="py-1.5 text-right">{f.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Recommended Interventions */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
          <h2 className="font-semibold mb-4">Top Recommended Interventions</h2>
          {data.recommended_interventions.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No data available</p>
          ) : (
            <div className="space-y-2">
              {data.recommended_interventions.map((i) => (
                <HBar key={i.intervention} label={i.intervention} value={i.count} max={intervMax} />
              ))}
            </div>
          )}
        </div>

        {/* Interventions table */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5">
          <h2 className="font-semibold mb-4">Recommended Interventions — Table</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
                <th className="pb-2">Intervention</th>
                <th className="pb-2 text-right">Count</th>
              </tr>
            </thead>
            <tbody>
              {data.recommended_interventions.map((i) => (
                <tr key={i.intervention} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{i.intervention}</td>
                  <td className="py-1.5 text-right">{i.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
